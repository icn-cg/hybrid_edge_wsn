from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from analysis.final_campaign import (
    EXPECTED_COMMIT,
    FinalCampaignAnalysisError,
    LoadedRun,
    _failure_analysis,
    _load_run,
    describe_values,
)


def test_describe_values_uses_sample_standard_deviation() -> None:
    result = describe_values([1, 2, 3])

    assert result == {"n": 3, "mean": 2.0, "stddev": 1.0, "min": 1.0, "max": 3.0}


def test_load_run_independently_validates_unique_measurement_outputs(
    tmp_path: Path,
) -> None:
    raw_campaign = tmp_path / "raw"
    run_dir = raw_campaign / "condition" / "run-1"
    processed_dir = tmp_path / "processed" / "run-1"
    run_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)
    _write_json(
        run_dir / "manifest.json",
        {"run_id": "run-1", "git_commit": EXPECTED_COMMIT, "git_dirty": False},
    )
    _write_json(run_dir / "run_summary.json", {"run_id": "run-1", "status": "success"})
    _write_json(
        processed_dir / "metrics.json",
        {
            "run_id": "run-1",
            "analysis_git_dirty": False,
            "scientific_admission": {"passed": True},
            "virtual_unique_received_readings": 2,
            "collector_messages_actual": 1,
            "collector_messages_unique": 1,
            "collector_bytes_actual": 10,
            "collector_bytes_unique": 10,
        },
    )
    pd.DataFrame(
        [
            {"node_id": "virtual-000", "gateway_received_at_ms": 1, "sequence_status": "IN_ORDER"},
            {"node_id": "virtual-001", "gateway_received_at_ms": 1, "sequence_status": "IN_ORDER"},
        ]
    ).to_csv(processed_dir / "measurement_readings.csv", index=False)
    pd.DataFrame([{"upstream_wire_bytes": 10, "record": "{}"}]).to_csv(
        processed_dir / "measurement_upstream_unique.csv", index=False
    )
    admission = {
        "run_id": "run-1",
        "condition_id": "condition",
        "repetition_index": 0,
        "random_seed": 662,
        "run_directory": str(run_dir),
        "processed_directory": str(processed_dir),
        "admission": {"passed": True},
    }

    loaded = _load_run(admission, raw_campaign.resolve(), {"git_commit": EXPECTED_COMMIT})

    assert len(loaded.measurement_readings) == 2
    assert loaded.metrics["collector_messages_actual"] == 1
    assert loaded.metrics["collector_messages_unique"] == 1

    metrics = json.loads((processed_dir / "metrics.json").read_text())
    metrics["collector_bytes_unique"] = 9
    _write_json(processed_dir / "metrics.json", metrics)
    with pytest.raises(FinalCampaignAnalysisError, match="unique collector byte mismatch"):
        _load_run(admission, raw_campaign.resolve(), {"git_commit": EXPECTED_COMMIT})


def test_failure_analysis_distinguishes_absence_from_packet_loss() -> None:
    runs = [_failure_run(index) for index in range(3)]

    summary, throughput, events = _failure_analysis(runs)

    assert summary["deliberately_absent_total"] == 270
    assert summary["statistics"]["generated_delivery_ratio"]["mean"] == 1.0
    assert summary["statistics"]["scheduled_delivery_ratio"]["mean"] == 0.988
    assert summary["statistics"]["failed_node_resets"]["mean"] == 1.0
    assert summary["statistics"]["healthy_peer_non_in_order"]["mean"] == 0.0
    assert len(throughput) == 180
    assert len(events) == 9


def _failure_run(index: int) -> LoadedRun:
    rows = []
    for node in range(25):
        count = 210 if node == 0 else 300
        for sequence in range(count):
            elapsed = sequence if node else sequence if sequence < 90 else sequence + 90
            status = "RESET" if node == 0 and sequence == 90 else "IN_ORDER"
            rows.append(
                {
                    "node_id": f"virtual-{node:03d}",
                    "gateway_received_at_ms": elapsed * 1000,
                    "sequence_status": status,
                }
            )
    metrics = {
        "measurement_start_ms": 0,
        "scheduled_readings": 7500,
        "generated_readings": 7410,
        "virtual_unique_received_readings": 7410,
        "failure_suspect_detection_time_ms": 2800 + index,
        "failure_offline_detection_time_ms": 4800 + index,
        "recovery_detection_time_ms": 2,
        "healthy_peer_throughput_during_failure": 24.0,
    }
    health = pd.DataFrame(
        [
            {"node_id": "virtual-000", "new_state": "ONLINE"},
            {"node_id": "virtual-000", "new_state": "SUSPECT"},
            {"node_id": "virtual-000", "new_state": "OFFLINE"},
            {"node_id": "virtual-000", "new_state": "ONLINE"},
        ]
    )
    return LoadedRun(
        condition_id="failure-n025-raw",
        repetition_index=index,
        random_seed=662 + index,
        run_id=f"failure-{index}",
        run_dir=Path("."),
        processed_dir=Path("."),
        manifest={"failure_at_seconds": 90, "recovery_at_seconds": 180},
        run_summary={},
        metrics=metrics,
        measurement_readings=pd.DataFrame(rows),
        health_events=health,
    )


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
