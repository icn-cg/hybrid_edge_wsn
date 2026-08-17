"""Canonical campaign-level analysis for the frozen final controlled matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.final_campaign_plots import create_campaign_figures

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RAW_CAMPAIGN = REPOSITORY / "results" / "raw" / "final-controlled-v1"
DEFAULT_PROCESSED_CAMPAIGN = (
    REPOSITORY / "results" / "processed" / "final-controlled-v1"
)
DEFAULT_FIGURES_ROOT = REPOSITORY / "results" / "figures"
DEFAULT_EXPLORATORY_ANALYSIS = (
    REPOSITORY
    / "results"
    / "processed"
    / "exploratory-shower-20260817-0115-analysis-v1"
)
DEFAULT_EXPLORATORY_FIGURES = (
    REPOSITORY
    / "results"
    / "figures"
    / "exploratory-shower-20260817-0115-analysis-v1"
)
ANALYSIS_ID = "campaign-analysis-v1"
ANALYSIS_SCHEMA_VERSION = 1
EXPECTED_COMMIT = "4ef6a919fef08956ff5623f233de17e2e1a9d064"
FAILED_NODE_ID = "virtual-000"

STATISTIC_FIELDS = (
    "scheduled_readings",
    "generated_readings",
    "virtual_unique_received_readings",
    "delivery_ratio",
    "generated_reading_delivery_ratio",
    "generation_availability",
    "attempted_sends",
    "successful_application_writes",
    "gateway_received_readings",
    "collector_messages_actual",
    "collector_messages_unique",
    "collector_bytes_actual",
    "collector_bytes_unique",
    "collector_extra_messages",
    "collector_extra_bytes",
    "upstream_message_reduction_actual",
    "upstream_message_reduction_unique",
    "upstream_byte_reduction_actual",
    "upstream_byte_reduction_unique",
    "information_delay_mean_ms",
    "information_delay_p95_ms",
    "latency_median_ms",
    "latency_p95_ms",
    "virtual_unique_throughput_readings_per_second",
    "process_cpu_mean_percent",
    "process_rss_mean_bytes",
    "process_rss_max_bytes",
    "process_vms_mean_bytes",
    "application_drops",
    "upstream_queue_full_drops",
    "collector_duplicate_record_ids",
    "deliberately_absent_readings",
    "failure_suspect_detection_time_ms",
    "failure_offline_detection_time_ms",
    "failure_detection_time_ms",
    "recovery_detection_time_ms",
    "healthy_peer_throughput_during_failure",
)


class FinalCampaignAnalysisError(ValueError):
    """Raised when canonical campaign evidence is absent or inconsistent."""


@dataclass
class LoadedRun:
    condition_id: str
    repetition_index: int
    random_seed: int
    run_id: str
    run_dir: Path
    processed_dir: Path
    manifest: dict[str, Any]
    run_summary: dict[str, Any]
    metrics: dict[str, Any]
    measurement_readings: pd.DataFrame
    health_events: pd.DataFrame


def analyze_final_campaign(
    raw_campaign: str | Path = DEFAULT_RAW_CAMPAIGN,
    *,
    processed_campaign: str | Path = DEFAULT_PROCESSED_CAMPAIGN,
    figures_root: str | Path = DEFAULT_FIGURES_ROOT,
    exploratory_analysis: str | Path = DEFAULT_EXPLORATORY_ANALYSIS,
    exploratory_figures: str | Path = DEFAULT_EXPLORATORY_FIGURES,
) -> tuple[dict[str, Any], Path, Path]:
    """Independently aggregate the admitted campaign and write exclusive outputs."""

    raw_dir = Path(raw_campaign).resolve()
    processed_root = Path(processed_campaign).resolve()
    output_dir = processed_root / ANALYSIS_ID
    figures_dir = Path(figures_root).resolve() / f"final-controlled-v1-{ANALYSIS_ID}"
    if output_dir.exists() or figures_dir.exists():
        existing = output_dir if output_dir.exists() else figures_dir
        raise FinalCampaignAnalysisError(f"refusing to overwrite existing analysis: {existing}")

    manifest_path = raw_dir / "campaign_manifest.json"
    summary_path = raw_dir / "campaign_summary.json"
    campaign_manifest = _read_json(manifest_path)
    campaign_summary = _read_json(summary_path)
    _validate_campaign_headers(campaign_manifest, campaign_summary)

    admissions = campaign_summary["admissions"]
    raw_input_paths = sorted(path for path in raw_dir.rglob("*") if path.is_file())
    processed_input_paths = sorted(
        Path(admission["processed_directory"]) / filename
        for admission in admissions
        for filename in (
            "metrics.json",
            "measurement_readings.csv",
            "measurement_upstream_unique.csv",
        )
    )
    source_before = {
        "raw_campaign": _tree_digest(raw_input_paths, raw_dir),
        "processed_run_outputs": _tree_digest(processed_input_paths, processed_root),
        "campaign_manifest_sha256": _sha256(manifest_path),
        "campaign_summary_sha256": _sha256(summary_path),
    }

    runs = [
        _load_run(admission, raw_dir, campaign_manifest)
        for admission in admissions
    ]
    _validate_campaign_membership(runs, campaign_manifest)
    run_frame = _run_frame(runs, campaign_manifest)
    condition_statistics = _condition_statistics(runs, campaign_manifest)
    campaign_integrity = _campaign_integrity(runs)
    failure_summary, failure_timeseries, failure_events = _failure_analysis(runs)
    physical_validation = _physical_validation(
        Path(exploratory_analysis), Path(exploratory_figures)
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    run_frame.to_csv(output_dir / "run_metrics.csv", index=False)
    _condition_frame(condition_statistics).to_csv(
        output_dir / "condition_statistics.csv", index=False
    )
    failure_timeseries.to_csv(output_dir / "failure_throughput_timeseries.csv", index=False)
    failure_events.to_csv(output_dir / "failure_liveness_events.csv", index=False)
    tables = _write_paper_tables(output_dir, condition_statistics, failure_summary)
    figures = create_campaign_figures(
        run_frame, failure_timeseries, failure_events, figures_dir
    )

    result: dict[str, Any] = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": ANALYSIS_ID,
        "campaign_id": campaign_manifest["campaign_id"],
        "classification": "canonical campaign-level analysis of frozen controlled evidence",
        "research_question": campaign_manifest["research_question"],
        "frozen_git_commit": campaign_manifest["git_commit"],
        "source_directory": str(raw_dir),
        "methodology": {
            "repetitions_per_condition": campaign_manifest["repetitions_per_condition"],
            "seeds": campaign_manifest["repetition_seeds"],
            "warmup_seconds": campaign_manifest["warmup_seconds"],
            "measurement_seconds": campaign_manifest["measurement_seconds"],
            "sampling_interval_ms": campaign_manifest["sampling_interval_ms"],
            "standard_deviation": "sample standard deviation (ddof=1)",
            "primary_network_accounting": (
                "actual collector messages and bytes including retransmissions"
            ),
            "logical_network_accounting": (
                "unique collector records and their unique logical bytes retained separately"
            ),
            "information_delay_term": (
                "aggregation holding plus forwarding delay; not labeled network latency"
            ),
        },
        "source_integrity": source_before,
        "campaign_integrity": campaign_integrity,
        "condition_statistics": condition_statistics,
        "failure_recovery": failure_summary,
        "physical_validation": physical_validation,
        "tables": tables,
        "figures": figures,
        "limitations": _limitations(),
        "supported_claims": _supported_claims(),
        "unsupported_claims": _unsupported_claims(),
        "discrepancies_with_campaign_summary": [],
    }
    _write_json(output_dir / "campaign_level_summary.json", result)
    (output_dir / "FINAL_RESULTS.md").write_text(
        _render_final_results(result), encoding="utf-8"
    )

    source_after = {
        "raw_campaign": _tree_digest(raw_input_paths, raw_dir),
        "processed_run_outputs": _tree_digest(processed_input_paths, processed_root),
        "campaign_manifest_sha256": _sha256(manifest_path),
        "campaign_summary_sha256": _sha256(summary_path),
    }
    if source_after != source_before:
        raise FinalCampaignAnalysisError("campaign evidence changed during analysis")
    return result, output_dir, figures_dir


def _validate_campaign_headers(
    manifest: dict[str, Any], summary: dict[str, Any]
) -> None:
    if manifest.get("campaign_id") != summary.get("campaign_id"):
        raise FinalCampaignAnalysisError("campaign manifest/summary ID mismatch")
    if summary.get("status") != "success":
        raise FinalCampaignAnalysisError("campaign summary is not successful")
    if summary.get("failed_run_id") is not None:
        raise FinalCampaignAnalysisError("campaign summary identifies a failed run")
    if manifest.get("git_commit") != EXPECTED_COMMIT or manifest.get("git_dirty"):
        raise FinalCampaignAnalysisError("campaign is not the frozen clean commit")
    expected = int(manifest["unique_executed_runs"])
    if int(summary.get("completed_run_count", -1)) != expected:
        raise FinalCampaignAnalysisError("campaign completed-run count mismatch")
    if len(summary.get("admissions", [])) != expected:
        raise FinalCampaignAnalysisError("campaign admission count mismatch")


def _load_run(
    admission: dict[str, Any],
    raw_campaign: Path,
    campaign_manifest: dict[str, Any],
) -> LoadedRun:
    run_dir = Path(admission["run_directory"]).resolve()
    processed_dir = Path(admission["processed_directory"]).resolve()
    if not run_dir.is_relative_to(raw_campaign):
        raise FinalCampaignAnalysisError(f"run outside campaign root: {run_dir}")
    manifest = _read_json(run_dir / "manifest.json")
    run_summary = _read_json(run_dir / "run_summary.json")
    metrics = _read_json(processed_dir / "metrics.json")

    identifiers = {
        str(admission["run_id"]),
        str(manifest.get("run_id")),
        str(run_summary.get("run_id")),
        str(metrics.get("run_id")),
    }
    if len(identifiers) != 1:
        raise FinalCampaignAnalysisError(f"run identifier mismatch: {sorted(identifiers)}")
    run_id = identifiers.pop()
    if manifest.get("git_commit") != campaign_manifest.get("git_commit"):
        raise FinalCampaignAnalysisError(f"commit mismatch for {run_id}")
    if manifest.get("git_dirty") or metrics.get("analysis_git_dirty"):
        raise FinalCampaignAnalysisError(f"dirty evidence or analysis for {run_id}")
    if run_summary.get("status") != "success":
        raise FinalCampaignAnalysisError(f"unsuccessful run summary for {run_id}")
    if not admission.get("admission", {}).get("passed"):
        raise FinalCampaignAnalysisError(f"failed campaign admission for {run_id}")
    if not metrics.get("scientific_admission", {}).get("passed"):
        raise FinalCampaignAnalysisError(f"failed metrics admission for {run_id}")

    readings_path = processed_dir / "measurement_readings.csv"
    readings = pd.read_csv(
        readings_path,
        usecols=["node_id", "gateway_received_at_ms", "sequence_status"],
    )
    expected_readings = int(metrics["virtual_unique_received_readings"])
    if len(readings) != expected_readings:
        raise FinalCampaignAnalysisError(
            f"measurement reading count mismatch for {run_id}: "
            f"{len(readings)} != {expected_readings}"
        )

    upstream_path = processed_dir / "measurement_upstream_unique.csv"
    unique_messages = 0
    unique_bytes = 0
    with upstream_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            unique_messages += 1
            unique_bytes += int(row["upstream_wire_bytes"])
    if unique_messages != int(metrics["collector_messages_unique"]):
        raise FinalCampaignAnalysisError(f"unique collector message mismatch for {run_id}")
    if unique_bytes != int(metrics["collector_bytes_unique"]):
        raise FinalCampaignAnalysisError(f"unique collector byte mismatch for {run_id}")
    if int(metrics["collector_messages_actual"]) < unique_messages:
        raise FinalCampaignAnalysisError(f"actual messages below unique messages for {run_id}")
    if int(metrics["collector_bytes_actual"]) < unique_bytes:
        raise FinalCampaignAnalysisError(f"actual bytes below unique bytes for {run_id}")

    health_path = run_dir / "health_events.csv"
    health_events = pd.read_csv(health_path) if health_path.exists() else pd.DataFrame()
    return LoadedRun(
        condition_id=str(admission["condition_id"]),
        repetition_index=int(admission["repetition_index"]),
        random_seed=int(admission["random_seed"]),
        run_id=run_id,
        run_dir=run_dir,
        processed_dir=processed_dir,
        manifest=manifest,
        run_summary=run_summary,
        metrics=metrics,
        measurement_readings=readings,
        health_events=health_events,
    )


def _validate_campaign_membership(
    runs: list[LoadedRun], campaign_manifest: dict[str, Any]
) -> None:
    expected_conditions = {
        condition["condition_id"] for condition in campaign_manifest["conditions"]
    }
    observed_conditions = {run.condition_id for run in runs}
    if observed_conditions != expected_conditions:
        raise FinalCampaignAnalysisError("campaign condition membership mismatch")
    expected_seeds = set(campaign_manifest["repetition_seeds"])
    repetitions = int(campaign_manifest["repetitions_per_condition"])
    for condition_id in expected_conditions:
        selected = [run for run in runs if run.condition_id == condition_id]
        if len(selected) != repetitions:
            raise FinalCampaignAnalysisError(f"repetition count mismatch for {condition_id}")
        if {run.random_seed for run in selected} != expected_seeds:
            raise FinalCampaignAnalysisError(f"seed mismatch for {condition_id}")


def _run_frame(
    runs: list[LoadedRun], campaign_manifest: dict[str, Any]
) -> pd.DataFrame:
    conditions = {
        condition["condition_id"]: condition
        for condition in campaign_manifest["conditions"]
    }
    rows = []
    for run in runs:
        metrics = run.metrics
        condition = conditions[run.condition_id]
        generated = int(metrics["generated_readings"])
        received = int(metrics["virtual_unique_received_readings"])
        row = {
            "condition_id": run.condition_id,
            "run_id": run.run_id,
            "repetition_index": run.repetition_index,
            "random_seed": run.random_seed,
            "node_count": int(metrics["node_count"]),
            "aggregation_mode": metrics["aggregation_mode"],
            "aggregation_window_seconds": float(metrics["aggregation_window_seconds"]),
            "comparison_group_scaling": "scaling" in condition["comparison_groups"],
            "comparison_group_aggregation_window": (
                "aggregation_window" in condition["comparison_groups"]
            ),
            "generated_reading_delivery_ratio": received / generated if generated else None,
            "collector_extra_messages": (
                int(metrics["collector_messages_actual"])
                - int(metrics["collector_messages_unique"])
            ),
            "collector_extra_bytes": (
                int(metrics["collector_bytes_actual"])
                - int(metrics["collector_bytes_unique"])
            ),
            "deliberately_absent_readings": (
                int(metrics["scheduled_readings"]) - generated
            ),
        }
        for field in STATISTIC_FIELDS:
            if field in metrics:
                row[field] = metrics[field]
        rows.append(row)
    return pd.DataFrame(rows)


def _condition_statistics(
    runs: list[LoadedRun], campaign_manifest: dict[str, Any]
) -> dict[str, Any]:
    conditions = {
        condition["condition_id"]: condition
        for condition in campaign_manifest["conditions"]
    }
    result: dict[str, Any] = {}
    for condition_id, condition in conditions.items():
        selected = [run for run in runs if run.condition_id == condition_id]
        frame = _run_frame(selected, campaign_manifest)
        statistics: dict[str, Any] = {}
        for field in STATISTIC_FIELDS:
            if field not in frame:
                continue
            values = [value for value in frame[field].tolist() if _is_number(value)]
            if values:
                statistics[field] = describe_values(values)
        sequence_fields = ("in_order", "reset", "duplicate", "gap", "out_of_order")
        for field in sequence_fields:
            values = [int(run.metrics["sequence_counts"][field]) for run in selected]
            statistics[f"sequence_{field}"] = describe_values(values)
        result[condition_id] = {
            "n": len(selected),
            "configuration": condition,
            "run_ids": [run.run_id for run in selected],
            "seeds": [run.random_seed for run in selected],
            "statistics": statistics,
        }
    return result


def describe_values(values: Iterable[float]) -> dict[str, float | int]:
    """Return n, mean, sample SD, minimum, and maximum."""

    numeric = [float(value) for value in values]
    if not numeric:
        raise FinalCampaignAnalysisError("cannot describe an empty sample")
    series = pd.Series(numeric, dtype="float64")
    return {
        "n": len(numeric),
        "mean": float(series.mean()),
        "stddev": float(series.std(ddof=1)) if len(numeric) > 1 else 0.0,
        "min": float(series.min()),
        "max": float(series.max()),
    }


def _condition_frame(condition_statistics: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for condition_id, condition in condition_statistics.items():
        row: dict[str, Any] = {
            "condition_id": condition_id,
            "n": condition["n"],
            **condition["configuration"],
        }
        for metric, statistic in condition["statistics"].items():
            for name, value in statistic.items():
                row[f"{metric}_{name}"] = value
        rows.append(row)
    return pd.DataFrame(rows)


def _campaign_integrity(runs: list[LoadedRun]) -> dict[str, Any]:
    metrics = [run.metrics for run in runs]
    invalid_counter_total = sum(
        sum(metric["scientific_admission"]["invalid_evidence_counters"].values())
        for metric in metrics
    )
    abandoned = sum(
        int(run.run_summary["gateway"]["upstream"]["records_abandoned_on_shutdown"])
        for run in runs
    )
    failed_checks = [
        {"run_id": run.run_id, "checks": run.metrics["scientific_admission"]["failed_checks"]}
        for run in runs
        if run.metrics["scientific_admission"]["failed_checks"]
    ]
    return {
        "run_count": len(runs),
        "admitted_run_count": sum(
            bool(metric["scientific_admission"]["passed"]) for metric in metrics
        ),
        "total_scheduled_readings": sum(int(m["scheduled_readings"]) for m in metrics),
        "total_generated_readings": sum(int(m["generated_readings"]) for m in metrics),
        "total_unique_received_readings": sum(
            int(m["virtual_unique_received_readings"]) for m in metrics
        ),
        "collector_messages_actual": sum(
            int(m["collector_messages_actual"]) for m in metrics
        ),
        "collector_messages_unique": sum(
            int(m["collector_messages_unique"]) for m in metrics
        ),
        "collector_bytes_actual": sum(int(m["collector_bytes_actual"]) for m in metrics),
        "collector_bytes_unique": sum(int(m["collector_bytes_unique"]) for m in metrics),
        "collector_duplicate_record_ids": sum(
            int(m["collector_duplicate_record_ids"]) for m in metrics
        ),
        "application_drops": sum(int(m["application_drops"]) for m in metrics),
        "upstream_queue_full_drops": sum(
            int(m["upstream_queue_full_drops"]) for m in metrics
        ),
        "records_abandoned_on_shutdown": abandoned,
        "invalid_evidence_counter_total": invalid_counter_total,
        "failed_scientific_admission_checks": failed_checks,
        "warning_count": sum(len(m["warnings"]) for m in metrics),
        "collector_gateway_message_parity_all_runs": all(
            m["scientific_admission"]["checks"]["collector_message_parity"]
            for m in metrics
        ),
        "collector_gateway_byte_parity_all_runs": all(
            m["scientific_admission"]["checks"]["collector_byte_parity"]
            for m in metrics
        ),
        "actual_equals_unique_messages": all(
            int(m["collector_messages_actual"]) == int(m["collector_messages_unique"])
            for m in metrics
        ),
        "actual_equals_unique_bytes": all(
            int(m["collector_bytes_actual"]) == int(m["collector_bytes_unique"])
            for m in metrics
        ),
    }


def _failure_analysis(
    runs: list[LoadedRun],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    selected = [run for run in runs if run.condition_id == "failure-n025-raw"]
    if len(selected) != 3:
        raise FinalCampaignAnalysisError("failure condition must contain three runs")
    event_rows = []
    throughput_rows = []
    per_run = []
    for run in selected:
        metrics = run.metrics
        readings = run.measurement_readings.copy()
        start_ms = int(metrics["measurement_start_ms"])
        readings["elapsed_seconds"] = (
            readings["gateway_received_at_ms"] - start_ms
        ) / 1000.0
        readings = readings[
            (readings["elapsed_seconds"] >= 0) & (readings["elapsed_seconds"] < 300)
        ]
        readings["bin_start_seconds"] = (
            readings["elapsed_seconds"] // 5 * 5
        ).astype(int)
        for bin_start in range(0, 300, 5):
            bin_rows = readings[readings["bin_start_seconds"] == bin_start]
            healthy = bin_rows[bin_rows["node_id"] != FAILED_NODE_ID]
            throughput_rows.append(
                {
                    "run_id": run.run_id,
                    "repetition_index": run.repetition_index,
                    "bin_start_seconds": bin_start,
                    "total_throughput": len(bin_rows) / 5.0,
                    "healthy_throughput": len(healthy) / 5.0,
                }
            )
        failed_rows = readings[readings["node_id"] == FAILED_NODE_ID]
        healthy_rows = readings[readings["node_id"] != FAILED_NODE_ID]
        healthy_counts = healthy_rows.groupby("node_id").size()
        healthy_non_in_order = int((healthy_rows["sequence_status"] != "IN_ORDER").sum())
        failed_status = failed_rows["sequence_status"].value_counts().to_dict()
        if not (len(healthy_counts) == 24 and (healthy_counts == 300).all()):
            raise FinalCampaignAnalysisError(f"healthy-peer count mismatch for {run.run_id}")
        if healthy_non_in_order:
            raise FinalCampaignAnalysisError(f"healthy-peer sequence anomaly for {run.run_id}")
        if len(failed_rows) != 210 or failed_status.get("RESET") != 1:
            raise FinalCampaignAnalysisError(f"failed-node reset mismatch for {run.run_id}")

        configured_failure = float(run.manifest["failure_at_seconds"])
        configured_recovery = float(run.manifest["recovery_at_seconds"])
        detected_events = {
            "SUSPECT": configured_failure
            + float(metrics["failure_suspect_detection_time_ms"]) / 1000,
            "OFFLINE": configured_failure
            + float(metrics["failure_offline_detection_time_ms"]) / 1000,
            "RECOVERED": configured_recovery
            + float(metrics["recovery_detection_time_ms"]) / 1000,
        }
        target_events = run.health_events[run.health_events["node_id"] == FAILED_NODE_ID]
        observed_states = target_events["new_state"].tolist()
        if observed_states[-3:] != ["SUSPECT", "OFFLINE", "ONLINE"]:
            raise FinalCampaignAnalysisError(f"liveness transition mismatch for {run.run_id}")
        for event, elapsed in detected_events.items():
            event_rows.append(
                {
                    "run_id": run.run_id,
                    "repetition_index": run.repetition_index,
                    "event": event,
                    "elapsed_seconds": elapsed,
                }
            )
        scheduled = int(metrics["scheduled_readings"])
        generated = int(metrics["generated_readings"])
        received = int(metrics["virtual_unique_received_readings"])
        per_run.append(
            {
                "run_id": run.run_id,
                "seed": run.random_seed,
                "scheduled": scheduled,
                "generated": generated,
                "received": received,
                "absent": scheduled - generated,
                "scheduled_delivery_ratio": received / scheduled,
                "generated_delivery_ratio": received / generated,
                "suspect_detection_ms": metrics["failure_suspect_detection_time_ms"],
                "offline_detection_ms": metrics["failure_offline_detection_time_ms"],
                "recovery_detection_ms": metrics["recovery_detection_time_ms"],
                "healthy_peer_throughput": metrics[
                    "healthy_peer_throughput_during_failure"
                ],
                "healthy_peer_non_in_order": healthy_non_in_order,
                "failed_node_in_order": failed_status.get("IN_ORDER", 0),
                "failed_node_resets": failed_status.get("RESET", 0),
            }
        )

    def stats(name: str) -> dict[str, float | int]:
        return describe_values(row[name] for row in per_run)

    summary = {
        "n": 3,
        "configured_outage_seconds": 90.0,
        "failed_node_id": FAILED_NODE_ID,
        "per_run": per_run,
        "statistics": {
            field: stats(field)
            for field in (
                "scheduled",
                "generated",
                "received",
                "absent",
                "scheduled_delivery_ratio",
                "generated_delivery_ratio",
                "suspect_detection_ms",
                "offline_detection_ms",
                "recovery_detection_ms",
                "healthy_peer_throughput",
                "healthy_peer_non_in_order",
                "failed_node_in_order",
                "failed_node_resets",
            )
        },
        "deliberately_absent_total": sum(row["absent"] for row in per_run),
        "healthy_peer_integrity": (
            "All 24 healthy peers contributed exactly 300 measurement readings per run; "
            "all healthy-peer sequence statuses were IN_ORDER."
        ),
        "failed_node_sequence_behavior": (
            "The failed node contributed 210 readings per run with 209 IN_ORDER and one "
            "expected RESET after recovery."
        ),
    }
    return summary, pd.DataFrame(throughput_rows), pd.DataFrame(event_rows)


def _physical_validation(analysis_dir: Path, figures_dir: Path) -> dict[str, Any]:
    summary_path = analysis_dir / "analysis_summary.json"
    summary = _read_json(summary_path)
    recommended = [
        figures_dir / "relative_humidity_timeseries.pdf",
        figures_dir / "door_opening_humidity_focus.pdf",
    ]
    missing = [path for path in recommended if not path.exists()]
    if missing:
        raise FinalCampaignAnalysisError(f"missing physical-validation figure: {missing[0]}")
    intervention = summary["bathroom_intervention"]
    return {
        "classification": summary["classification"],
        "analysis_summary": str(summary_path),
        "primary_recommendation": {
            "figure": str(recommended[0]),
            "reason": (
                "Strongest single physical-validation figure: it shows the localized bathroom "
                "RH response with all three physical nodes for environmental context."
            ),
        },
        "optional_second_figure": {
            "figure": str(recommended[1]),
            "reason": (
                "Use only if space permits: it shows opposing bathroom/room RH changes around "
                "door opening, while remaining explicitly exploratory."
            ),
        },
        "key_values": {
            "bathroom_baseline_rh_pct": intervention["baseline"]["humidity_pct"]["mean"],
            "bathroom_peak_rh_pct": intervention[
                "peaks_during_reported_shower_interval"
            ]["humidity_pct"]["value"],
            "bathroom_peak_delta_points": intervention[
                "peaks_during_reported_shower_interval"
            ]["humidity_pct"]["delta_from_baseline_mean"],
            "bathroom_five_minute_post_door_change_points": intervention[
                "door_opening_response"
            ]["bathroom_rh_5_minute_change"],
            "room_five_minute_post_door_change_points": intervention[
                "door_opening_response"
            ]["room_rh_5_minute_change"],
        },
    }


def _write_paper_tables(
    output_dir: Path,
    conditions: dict[str, Any],
    failure: dict[str, Any],
) -> list[dict[str, str]]:
    scale_ids = (
        "scale-n005-raw",
        "scale-n005-agg05",
        "scale-n025-raw",
        "scale-n025-agg05",
        "scale-n100-raw",
        "scale-n100-agg05",
    )
    tradeoff_ids = (
        "scale-n025-raw",
        "tradeoff-n025-agg01",
        "scale-n025-agg05",
        "tradeoff-n025-agg10",
    )
    scale = pd.DataFrame([_paper_condition_row(conditions[key]) for key in scale_ids])
    tradeoff = pd.DataFrame([_paper_condition_row(conditions[key]) for key in tradeoff_ids])
    failure_table = _paper_failure_frame(failure)
    tables = []
    for name, frame, columns in (
        (
            "table_a_scaling_aggregation",
            scale,
            _scaling_display_columns(),
        ),
        (
            "table_b_aggregation_tradeoff",
            tradeoff,
            _tradeoff_display_columns(),
        ),
        (
            "table_c_failure_recovery",
            failure_table,
            _failure_display_columns(),
        ),
    ):
        csv_path = output_dir / f"{name}.csv"
        md_path = output_dir / f"{name}.md"
        tex_path = output_dir / f"{name}.tex"
        frame.to_csv(csv_path, index=False)
        display = _prepare_display_frame(frame)
        display = display[[column for column, _label in columns]].copy()
        display.columns = [label for _column, label in columns]
        md_path.write_text(_markdown_table(display), encoding="utf-8")
        tex_path.write_text(_latex_table(display), encoding="utf-8")
        tables.append(
            {
                "table_id": name,
                "csv": str(csv_path),
                "markdown": str(md_path),
                "latex": str(tex_path),
            }
        )
    return tables


def _paper_condition_row(condition: dict[str, Any]) -> dict[str, Any]:
    stats = condition["statistics"]
    config = condition["configuration"]
    return {
        "node_count": int(config["node_count"]),
        "mode": "RAW" if config["aggregation_mode"] == "raw" else "Aggregated",
        "window_seconds": float(config["aggregation_window_seconds"]),
        "n": condition["n"],
        **_mean_sd_columns(stats, "scheduled_readings", "scheduled"),
        **_mean_sd_columns(stats, "generated_readings", "generated"),
        **_mean_sd_columns(stats, "virtual_unique_received_readings", "received"),
        **_percent_columns(stats, "delivery_ratio", "delivery_pct"),
        **_mean_sd_columns(stats, "collector_messages_actual", "actual_messages"),
        **_mean_sd_columns(stats, "collector_messages_unique", "unique_messages"),
        **_scaled_columns(stats, "collector_bytes_actual", "actual_bytes_kib", 1024.0),
        **_scaled_columns(stats, "collector_bytes_unique", "unique_bytes_kib", 1024.0),
        **_percent_columns(
            stats, "upstream_message_reduction_actual", "message_reduction_pct"
        ),
        **_percent_columns(
            stats, "upstream_byte_reduction_actual", "byte_reduction_pct"
        ),
        **_scaled_columns(stats, "information_delay_mean_ms", "information_delay_s", 1000),
        **_mean_sd_columns(stats, "process_cpu_mean_percent", "gateway_cpu_pct"),
        **_scaled_columns(stats, "process_rss_mean_bytes", "gateway_rss_mib", 1024**2),
    }


def _paper_failure_frame(failure: dict[str, Any]) -> pd.DataFrame:
    stats = failure["statistics"]
    row = {
        "n": failure["n"],
        **_mean_sd_columns(stats, "scheduled", "scheduled"),
        **_mean_sd_columns(stats, "generated", "generated"),
        **_mean_sd_columns(stats, "received", "received"),
        **_mean_sd_columns(stats, "absent", "absent"),
        **_percent_columns(stats, "scheduled_delivery_ratio", "scheduled_delivery_pct"),
        **_percent_columns(stats, "generated_delivery_ratio", "generated_delivery_pct"),
        **_scaled_columns(stats, "suspect_detection_ms", "suspect_detection_s", 1000),
        **_scaled_columns(stats, "offline_detection_ms", "offline_detection_s", 1000),
        **_scaled_columns(stats, "recovery_detection_ms", "recovery_detection_s", 1000),
        **_mean_sd_columns(
            stats, "healthy_peer_throughput", "healthy_peer_throughput_rps"
        ),
        **_mean_sd_columns(stats, "failed_node_resets", "failed_node_resets"),
        **_mean_sd_columns(
            stats, "healthy_peer_non_in_order", "healthy_peer_sequence_anomalies"
        ),
    }
    return pd.DataFrame([row])


def _mean_sd_columns(
    statistics: dict[str, Any], metric: str, prefix: str
) -> dict[str, float]:
    item = statistics[metric]
    return {f"{prefix}_mean": item["mean"], f"{prefix}_sd": item["stddev"]}


def _percent_columns(
    statistics: dict[str, Any], metric: str, prefix: str
) -> dict[str, float]:
    item = statistics[metric]
    return {f"{prefix}_mean": 100 * item["mean"], f"{prefix}_sd": 100 * item["stddev"]}


def _scaled_columns(
    statistics: dict[str, Any], metric: str, prefix: str, divisor: float
) -> dict[str, float]:
    item = statistics[metric]
    return {
        f"{prefix}_mean": item["mean"] / divisor,
        f"{prefix}_sd": item["stddev"] / divisor,
    }


def _scaling_display_columns() -> list[tuple[str, str]]:
    return [
        ("node_count", "Nodes"),
        ("mode", "Mode"),
        ("window_seconds", "Window (s)"),
        ("n", "n"),
        ("sgr", "Scheduled/generated/received"),
        ("delivery", "Delivery (%)"),
        ("actual_messages", "Actual messages"),
        ("actual_bytes", "Actual bytes (KiB)"),
        ("reductions", "Message/byte reduction (%)"),
        ("delay", "Information delay (s)"),
        ("resources", "CPU (%)/RSS (MiB)"),
    ]


def _tradeoff_display_columns() -> list[tuple[str, str]]:
    return [
        ("window_label", "Window"),
        ("n", "n"),
        ("sgr", "Scheduled/generated/received"),
        ("delivery", "Delivery (%)"),
        ("actual_messages", "Actual messages"),
        ("actual_bytes", "Actual bytes (KiB)"),
        ("reductions", "Message/byte reduction (%)"),
        ("delay", "Information delay (s)"),
        ("resources", "CPU (%)/RSS (MiB)"),
    ]


def _failure_display_columns() -> list[tuple[str, str]]:
    return [
        ("n", "n"),
        ("sgr", "Scheduled/generated/received"),
        ("absent", "Absent"),
        ("delivery", "Scheduled/generated delivery (%)"),
        ("detection", "SUSPECT/OFFLINE/recovery (s)"),
        ("healthy", "Healthy throughput (readings/s)"),
        ("integrity", "Failed resets/healthy anomalies"),
    ]


def _add_display_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["window_label"] = result["window_seconds"].map(
        lambda value: "RAW" if value == 0 else f"{value:g} s"
    )
    result["sgr"] = result.apply(
        lambda row: (
            f"{_count_pair(row, 'scheduled')}/"
            f"{_count_pair(row, 'generated')}/"
            f"{_count_pair(row, 'received')}"
        ),
        axis=1,
    )
    result["delivery"] = result.apply(
        lambda row: _pair(row, "delivery_pct", 2), axis=1
    )
    result["actual_messages"] = result.apply(
        lambda row: _pair(row, "actual_messages", 1), axis=1
    )
    result["actual_bytes"] = result.apply(
        lambda row: _pair(row, "actual_bytes_kib", 1), axis=1
    )
    result["reductions"] = result.apply(
        lambda row: (
            f"{row['message_reduction_pct_mean']:.2f} +/- "
            f"{row['message_reduction_pct_sd']:.2f}/"
            f"{row['byte_reduction_pct_mean']:.2f} +/- "
            f"{row['byte_reduction_pct_sd']:.2f}"
        ),
        axis=1,
    )
    result["delay"] = result.apply(
        lambda row: _pair(row, "information_delay_s", 3), axis=1
    )
    result["resources"] = result.apply(
        lambda row: (
            f"{row['gateway_cpu_pct_mean']:.3f} +/- {row['gateway_cpu_pct_sd']:.3f}/"
            f"{row['gateway_rss_mib_mean']:.1f} +/- {row['gateway_rss_mib_sd']:.1f}"
        ),
        axis=1,
    )
    return result


def _add_failure_display_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["sgr"] = result.apply(
        lambda row: (
            f"{_count_pair(row, 'scheduled')}/"
            f"{_count_pair(row, 'generated')}/"
            f"{_count_pair(row, 'received')}"
        ),
        axis=1,
    )
    result["absent"] = result.apply(lambda row: _count_pair(row, "absent"), axis=1)
    result["delivery"] = result.apply(
        lambda row: (
            f"{row['scheduled_delivery_pct_mean']:.2f} +/- "
            f"{row['scheduled_delivery_pct_sd']:.2f}/"
            f"{row['generated_delivery_pct_mean']:.2f} +/- "
            f"{row['generated_delivery_pct_sd']:.2f}"
        ),
        axis=1,
    )
    result["detection"] = result.apply(
        lambda row: (
            f"{row['suspect_detection_s_mean']:.3f} +/- "
            f"{row['suspect_detection_s_sd']:.3f}/"
            f"{row['offline_detection_s_mean']:.3f} +/- "
            f"{row['offline_detection_s_sd']:.3f}/"
            f"{row['recovery_detection_s_mean']:.3f} +/- "
            f"{row['recovery_detection_s_sd']:.3f}"
        ),
        axis=1,
    )
    result["healthy"] = result.apply(
        lambda row: _pair(row, "healthy_peer_throughput_rps", 3), axis=1
    )
    result["integrity"] = result.apply(
        lambda row: (
            f"{row['failed_node_resets_mean']:.0f} +/- {row['failed_node_resets_sd']:.0f}/"
            f"{row['healthy_peer_sequence_anomalies_mean']:.0f} +/- "
            f"{row['healthy_peer_sequence_anomalies_sd']:.0f}"
        ),
        axis=1,
    )
    return result


def _count_pair(row: pd.Series, prefix: str) -> str:
    mean = row[f"{prefix}_mean"]
    stddev = row[f"{prefix}_sd"]
    mean_text = f"{mean:.0f}" if float(mean).is_integer() else f"{mean:.1f}"
    sd_text = f"{stddev:.0f}" if float(stddev).is_integer() else f"{stddev:.1f}"
    return f"{mean_text} +/- {sd_text}"


def _pair(row: pd.Series, prefix: str, digits: int) -> str:
    return f"{row[f'{prefix}_mean']:.{digits}f} +/- {row[f'{prefix}_sd']:.{digits}f}"


def _markdown_table(frame: pd.DataFrame) -> str:
    header = "| " + " | ".join(frame.columns) + " |"
    separator = "| " + " | ".join("---" for _ in frame.columns) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows]) + "\n"


def _latex_table(frame: pd.DataFrame) -> str:
    columns = "l" * len(frame.columns)
    lines = [
        "\\begin{tabular}{" + columns + "}",
        "\\toprule",
        " & ".join(_latex_escape(str(column)) for column in frame.columns) + " \\\\",
        "\\midrule",
    ]
    lines.extend(
        " & ".join(_latex_escape(str(value)).replace("+/-", "$\\pm$") for value in row)
        + " \\\\"
        for row in frame.itertuples(index=False, name=None)
    )
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    return "\n".join(lines)


def _prepare_display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "window_seconds" in frame:
        return _add_display_columns(frame)
    return _add_failure_display_columns(frame)


def _latex_escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
    )


def _render_final_results(result: dict[str, Any]) -> str:
    conditions = result["condition_statistics"]
    integrity = result["campaign_integrity"]
    failure = result["failure_recovery"]

    def mean(condition: str, metric: str) -> float:
        return conditions[condition]["statistics"][metric]["mean"]

    scale_100_msg = 100 * mean(
        "scale-n100-agg05", "upstream_message_reduction_actual"
    )
    scale_100_bytes = 100 * mean(
        "scale-n100-agg05", "upstream_byte_reduction_actual"
    )
    failure_stats = failure["statistics"]
    physical = result["physical_validation"]
    suspect = failure_stats["suspect_detection_ms"]
    offline = failure_stats["offline_detection_ms"]
    recovery = failure_stats["recovery_detection_ms"]
    suspect_text = f"{suspect['mean'] / 1000:.3f} +/- {suspect['stddev'] / 1000:.3f} s"
    offline_text = f"{offline['mean'] / 1000:.3f} +/- {offline['stddev'] / 1000:.3f} s"
    recovery_text = f"{recovery['mean'] / 1000:.3f} +/- {recovery['stddev'] / 1000:.3f} s"
    return f"""# Final controlled campaign evidence

## Experimental question

{result['research_question']}

## Frozen methodology

The campaign is a controlled final virtual-node matrix frozen at Git commit
`{result['frozen_git_commit']}`. It used nine conditions, three repetitions per condition,
seeds 662/663/664, a 60 s warm-up, a 300 s measurement window, and 1 Hz sampling.
Reported uncertainty is sample standard deviation across the three repetitions. Actual collector
messages and bytes, including retransmissions, are the primary network-efficiency quantities;
unique logical records and bytes are retained separately. Aggregation holding is reported as
information delay, not network latency.

## Campaign integrity

All {integrity['run_count']} runs passed scientific admission. The campaign scheduled \
{integrity['total_scheduled_readings']:,} readings, generated \
{integrity['total_generated_readings']:,}, and uniquely received \
{integrity['total_unique_received_readings']:,}. It recorded \
{integrity['collector_messages_actual']:,} actual collector messages and \
{integrity['collector_bytes_actual']:,} actual collector bytes. There were zero duplicate record
IDs, invalid evidence counters, queue drops, abandoned records, failed admission checks, and
warnings. Actual and unique collector counts were equal in this campaign, showing no observed
retransmission duplicates while preserving the accounting distinction.

## Principal scaling result

RAW actual upstream messages increased from 1,500 at 5 nodes to 30,000 at 100 nodes, exactly
tracking the 20x tested load increase. RAW actual bytes increased from approximately 676 kB to
13.51 MB. Five-second aggregation kept mean actual messages near 56-58 per run across 5, 25, and
100 nodes. At 100 nodes it reduced actual messages by {scale_100_msg:.2f}% and actual bytes by
{scale_100_bytes:.2f}% while preserving 100% scheduled delivery. This supports performance only
through the tested 100-node load.

## Principal aggregation result

At 25 nodes, mean actual-message reductions for 1 s, 5 s, and 10 s windows were \
{100 * mean('tradeoff-n025-agg01', 'upstream_message_reduction_actual'):.2f}%, \
{100 * mean('scale-n025-agg05', 'upstream_message_reduction_actual'):.2f}%, and \
{100 * mean('tradeoff-n025-agg10', 'upstream_message_reduction_actual'):.2f}%. Mean information \
delays were {mean('tradeoff-n025-agg01', 'information_delay_mean_ms') / 1000:.3f} s, \
{mean('scale-n025-agg05', 'information_delay_mean_ms') / 1000:.3f} s, and \
{mean('tradeoff-n025-agg10', 'information_delay_mean_ms') / 1000:.3f} s, respectively. Delivery
was 100% in every non-failure condition. Longer windows therefore bought diminishing additional
traffic reduction at approximately proportional information delay.

## Principal failure/recovery result

The predetermined 90 s outage removed exactly 90 scheduled readings per run; these were not
packet loss. Each run scheduled 7,500, generated 7,410, and uniquely received all 7,410 generated
readings. Scheduled delivery was 98.8%, while generated-reading delivery was 100%. Mean SUSPECT
detection was {suspect_text}, OFFLINE detection was {offline_text}, and recovery detection was
{recovery_text}. All 24 healthy peers remained
in-order at approximately 24 readings/s. The recovered node produced one expected sequence RESET
per run.

## Physical application validation

The separate exploratory three-node physical measurement demonstrates end-to-end environmental
sensing but is not a controlled performance experiment. Bathroom RH rose from a baseline mean of
{physical['key_values']['bathroom_baseline_rh_pct']:.2f}% to \
{physical['key_values']['bathroom_peak_rh_pct']:.2f}%. The recommended physical figure is
`relative_humidity_timeseries.pdf`; `door_opening_humidity_focus.pdf` is optional if space permits.

## Limitations

{chr(10).join(f'- {item}' for item in result['limitations'])}

## Claims directly supported by evidence

{chr(10).join(f'- {item}' for item in result['supported_claims'])}

## Claims not supported by evidence

{chr(10).join(f'- {item}' for item in result['unsupported_claims'])}

## Recommended paper figures

1. Actual upstream scaling: RAW versus 5 s aggregation.
2. Aggregation-window traffic/information-delay tradeoff.
3. Gateway process resource use across the tested node counts.
4. Failure/recovery timeline with healthy-peer throughput.
5. One physical-validation RH figure; use the door-opening close-up only if space permits.

## Recommended paper tables

1. Scaling plus 5 s aggregation summary.
2. 25-node aggregation-window tradeoff.
3. Failure/recovery summary.
"""


def _limitations() -> list[str]:
    return [
        (
            "Controlled performance runs used virtual nodes over localhost TCP on one macOS "
            "host; they do not measure Wi-Fi airtime, radio energy, or Internet behavior."
        ),
        "The tested scale ends at 100 virtual nodes, so no claim is made beyond that load.",
        (
            "Only three repetitions per condition were run; uncertainty is descriptive sample "
            "SD, not a population confidence interval."
        ),
        (
            "Configured aggregation holding is information delay and must not be interpreted "
            "as network latency."
        ),
        (
            "Application loss/delay experiments were explicitly deferred and are not part of "
            "this campaign."
        ),
        (
            "The physical shower-cycle dataset is exploratory, unrandomized, and not "
            "sensor-calibrated or environmentally controlled."
        ),
        (
            "Gateway CPU/RSS values characterize the campaign host and implementation under "
            "this workload, not Raspberry Pi resource use."
        ),
    ]


def _supported_claims() -> list[str]:
    return [
        (
            "Across 5, 25, and 100 tested virtual nodes, RAW upstream traffic increased with "
            "the scheduled reading load."
        ),
        (
            "Five-second edge aggregation reduced actual collector messages and bytes "
            "substantially while preserving scheduled delivery in all non-failure runs."
        ),
        (
            "At 25 nodes, longer aggregation windows reduced traffic further while increasing "
            "information delay approximately with the configured window."
        ),
        (
            "During a predetermined single-node outage, all generated readings were delivered "
            "and 24 healthy peers maintained in-order throughput."
        ),
        "The three-node physical system captured a localized environmental RH response end to end.",
    ]


def _unsupported_claims() -> list[str]:
    return [
        "Scalability beyond 100 virtual nodes.",
        "Wi-Fi packet-loss tolerance, RF performance, energy savings, or battery lifetime.",
        "Physical one-way network latency from unsynchronized ESP32, Pi, and Mac clocks.",
        "Causal environmental conclusions from the exploratory shower-cycle measurement.",
        (
            "Production behavior under Internet, multi-gateway, adversarial, or "
            "application-impairment conditions not included in the frozen matrix."
        ),
    ]


def _tree_digest(paths: list[Path], root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    total_bytes = 0
    for path in sorted(paths):
        if not path.exists():
            raise FinalCampaignAnalysisError(f"missing source input: {path}")
        file_hash = _sha256(path)
        total_bytes += path.stat().st_size
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = str(path)
        digest.update(f"{name}\0{file_hash}\n".encode())
    return {
        "algorithm": "SHA-256 over sorted relative-path, NUL, file SHA-256, newline entries",
        "file_count": len(paths),
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalCampaignAnalysisError(f"cannot read JSON evidence {path}: {error}") from error


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-campaign", type=Path, default=DEFAULT_RAW_CAMPAIGN)
    parser.add_argument("--processed-campaign", type=Path, default=DEFAULT_PROCESSED_CAMPAIGN)
    parser.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES_ROOT)
    parser.add_argument("--exploratory-analysis", type=Path, default=DEFAULT_EXPLORATORY_ANALYSIS)
    parser.add_argument("--exploratory-figures", type=Path, default=DEFAULT_EXPLORATORY_FIGURES)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _summary, processed, figures = analyze_final_campaign(
        args.raw_campaign,
        processed_campaign=args.processed_campaign,
        figures_root=args.figures_root,
        exploratory_analysis=args.exploratory_analysis,
        exploratory_figures=args.exploratory_figures,
    )
    print(processed)
    print(figures)


if __name__ == "__main__":
    main()
