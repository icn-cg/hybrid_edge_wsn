"""Publication-oriented plots for the frozen three-node exploratory measurement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

NODE_LABELS = {
    "physical-001": "Balcony (physical-001)",
    "physical-002": "Room (physical-002)",
    "physical-003": "Bathroom (physical-003)",
}
NODE_COLORS = {
    "physical-001": "#0072B2",
    "physical-002": "#009E73",
    "physical-003": "#D55E00",
}
NODE_STYLES = {
    "physical-001": "-",
    "physical-002": "--",
    "physical-003": "-.",
}


def create_candidate_figures(
    readings: pd.DataFrame,
    cadence_intervals: pd.DataFrame,
    events: dict[str, pd.Timestamp],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Create the five claim-oriented candidate figures in a new directory."""

    output_dir.mkdir(parents=True, exist_ok=False)
    _configure_style()
    outputs = []
    outputs.append(
        _plot_environment_series(
            readings,
            events,
            output_dir,
            column="humidity_pct",
            ylabel="Relative humidity (%)",
            stem="relative_humidity_timeseries",
            resample_rule="30s",
            claim="Localized bathroom humidity response and cross-node context.",
        )
    )
    outputs.append(
        _plot_environment_series(
            readings,
            events,
            output_dir,
            column="temperature_c",
            ylabel="Temperature (°C)",
            stem="temperature_timeseries",
            resample_rule="30s",
            claim="Localized bathroom warming relative to the other placements.",
        )
    )
    outputs.append(_plot_door_focus(readings, events, output_dir))
    outputs.append(
        _plot_environment_series(
            readings,
            events,
            output_dir,
            column="pressure_hpa",
            ylabel="Pressure (hPa)",
            stem="pressure_timeseries",
            resample_rule="60s",
            claim="Pressure as a control-like variable during the humidity event.",
        )
    )
    outputs.append(_plot_cadence(cadence_intervals, output_dir))
    return outputs


def _configure_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "legend.fontsize": 7,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def _plot_environment_series(
    readings: pd.DataFrame,
    events: dict[str, pd.Timestamp],
    output_dir: Path,
    *,
    column: str,
    ylabel: str,
    stem: str,
    resample_rule: str,
    claim: str,
) -> dict[str, Any]:
    figure, axis = plt.subplots(figsize=(7.16, 3.25))
    for node_id in NODE_LABELS:
        node = readings[readings["node_id"] == node_id]
        series = (
            node.set_index("gateway_time")[[column]]
            .resample(resample_rule)
            .mean()
            .dropna()
        )
        axis.plot(
            series.index,
            series[column],
            color=NODE_COLORS[node_id],
            linestyle=NODE_STYLES[node_id],
            linewidth=1.35,
            label=NODE_LABELS[node_id],
        )
    _annotate_events(axis, events)
    axis.set_xlabel("Local time (PDT, 2026-08-17)")
    axis.set_ylabel(ylabel)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=events["shower_on"].tz))
    axis.grid(True, alpha=0.22, linewidth=0.6)
    axis.legend(loc="best", frameon=False, ncol=3)
    figure.tight_layout()
    _save_figure(figure, output_dir, stem)
    plt.close(figure)
    return _figure_metadata(stem, claim, resample_rule)


def _plot_door_focus(
    readings: pd.DataFrame,
    events: dict[str, pd.Timestamp],
    output_dir: Path,
) -> dict[str, Any]:
    door = events["bathroom_door_opened_toward_physical-002"]
    start = door - pd.Timedelta(minutes=12)
    end = min(readings["gateway_time"].max(), door + pd.Timedelta(minutes=9))
    figure, axis = plt.subplots(figsize=(7.16, 3.15))
    for node_id in ("physical-002", "physical-003"):
        node = readings[
            (readings["node_id"] == node_id)
            & (readings["gateway_time"] >= start)
            & (readings["gateway_time"] <= end)
        ]
        series = (
            node.set_index("gateway_time")[["humidity_pct"]]
            .resample("15s")
            .mean()
            .dropna()
        )
        axis.plot(
            series.index,
            series["humidity_pct"],
            color=NODE_COLORS[node_id],
            linestyle=NODE_STYLES[node_id],
            linewidth=1.6,
            label=NODE_LABELS[node_id],
        )
    axis.axvline(door, color="#5B5B5B", linewidth=1.1, linestyle=":", label="Door opened")
    axis.set_xlabel("Local time (PDT, 2026-08-17)")
    axis.set_ylabel("Relative humidity (%)")
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=door.tz))
    axis.grid(True, alpha=0.22, linewidth=0.6)
    axis.legend(loc="best", frameon=False)
    figure.tight_layout()
    stem = "door_opening_humidity_focus"
    _save_figure(figure, output_dir, stem)
    plt.close(figure)
    return _figure_metadata(
        stem,
        "Opposing bathroom and room humidity changes around the door-opening event.",
        "15s",
    )


def _plot_cadence(cadence: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    figure, axis = plt.subplots(figsize=(7.16, 3.0))
    for node_id in NODE_LABELS:
        interval_ms = cadence.loc[
            cadence["node_id"] == node_id, "interval_ms"
        ].to_numpy()
        absolute_error_ms = np.sort(
            np.maximum(np.abs(interval_ms - 1000.0), 0.5)
        )
        exceedance_probability = (
            len(absolute_error_ms) - np.arange(len(absolute_error_ms))
        ) / len(absolute_error_ms)
        axis.step(
            absolute_error_ms,
            exceedance_probability,
            where="post",
            color=NODE_COLORS[node_id],
            linestyle=NODE_STYLES[node_id],
            linewidth=1.35,
            label=NODE_LABELS[node_id],
        )
    axis.axvline(50, color="#5B5B5B", linewidth=0.9, linestyle=":")
    axis.text(52, 0.55, "50 ms", color="#5B5B5B", fontsize=7)
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel("Absolute gateway inter-arrival error |Δt − 1000 ms| (ms)")
    axis.set_ylabel("Fraction of intervals at or above error")
    axis.set_xlim(0.5, 3000)
    axis.set_ylim(1e-4, 1.05)
    axis.grid(True, alpha=0.22, linewidth=0.6)
    axis.legend(loc="upper right", frameon=False)
    figure.tight_layout()
    stem = "sampling_cadence_tail"
    _save_figure(figure, output_dir, stem)
    plt.close(figure)
    return _figure_metadata(
        stem,
        "Typical one-second cadence with a small receive-side timing tail.",
        "raw inter-arrival intervals",
    )


def _annotate_events(axis: plt.Axes, events: dict[str, pd.Timestamp]) -> None:
    shower_on = events["shower_on"]
    shower_off = events["shower_off"]
    door = events["bathroom_door_opened_toward_physical-002"]
    axis.axvspan(
        shower_on,
        shower_off,
        color="#BDBDBD",
        alpha=0.14,
        label="Reported shower interval (approx.)",
    )
    axis.axvline(door, color="#5B5B5B", linewidth=1.0, linestyle=":", label="Door opened")


def _save_figure(figure: plt.Figure, output_dir: Path, stem: str) -> None:
    figure.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    figure.savefig(output_dir / f"{stem}.png", bbox_inches="tight", dpi=300)


def _figure_metadata(stem: str, claim: str, resolution: str) -> dict[str, Any]:
    return {
        "stem": stem,
        "files": [f"{stem}.pdf", f"{stem}.png"],
        "supported_claim": claim,
        "display_resolution": resolution,
    }
