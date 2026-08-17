"""Publication figures for the frozen final controlled campaign."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RAW_COLOR = "#0072B2"
AGG_COLOR = "#D55E00"
BYTE_COLOR = "#009E73"
HEALTHY_COLOR = "#0072B2"
TOTAL_COLOR = "#D55E00"


def create_campaign_figures(
    runs: pd.DataFrame,
    failure_timeseries: pd.DataFrame,
    failure_events: pd.DataFrame,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Create the four predeclared paper figures in PDF and 300-DPI PNG."""

    output_dir.mkdir(parents=True, exist_ok=False)
    _configure_style()
    figures = [
        _plot_scaling(runs, output_dir),
        _plot_tradeoff(runs, output_dir),
        _plot_resources(runs, output_dir),
        _plot_failure(failure_timeseries, failure_events, output_dir),
    ]
    return figures


def _configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.labelsize": 9,
            "axes.titlesize": 9.5,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "grid.linewidth": 0.6,
            "lines.linewidth": 1.5,
            "lines.markersize": 5,
            "figure.dpi": 120,
            "savefig.bbox": "tight",
        }
    )


def _mean_sd(frame: pd.DataFrame, x: str, y: str) -> pd.DataFrame:
    return (
        frame.groupby(x, as_index=False)[y]
        .agg(["mean", "std"])
        .reset_index()
        .fillna({"std": 0.0})
    )


def _error_line(
    axis: plt.Axes,
    frame: pd.DataFrame,
    x: str,
    y: str,
    *,
    label: str,
    color: str,
    marker: str,
) -> None:
    grouped = _mean_sd(frame, x, y)
    axis.errorbar(
        grouped[x],
        grouped["mean"],
        yerr=grouped["std"],
        color=color,
        marker=marker,
        capsize=3,
        label=label,
    )


def _plot_scaling(runs: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    usable = runs[runs["comparison_group_scaling"]].copy()
    raw = usable[usable["aggregation_mode"] == "raw"]
    aggregated = usable[usable["aggregation_window_seconds"] == 5.0]

    figure, axes = plt.subplots(1, 2, figsize=(7.05, 2.75))
    for axis, metric, ylabel in (
        (axes[0], "collector_messages_actual", "Actual collector messages"),
        (axes[1], "collector_bytes_actual", "Actual collector bytes"),
    ):
        _error_line(
            axis,
            raw,
            "node_count",
            metric,
            label="RAW",
            color=RAW_COLOR,
            marker="o",
        )
        _error_line(
            axis,
            aggregated,
            "node_count",
            metric,
            label="5 s aggregation",
            color=AGG_COLOR,
            marker="s",
        )
        axis.set_yscale("log")
        axis.set_xticks([5, 25, 100])
        axis.set_xlabel("Virtual node count")
        axis.set_ylabel(ylabel)
        axis.legend(frameon=False)
    axes[0].set_title("(a) Upstream message scaling")
    axes[1].set_title("(b) Upstream byte scaling")
    figure.suptitle("Actual upstream traffic across the tested load range", y=1.02)
    figure.tight_layout()
    paths = _save(figure, output_dir, "scaling_actual_upstream")
    return {
        "figure_id": "scaling_actual_upstream",
        "purpose": (
            "Actual collector traffic including retransmissions versus tested node count; "
            "RAW compared with 5-second aggregation."
        ),
        **paths,
    }


def _plot_tradeoff(runs: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    usable = runs[runs["comparison_group_aggregation_window"]].copy()
    usable["information_delay_seconds"] = usable["information_delay_mean_ms"] / 1000.0
    usable["actual_messages_remaining_pct"] = (
        100.0 * (1.0 - usable["upstream_message_reduction_actual"])
    )
    usable["actual_bytes_remaining_pct"] = (
        100.0 * (1.0 - usable["upstream_byte_reduction_actual"])
    )

    points = []
    for window, group in usable.groupby("aggregation_window_seconds"):
        points.append(
            {
                "window": float(window),
                "delay_mean": group["information_delay_seconds"].mean(),
                "delay_sd": group["information_delay_seconds"].std(ddof=1),
                "messages_mean": group["actual_messages_remaining_pct"].mean(),
                "messages_sd": group["actual_messages_remaining_pct"].std(ddof=1),
                "bytes_mean": group["actual_bytes_remaining_pct"].mean(),
                "bytes_sd": group["actual_bytes_remaining_pct"].std(ddof=1),
            }
        )
    grouped = pd.DataFrame(points).sort_values("window")

    figure, axis = plt.subplots(figsize=(5.2, 3.25))
    for metric, error, label, color, marker in (
        (
            "messages_mean",
            "messages_sd",
            "Actual messages remaining",
            AGG_COLOR,
            "o",
        ),
        ("bytes_mean", "bytes_sd", "Actual bytes remaining", BYTE_COLOR, "s"),
    ):
        axis.errorbar(
            grouped["delay_mean"],
            grouped[metric],
            xerr=grouped["delay_sd"].fillna(0),
            yerr=grouped[error].fillna(0),
            marker=marker,
            color=color,
            capsize=3,
            label=label,
        )
    for row in grouped.itertuples(index=False):
        label = "RAW" if row.window == 0 else f"{row.window:g} s"
        axis.annotate(
            label,
            (row.delay_mean, row.bytes_mean),
            xytext=(4, 5),
            textcoords="offset points",
            fontsize=7.5,
        )
    axis.set_yscale("log")
    axis.set_ylim(0.25, 160)
    axis.set_xlim(-0.3, 10.7)
    axis.set_xlabel("Mean information delay (s)")
    axis.set_ylabel("Traffic remaining relative to RAW (%)")
    axis.set_title("25-node aggregation tradeoff")
    axis.legend(frameon=False, loc="upper right")
    axis.text(
        0.02,
        0.04,
        "Lower traffic is better; delay reflects edge holding time.",
        transform=axis.transAxes,
        fontsize=7.5,
    )
    figure.tight_layout()
    paths = _save(figure, output_dir, "aggregation_window_tradeoff")
    return {
        "figure_id": "aggregation_window_tradeoff",
        "purpose": (
            "Actual traffic remaining relative to RAW versus measured information delay at "
            "25 nodes."
        ),
        **paths,
    }


def _plot_resources(runs: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    usable = runs[runs["comparison_group_scaling"]].copy()
    raw = usable[usable["aggregation_mode"] == "raw"]
    aggregated = usable[usable["aggregation_window_seconds"] == 5.0]
    usable["process_rss_mean_mib"] = usable["process_rss_mean_bytes"] / (1024.0**2)
    raw = usable[(usable["aggregation_mode"] == "raw")]
    aggregated = usable[usable["aggregation_window_seconds"] == 5.0]

    figure, axes = plt.subplots(1, 2, figsize=(7.05, 2.75))
    for axis, metric, ylabel in (
        (axes[0], "process_cpu_mean_percent", "Gateway process CPU (%)"),
        (axes[1], "process_rss_mean_mib", "Gateway mean RSS (MiB)"),
    ):
        _error_line(
            axis,
            raw,
            "node_count",
            metric,
            label="RAW",
            color=RAW_COLOR,
            marker="o",
        )
        _error_line(
            axis,
            aggregated,
            "node_count",
            metric,
            label="5 s aggregation",
            color=AGG_COLOR,
            marker="s",
        )
        axis.set_xticks([5, 25, 100])
        axis.set_xlabel("Virtual node count")
        axis.set_ylabel(ylabel)
        axis.legend(frameon=False)
    axes[0].set_ylim(bottom=0)
    axes[0].set_title("(a) CPU across tested load")
    axes[1].set_title("(b) Memory across tested load")
    figure.suptitle("Gateway process resources on the campaign host", y=1.02)
    figure.tight_layout()
    paths = _save(figure, output_dir, "gateway_resources_scaling")
    return {
        "figure_id": "gateway_resources_scaling",
        "purpose": (
            "Observed gateway process CPU and RSS on the campaign host; no extrapolation "
            "beyond 100 virtual nodes."
        ),
        **paths,
    }


def _plot_failure(
    timeseries: pd.DataFrame,
    events: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Any]:
    aggregate = (
        timeseries.groupby("bin_start_seconds", as_index=False)[
            ["total_throughput", "healthy_throughput"]
        ]
        .agg(["mean", "std"])
        .reset_index()
    )
    x = aggregate["bin_start_seconds"].to_numpy(dtype=float) + 2.5

    figure, (top, bottom) = plt.subplots(
        2,
        1,
        figsize=(7.05, 3.75),
        sharex=True,
        gridspec_kw={"height_ratios": [3.0, 1.15]},
    )
    for metric, label, color in (
        ("total_throughput", "All nodes", TOTAL_COLOR),
        ("healthy_throughput", "24 healthy peers", HEALTHY_COLOR),
    ):
        mean = aggregate[(metric, "mean")].to_numpy(dtype=float)
        std = aggregate[(metric, "std")].fillna(0).to_numpy(dtype=float)
        top.plot(x, mean, color=color, label=label)
        top.fill_between(x, mean - std, mean + std, color=color, alpha=0.16)
    top.axvspan(90, 180, color="#999999", alpha=0.12, label="Configured outage")
    top.axvline(90, color="#555555", linestyle="--", linewidth=1)
    top.axvline(180, color="#555555", linestyle="--", linewidth=1)
    top.set_ylabel("Received readings/s")
    top.set_ylim(22.8, 25.5)
    top.set_title("Controlled target-node outage with healthy-peer continuity")
    top.legend(frameon=False, ncol=3, loc="lower left")

    suspect = events.loc[events["event"] == "SUSPECT", "elapsed_seconds"]
    offline = events.loc[events["event"] == "OFFLINE", "elapsed_seconds"]
    online = events.loc[events["event"] == "RECOVERED", "elapsed_seconds"]
    suspect_mean = suspect.mean()
    offline_mean = offline.mean()
    online_mean = online.mean()
    state_spans = (
        (0.0, suspect_mean, "ONLINE", "#56B4E9"),
        (suspect_mean, offline_mean, "SUSPECT", "#F0E442"),
        (offline_mean, online_mean, "OFFLINE", "#CC79A7"),
        (online_mean, 300.0, "ONLINE", "#56B4E9"),
    )
    for start, end, label, color in state_spans:
        bottom.axvspan(start, end, color=color, alpha=0.62, linewidth=0)
        if end - start > 12:
            bottom.text((start + end) / 2, 0.62, label, ha="center", va="center", fontsize=8)
    bottom.annotate(
        "SUSPECT",
        xy=((suspect_mean + offline_mean) / 2, 0.72),
        xytext=(112, 0.88),
        arrowprops={"arrowstyle": "->", "linewidth": 0.8, "color": "#333333"},
        fontsize=7.5,
        ha="center",
    )
    for index, event in enumerate((suspect, offline, online)):
        bottom.scatter(event, np.full(len(event), 0.18 + index * 0.12), s=15, color="#222222")
    bottom.axvline(90, color="#555555", linestyle="--", linewidth=1)
    bottom.axvline(180, color="#555555", linestyle="--", linewidth=1)
    bottom.text(90, 1.02, "fail", ha="center", va="bottom", fontsize=7.5)
    bottom.text(180, 1.02, "recover", ha="center", va="bottom", fontsize=7.5)
    bottom.set_ylim(0, 1)
    bottom.set_yticks([])
    bottom.set_xlabel("Elapsed measurement time (s)")
    bottom.set_ylabel("Target\nstate", rotation=0, labelpad=24, va="center")
    bottom.grid(False)
    bottom.spines["left"].set_visible(False)
    figure.tight_layout()
    paths = _save(figure, output_dir, "failure_recovery_timeline")
    return {
        "figure_id": "failure_recovery_timeline",
        "purpose": (
            "Mean 5-second throughput across repetitions and observed target-node liveness "
            "transitions during the predetermined outage."
        ),
        **paths,
    }


def _save(figure: plt.Figure, output_dir: Path, filename: str) -> dict[str, str]:
    pdf = output_dir / f"{filename}.pdf"
    png = output_dir / f"{filename}.png"
    figure.savefig(pdf)
    figure.savefig(png, dpi=300)
    plt.close(figure)
    return {"pdf": str(pdf), "png": str(png)}
