from __future__ import annotations

import pytest

from experiments.final_matrix import (
    FinalMatrixError,
    build_campaign_plan,
    final_conditions,
)


def test_final_matrix_reuses_identical_25_node_conditions() -> None:
    conditions = final_conditions()

    assert len(conditions) == 9
    assert len(conditions) * 3 == 27
    by_id = {condition.condition_id: condition for condition in conditions}
    assert by_id["scale-n025-raw"].comparison_groups == (
        "scaling",
        "aggregation_window",
    )
    assert by_id["scale-n025-agg05"].comparison_groups == (
        "scaling",
        "aggregation_window",
    )
    assert "tradeoff-n025-raw" not in by_id
    assert "tradeoff-n025-agg05" not in by_id


def test_final_campaign_plan_freezes_runtime_and_seed_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "experiments.final_matrix.git_context", lambda _repository: ("abc123", False, [])
    )

    plan = build_campaign_plan(campaign_id="final-v1")

    assert plan["git_commit"] == "abc123"
    assert plan["repetition_seeds"] == [662, 663, 664]
    assert plan["unique_executed_runs"] == 27
    assert plan["nominal_acquisition_seconds"] == 9_720
    assert plan["analytical_run_counts"] == {
        "scaling_and_aggregation": 18,
        "aggregation_window": 12,
        "failure_recovery": 3,
    }


@pytest.mark.parametrize(
    ("warmup", "duration", "message"),
    ((29, 300, "warm-up"), (60, 119, "measurement")),
)
def test_final_campaign_refuses_silent_duration_shortening(
    warmup: float,
    duration: float,
    message: str,
) -> None:
    with pytest.raises(FinalMatrixError, match=message):
        build_campaign_plan(
            campaign_id="shortened",
            warmup_seconds=warmup,
            measurement_seconds=duration,
        )
