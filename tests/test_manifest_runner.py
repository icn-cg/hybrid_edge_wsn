import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from experiments.config import ExperimentConfig
from experiments.manifest import RunArtifacts, build_manifest, new_run_id, write_json_exclusive
from experiments.run import ExperimentChildError, ExperimentRunner


def config(**overrides) -> ExperimentConfig:
    values = {
        "experiment_type": "scaling",
        "node_count": 2,
        "virtual_node_count": 2,
        "duration_seconds": 0.12,
        "warmup_seconds": 0.02,
        "sampling_interval_ms": 20,
        "aggregation_mode": "raw",
        "aggregation_window_seconds": 0,
        "expected_interval_seconds": 0.02,
        "liveness_check_interval_seconds": 0.01,
        "metrics_interval_seconds": 0.02,
    }
    values.update(overrides)
    return ExperimentConfig(**values)


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_manifest_captures_real_git_commit_dirty_state_and_config(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.name", "Phase Three Test")
    git(repository, "config", "user.email", "phase3@example.invalid")
    (repository / "tracked.txt").write_text("evidence\n")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "test fixture")

    manifest = build_manifest(config(), "test-run", repository=repository)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert manifest["git_commit"] == expected
    assert manifest["git_dirty"] is False
    assert manifest["node_count"] == 2
    assert manifest["random_seed"] == 662

    (repository / "untracked.txt").write_text("dirty\n")
    dirty_manifest = build_manifest(config(), "dirty-run", repository=repository)
    assert dirty_manifest["git_dirty"] is True
    assert dirty_manifest["git_status"] == ["?? untracked.txt"]


def test_unique_run_directory_and_exclusive_manifest(tmp_path: Path) -> None:
    first = RunArtifacts.create(tmp_path, new_run_id("scaling"))
    second = RunArtifacts.create(tmp_path, new_run_id("scaling"))
    assert first.run_dir != second.run_dir

    write_json_exclusive(first.manifest, {"run_id": first.run_id})
    with pytest.raises(FileExistsError):
        write_json_exclusive(first.manifest, {"run_id": "overwrite"})


async def test_short_runner_run_produces_complete_evidence(tmp_path: Path) -> None:
    runner = ExperimentRunner(results_root=tmp_path)
    artifacts = await runner.run_once(config())

    summary = json.loads(artifacts.run_summary.read_text())
    manifest = json.loads(artifacts.manifest.read_text())
    assert summary["status"] == "complete"
    assert set(summary["children"].values()) == {0}
    assert manifest["git_commit"]
    for path in (
        artifacts.readings,
        artifacts.upstream,
        artifacts.health_events,
        artifacts.gateway_events,
        artifacts.system_metrics,
        artifacts.node_stats,
        artifacts.gateway_summary,
        artifacts.collector_summary,
    ):
        assert path.exists(), path


async def test_failed_child_is_reported_and_partial_evidence_preserved(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        results_root=tmp_path, simulator_module="virtual_nodes.module_does_not_exist"
    )
    with pytest.raises(ExperimentChildError):
        await runner.run_once(config())

    run_dir = next(tmp_path.iterdir())
    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["status"] == "failed"
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "readings.ndjson").exists()


async def test_cancelled_run_preserves_interrupted_summary(tmp_path: Path) -> None:
    runner = ExperimentRunner(results_root=tmp_path)
    task = asyncio.create_task(
        runner.run_once(config(duration_seconds=2.0, warmup_seconds=0.0))
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run_dir = next(tmp_path.iterdir())
    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["status"] == "interrupted"
    assert (run_dir / "manifest.json").exists()


async def test_repeated_runs_create_distinct_directories_and_seeds(tmp_path: Path) -> None:
    runner = ExperimentRunner(results_root=tmp_path)
    first = await runner.run_once(config(random_seed=662))
    second = await runner.run_once(config(random_seed=663))

    assert first.run_dir != second.run_dir
    first_manifest = json.loads(first.manifest.read_text())
    second_manifest = json.loads(second.manifest.read_text())
    assert [first_manifest["random_seed"], second_manifest["random_seed"]] == [662, 663]


async def test_local_runner_rejects_unstarted_physical_nodes(tmp_path: Path) -> None:
    runner = ExperimentRunner(results_root=tmp_path)

    with pytest.raises(ValueError, match="physical_node_count must be zero"):
        await runner.run_once(
            config(node_count=2, virtual_node_count=1, physical_node_count=1)
        )

    assert not tuple(tmp_path.iterdir())
