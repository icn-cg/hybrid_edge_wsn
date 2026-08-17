"""Frozen, minimal controlled matrix for the COMPE 662 final report."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from analysis.analyze import analyze_run
from experiments.config import ExperimentConfig
from experiments.manifest import git_context, write_json_exclusive
from experiments.run import ExperimentRunner

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = REPOSITORY / "results" / "raw"
DEFAULT_PROCESSED_ROOT = REPOSITORY / "results" / "processed"
DEFAULT_CAMPAIGN_ID = "final-controlled-v1"
DEFAULT_WARMUP_SECONDS = 60.0
DEFAULT_MEASUREMENT_SECONDS = 300.0
DEFAULT_BASE_SEED = 662
REPETITIONS = 3
FAILURE_AT_SECONDS = 90.0
RECOVERY_AT_SECONDS = 180.0


class FinalMatrixError(RuntimeError):
    """Raised when a final campaign cannot proceed without weakening its design."""


@dataclass(frozen=True, slots=True)
class MatrixCondition:
    condition_id: str
    experiment_type: Literal["scaling", "aggregation", "failure"]
    node_count: int
    aggregation_mode: Literal["raw", "aggregated"]
    aggregation_window_seconds: float
    comparison_groups: tuple[str, ...]
    failure_at_seconds: float | None = None
    recovery_at_seconds: float | None = None


def final_conditions() -> tuple[MatrixCondition, ...]:
    """Return nine unique conditions; shared 25-node evidence is not rerun."""

    scaling = []
    for nodes in (5, 25, 100):
        shared_raw = ("scaling", "aggregation_window") if nodes == 25 else ("scaling",)
        shared_agg = ("scaling", "aggregation_window") if nodes == 25 else ("scaling",)
        scaling.extend(
            (
                MatrixCondition(
                    condition_id=f"scale-n{nodes:03d}-raw",
                    experiment_type="scaling",
                    node_count=nodes,
                    aggregation_mode="raw",
                    aggregation_window_seconds=0.0,
                    comparison_groups=shared_raw,
                ),
                MatrixCondition(
                    condition_id=f"scale-n{nodes:03d}-agg05",
                    experiment_type="scaling",
                    node_count=nodes,
                    aggregation_mode="aggregated",
                    aggregation_window_seconds=5.0,
                    comparison_groups=shared_agg,
                ),
            )
        )
    tradeoff = (
        MatrixCondition(
            condition_id="tradeoff-n025-agg01",
            experiment_type="aggregation",
            node_count=25,
            aggregation_mode="aggregated",
            aggregation_window_seconds=1.0,
            comparison_groups=("aggregation_window",),
        ),
        MatrixCondition(
            condition_id="tradeoff-n025-agg10",
            experiment_type="aggregation",
            node_count=25,
            aggregation_mode="aggregated",
            aggregation_window_seconds=10.0,
            comparison_groups=("aggregation_window",),
        ),
    )
    failure = MatrixCondition(
        condition_id="failure-n025-raw",
        experiment_type="failure",
        node_count=25,
        aggregation_mode="raw",
        aggregation_window_seconds=0.0,
        comparison_groups=("failure_recovery",),
        failure_at_seconds=FAILURE_AT_SECONDS,
        recovery_at_seconds=RECOVERY_AT_SECONDS,
    )
    return (*scaling, *tradeoff, failure)


def build_campaign_plan(
    *,
    campaign_id: str,
    warmup_seconds: float = DEFAULT_WARMUP_SECONDS,
    measurement_seconds: float = DEFAULT_MEASUREMENT_SECONDS,
    base_seed: int = DEFAULT_BASE_SEED,
) -> dict[str, object]:
    """Build the machine-readable frozen plan without starting a run."""

    if warmup_seconds < 30:
        raise FinalMatrixError("final warm-up must be at least 30 seconds")
    if measurement_seconds < 120:
        raise FinalMatrixError("final measurement must be at least 120 seconds")
    conditions = final_conditions()
    unique_runs = len(conditions) * REPETITIONS
    nominal_seconds = unique_runs * (warmup_seconds + measurement_seconds)
    commit, dirty, status = git_context(REPOSITORY)
    return {
        "campaign_schema_version": 1,
        "campaign_id": campaign_id,
        "classification": "controlled final virtual-node experiment matrix",
        "research_question": (
            "How effectively can edge aggregation reduce upstream communication in a hybrid "
            "physical/virtual wireless sensor network while preserving reliable sensing as the "
            "network scales and experiences node failure?"
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_status": status,
        "warmup_seconds": warmup_seconds,
        "measurement_seconds": measurement_seconds,
        "sampling_interval_ms": 1000.0,
        "repetitions_per_condition": REPETITIONS,
        "base_seed": base_seed,
        "seed_strategy": "random_seed = base_seed + zero-based repetition_index",
        "repetition_seeds": [base_seed + index for index in range(REPETITIONS)],
        "unique_conditions": len(conditions),
        "unique_executed_runs": unique_runs,
        "nominal_acquisition_seconds": nominal_seconds,
        "nominal_acquisition_minutes": nominal_seconds / 60,
        "analytical_run_counts": {
            "scaling_and_aggregation": 18,
            "aggregation_window": 12,
            "failure_recovery": 3,
        },
        "reuse_policy": (
            "The three 25-node RAW and three 25-node 5-second runs belong to both scaling and "
            "aggregation-window comparison groups and are executed only once."
        ),
        "deferred": "application loss/delay matrix",
        "conditions": [asdict(condition) for condition in conditions],
    }


async def execute_campaign(
    *,
    campaign_id: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
    warmup_seconds: float = DEFAULT_WARMUP_SECONDS,
    measurement_seconds: float = DEFAULT_MEASUREMENT_SECONDS,
    base_seed: int = DEFAULT_BASE_SEED,
) -> tuple[Path, Path]:
    """Execute the frozen matrix, stopping at the first failed scientific gate."""

    plan = build_campaign_plan(
        campaign_id=campaign_id,
        warmup_seconds=warmup_seconds,
        measurement_seconds=measurement_seconds,
        base_seed=base_seed,
    )
    if plan["git_dirty"]:
        raise FinalMatrixError("final campaign requires a clean tracked worktree")
    raw_campaign = raw_root / campaign_id
    processed_campaign = processed_root / campaign_id
    raw_campaign.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(raw_campaign / "campaign_manifest.json", plan)
    admissions: list[dict[str, object]] = []
    failed_run: str | None = None
    for condition in final_conditions():
        runner = ExperimentRunner(results_root=raw_campaign / condition.condition_id)
        for repetition_index in range(REPETITIONS):
            seed = base_seed + repetition_index
            config = ExperimentConfig(
                experiment_type=condition.experiment_type,
                node_count=condition.node_count,
                virtual_node_count=condition.node_count,
                duration_seconds=measurement_seconds,
                warmup_seconds=warmup_seconds,
                sampling_interval_ms=1000.0,
                aggregation_mode=condition.aggregation_mode,
                aggregation_window_seconds=condition.aggregation_window_seconds,
                random_seed=seed,
                base_seed=base_seed,
                repetition_index=repetition_index,
                campaign_id=campaign_id,
                condition_id=condition.condition_id,
                run_classification="controlled_final",
                expected_interval_seconds=1.0,
                failure_at_seconds=condition.failure_at_seconds,
                recovery_at_seconds=condition.recovery_at_seconds,
            )
            artifacts = await runner.run_once(config)
            metrics, output = analyze_run(
                artifacts.run_dir,
                processed_root=processed_campaign,
            )
            admission = metrics["scientific_admission"]
            admissions.append(
                {
                    "condition_id": condition.condition_id,
                    "repetition_index": repetition_index,
                    "random_seed": seed,
                    "run_id": artifacts.run_id,
                    "run_directory": str(artifacts.run_dir),
                    "processed_directory": str(output),
                    "admission": admission,
                }
            )
            print(artifacts.run_dir, flush=True)
            if not admission["passed"]:
                failed_run = artifacts.run_id
                break
        if failed_run is not None:
            break
    summary = {
        "campaign_schema_version": 1,
        "campaign_id": campaign_id,
        "status": "admission_failed" if failed_run else "success",
        "failed_run_id": failed_run,
        "completed_run_count": len(admissions),
        "admissions": admissions,
    }
    write_json_exclusive(raw_campaign / "campaign_summary.json", summary)
    if failed_run is not None:
        raise FinalMatrixError(
            f"scientific admission failed for {failed_run}; evidence was preserved"
        )
    return raw_campaign, processed_campaign


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or execute the frozen final matrix")
    parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--warmup", type=float, default=DEFAULT_WARMUP_SECONDS)
    parser.add_argument("--duration", type=float, default=DEFAULT_MEASUREMENT_SECONDS)
    parser.add_argument("--seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="execute all 27 unique final runs; without this flag only print the plan",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    plan = build_campaign_plan(
        campaign_id=args.campaign_id,
        warmup_seconds=args.warmup,
        measurement_seconds=args.duration,
        base_seed=args.seed,
    )
    print(
        f"{plan['unique_executed_runs']} runs; "
        f"{plan['nominal_acquisition_minutes']:.1f} nominal minutes; "
        f"seeds={plan['repetition_seeds']}"
    )
    if not args.execute:
        return
    raw, processed = asyncio.run(
        execute_campaign(
            campaign_id=args.campaign_id,
            raw_root=args.raw_root,
            processed_root=args.processed_root,
            warmup_seconds=args.warmup,
            measurement_seconds=args.duration,
            base_seed=args.seed,
        )
    )
    print(raw)
    print(processed)


if __name__ == "__main__":
    main()
