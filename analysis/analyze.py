"""Derive metrics and supported comparisons directly from immutable run evidence."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from experiments.manifest import write_json_exclusive
from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus
from gateway.upstream_protocol import RawUpstreamRecord, encode_upstream_record

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = REPOSITORY / "results" / "raw"
DEFAULT_PROCESSED_ROOT = REPOSITORY / "results" / "processed"
DEFAULT_FIGURES_ROOT = REPOSITORY / "results" / "figures"


class AnalysisError(ValueError):
    pass


@dataclass(slots=True)
class LoadedRun:
    run_dir: Path
    manifest: dict[str, Any]
    simulator: dict[str, Any]
    readings: list[dict[str, Any]]
    upstream: list[dict[str, Any]]
    node_stats: pd.DataFrame
    system_metrics: pd.DataFrame
    health_events: pd.DataFrame
    gateway_summary: dict[str, Any]


def analyze_run(
    run_dir: str | Path,
    *,
    processed_root: str | Path = DEFAULT_PROCESSED_ROOT,
) -> tuple[dict[str, Any], Path]:
    loaded = load_run(run_dir)
    measurement_start = int(loaded.simulator["measurement_start_ms"])
    measurement_end = int(loaded.simulator["measurement_end_ms"])
    duration_seconds = (measurement_end - measurement_start) / 1_000
    if duration_seconds <= 0:
        raise AnalysisError("invalid measurement interval")

    readings = [
        item
        for item in loaded.readings
        if item.get("type") == "reading"
        and measurement_start <= int(item["gateway_received_at_ms"]) < measurement_end
    ]
    unique_upstream, upstream_duplicates = _deduplicate_upstream(
        _filter_upstream(loaded.upstream, measurement_start, measurement_end)
    )
    scheduled = _sum_column(loaded.node_stats, "scheduled_readings")
    generated = _sum_column(loaded.node_stats, "samples_generated")
    attempted = _sum_column(loaded.node_stats, "send_attempts")
    successful_writes = _sum_column(loaded.node_stats, "successful_writes")
    application_drops = _sum_column(loaded.node_stats, "application_drops")
    gateway_received = len(readings)
    latencies = [
        int(item["gateway_received_at_ms"]) - int(item["timestamp_ms"])
        for item in readings
        if item.get("node_kind") == "virtual"
    ]
    latencies = [value for value in latencies if value >= 0]
    baseline_bytes = sum(_hypothetical_raw_bytes(item) for item in readings)
    unique_upstream_bytes = sum(int(item["upstream_wire_bytes"]) for item in unique_upstream)
    actual_upstream_bytes = sum(
        int(item["upstream_wire_bytes"])
        for item in _filter_upstream(loaded.upstream, measurement_start, measurement_end)
    )
    information_delays = [_information_delay(item) for item in unique_upstream]
    information_delays = [value for value in information_delays if value >= 0]
    metrics = _filter_frame(
        loaded.system_metrics, "timestamp_ms", measurement_start, measurement_end
    )
    sequence_counts = {
        status.value.lower(): sum(
            item.get("sequence_status") == status.value for item in readings
        )
        for status in SequenceStatus
    }
    virtual_received = sum(item.get("node_kind") == "virtual" for item in readings)
    physical_received = sum(item.get("node_kind") == "physical" for item in readings)
    warnings: list[str] = []
    if loaded.manifest.get("git_dirty"):
        warnings.append("run was created from a dirty Git worktree")
    queue_drops = int(
        loaded.gateway_summary.get("upstream", {}).get("queue_full_drops", 0)
        if loaded.gateway_summary.get("upstream")
        else 0
    )
    if queue_drops:
        warnings.append("upstream queue drops occurred; reduction includes forwarding loss")
    if loaded.manifest["aggregation_mode"] == "aggregated" and not unique_upstream:
        warnings.append("no complete aggregation window fell wholly inside measurement interval")

    result: dict[str, Any] = {
        "analysis_schema_version": 1,
        "run_id": loaded.manifest["run_id"],
        "experiment_type": loaded.manifest["experiment_type"],
        "node_count": loaded.manifest["node_count"],
        "aggregation_mode": loaded.manifest["aggregation_mode"],
        "aggregation_window_seconds": loaded.manifest["aggregation_window_seconds"],
        "drop_probability": loaded.manifest["drop_probability"],
        "artificial_delay_ms": loaded.manifest["artificial_delay_ms"],
        "measurement_start_ms": measurement_start,
        "measurement_end_ms": measurement_end,
        "measurement_duration_seconds": duration_seconds,
        "scheduled_readings": scheduled,
        "generated_readings": generated,
        "attempted_sends": attempted,
        "successful_application_writes": successful_writes,
        "application_drops": application_drops,
        "gateway_received_readings": gateway_received,
        "virtual_received_readings": virtual_received,
        "physical_received_readings": physical_received,
        "delivery_ratio": _safe_ratio(gateway_received, scheduled),
        "generation_availability": _safe_ratio(generated, scheduled),
        "send_success_ratio": _safe_ratio(successful_writes, attempted),
        "throughput_readings_per_second": gateway_received / duration_seconds,
        "sequence_counts": sequence_counts,
        "latency_mean_ms": _mean(latencies),
        "latency_median_ms": _median(latencies),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "process_cpu_mean_percent": _frame_mean(metrics, "process_cpu_percent"),
        "process_rss_mean_bytes": _frame_mean(metrics, "process_rss_bytes"),
        "process_rss_max_bytes": _frame_max(metrics, "process_rss_bytes"),
        "process_vms_mean_bytes": _frame_mean(metrics, "process_vms_bytes"),
        "collector_messages_actual": len(
            _filter_upstream(loaded.upstream, measurement_start, measurement_end)
        ),
        "collector_bytes_actual": actual_upstream_bytes,
        "collector_messages_unique": len(unique_upstream),
        "collector_bytes_unique": unique_upstream_bytes,
        "collector_duplicate_record_ids": upstream_duplicates,
        "raw_baseline_messages": gateway_received,
        "raw_baseline_upstream_bytes": baseline_bytes,
        "upstream_message_reduction": _reduction(
            len(unique_upstream), gateway_received
        ),
        "upstream_byte_reduction": _reduction(unique_upstream_bytes, baseline_bytes),
        "information_delay_mean_ms": _mean(information_delays),
        "information_delay_p95_ms": _percentile(information_delays, 0.95),
        "upstream_queue_full_drops": queue_drops,
        **_resilience_metrics(loaded, readings),
        "warnings": warnings,
    }
    output_dir = Path(processed_root) / str(loaded.manifest["run_id"])
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(output_dir / "metrics.json", result)
    pd.DataFrame(readings).to_csv(output_dir / "measurement_readings.csv", index=False)
    pd.DataFrame(unique_upstream).to_csv(
        output_dir / "measurement_upstream_unique.csv", index=False
    )
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return result, output_dir


def load_run(run_dir: str | Path) -> LoadedRun:
    directory = Path(run_dir)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisError("missing manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("manifest_schema_version") != 1:
        raise AnalysisError("unsupported manifest schema")
    simulator_path = directory / "simulator_summary.json"
    if not simulator_path.exists():
        raise AnalysisError("missing simulator_summary.json")
    simulator = json.loads(simulator_path.read_text())
    required = ("measurement_start_ms", "measurement_end_ms")
    if any(key not in simulator for key in required):
        raise AnalysisError("simulator summary lacks measurement boundaries")
    required_files = (
        "readings.ndjson",
        "upstream.ndjson",
        "node_stats.csv",
        "system_metrics.csv",
        "health_events.csv",
    )
    missing = [name for name in required_files if not (directory / name).exists()]
    if missing:
        raise AnalysisError(f"missing run evidence: {', '.join(missing)}")
    return LoadedRun(
        run_dir=directory,
        manifest=manifest,
        simulator=simulator,
        readings=_read_ndjson(directory / "readings.ndjson"),
        upstream=_read_ndjson(directory / "upstream.ndjson"),
        node_stats=pd.read_csv(directory / "node_stats.csv"),
        system_metrics=pd.read_csv(directory / "system_metrics.csv"),
        health_events=pd.read_csv(directory / "health_events.csv"),
        gateway_summary=_read_json_optional(directory / "gateway_summary.json"),
    )


def analyze_experiment(
    experiment_type: str,
    *,
    raw_root: str | Path = DEFAULT_RAW_ROOT,
    processed_root: str | Path = DEFAULT_PROCESSED_ROOT,
    figures_root: str | Path = DEFAULT_FIGURES_ROOT,
) -> Path:
    from analysis.plots import plot_comparison

    run_dirs = []
    for manifest_path in Path(raw_root).glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("experiment_type") == experiment_type:
            run_dirs.append(manifest_path.parent)
    if not run_dirs:
        raise AnalysisError(f"no {experiment_type} runs found")
    manifests = [load_run(path).manifest for path in run_dirs]
    _validate_comparison(manifests, experiment_type)
    metrics = []
    for run_dir in run_dirs:
        output = Path(processed_root) / run_dir.name
        if output.exists():
            metrics.append(json.loads((output / "metrics.json").read_text()))
        else:
            result, _output = analyze_run(run_dir, processed_root=processed_root)
            metrics.append(result)
    comparison_dir = Path(processed_root) / f"comparison-{experiment_type}"
    comparison_dir.mkdir(parents=True, exist_ok=False)
    frame = pd.json_normalize(metrics, sep=".")
    frame.to_csv(comparison_dir / "comparison.csv", index=False)
    plot_comparison(frame, experiment_type, Path(figures_root) / experiment_type)
    return comparison_dir


def _validate_comparison(manifests: list[dict[str, Any]], experiment_type: str) -> None:
    common_dimensions = (
        "duration_seconds",
        "warmup_seconds",
        "physical_node_count",
        "expected_interval_seconds",
        "suspect_after_intervals",
        "offline_after_intervals",
        "forwarder_queue_size",
        "storage_queue_size",
        "event_queue_size",
    )
    comparison_dimensions = {
        "scaling": (
            "aggregation_mode",
            "aggregation_window_seconds",
            "impairment_mode",
            "drop_probability",
            "artificial_delay_ms",
            "sampling_interval_ms",
        ),
        "aggregation": (
            "node_count",
            "impairment_mode",
            "drop_probability",
            "artificial_delay_ms",
            "sampling_interval_ms",
        ),
        "impairment": (
            "node_count",
            "aggregation_mode",
            "aggregation_window_seconds",
            "sampling_interval_ms",
        ),
        "failure": (
            "node_count",
            "sampling_interval_ms",
            "aggregation_mode",
            "aggregation_window_seconds",
            "impairment_mode",
            "drop_probability",
            "artificial_delay_ms",
            "failure_at_seconds",
            "recovery_at_seconds",
        ),
    }
    try:
        dimensions = common_dimensions + comparison_dimensions[experiment_type]
    except KeyError as exc:
        raise AnalysisError(f"unsupported experiment type: {experiment_type}") from exc
    for dimension in dimensions:
        values = {manifest.get(dimension) for manifest in manifests}
        if len(values) > 1:
            raise AnalysisError(
                f"incompatible {experiment_type} runs differ in {dimension}: {values}"
            )
    if experiment_type == "impairment":
        drop_values = {manifest.get("drop_probability") for manifest in manifests}
        delay_values = {manifest.get("artificial_delay_ms") for manifest in manifests}
        if len(drop_values) > 1 and len(delay_values) > 1:
            raise AnalysisError(
                "impairment comparison varies both drop_probability and "
                "artificial_delay_ms; analyze one dimension at a time"
            )


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    values = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"invalid NDJSON at {path}:{line_number}") from exc
    return values


def _read_json_optional(path: Path) -> dict[str, Any]:
    return {} if not path.exists() else json.loads(path.read_text())


def _filter_upstream(
    records: list[dict[str, Any]], start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    filtered = []
    for wrapper in records:
        record = wrapper.get("record", {})
        if record.get("type") == "raw":
            timestamp = int(record["gateway_received_at_ms"])
            include = start_ms <= timestamp < end_ms
        elif record.get("type") == "aggregate":
            include = (
                int(record["window_start_ms"]) >= start_ms
                and int(record["window_end_ms"]) <= end_ms
            )
        else:
            continue
        if include:
            filtered.append(wrapper)
    return filtered


def _deduplicate_upstream(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    unique = []
    duplicates = 0
    for wrapper in records:
        record_id = str(wrapper["record"]["record_id"])
        if record_id in seen:
            duplicates += 1
            continue
        seen.add(record_id)
        unique.append(wrapper)
    return unique, duplicates


def _hypothetical_raw_bytes(item: dict[str, Any]) -> int:
    reading = ReadingMessage.model_validate(
        {
            key: item[key]
            for key in (
                "type",
                "version",
                "node_id",
                "node_kind",
                "sequence",
                "timestamp_ms",
                "temperature_c",
                "humidity_pct",
                "pressure_hpa",
            )
        }
    )
    record = RawUpstreamRecord(
        type="raw",
        version=1,
        record_id=(
            f"raw:{reading.node_id}:{reading.sequence}:"
            f"{item['gateway_received_monotonic_ns']}"
        ),
        forwarded_at_ms=int(item["gateway_received_at_ms"]),
        gateway_received_at_ms=int(item["gateway_received_at_ms"]),
        gateway_received_monotonic_ns=int(item["gateway_received_monotonic_ns"]),
        sensor_wire_bytes=int(item["sensor_wire_bytes"]),
        sequence_status=SequenceStatus(item["sequence_status"]),
        reading=reading,
    )
    return len(encode_upstream_record(record))


def _information_delay(wrapper: dict[str, Any]) -> int:
    record = wrapper["record"]
    origin = (
        record["gateway_received_at_ms"]
        if record["type"] == "raw"
        else record["window_start_ms"]
    )
    return int(wrapper["collector_received_at_ms"]) - int(origin)


def _resilience_metrics(
    loaded: LoadedRun, readings: list[dict[str, Any]]
) -> dict[str, float | None]:
    failure = loaded.simulator.get("failure_timestamp_ms")
    recovery = loaded.simulator.get("recovery_timestamp_ms")
    detection = None
    recovery_detection = None
    healthy_throughput = None
    if failure is not None and not loaded.health_events.empty:
        offline = loaded.health_events[
            (loaded.health_events["node_id"] == "virtual-000")
            & (loaded.health_events["new_state"] == "OFFLINE")
            & (loaded.health_events["timestamp_ms"] >= failure)
        ]
        if not offline.empty:
            detection = float(offline.iloc[0]["timestamp_ms"] - failure)
    if recovery is not None and not loaded.health_events.empty:
        online = loaded.health_events[
            (loaded.health_events["node_id"] == "virtual-000")
            & (loaded.health_events["new_state"] == "ONLINE")
            & (loaded.health_events["timestamp_ms"] >= recovery)
        ]
        if not online.empty:
            recovery_detection = float(online.iloc[0]["timestamp_ms"] - recovery)
    if failure is not None:
        interval_end = recovery or loaded.simulator["measurement_end_ms"]
        interval_seconds = (interval_end - failure) / 1_000
        if interval_seconds > 0:
            healthy = sum(
                item["node_id"] != "virtual-000"
                and failure <= item["gateway_received_at_ms"] < interval_end
                for item in readings
            )
            healthy_throughput = healthy / interval_seconds
    return {
        "failure_detection_time_ms": detection,
        "recovery_detection_time_ms": recovery_detection,
        "healthy_peer_throughput_during_failure": healthy_throughput,
    }


def _filter_frame(frame: pd.DataFrame, column: str, start: int, end: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[(frame[column] >= start) & (frame[column] < end)]


def _sum_column(frame: pd.DataFrame, column: str) -> int:
    return 0 if frame.empty else int(frame[column].sum())


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if denominator == 0 else float(numerator / denominator)


def _reduction(actual: int | float, baseline: int | float) -> float | None:
    return None if baseline == 0 else float(1 - actual / baseline)


def _mean(values: list[int | float]) -> float | None:
    return None if not values else float(statistics.fmean(values))


def _median(values: list[int | float]) -> float | None:
    return None if not values else float(statistics.median(values))


def _percentile(values: list[int | float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile + 0.999999)))
    return float(ordered[index])


def _frame_mean(frame: pd.DataFrame, column: str) -> float | None:
    return None if frame.empty else float(frame[column].mean())


def _frame_max(frame: pd.DataFrame, column: str) -> float | None:
    return None if frame.empty else float(frame[column].max())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze immutable hybrid WSN evidence")
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument(
        "--experiment", choices=("scaling", "aggregation", "failure", "impairment")
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES_ROOT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if (args.run_dir is None) == (args.experiment is None):
        raise SystemExit("provide exactly one run directory or --experiment")
    if args.run_dir is not None:
        _metrics, output = analyze_run(args.run_dir, processed_root=args.processed_root)
    else:
        output = analyze_experiment(
            args.experiment,
            raw_root=args.raw_root,
            processed_root=args.processed_root,
            figures_root=args.figures_root,
        )
    print(output)


if __name__ == "__main__":
    main()
