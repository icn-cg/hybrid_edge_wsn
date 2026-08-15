"""Minimal scientific plots supported by each experiment family."""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def plot_comparison(frame: pd.DataFrame, experiment_type: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    if experiment_type == "scaling":
        _plot(frame, "node_count", "latency_median_ms", output_dir, "latency_vs_nodes")
        _plot(
            frame,
            "node_count",
            "virtual_unique_throughput_readings_per_second",
            output_dir,
            "throughput_vs_nodes",
        )
        _plot(
            frame,
            "node_count",
            "process_cpu_mean_percent",
            output_dir,
            "cpu_vs_nodes",
        )
        _plot(
            frame,
            "node_count",
            "process_rss_mean_bytes",
            output_dir,
            "memory_vs_nodes",
        )
    elif experiment_type == "aggregation":
        _plot(
            frame,
            "aggregation_window_seconds",
            "collector_bytes_actual",
            output_dir,
            "upstream_actual_bytes_vs_window",
        )
        _plot(
            frame,
            "aggregation_window_seconds",
            "upstream_message_reduction_actual",
            output_dir,
            "upstream_actual_message_reduction_vs_window",
        )
        _plot(
            frame,
            "aggregation_window_seconds",
            "information_delay_mean_ms",
            output_dir,
            "information_delay_vs_window",
        )
    elif experiment_type == "impairment":
        if frame["drop_probability"].nunique() > 1:
            _plot(
                frame,
                "drop_probability",
                "delivery_ratio",
                output_dir,
                "delivery_vs_application_drop",
            )
        elif frame["artificial_delay_ms"].nunique() > 1:
            _plot(
                frame,
                "artificial_delay_ms",
                "delivery_ratio",
                output_dir,
                "delivery_vs_application_delay",
            )
    elif experiment_type == "failure":
        _plot(
            frame,
            "run_id",
            "failure_detection_time_ms",
            output_dir,
            "failure_suspect_detection_by_run",
        )


def _plot(
    frame: pd.DataFrame,
    x: str,
    y: str,
    output_dir: Path,
    filename: str,
) -> None:
    usable = frame[[x, y]].dropna()
    if usable.empty:
        return
    grouped = usable.groupby(x, as_index=False)[y].agg(["mean", "std"]).reset_index()
    figure, axis = plt.subplots(figsize=(6.4, 4.0))
    errors = grouped["std"].fillna(0)
    axis.errorbar(grouped[x], grouped["mean"], yerr=errors, marker="o", capsize=3)
    axis.set_xlabel(x.replace("_", " "))
    axis.set_ylabel(y.replace("_", " "))
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_dir / f"{filename}.png", dpi=160)
    plt.close(figure)
