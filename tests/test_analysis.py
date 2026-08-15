import csv
import json
from pathlib import Path

import pytest

from analysis.analyze import AnalysisError, _validate_comparison, analyze_run


def write_run(tmp_path: Path, *, schema: int = 1) -> Path:
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
    assert metrics["collector_bytes_unique"] == 810
    assert metrics["upstream_message_reduction"] == 0
    assert metrics["upstream_byte_reduction"] == pytest.approx(
        1 - 810 / metrics["raw_baseline_upstream_bytes"]
    )
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
