import csv
import json
from pathlib import Path

import pytest

from analysis.analyze import (
    AnalysisError,
    _validate_comparison,
    analyze_experiment,
    analyze_run,
)


def write_run(tmp_path: Path, *, schema: int = 1, status: str | None = "complete") -> Path:
    run_dir = tmp_path / "run-001"
    run_dir.mkdir(parents=True)
    manifest = {
        "manifest_schema_version": schema,
        "run_id": "run-001",
        "experiment_type": "scaling",
        "node_count": 1,
        "aggregation_mode": "raw",
        "aggregation_window_seconds": 0.0,
        "drop_probability": 0.0,
        "artificial_delay_ms": 0.0,
        "sampling_interval_ms": 100,
        "git_dirty": False,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    (run_dir / "simulator_summary.json").write_text(
        json.dumps(
            {
                "measurement_start_ms": 2_000,
                "measurement_end_ms": 3_000,
                "failure_timestamp_ms": None,
                "recovery_timestamp_ms": None,
            }
        )
    )
    readings = [
        reading(0, gateway_ms=1_900),
        reading(1, gateway_ms=2_100),
        reading(2, gateway_ms=2_200),
    ]
    (run_dir / "readings.ndjson").write_text(
        "".join(json.dumps(item) + "\n" for item in readings)
    )
    upstream = [upstream_wrapper(readings[1], "id-1", 400)]
    upstream.append(upstream[0].copy())
    upstream.append(upstream_wrapper(readings[2], "id-2", 410))
    (run_dir / "upstream.ndjson").write_text(
        "".join(json.dumps(item) + "\n" for item in upstream)
    )
    write_csv(
        run_dir / "node_stats.csv",
        [
            {
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "scheduled_readings": 10,
                "samples_generated": 9,
                "send_attempts": 8,
                "successful_writes": 8,
                "application_drops": 1,
                "incarnations": 1,
            }
        ],
    )
    write_csv(
        run_dir / "system_metrics.csv",
        [
            {
                "timestamp_ms": 2_100,
                "process_cpu_percent": 10,
                "process_rss_bytes": 1000,
                "process_vms_bytes": 2000,
                "system_cpu_percent": 20,
            }
        ],
    )
    write_csv(
        run_dir / "health_events.csv",
        [
            {
                "timestamp_ms": 2_000,
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "old_state": "",
                "new_state": "ONLINE",
                "reason": "initial_message",
                "last_seen_ms": 2_000,
                "last_sequence": 0,
            }
        ],
    )
    (run_dir / "gateway_summary.json").write_text(
        json.dumps({"upstream": {"queue_full_drops": 0}})
    )
    if status is not None:
        (run_dir / "run_summary.json").write_text(
            json.dumps(
                {
                    "run_summary_schema_version": 1,
                    "run_id": "run-001",
                    "status": status,
                    "children": {"collector": 0, "gateway": 0, "simulator": 0},
                }
            )
        )
    return run_dir


def reading(sequence: int, *, gateway_ms: int) -> dict[str, object]:
    return {
        "version": 1,
        "node_id": "virtual-000",
        "node_kind": "virtual",
        "sequence": sequence,
        "timestamp_ms": gateway_ms - 5,
        "type": "reading",
        "temperature_c": 22.0,
        "humidity_pct": 48.0,
        "pressure_hpa": 1013.0,
        "gateway_received_at_ms": gateway_ms,
        "gateway_received_monotonic_ns": 10_000 + sequence,
        "sensor_wire_bytes": 180,
        "sequence_status": "FIRST" if sequence == 0 else "IN_ORDER",
    }


def upstream_wrapper(
    source: dict[str, object], record_id: str, wire_bytes: int
) -> dict[str, object]:
    reading_value = {
        key: source[key]
        for key in (
            "version",
            "node_id",
            "node_kind",
            "sequence",
            "timestamp_ms",
            "type",
            "temperature_c",
            "humidity_pct",
            "pressure_hpa",
        )
    }
    return {
        "collector_received_at_ms": source["gateway_received_at_ms"] + 2,
        "collector_received_monotonic_ns": 20_000,
        "upstream_wire_bytes": wire_bytes,
        "record": {
            "type": "raw",
            "version": 1,
            "record_id": record_id,
            "forwarded_at_ms": source["gateway_received_at_ms"],
            "gateway_received_at_ms": source["gateway_received_at_ms"],
            "gateway_received_monotonic_ns": source["gateway_received_monotonic_ns"],
            "sensor_wire_bytes": source["sensor_wire_bytes"],
            "sequence_status": source["sequence_status"],
            "reading": reading_value,
        },
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_analysis_deduplicates_collector_and_excludes_warmup(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    metrics, output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["gateway_received_readings"] == 2
    assert metrics["scheduled_readings"] == 10
    assert metrics["delivery_ratio"] == pytest.approx(0.2)
    assert metrics["collector_messages_actual"] == 3
    assert metrics["collector_messages_unique"] == 2
    assert metrics["collector_duplicate_record_ids"] == 1
    assert metrics["collector_bytes_actual"] == 1_210
    assert metrics["collector_bytes_unique"] == 810
    assert metrics["upstream_message_reduction"] == pytest.approx(-0.5)
    assert metrics["upstream_message_reduction_actual"] == pytest.approx(-0.5)
    assert metrics["upstream_message_reduction_unique"] == 0
    assert metrics["upstream_byte_reduction"] == pytest.approx(
        1 - 1_210 / metrics["raw_baseline_upstream_bytes"]
    )
    assert metrics["upstream_byte_reduction_actual"] == metrics[
        "upstream_byte_reduction"
    ]
    assert metrics["upstream_byte_reduction_unique"] == pytest.approx(
        1 - 810 / metrics["raw_baseline_upstream_bytes"]
    )
    assert metrics["upstream_byte_reduction_unique"] > metrics[
        "upstream_byte_reduction_actual"
    ]
    assert metrics["latency_median_ms"] == 5
    assert (output / "metrics.json").exists()


def test_missing_or_incompatible_manifest_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AnalysisError, match="missing manifest"):
        analyze_run(empty, processed_root=tmp_path / "processed")

    bad = write_run(tmp_path / "bad-parent", schema=99)
    with pytest.raises(AnalysisError, match="unsupported manifest"):
        analyze_run(bad, processed_root=tmp_path / "processed2")


def test_mixed_scaling_configuration_is_not_silently_combined() -> None:
    base = {
        "aggregation_mode": "raw",
        "aggregation_window_seconds": 0,
        "drop_probability": 0,
        "artificial_delay_ms": 0,
        "sampling_interval_ms": 100,
    }
    incompatible = {**base, "aggregation_mode": "aggregated"}

    with pytest.raises(AnalysisError, match="aggregation_mode"):
        _validate_comparison([base, incompatible], "scaling")

    different_duration = {**base, "duration_seconds": 60}
    with pytest.raises(AnalysisError, match="duration_seconds"):
        _validate_comparison(
            [{**base, "duration_seconds": 30}, different_duration], "scaling"
        )


def test_impairment_analysis_changes_only_one_dimension() -> None:
    base = {
        "node_count": 10,
        "aggregation_mode": "raw",
        "aggregation_window_seconds": 0,
        "sampling_interval_ms": 100,
    }
    both_changed = {
        **base,
        "drop_probability": 0.2,
        "artificial_delay_ms": 10,
    }
    with pytest.raises(AnalysisError, match="one dimension at a time"):
        _validate_comparison(
            [
                {**base, "drop_probability": 0, "artificial_delay_ms": 0},
                both_changed,
            ],
            "impairment",
        )


def test_incomplete_runs_are_rejected(tmp_path: Path) -> None:
    failed = write_run(tmp_path / "failed", status="failed")
    interrupted = write_run(tmp_path / "interrupted", status="interrupted")
    missing = write_run(tmp_path / "missing", status=None)

    with pytest.raises(AnalysisError):
        analyze_run(failed, processed_root=tmp_path / "processed-failed")
    with pytest.raises(AnalysisError):
        analyze_run(interrupted, processed_root=tmp_path / "processed-interrupted")
    with pytest.raises(AnalysisError):
        analyze_run(missing, processed_root=tmp_path / "processed-missing")


def test_mismatched_run_summary_is_rejected(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    summary = json.loads((run_dir / "run_summary.json").read_text())
    summary["run_id"] = "different-run"
    (run_dir / "run_summary.json").write_text(json.dumps(summary))

    with pytest.raises(AnalysisError, match="does not match manifest"):
        analyze_run(run_dir, processed_root=tmp_path / "processed")


def test_warmup_generated_reading_received_in_window_is_flagged(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    leaked = reading(3, gateway_ms=2_300)
    leaked["timestamp_ms"] = 1_500
    with (run_dir / "readings.ndjson").open("a", encoding="utf-8") as output:
        output.write(json.dumps(leaked) + "\n")

    metrics, _output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["gateway_received_readings"] == 3
    assert metrics["delivery_ratio"] is None
    assert metrics["delivery_ratio_valid"] is False
    assert any("before measurement_start" in warning for warning in metrics["warnings"])


def test_delivery_ratio_above_one_is_flagged(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    write_csv(
        run_dir / "node_stats.csv",
        [
            {
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "scheduled_readings": 1,
                "samples_generated": 2,
                "send_attempts": 2,
                "successful_writes": 2,
                "application_drops": 0,
                "incarnations": 1,
            }
        ],
    )

    metrics, _output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["delivery_ratio"] is None
    assert metrics["delivery_ratio_valid"] is False
    assert "received_exceeds_scheduled" in metrics["delivery_ratio_invalid_reasons"]
    assert any("readings exceed scheduled" in warning for warning in metrics["warnings"])
    assert any("generated readings exceed" in warning for warning in metrics["warnings"])


def test_aggregate_window_spanning_warmup_is_excluded(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["aggregation_mode"] = "aggregated"
    manifest["aggregation_window_seconds"] = 0.5
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    (run_dir / "upstream.ndjson").write_text(
        "".join(
            json.dumps(item) + "\n"
            for item in (
                {
                    "collector_received_at_ms": 2_400,
                    "collector_received_monotonic_ns": 30_000,
                    "upstream_wire_bytes": 220,
                    "record": {
                        "type": "aggregate",
                        "record_id": "agg-mixed",
                        "window_start_ms": 1_900,
                        "window_end_ms": 2_400,
                    },
                },
                {
                    "collector_received_at_ms": 2_800,
                    "collector_received_monotonic_ns": 40_000,
                    "upstream_wire_bytes": 230,
                    "record": {
                        "type": "aggregate",
                        "record_id": "agg-inside",
                        "window_start_ms": 2_100,
                        "window_end_ms": 2_600,
                    },
                },
            )
        )
    )

    metrics, _output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["collector_messages_actual"] == 1
    assert metrics["collector_messages_unique"] == 1
    assert metrics["collector_bytes_unique"] == 230
    assert metrics["raw_baseline_messages"] == 2


def test_conflicting_duplicate_record_id_is_rejected(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    records = [json.loads(line) for line in (run_dir / "upstream.ndjson").read_text().splitlines()]
    records[1]["record"]["reading"]["temperature_c"] = 99
    (run_dir / "upstream.ndjson").write_text(
        "".join(json.dumps(item) + "\n" for item in records)
    )

    with pytest.raises(AnalysisError, match="conflicting payloads"):
        analyze_run(run_dir, processed_root=tmp_path / "processed")


def test_comparison_rejects_dirty_runs_and_mixed_sampling_cadence() -> None:
    base = {
        "run_id": "clean",
        "git_dirty": False,
        "aggregation_mode": "raw",
        "aggregation_window_seconds": 0,
        "impairment_mode": "none",
        "drop_probability": 0,
        "artificial_delay_ms": 0,
        "sampling_interval_ms": 100,
        "liveness_check_interval_seconds": 0.05,
        "metrics_interval_seconds": 0.5,
    }
    dirty = {**base, "run_id": "dirty", "git_dirty": True}
    with pytest.raises(AnalysisError, match="dirty Git runs"):
        _validate_comparison([base, dirty], "scaling")
    _validate_comparison([base, dirty], "scaling", allow_dirty=True)

    different_metrics = {**base, "metrics_interval_seconds": 1.0}
    with pytest.raises(AnalysisError, match="metrics_interval_seconds"):
        _validate_comparison([base, different_metrics], "scaling")


def test_experiment_analysis_refuses_stale_processed_output(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    run_dir = write_run(raw_root)
    processed_root = tmp_path / "processed"
    (processed_root / run_dir.name).mkdir(parents=True)

    with pytest.raises(AnalysisError, match="stale processed output"):
        analyze_experiment(
            "scaling",
            raw_root=raw_root,
            processed_root=processed_root,
            figures_root=tmp_path / "figures",
            allow_dirty=True,
        )


def test_failure_analysis_separates_suspect_offline_and_recovery(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path)
    (run_dir / "simulator_summary.json").write_text(
        json.dumps(
            {
                "measurement_start_ms": 2_000,
                "measurement_end_ms": 3_000,
                "failure_node_id": "virtual-000",
                "failure_timestamp_ms": 2_050,
                "recovery_timestamp_ms": 2_500,
            }
        )
    )
    write_csv(
        run_dir / "health_events.csv",
        [
            {
                "timestamp_ms": 2_100,
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "old_state": "ONLINE",
                "new_state": "SUSPECT",
                "reason": "liveness_timeout",
                "last_seen_ms": 2_040,
                "last_sequence": 4,
            },
            {
                "timestamp_ms": 2_200,
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "old_state": "SUSPECT",
                "new_state": "OFFLINE",
                "reason": "liveness_timeout",
                "last_seen_ms": 2_040,
                "last_sequence": 4,
            },
            {
                "timestamp_ms": 2_550,
                "node_id": "virtual-000",
                "node_kind": "virtual",
                "old_state": "OFFLINE",
                "new_state": "ONLINE",
                "reason": "valid_message_recovery",
                "last_seen_ms": 2_550,
                "last_sequence": 0,
            },
        ],
    )

    metrics, _output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["failure_detection_time_ms"] == 50
    assert metrics["failure_suspect_detection_time_ms"] == 50
    assert metrics["failure_offline_detection_time_ms"] == 150
    assert metrics["recovery_detection_time_ms"] == 50


def test_scientific_admission_requires_explicit_integrity_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = write_run(tmp_path)
    summary = json.loads((run_dir / "run_summary.json").read_text())
    summary["status"] = "success"
    summary["collector"] = {
        "messages_received": 3,
        "bytes_received": 1_210,
        "invalid_messages": 0,
        "overlong_messages": 0,
        "truncated_messages": 0,
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary))
    gateway = {
        "gateway": {
            "invalid_messages": 0,
            "malformed_json": 0,
            "rejected_readings": 0,
            "schema_rejections": 0,
            "overlong_messages": 0,
            "truncated_messages": 0,
        },
        "storage": {"records_enqueued": 3, "records_written": 3},
        "upstream": {
            "upstream_messages": 3,
            "upstream_bytes": 1_210,
            "queue_full_drops": 0,
            "records_abandoned_on_shutdown": 0,
        },
        "events": {
            "gateway": {"queue_full_drops": 0},
            "health": {"queue_full_drops": 0},
        },
    }
    (run_dir / "gateway_summary.json").write_text(json.dumps(gateway))
    monkeypatch.setattr("analysis.analyze.git_context", lambda _repository: ("abc", False, []))

    metrics, _output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["scientific_admission"]["passed"] is True
    assert metrics["scientific_admission"]["failed_checks"] == []


def test_scientific_admission_preserves_but_excludes_failed_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = write_run(tmp_path)
    monkeypatch.setattr("analysis.analyze.git_context", lambda _repository: ("abc", False, []))

    metrics, output = analyze_run(run_dir, processed_root=tmp_path / "processed")

    assert metrics["scientific_admission"]["passed"] is False
    assert "run_summary_status_success" in metrics["scientific_admission"][
        "failed_checks"
    ]
    assert (output / "metrics.json").exists()
