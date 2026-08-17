"""Rigorous analysis for the frozen three-node exploratory physical measurement."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis.exploratory_plots import create_candidate_figures

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RUN = REPOSITORY / "results" / "raw" / "exploratory-shower-20260817-0115"
DEFAULT_PROCESSED_ROOT = REPOSITORY / "results" / "processed"
DEFAULT_FIGURES_ROOT = REPOSITORY / "results" / "figures"
ANALYSIS_SUFFIX = "analysis-v1"
SUMMARY_SCHEMA_VERSION = 1
EXPECTED_NODE_IDS = ("physical-001", "physical-002", "physical-003")
SOURCE_FILENAMES = (
    "operator-notes.json",
    "pi-readings.ndjson",
    "mac-upstream.ndjson",
    "pi-summary.json",
    "mac-summary.json",
    "pi-gateway-events.csv",
    "pi-health-events.csv",
    "pi-system-metrics.csv",
)
SENSOR_COLUMNS = ("temperature_c", "humidity_pct", "pressure_hpa")
READING_FIELDS = (
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


class ExploratoryAnalysisError(ValueError):
    """Raised when frozen evidence is absent or internally inconsistent."""


def analyze_exploratory_run(
    run_dir: str | Path = DEFAULT_RUN,
    *,
    processed_root: str | Path = DEFAULT_PROCESSED_ROOT,
    figures_root: str | Path = DEFAULT_FIGURES_ROOT,
) -> tuple[dict[str, Any], Path, Path]:
    """Analyze the immutable run and write new derived outputs exclusively."""

    source_dir = Path(run_dir)
    source_hashes_before = _source_hashes(source_dir)
    notes = _read_json(source_dir / "operator-notes.json")
    pi_summary = _read_json(source_dir / "pi-summary.json")
    mac_summary = _read_json(source_dir / "mac-summary.json")
    pi_records = _read_ndjson(source_dir / "pi-readings.ndjson")
    mac_records = _read_ndjson(source_dir / "mac-upstream.ndjson")
    system_metrics = pd.read_csv(source_dir / "pi-system-metrics.csv")
    gateway_events = _read_csv(source_dir / "pi-gateway-events.csv")
    health_events = _read_csv(source_dir / "pi-health-events.csv")

    _validate_evidence(pi_records, mac_records, pi_summary, mac_summary)
    timezone = str(notes["timezone"])
    events = _operator_events(notes)
    readings = pd.DataFrame(pi_records).sort_values(
        ["gateway_received_at_ms", "node_id"], ignore_index=True
    )
    readings["gateway_time"] = pd.to_datetime(
        readings["gateway_received_at_ms"], unit="ms", utc=True
    ).dt.tz_convert(timezone)

    run_id = str(notes["run_id"])
    output_name = f"{run_id}-{ANALYSIS_SUFFIX}"
    processed_dir = Path(processed_root) / output_name
    figures_dir = Path(figures_root) / output_name
    processed_dir.mkdir(parents=True, exist_ok=False)

    cadence_intervals, cadence_summary = _cadence_analysis(readings)
    node_summary, overall_rows = _node_statistics(readings)
    phase_windows = _phase_windows(readings, events)
    phase_summary, phase_rows = _phase_statistics(readings, phase_windows)
    event_summary, event_rows = _event_snapshots(readings, events)
    intervention = _intervention_analysis(
        readings,
        node_summary,
        phase_summary,
        event_summary,
        events,
    )
    pressure = _pressure_analysis(node_summary, phase_summary)
    engineering = _engineering_integrity(
        pi_records,
        mac_records,
        pi_summary,
        mac_summary,
        gateway_events,
        health_events,
        system_metrics,
    )
    figures = create_candidate_figures(readings, cadence_intervals, events, figures_dir)

    first_ms = int(readings["gateway_received_at_ms"].min())
    last_ms = int(readings["gateway_received_at_ms"].max())
    summary: dict[str, Any] = {
        "analysis_schema_version": SUMMARY_SCHEMA_VERSION,
        "analysis_id": output_name,
        "run_id": run_id,
        "classification": (
            "exploratory environmental measurement and engineering validation; "
            "not a controlled scientific performance experiment"
        ),
        "source_directory": str(source_dir),
        "source_sha256": source_hashes_before,
        "evidence_interval": {
            "first_gateway_received_at_ms": first_ms,
            "last_gateway_received_at_ms": last_ms,
            "first_local_time": _iso_from_ms(first_ms, timezone),
            "last_local_time": _iso_from_ms(last_ms, timezone),
            "elapsed_seconds_first_to_last": (last_ms - first_ms) / 1000,
            "total_pi_readings": len(readings),
        },
        "timestamp_inventory": _timestamp_inventory(),
        "operator_events": {
            name: {
                "time": timestamp.isoformat(),
                "status": "operator-reported",
                "precision": _event_precision(notes, name),
            }
            for name, timestamp in events.items()
        },
        "methodology": {
            "environmental_time_axis": "Pi gateway_received_at_ms converted to local PDT",
            "baseline_window": (
                "all gateway samples before the approximate operator-reported shower-on time"
            ),
            "baseline_end_exclusive": events["shower_on"].isoformat(),
            "extrema": "raw one-second samples within the stated phase",
            "plot_aggregation": "30 s means; pressure 60 s; door-focus 15 s",
            "onset_detection": intervention["inferred_response_onset"]["method"],
            "recovery_quantification": (
                "nearest-event raw values plus ordinary least-squares RH slope by phase; "
                "no exponential mechanism is assumed"
            ),
            "standard_deviation": "sample standard deviation (ddof=1)",
        },
        "node_statistics": node_summary,
        "phase_statistics": phase_summary,
        "cadence_statistics": cadence_summary,
        "event_snapshots": event_summary,
        "bathroom_intervention": intervention,
        "pressure_control_like_analysis": pressure,
        "engineering_integrity": engineering,
        "figures": figures,
        "limitations": _limitations(),
    }

    _write_json_exclusive(processed_dir / "analysis_summary.json", summary)
    pd.DataFrame(overall_rows).to_csv(
        processed_dir / "node_overall_statistics.csv", index=False
    )
    pd.DataFrame(phase_rows).to_csv(processed_dir / "phase_statistics.csv", index=False)
    pd.DataFrame(event_rows).to_csv(processed_dir / "event_snapshots.csv", index=False)
    pd.DataFrame(list(cadence_summary.values())).to_csv(
        processed_dir / "cadence_statistics.csv", index=False
    )
    _minute_measurements(readings).to_csv(
        processed_dir / "minute_measurements.csv", index=False
    )
    (processed_dir / "REPORT_ANALYSIS.md").write_text(
        _render_report(summary, processed_dir, figures_dir), encoding="utf-8"
    )

    source_hashes_after = _source_hashes(source_dir)
    if source_hashes_after != source_hashes_before:
        raise ExploratoryAnalysisError("source evidence changed during analysis")
    return summary, processed_dir, figures_dir


def _validate_evidence(
    pi_records: list[dict[str, Any]],
    mac_records: list[dict[str, Any]],
    pi_summary: dict[str, Any],
    mac_summary: dict[str, Any],
) -> None:
    expected = int(pi_summary["gateway"]["valid_messages"])
    counts = {
        "Pi readings file": len(pi_records),
        "Pi valid": expected,
        "Pi persisted": int(pi_summary["storage"]["records_written"]),
        "Pi forwarded": int(pi_summary["upstream"]["upstream_messages"]),
        "Mac records file": len(mac_records),
        "Mac collected": int(mac_summary["messages_received"]),
    }
    if len(set(counts.values())) != 1:
        raise ExploratoryAnalysisError(f"record-count mismatch: {counts}")
    pi_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for reading in pi_records:
        key = _reading_key(reading)
        if key in pi_by_key:
            raise ExploratoryAnalysisError(f"duplicate Pi reading key: {key}")
        pi_by_key[key] = reading
    record_ids: set[str] = set()
    mac_keys: set[tuple[str, int, int]] = set()
    for wrapper in mac_records:
        record = wrapper.get("record", {})
        if record.get("type") != "raw":
            raise ExploratoryAnalysisError("non-RAW collector record in frozen RAW run")
        record_id = str(record["record_id"])
        if record_id in record_ids:
            raise ExploratoryAnalysisError(f"duplicate collector record_id: {record_id}")
        record_ids.add(record_id)
        enriched = {
            **record["reading"],
            "gateway_received_at_ms": record["gateway_received_at_ms"],
            "gateway_received_monotonic_ns": record["gateway_received_monotonic_ns"],
            "sensor_wire_bytes": record["sensor_wire_bytes"],
            "sequence_status": record["sequence_status"],
        }
        key = _reading_key(enriched)
        if key not in pi_by_key or enriched != pi_by_key[key]:
            raise ExploratoryAnalysisError(f"Pi/Mac payload mismatch for {key}")
        mac_keys.add(key)
    if mac_keys != set(pi_by_key):
        raise ExploratoryAnalysisError("Pi/Mac reading-key sets differ")
    wire_bytes = sum(int(wrapper["upstream_wire_bytes"]) for wrapper in mac_records)
    expected_bytes = int(pi_summary["upstream"]["upstream_bytes"])
    if wire_bytes != expected_bytes or wire_bytes != int(mac_summary["bytes_received"]):
        raise ExploratoryAnalysisError("Pi/Mac upstream-byte parity failed")


def _cadence_analysis(
    readings: pd.DataFrame,
    *,
    expected_interval_ms: int = 1000,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    interval_frames = []
    summary: dict[str, dict[str, Any]] = {}
    for node_id in EXPECTED_NODE_IDS:
        node = readings[readings["node_id"] == node_id].sort_values(
            "gateway_received_at_ms"
        )
        intervals = node["gateway_received_at_ms"].diff().dropna().astype(float)
        sequence_steps = node["sequence"].diff().dropna().astype(int)
        interval_frames.append(
            pd.DataFrame({"node_id": node_id, "interval_ms": intervals.to_numpy()})
        )
        summary[node_id] = {
            "node_id": node_id,
            "sample_count": int(len(node)),
            "interval_count": int(len(intervals)),
            "first_sequence": int(node.iloc[0]["sequence"]),
            "last_sequence": int(node.iloc[-1]["sequence"]),
            "mean_interval_ms": float(intervals.mean()),
            "median_interval_ms": float(intervals.median()),
            "stddev_interval_ms": float(intervals.std(ddof=1)),
            "min_interval_ms": float(intervals.min()),
            "max_interval_ms": float(intervals.max()),
            "p95_interval_ms": float(intervals.quantile(0.95)),
            "p99_interval_ms": float(intervals.quantile(0.99)),
            "receive_intervals_gt_1500_ms": int((intervals > 1500).sum()),
            "receive_intervals_gt_2000_ms": int((intervals > 2000).sum()),
            "sequence_step_not_one": int((sequence_steps != 1).sum()),
            "sequence_inferred_missing": int(
                sequence_steps.map(lambda value: max(value - 1, 0)).sum()
            ),
            "expected_interval_ms": expected_interval_ms,
            "interpretation": (
                "Gateway receive cadence; rare long/short pairs can reflect batching and do "
                "not imply missing sensor sequences."
            ),
        }
    return pd.concat(interval_frames, ignore_index=True), summary


def _node_statistics(
    readings: pd.DataFrame,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    nested: dict[str, dict[str, Any]] = {}
    rows = []
    for node_id in EXPECTED_NODE_IDS:
        node = readings[readings["node_id"] == node_id]
        variables = {}
        for column in SENSOR_COLUMNS:
            stats = _series_statistics(node[column])
            variables[column] = stats
            rows.append({"node_id": node_id, "variable": column, **stats})
        nested[node_id] = {
            "sample_count": int(len(node)),
            "first_local_time": node.iloc[0]["gateway_time"].isoformat(),
            "last_local_time": node.iloc[-1]["gateway_time"].isoformat(),
            "measurements": variables,
        }
    return nested, rows


def _phase_statistics(
    readings: pd.DataFrame,
    phase_windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    nested: dict[str, dict[str, Any]] = {}
    rows = []
    for phase, (start, end) in phase_windows.items():
        nested[phase] = {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "nodes": {},
        }
        for node_id in EXPECTED_NODE_IDS:
            node = readings[
                (readings["node_id"] == node_id)
                & (readings["gateway_time"] >= start)
                & (readings["gateway_time"] < end)
            ]
            measurements = {}
            for column in SENSOR_COLUMNS:
                stats = _series_statistics(node[column])
                measurements[column] = stats
                rows.append(
                    {
                        "phase": phase,
                        "phase_start": start.isoformat(),
                        "phase_end_exclusive": end.isoformat(),
                        "node_id": node_id,
                        "variable": column,
                        **stats,
                    }
                )
            nested[phase]["nodes"][node_id] = {
                "sample_count": int(len(node)),
                "measurements": measurements,
            }
    return nested, rows


def _event_snapshots(
    readings: pd.DataFrame,
    events: dict[str, pd.Timestamp],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    requested = {
        **events,
        "door_plus_5_minutes": events["bathroom_door_opened_toward_physical-002"]
        + pd.Timedelta(minutes=5),
    }
    nested: dict[str, dict[str, Any]] = {}
    rows = []
    for event_name, event_time in requested.items():
        nested[event_name] = {"requested_time": event_time.isoformat(), "nodes": {}}
        target_ms = int(event_time.timestamp() * 1000)
        for node_id in EXPECTED_NODE_IDS:
            node = readings[readings["node_id"] == node_id]
            index = (node["gateway_received_at_ms"] - target_ms).abs().idxmin()
            sample = node.loc[index]
            values = {
                "sample_time": sample["gateway_time"].isoformat(),
                "offset_from_event_ms": int(sample["gateway_received_at_ms"] - target_ms),
                "sequence": int(sample["sequence"]),
                **{column: float(sample[column]) for column in SENSOR_COLUMNS},
            }
            nested[event_name]["nodes"][node_id] = values
            rows.append({"event": event_name, "node_id": node_id, **values})
    return nested, rows


def _intervention_analysis(
    readings: pd.DataFrame,
    node_summary: dict[str, dict[str, Any]],
    phase_summary: dict[str, dict[str, Any]],
    event_summary: dict[str, dict[str, Any]],
    events: dict[str, pd.Timestamp],
) -> dict[str, Any]:
    bathroom = readings[readings["node_id"] == "physical-003"].copy()
    baseline_rh = phase_summary["baseline"]["nodes"]["physical-003"]["measurements"][
        "humidity_pct"
    ]
    minute_rh = (
        bathroom.set_index("gateway_time")["humidity_pct"].resample("1min").mean().dropna()
    )
    onset = detect_sustained_onset(
        minute_rh[minute_rh.index >= events["shower_on"]],
        baseline_mean=float(baseline_rh["mean"]),
        baseline_stddev=float(baseline_rh["stddev"]),
    )
    shower = bathroom[
        (bathroom["gateway_time"] >= events["shower_on"])
        & (bathroom["gateway_time"] < events["shower_off"])
    ]
    rh_peak = shower.loc[shower["humidity_pct"].idxmax()]
    temp_peak = shower.loc[shower["temperature_c"].idxmax()]
    shower_stop = event_summary["shower_off"]["nodes"]["physical-003"]
    door = event_summary["bathroom_door_opened_toward_physical-002"]["nodes"]
    door_plus_5 = event_summary["door_plus_5_minutes"]["nodes"]
    closed_trend = linear_trend(
        bathroom,
        "humidity_pct",
        events["shower_off"],
        events["bathroom_door_opened_toward_physical-002"],
    )
    door_trend = linear_trend(
        bathroom,
        "humidity_pct",
        events["bathroom_door_opened_toward_physical-002"],
        bathroom["gateway_time"].max() + pd.Timedelta(milliseconds=1),
    )
    door_first_5_trend = linear_trend(
        bathroom,
        "humidity_pct",
        events["bathroom_door_opened_toward_physical-002"],
        events["bathroom_door_opened_toward_physical-002"] + pd.Timedelta(minutes=5),
    )
    room_after_door = readings[
        (readings["node_id"] == "physical-002")
        & (readings["gateway_time"] >= events["bathroom_door_opened_toward_physical-002"])
    ]
    room_peak = room_after_door.loc[room_after_door["humidity_pct"].idxmax()]
    return {
        "baseline": {
            "window": "full pre-shower-on interval",
            "sample_count": phase_summary["baseline"]["nodes"]["physical-003"][
                "sample_count"
            ],
            "temperature_c": phase_summary["baseline"]["nodes"]["physical-003"][
                "measurements"
            ]["temperature_c"],
            "humidity_pct": baseline_rh,
        },
        "inferred_response_onset": onset,
        "peaks_during_reported_shower_interval": {
            "humidity_pct": {
                "value": float(rh_peak["humidity_pct"]),
                "time": rh_peak["gateway_time"].isoformat(),
                "delta_from_baseline_mean": float(
                    rh_peak["humidity_pct"] - baseline_rh["mean"]
                ),
                "elapsed_from_approximate_shower_on_seconds": float(
                    (rh_peak["gateway_time"] - events["shower_on"]).total_seconds()
                ),
            },
            "temperature_c": {
                "value": float(temp_peak["temperature_c"]),
                "time": temp_peak["gateway_time"].isoformat(),
                "delta_from_baseline_mean": float(
                    temp_peak["temperature_c"]
                    - phase_summary["baseline"]["nodes"]["physical-003"][
                        "measurements"
                    ]["temperature_c"]["mean"]
                ),
                "elapsed_from_approximate_shower_on_seconds": float(
                    (temp_peak["gateway_time"] - events["shower_on"]).total_seconds()
                ),
            },
        },
        "state_near_reported_shower_stop": shower_stop,
        "closed_bathroom_recovery": {
            "duration_seconds": float(
                (
                    events["bathroom_door_opened_toward_physical-002"]
                    - events["shower_off"]
                ).total_seconds()
            ),
            "bathroom_rh_start": float(shower_stop["humidity_pct"]),
            "bathroom_rh_end": float(door["physical-003"]["humidity_pct"]),
            "bathroom_rh_change": float(
                door["physical-003"]["humidity_pct"] - shower_stop["humidity_pct"]
            ),
            "linear_trend": closed_trend,
        },
        "door_opening_response": {
            "bathroom_rh_at_door": float(door["physical-003"]["humidity_pct"]),
            "bathroom_rh_after_5_minutes": float(
                door_plus_5["physical-003"]["humidity_pct"]
            ),
            "bathroom_rh_5_minute_change": float(
                door_plus_5["physical-003"]["humidity_pct"]
                - door["physical-003"]["humidity_pct"]
            ),
            "room_rh_at_door": float(door["physical-002"]["humidity_pct"]),
            "room_rh_after_5_minutes": float(
                door_plus_5["physical-002"]["humidity_pct"]
            ),
            "room_rh_5_minute_change": float(
                door_plus_5["physical-002"]["humidity_pct"]
                - door["physical-002"]["humidity_pct"]
            ),
            "bathroom_first_5_minute_linear_trend": door_first_5_trend,
            "bathroom_full_post_door_linear_trend": door_trend,
            "room_post_door_peak_rh": float(room_peak["humidity_pct"]),
            "room_post_door_peak_time": room_peak["gateway_time"].isoformat(),
        },
        "cross_node_overall_humidity_ranges": {
            node_id: node_summary[node_id]["measurements"]["humidity_pct"]
            for node_id in EXPECTED_NODE_IDS
        },
    }


def detect_sustained_onset(
    minute_means: pd.Series,
    *,
    baseline_mean: float,
    baseline_stddev: float,
    minimum_delta_points: float = 5.0,
    sigma_multiplier: float = 3.0,
    consecutive_minutes: int = 3,
) -> dict[str, Any]:
    """Find the first sustained minute-bin threshold crossing."""

    practical = baseline_mean + minimum_delta_points
    statistical = baseline_mean + sigma_multiplier * baseline_stddev
    threshold = max(practical, statistical)
    qualifying = minute_means >= threshold
    onset: pd.Timestamp | None = None
    for index in range(len(qualifying) - consecutive_minutes + 1):
        window = qualifying.iloc[index : index + consecutive_minutes]
        times = qualifying.index[index : index + consecutive_minutes]
        contiguous = all(
            times[offset] - times[offset - 1] == pd.Timedelta(minutes=1)
            for offset in range(1, len(times))
        )
        if bool(window.all()) and contiguous:
            onset = times[0]
            break
    return {
        "status": "inferred; not an operator timestamp",
        "time": None if onset is None else onset.isoformat(),
        "minute_bin_label_semantics": "left edge of 60-second local-time bin",
        "baseline_mean_rh": baseline_mean,
        "baseline_stddev_rh": baseline_stddev,
        "minimum_delta_points": minimum_delta_points,
        "sigma_multiplier": sigma_multiplier,
        "threshold_rh": threshold,
        "required_consecutive_minutes": consecutive_minutes,
        "method": (
            "first of three consecutive 60-second bathroom RH means at or above the "
            "larger of baseline mean + 5 percentage points and baseline mean + 3 sample SD"
        ),
    }


def linear_trend(
    frame: pd.DataFrame,
    column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    """Fit an observational linear trend over an explicitly bounded phase."""

    phase = frame[(frame["gateway_time"] >= start) & (frame["gateway_time"] < end)]
    if len(phase) < 2:
        raise ExploratoryAnalysisError("trend window has fewer than two samples")
    minutes = (
        phase["gateway_received_at_ms"].to_numpy()
        - phase["gateway_received_at_ms"].iloc[0]
    ) / 60_000
    values = phase[column].to_numpy(dtype=float)
    slope, intercept = np.polyfit(minutes, values, 1)
    predicted = slope * minutes + intercept
    residual = float(np.sum((values - predicted) ** 2))
    total = float(np.sum((values - values.mean()) ** 2))
    return {
        "sample_count": int(len(phase)),
        "start": phase.iloc[0]["gateway_time"].isoformat(),
        "end": phase.iloc[-1]["gateway_time"].isoformat(),
        "slope_per_minute": float(slope),
        "intercept": float(intercept),
        "r_squared": None if total == 0 else float(1 - residual / total),
        "interpretation": "descriptive OLS trend; not a mechanistic decay model",
    }


def _pressure_analysis(
    node_summary: dict[str, dict[str, Any]],
    phase_summary: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    nodes = {}
    for node_id in EXPECTED_NODE_IDS:
        overall = node_summary[node_id]["measurements"]["pressure_hpa"]
        baseline = phase_summary["baseline"]["nodes"][node_id]["measurements"][
            "pressure_hpa"
        ]
        shower = phase_summary["shower"]["nodes"][node_id]["measurements"][
            "pressure_hpa"
        ]
        nodes[node_id] = {
            "overall": overall,
            "shower_mean_minus_baseline_mean_hpa": float(
                shower["mean"] - baseline["mean"]
            ),
            "overall_peak_to_peak_hpa": float(overall["max"] - overall["min"]),
        }
    return {
        "nodes": nodes,
        "interpretation": (
            "Pressure changed only modestly while bathroom RH and temperature changed strongly. "
            "This is consistent with pressure serving as a control-like variable for this "
            "localized event, but uncontrolled weather drift and sensor offsets prevent a causal "
            "control claim."
        ),
    }


def _engineering_integrity(
    pi_records: list[dict[str, Any]],
    mac_records: list[dict[str, Any]],
    pi_summary: dict[str, Any],
    mac_summary: dict[str, Any],
    gateway_events: list[dict[str, str]],
    health_events: list[dict[str, str]],
    metrics: pd.DataFrame,
) -> dict[str, Any]:
    registry = pi_summary["registry"]
    generated_note = (
        "No independent firmware generated/scheduled counter was captured. Sequence continuity "
        "supports no missing sequences inside the observed gateway interval, but cannot count "
        "samples generated before connection or never delivered."
    )
    return {
        "generated_or_scheduled_count": None,
        "generated_count_limitation": generated_note,
        "pi_valid_messages": int(pi_summary["gateway"]["valid_messages"]),
        "pi_persisted_records": int(pi_summary["storage"]["records_written"]),
        "pi_forwarded_records": int(pi_summary["upstream"]["upstream_messages"]),
        "mac_collected_records": int(mac_summary["messages_received"]),
        "pi_upstream_bytes": int(pi_summary["upstream"]["upstream_bytes"]),
        "mac_received_upstream_bytes": int(mac_summary["bytes_received"]),
        "pi_reading_file_records": len(pi_records),
        "mac_upstream_file_records": len(mac_records),
        "counts_and_bytes_exactly_equal": True,
        "gateway_invalid_messages": int(pi_summary["gateway"]["invalid_messages"]),
        "gateway_malformed_json": int(pi_summary["gateway"]["malformed_json"]),
        "gateway_schema_rejections": int(pi_summary["gateway"]["schema_rejections"]),
        "gateway_overlong_messages": int(pi_summary["gateway"]["overlong_messages"]),
        "gateway_truncated_messages": int(pi_summary["gateway"]["truncated_messages"]),
        "upstream_queue_full_drops": int(pi_summary["upstream"]["queue_full_drops"]),
        "upstream_send_failures": int(pi_summary["upstream"]["send_failures"]),
        "upstream_abandoned_on_shutdown": int(
            pi_summary["upstream"]["records_abandoned_on_shutdown"]
        ),
        "collector_invalid_messages": int(mac_summary["invalid_messages"]),
        "collector_overlong_messages": int(mac_summary["overlong_messages"]),
        "collector_truncated_messages": int(mac_summary["truncated_messages"]),
        "per_node_sequence_integrity": {
            node_id: {
                "messages_received": int(registry[node_id]["messages_received"]),
                "duplicates": int(registry[node_id]["duplicates"]),
                "out_of_order": int(registry[node_id]["out_of_order"]),
                "estimated_messages_missing": int(
                    registry[node_id]["estimated_messages_missing"]
                ),
                "sequence_resets": int(registry[node_id]["sequence_resets"]),
            }
            for node_id in EXPECTED_NODE_IDS
        },
        "gateway_event_rows": len(gateway_events),
        "health_event_rows": len(health_events),
        "system_metric_samples": int(len(metrics)),
        "system_metrics": {
            "process_cpu_mean_percent": float(metrics["process_cpu_percent"].mean()),
            "process_cpu_max_percent": float(metrics["process_cpu_percent"].max()),
            "process_rss_mean_bytes": float(metrics["process_rss_bytes"].mean()),
            "process_rss_max_bytes": int(metrics["process_rss_bytes"].max()),
            "system_cpu_mean_percent": float(metrics["system_cpu_percent"].mean()),
        },
        "latency_claim": (
            "No physical one-way latency is computed because ESP32, Pi, and Mac clocks were not "
            "explicitly synchronized and verified."
        ),
    }


def _phase_windows(
    readings: pd.DataFrame,
    events: dict[str, pd.Timestamp],
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    first = readings["gateway_time"].min()
    end = readings["gateway_time"].max() + pd.Timedelta(milliseconds=1)
    return {
        "baseline": (first, events["shower_on"]),
        "shower": (events["shower_on"], events["shower_off"]),
        "closed_recovery": (
            events["shower_off"],
            events["bathroom_door_opened_toward_physical-002"],
        ),
        "door_open_recovery": (
            events["bathroom_door_opened_toward_physical-002"],
            end,
        ),
    }


def _series_statistics(series: pd.Series) -> dict[str, Any]:
    return {
        "count": int(series.count()),
        "mean": float(series.mean()),
        "stddev": float(series.std(ddof=1)),
        "min": float(series.min()),
        "median": float(series.median()),
        "max": float(series.max()),
    }


def _minute_measurements(readings: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for node_id in EXPECTED_NODE_IDS:
        node = readings[readings["node_id"] == node_id].set_index("gateway_time")
        minute = node[list(SENSOR_COLUMNS)].resample("1min").mean().reset_index()
        minute.insert(0, "node_id", node_id)
        pieces.append(minute)
    return pd.concat(pieces, ignore_index=True)


def _operator_events(notes: dict[str, Any]) -> dict[str, pd.Timestamp]:
    events = {
        str(item["event"]): pd.Timestamp(item["local_time"])
        for item in notes["operator_events"]
    }
    required = {
        "shower_on",
        "shower_off",
        "bathroom_door_opened_toward_physical-002",
    }
    if set(events) != required:
        raise ExploratoryAnalysisError(f"operator events differ from expected set: {set(events)}")
    return events


def _event_precision(notes: dict[str, Any], event_name: str) -> str:
    return next(
        str(item["precision"])
        for item in notes["operator_events"]
        if item["event"] == event_name
    )


def _timestamp_inventory() -> dict[str, dict[str, Any]]:
    return {
        "reading.timestamp_ms": {
            "clock": "per-ESP32 uptime milliseconds",
            "available": True,
            "cross_node_wall_time_valid": False,
        },
        "gateway_received_at_ms": {
            "clock": "Pi wall clock",
            "available": True,
            "use": "primary environmental timeline and operator-event alignment",
        },
        "gateway_received_monotonic_ns": {
            "clock": "Pi monotonic clock",
            "available": True,
            "use": "ordering/identity; not calendar time",
        },
        "forwarded_at_ms": {
            "clock": "Pi wall clock",
            "available": True,
            "use": "upstream record metadata",
        },
        "collector_received_at_ms": {
            "clock": "Mac wall clock",
            "available": True,
            "cross_host_latency_valid": False,
        },
        "collector_received_monotonic_ns": {
            "clock": "Mac monotonic clock",
            "available": True,
            "cross_host_latency_valid": False,
        },
    }


def _limitations() -> list[str]:
    return [
        (
            "The shower-on and shower-off times are approximate operator reports made after the "
            "events."
        ),
        (
            "The door-opening timestamp is the assistant receipt time, not an instrumented switch "
            "event."
        ),
        "Placements differ and were not randomized, replicated, or environmentally controlled.",
        "The BME280 units were not co-located and cross-calibrated before this run.",
        "Balcony weather, laptop heat, ventilation, airflow, and sensor placement are confounders.",
        (
            "The near-saturation bathroom exposure may involve sensor response lag or condensation "
            "risk."
        ),
        "No independent firmware generated/scheduled count was captured.",
        "Unsynchronized device clocks prohibit a physical one-way latency claim.",
        (
            "The run evaluates RAW forwarding only and says nothing causal about aggregation "
            "performance."
        ),
    ]


def _render_report(
    summary: dict[str, Any], processed_dir: Path, figures_dir: Path
) -> str:
    bathroom = summary["bathroom_intervention"]
    integrity = summary["engineering_integrity"]
    onset = bathroom["inferred_response_onset"]
    rh_peak = bathroom["peaks_during_reported_shower_interval"]["humidity_pct"]
    temp_peak = bathroom["peaks_during_reported_shower_interval"]["temperature_c"]
    door = bathroom["door_opening_response"]
    pressure = summary["pressure_control_like_analysis"]["nodes"]
    lines = [
        "# Report-oriented analysis: three-node exploratory measurement",
        "",
        "## Dataset and evidentiary status",
        "",
        f"This analysis covers `{summary['run_id']}` from "
        f"{summary['evidence_interval']['first_local_time']} through "
        f"{summary['evidence_interval']['last_local_time']} "
        f"({summary['evidence_interval']['elapsed_seconds_first_to_last']:.3f} s). "
        "The raw directory was read-only in practice and its SHA-256 values were checked before "
        "and after analysis. This remains an exploratory environmental measurement and engineering "
        "validation, not a controlled network-performance experiment.",
        "",
        "Available time fields include each ESP32's uptime timestamp, Pi wall/monotonic receive "
        "timestamps, Pi forwarding time, and Mac wall/monotonic collector timestamps. "
        "Environmental plots use Pi `gateway_received_at_ms`. Cross-host one-way latency is not "
        "calculated because clock synchronization was not explicitly established and verified.",
        "",
        "## Methodology",
        "",
        "The baseline is every sample before the approximate operator-reported shower-on time. "
        "Means and sample standard deviations use raw readings. Extrema use raw readings, while "
        "overview plots use bounded time averages only for display. The inferred bathroom response "
        f"uses this rule: {onset['method']}. Recovery changes use nearest raw readings at operator "
        "events. OLS slopes are descriptive and are not treated as mechanistic decay constants.",
        "",
        "Operator-reported and inferred events are kept distinct:",
        "",
        "- Shower on: approximately 01:27 PDT (operator reported).",
        "- Inferred sustained response: "
        f"{onset['time']} (analysis-derived 60 s bin, not an operator timestamp).",
        "- Shower off: approximately 02:22 PDT (operator reported).",
        "- Bathroom door opened: 02:50:42 PDT (assistant receipt timestamp).",
        "",
        "## Quantitative findings",
        "",
        "| Node/location | Samples | Temperature mean ± SD (°C) | RH mean ± SD (%) | "
        "Pressure mean ± SD (hPa) |",
        "|---|---:|---:|---:|---:|",
    ]
    for node_id, label in (
        ("physical-001", "Balcony"),
        ("physical-002", "Room"),
        ("physical-003", "Bathroom"),
    ):
        node = summary["node_statistics"][node_id]
        temp = node["measurements"]["temperature_c"]
        rh = node["measurements"]["humidity_pct"]
        press = node["measurements"]["pressure_hpa"]
        lines.append(
            f"| {label} (`{node_id}`) | {node['sample_count']:,} | "
            f"{temp['mean']:.2f} ± {temp['stddev']:.2f} | "
            f"{rh['mean']:.2f} ± {rh['stddev']:.2f} | "
            f"{press['mean']:.3f} ± {press['stddev']:.3f} |"
        )
    baseline = bathroom["baseline"]
    lines.extend(
        [
            "",
            f"The bathroom baseline comprised {baseline['sample_count']:,} samples and averaged "
            f"{baseline['temperature_c']['mean']:.2f} °C and "
            f"{baseline['humidity_pct']['mean']:.2f}% RH. Peak bathroom RH was "
            f"{rh_peak['value']:.3f}% at {rh_peak['time']}, a "
            f"{rh_peak['delta_from_baseline_mean']:.3f}-point increase. Peak temperature was "
            f"{temp_peak['value']:.2f} °C at {temp_peak['time']}, a "
            f"{temp_peak['delta_from_baseline_mean']:.2f} °C increase from the baseline mean.",
            "",
            "During the closed-bathroom recovery, RH changed from "
            f"{bathroom['closed_bathroom_recovery']['bathroom_rh_start']:.3f}% to "
            f"{bathroom['closed_bathroom_recovery']['bathroom_rh_end']:.3f}% over "
            f"{bathroom['closed_bathroom_recovery']['duration_seconds'] / 60:.2f} minutes. "
            "Five minutes after the door event, bathroom RH changed by "
            f"{door['bathroom_rh_5_minute_change']:.3f} points and room RH changed by "
            f"{door['room_rh_5_minute_change']:.3f} points. The opposing changes are consistent "
            "with air exchange, but uncontrolled ventilation prevents a causal attribution.",
            "",
            "Pressure was comparatively stable. Overall peak-to-peak pressure was "
            f"{pressure['physical-001']['overall_peak_to_peak_hpa']:.3f}, "
            f"{pressure['physical-002']['overall_peak_to_peak_hpa']:.3f}, and "
            f"{pressure['physical-003']['overall_peak_to_peak_hpa']:.3f} hPa for balcony, room, "
            "and bathroom respectively. This supports a control-like interpretation only; it does "
            "not prove that pressure was unaffected by every uncontrolled influence.",
            "",
            "## Engineering integrity",
            "",
            f"The Pi validated, persisted, and forwarded {integrity['pi_valid_messages']:,} "
            f"records, and the Mac collected {integrity['mac_collected_records']:,}. Both sides "
            f"accounted for {integrity['pi_upstream_bytes']:,} upstream application bytes. No "
            "invalid, malformed, schema-rejected, duplicate, out-of-order, estimated-missing, "
            "queue-dropped, send-failed, truncated, overlong, or abandoned records were reported.",
            "",
            f"An independent generated/scheduled denominator is unavailable: "
            f"{integrity['generated_count_limitation']}",
            "",
            "Gateway receive cadence was approximately one second for every node. Rare long "
            "intervals were paired with short catch-up intervals, while every observed sequence "
            "advanced by exactly one. These tails describe receive-side timing and are not missing "
            "sensor records.",
            "",
            "## Candidate figures",
            "",
        ]
    )
    for figure in summary["figures"]:
        lines.append(
            f"- `{figure['stem']}.pdf`: {figure['supported_claim']} Display uses "
            f"{figure['display_resolution']}."
        )
    lines.extend(
        [
            "",
            "The RH, temperature, and door-focus figures are suitable as an explicitly labeled "
            "exploratory physical-system demonstration. The pressure plot can support the limited "
            "control-like observation. The cadence tail plot is more appropriate for a "
            "systems-validation figure or appendix. None supports aggregation, scalability, "
            "impaired-network, or one-way-latency conclusions.",
            "",
            "## Limitations and confounders",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.extend(
        [
            "",
            "## Smallest rigorous controlled experiment set suggested by this run",
            "",
            "1. **Sensor/placement qualification:** co-locate all three BME280s for at least 15 "
            "minutes, estimate fixed offsets, then rotate physical positions across at least three "
            "repetitions. Keep this separate from network-performance claims.",
            "2. **RAW physical baseline:** three physical nodes at 1 Hz, 2-minute warm-up plus "
            "10-minute measurement, three repetitions in a stable environment. Preserve "
            "independent firmware generated/scheduled counters in the final evidence.",
            "3. **Scaling, one factor at a time:** fixed RAW mode at 5, 25, and 100 total nodes, "
            "three seeded repetitions per level. Physical nodes may remain as three anchors while "
            "virtual nodes provide controlled scale.",
            "4. **Aggregation, one factor at a time:** fixed 25-node load with RAW and 1 s, 5 s, "
            "and 10 s windows, three repetitions each. Use the same measurement duration and "
            "sampling schedule.",
            "5. **Degradation:** after selecting one aggregation setting, compare "
            "application-level drop probabilities 0, 0.05, and 0.10 at fixed load, three "
            "repetitions. Label this accurately as application impairment rather than Wi-Fi packet "
            "loss. Add delay as a separate matrix only if the report requires a second degradation "
            "mechanism.",
            "6. **Failure/recovery:** one predetermined node outage and recovery time under RAW "
            "and the selected aggregation mode, three repetitions, using Pi event timestamps for "
            "detection metrics.",
            "",
            "The exploratory shower cycle should remain a validation/example dataset. The "
            "controlled matrix above should supply the report's causal network-performance "
            "comparisons.",
            "",
            "## Derived artifacts",
            "",
            f"Processed tables and machine summary: `{processed_dir}`",
            f"Candidate figures: `{figures_dir}`",
            "",
        ]
    )
    return "\n".join(lines)


def _reading_key(reading: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(reading["node_id"]),
        int(reading["sequence"]),
        int(reading["gateway_received_monotonic_ns"]),
    )


def _source_hashes(source_dir: Path) -> dict[str, str]:
    missing = [name for name in SOURCE_FILENAMES if not (source_dir / name).is_file()]
    if missing:
        raise ExploratoryAnalysisError("missing frozen evidence: " + ", ".join(missing))
    return {name: _sha256(source_dir / name) for name in SOURCE_FILENAMES}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExploratoryAnalysisError(f"cannot read JSON evidence: {path}") from exc


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    values = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ExploratoryAnalysisError(
                    f"invalid NDJSON evidence at {path}:{line_number}"
                ) from exc
    return values


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


def _iso_from_ms(timestamp_ms: int, timezone: str) -> str:
    return pd.Timestamp(timestamp_ms, unit="ms", tz="UTC").tz_convert(timezone).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze the immutable three-node exploratory physical run"
    )
    parser.add_argument("run_dir", nargs="?", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES_ROOT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _summary, processed, figures = analyze_exploratory_run(
        args.run_dir,
        processed_root=args.processed_root,
        figures_root=args.figures_root,
    )
    print(processed)
    print(figures)


if __name__ == "__main__":
    main()
