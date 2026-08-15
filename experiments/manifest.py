"""Immutable run directory and automatically captured manifest context."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from experiments.config import ExperimentConfig


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    run_id: str
    run_dir: Path
    manifest: Path
    readings: Path
    upstream: Path
    health_events: Path
    gateway_events: Path
    system_metrics: Path
    node_stats: Path
    simulator_summary: Path
    gateway_summary: Path
    collector_summary: Path
    run_summary: Path

    @classmethod
    def create(cls, results_root: str | Path, run_id: str) -> RunArtifacts:
        run_dir = Path(results_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            manifest=run_dir / "manifest.json",
            readings=run_dir / "readings.ndjson",
            upstream=run_dir / "upstream.ndjson",
            health_events=run_dir / "health_events.csv",
            gateway_events=run_dir / "gateway_events.csv",
            system_metrics=run_dir / "system_metrics.csv",
            node_stats=run_dir / "node_stats.csv",
            simulator_summary=run_dir / "simulator_summary.json",
            gateway_summary=run_dir / "gateway_summary.json",
            collector_summary=run_dir / "collector_summary.json",
            run_summary=run_dir / "run_summary.json",
        )


def new_run_id(experiment_type: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{experiment_type}-{uuid.uuid4().hex[:8]}"


def build_manifest(
    config: ExperimentConfig,
    run_id: str,
    *,
    repository: str | Path,
) -> dict[str, object]:
    commit, dirty, status = _git_context(Path(repository))
    return {
        "manifest_schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_status": status,
        "python_version": sys.version,
        "host_platform": platform.platform(),
        "orchestration_mode": "subprocess_local_tcp",
        **config.model_dump(),
    }


def write_json_exclusive(path: str | Path, value: dict[str, object]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


def _git_context(repository: Path) -> tuple[str, bool, list[str]]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status_output = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    status = [line for line in status_output.splitlines() if line]
    return commit, bool(status), status
