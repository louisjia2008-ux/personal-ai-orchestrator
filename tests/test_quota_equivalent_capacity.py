"""P4.2.6.5 — equivalent capacity is advisory, and says so.

The failure this suite guards against is a plausible number appearing where a
balance belongs. Every test below is really the same assertion from a different
angle: an estimate must be labelled ESTIMATED, must be absent rather than zero
when we cannot support it, and must never touch authoritative quota data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_equivalent_capacity import (
    DEFAULT_EQUIVALENT_CAPACITY_POLICY,
    EquivalentCapacityEstimate,
    EquivalentCapacityPolicy,
    EquivalentCapacityUnavailable,
    NoEstimateReason,
    TaskConsumptionSample,
    estimate_equivalent_capacity,
)
from personal_ai_orchestrator.quota_plan import ConsumptionUnitKind

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
POOL = "zai-coding-plan"
MODEL = "GLM-5.3"


def samples(
    *values: float,
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.PLAN_CREDITS,
    model_id: str = MODEL,
    pool_id: str = POOL,
    task_class: str = "default",
) -> list[TaskConsumptionSample]:
    return [
        TaskConsumptionSample(
            model_id=model_id,
            pool_id=pool_id,
            task_class=task_class,
            consumed_units=value,
            unit_kind=unit_kind,
            observed_at=NOW,
        )
        for value in values
    ]


def estimate(
    *,
    remaining_units: float | None = 1000.0,
    sample_values: tuple[float, ...] = (10, 10, 10, 10, 10),
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.PLAN_CREDITS,
    sample_unit_kind: ConsumptionUnitKind | None = None,
    policy: EquivalentCapacityPolicy = DEFAULT_EQUIVALENT_CAPACITY_POLICY,
    sample_list: list[TaskConsumptionSample] | None = None,
    task_class: str = "default",
):
    return estimate_equivalent_capacity(
        provider_id="zai",
        plan_id="coding-plan",
        pool_id=POOL,
        window_id="weekly",
        model_id=MODEL,
        remaining_units=remaining_units,
        remaining_unit_kind=unit_kind,
        samples=(
            sample_list
            if sample_list is not None
            else samples(*sample_values, unit_kind=sample_unit_kind or unit_kind)
        ),
        observed_at=NOW,
        task_class=task_class,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# Absence, not zero
# ---------------------------------------------------------------------------


def test_no_history_produces_no_estimate() -> None:
    result = estimate(sample_list=[])

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.NO_HISTORY
    # "0 tasks remaining" and "we do not know yet" look identical in a progress
    # bar and mean opposite things, so the absent case carries no number at all.
    assert not hasattr(result, "estimated_remaining_tasks")


def test_insufficient_sample_produces_no_estimate() -> None:
    result = estimate(sample_values=(10, 10, 10, 10))

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.INSUFFICIENT_SAMPLE
    assert result.sample_count == 4


def test_minimum_sample_threshold_is_pinned_at_its_boundary() -> None:
    """The threshold is policy, so both sides of it are asserted explicitly."""

    policy = DEFAULT_EQUIVALENT_CAPACITY_POLICY
    below = estimate(sample_values=tuple([10] * (policy.minimum_sample_count - 1)))
    at = estimate(sample_values=tuple([10] * policy.minimum_sample_count))

    assert isinstance(below, EquivalentCapacityUnavailable)
    assert isinstance(at, EquivalentCapacityEstimate)


def test_a_custom_threshold_is_honoured_not_hard_coded_to_five() -> None:
    strict = EquivalentCapacityPolicy(minimum_sample_count=10, high_confidence_sample_count=20)

    result = estimate(sample_values=tuple([10] * 8), policy=strict)

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.INSUFFICIENT_SAMPLE


def test_unknown_pool_remaining_produces_no_estimate() -> None:
    result = estimate(remaining_units=None)

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.POOL_REMAINING_UNKNOWN


def test_erratic_history_produces_no_estimate() -> None:
    """A number that would swing wildly between refreshes reads as instability
    in the quota rather than in our sample."""

    result = estimate(sample_values=(1, 1, 1, 1, 500))

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.UNSTABLE_HISTORY


def test_dividing_a_credit_pool_by_token_costs_is_refused() -> None:
    """GLM meters its pool in credits and its model usage in tokens.

    Dividing one by the other yields a confident number with no meaning, so the
    unit guard rejects it instead.
    """

    result = estimate(
        unit_kind=ConsumptionUnitKind.PLAN_CREDITS,
        sample_unit_kind=ConsumptionUnitKind.TOKENS,
    )

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.UNIT_MISMATCH


def test_samples_from_another_model_or_pool_are_not_borrowed() -> None:
    mixed = samples(10, 10, 10, 10, 10, model_id="GLM-5.3-Flash")

    result = estimate(sample_list=mixed)

    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.NO_HISTORY


def test_unlike_tasks_are_not_pooled_into_one_average() -> None:
    """Averaging a one-line fix with a twenty-file refactor is how a plausible
    number becomes a wrong one."""

    mixed = samples(10, 10, 10, task_class="small") + samples(
        400, 400, task_class="large"
    )

    result = estimate(sample_list=mixed, task_class="small")

    # Only the three "small" samples are comparable; the two "large" ones are
    # not borrowed to reach the threshold.
    assert isinstance(result, EquivalentCapacityUnavailable)
    assert result.reason is NoEstimateReason.INSUFFICIENT_SAMPLE
    assert result.sample_count == 3


# ---------------------------------------------------------------------------
# Producing an estimate
# ---------------------------------------------------------------------------


def test_stable_history_produces_an_estimated_result() -> None:
    result = estimate(remaining_units=1000.0, sample_values=(10, 10, 10, 10, 10))

    assert isinstance(result, EquivalentCapacityEstimate)
    assert result.estimated_remaining_tasks == pytest.approx(100.0)
    assert result.confidence is EvidenceConfidence.ESTIMATED
    assert result.sample_count == 5


def test_an_estimate_can_never_be_marked_exact() -> None:
    """The type itself refuses, so no caller or later refactor can promote a
    guess into provider truth."""

    with pytest.raises(ValidationError, match="only ever be ESTIMATED"):
        EquivalentCapacityEstimate(
            provider_id="zai",
            plan_id="coding-plan",
            pool_id=POOL,
            window_id="weekly",
            model_id=MODEL,
            estimated_remaining_tasks=100.0,
            mean_consumed_units=10.0,
            relative_stddev=0.0,
            sample_count=5,
            confidence=EvidenceConfidence.EXACT,
            observed_at=NOW,
        )


def test_a_small_but_publishable_sample_is_flagged_as_such() -> None:
    small = estimate(sample_values=(10, 10, 10, 10, 10))
    large = estimate(sample_values=tuple([10] * 20))

    assert isinstance(small, EquivalentCapacityEstimate)
    assert isinstance(large, EquivalentCapacityEstimate)
    assert small.small_sample is True
    assert large.small_sample is False


def test_a_higher_per_task_cost_lowers_the_estimate() -> None:
    cheap = estimate(remaining_units=1000.0, sample_values=(10, 10, 10, 10, 10))
    costly = estimate(remaining_units=1000.0, sample_values=(50, 50, 50, 50, 50))

    assert isinstance(cheap, EquivalentCapacityEstimate)
    assert isinstance(costly, EquivalentCapacityEstimate)
    assert costly.estimated_remaining_tasks < cheap.estimated_remaining_tasks
    assert costly.estimated_remaining_tasks == pytest.approx(20.0)


def test_a_change_in_shared_remaining_moves_every_model_estimate() -> None:
    """One pool, one remainder: models sharing it move together."""

    def for_model(model_id: str, remaining: float):
        return estimate_equivalent_capacity(
            provider_id="zai",
            plan_id="coding-plan",
            pool_id=POOL,
            window_id="weekly",
            model_id=model_id,
            remaining_units=remaining,
            remaining_unit_kind=ConsumptionUnitKind.PLAN_CREDITS,
            samples=samples(10, 10, 10, 10, 10, model_id=model_id),
            observed_at=NOW,
        )

    before = [for_model("GLM-5.3", 1000.0), for_model("GLM-5.3-Flash", 1000.0)]
    after = [for_model("GLM-5.3", 500.0), for_model("GLM-5.3-Flash", 500.0)]

    for earlier, later in zip(before, after, strict=True):
        assert isinstance(earlier, EquivalentCapacityEstimate)
        assert isinstance(later, EquivalentCapacityEstimate)
        assert later.estimated_remaining_tasks == pytest.approx(
            earlier.estimated_remaining_tasks / 2
        )


def test_estimating_does_not_mutate_the_quota_evidence_it_reads() -> None:
    history = samples(10, 10, 10, 10, 10)
    before = [sample.model_dump() for sample in history]

    result = estimate(sample_list=history)

    assert isinstance(result, EquivalentCapacityEstimate)
    assert [sample.model_dump() for sample in history] == before


def test_zero_remaining_quota_is_an_estimate_of_zero_not_an_absence() -> None:
    """Genuinely empty is a real answer, and distinct from "we cannot say"."""

    result = estimate(remaining_units=0.0)

    assert isinstance(result, EquivalentCapacityEstimate)
    assert result.estimated_remaining_tasks == pytest.approx(0.0)


def test_a_sample_costing_nothing_is_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        TaskConsumptionSample(
            model_id=MODEL,
            pool_id=POOL,
            consumed_units=0.0,
            unit_kind=ConsumptionUnitKind.PLAN_CREDITS,
            observed_at=NOW,
        )


def test_policy_thresholds_must_be_internally_consistent() -> None:
    with pytest.raises(ValidationError, match="high_confidence_sample_count"):
        EquivalentCapacityPolicy(minimum_sample_count=10, high_confidence_sample_count=3)
