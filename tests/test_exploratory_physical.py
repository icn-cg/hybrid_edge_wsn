from __future__ import annotations

import pandas as pd
import pytest

from analysis.exploratory_physical import (
    ExploratoryAnalysisError,
    _cadence_analysis,
    _validate_evidence,
    detect_sustained_onset,
    linear_trend,
)


def test_sustained_onset_is_inferred_only_after_three_complete_minutes() -> None:
    index = pd.date_range("2026-08-17 01:27:00-07:00", periods=7, freq="1min")
    minute_means = pd.Series([52.0, 57.0, 54.0, 57.0, 58.0, 59.0, 60.0], index=index)

    onset = detect_sustained_onset(
        minute_means,
        baseline_mean=51.0,
        baseline_stddev=0.5,
    )

    assert onset["time"] == index[3].isoformat()
    assert onset["threshold_rh"] == pytest.approx(56.0)
    assert onset["status"] == "inferred; not an operator timestamp"


def test_sustained_onset_uses_stricter_statistical_threshold() -> None:
    index = pd.date_range("2026-08-17 01:27:00-07:00", periods=4, freq="1min")
    minute_means = pd.Series([60.0, 61.0, 62.0, 63.0], index=index)

    onset = detect_sustained_onset(
        minute_means,
        baseline_mean=50.0,
        baseline_stddev=4.0,
    )

    assert onset["threshold_rh"] == pytest.approx(62.0)
    assert onset["time"] is None


def test_cadence_separates_receive_tails_from_sequence_gaps() -> None:
    rows = []
    for node_id in ("physical-001", "physical-002", "physical-003"):
        sequences = [10, 11, 12]
        if node_id == "physical-003":
            sequences[-1] = 13
        for timestamp, sequence in zip((1_000, 2_000, 4_100), sequences, strict=True):
            rows.append(
                {
                    "node_id": node_id,
                    "gateway_received_at_ms": timestamp,
                    "sequence": sequence,
                }
            )

    intervals, summary = _cadence_analysis(pd.DataFrame(rows))

    assert len(intervals) == 6
    assert summary["physical-001"]["receive_intervals_gt_2000_ms"] == 1
    assert summary["physical-001"]["sequence_inferred_missing"] == 0
    assert summary["physical-003"]["sequence_step_not_one"] == 1
    assert summary["physical-003"]["sequence_inferred_missing"] == 1


def test_linear_recovery_trend_reports_slope_without_decay_claim() -> None:
    times = pd.date_range("2026-08-17 02:22:00-07:00", periods=4, freq="1min")
    frame = pd.DataFrame(
        {
            "gateway_time": times,
            "gateway_received_at_ms": [
                int(timestamp.timestamp() * 1000) for timestamp in times
            ],
            "humidity_pct": [90.0, 88.0, 86.0, 84.0],
        }
    )

    trend = linear_trend(
        frame,
        "humidity_pct",
        times[0],
        times[-1] + pd.Timedelta(seconds=1),
    )

    assert trend["slope_per_minute"] == pytest.approx(-2.0)
    assert trend["r_squared"] == pytest.approx(1.0)
    assert "not a mechanistic" in trend["interpretation"]


def test_evidence_validation_rejects_pi_mac_payload_mismatch() -> None:
    reading = {
        "version": 1,
        "node_id": "physical-001",
        "node_kind": "physical",
        "sequence": 4,
        "timestamp_ms": 100,
        "type": "reading",
        "temperature_c": 20.0,
        "humidity_pct": 50.0,
        "pressure_hpa": 1013.0,
        "gateway_received_at_ms": 1_000,
        "gateway_received_monotonic_ns": 2_000,
        "sensor_wire_bytes": 180,
        "sequence_status": "FIRST",
    }
    wrapper = {
        "upstream_wire_bytes": 400,
        "record": {
            "type": "raw",
            "record_id": "raw-1",
            "reading": {
                key: reading[key]
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
            },
            "gateway_received_at_ms": reading["gateway_received_at_ms"],
            "gateway_received_monotonic_ns": reading["gateway_received_monotonic_ns"],
            "sensor_wire_bytes": reading["sensor_wire_bytes"],
            "sequence_status": reading["sequence_status"],
        },
    }
    wrapper["record"]["reading"]["temperature_c"] = 99.0
    pi_summary = {
        "gateway": {"valid_messages": 1},
        "storage": {"records_written": 1},
        "upstream": {"upstream_messages": 1, "upstream_bytes": 400},
    }
    mac_summary = {"messages_received": 1, "bytes_received": 400}

    with pytest.raises(ExploratoryAnalysisError, match="payload mismatch"):
        _validate_evidence([reading], [wrapper], pi_summary, mac_summary)
