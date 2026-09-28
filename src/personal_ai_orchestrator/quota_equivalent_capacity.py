"""Optional, explicitly ESTIMATED "how much work is left" for a shared pool.

This module answers a question no provider answers: *at the current shared
remainder, roughly how many more tasks like mine fit?* That is useful and it is
a guess. Every guard here exists to keep it from being read as a balance.

Design constraints
------------------
* The result is always :class:`EvidenceConfidence.ESTIMATED`. There is no code
  path that produces EXACT — :class:`EquivalentCapacityEstimate` rejects it.
* Too little history yields *no estimate*, never zero. "0 tasks remaining" and
  "we do not know yet" look identical in a progress bar and mean opposite
  things, so the absent case is a distinct return value the UI must render as
  "历史数据不足，暂不估算".
* Estimates never mutate quota truth. Nothing here writes to the snapshot
  cache, the journal, or the scheduler's authoritative inputs.

Why not "count the tasks"
-------------------------
Raw task counts are misleading because tasks are not interchangeable: a
one-line fix and a refactor across twenty files are both "one task". Estimation
is therefore built on *normalized workload* — the provider-metered units each
comparable execution actually consumed — and falls back to no estimate rather
than to a proxy like wall-clock duration, which correlates with waiting rather
than with spending.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite, sqrt

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    RegistryModel,
)
from personal_ai_orchestrator.quota_plan import (
    ConsumptionUnitKind,
    MeasurementSource,
)


class EstimateBasis(StrEnum):
    """What the estimate was computed from."""

    OBSERVED_TASK_CONSUMPTION = "OBSERVED_TASK_CONSUMPTION"
    PROVIDER_REPORTED_EQUIVALENT = "PROVIDER_REPORTED_EQUIVALENT"
    UNKNOWN = "UNKNOWN"


class NoEstimateReason(StrEnum):
    """Why no estimate was produced.

    These are stable machine codes; the Dashboard maps them to localized
    sentences. Each one is a *truthful* outcome, not an error.
    """

    NO_HISTORY = "NO_HISTORY"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    UNSTABLE_HISTORY = "UNSTABLE_HISTORY"
    POOL_REMAINING_UNKNOWN = "POOL_REMAINING_UNKNOWN"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    NON_POSITIVE_CONSUMPTION = "NON_POSITIVE_CONSUMPTION"


class TaskConsumptionSample(RegistryModel):
    """One completed, comparable execution and what it actually cost.

    ``task_class`` is the comparability key. Estimating "tasks remaining" by
    averaging across unlike work is how a plausible number becomes a wrong one,
    so samples are only ever pooled within one class.
    """

    model_id: str = Field(min_length=1)
    pool_id: str = Field(min_length=1)
    task_class: str = Field(default="default", min_length=1)
    consumed_units: float = Field(gt=0.0)
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.UNKNOWN
    observed_at: datetime

    @model_validator(mode="after")
    def validate_sample(self) -> TaskConsumptionSample:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not isfinite(self.consumed_units):
            raise ValueError("consumed_units must be finite")
        return self


class EquivalentCapacityPolicy(RegistryModel):
    """The thresholds that decide whether an estimate may be published.

    ``minimum_sample_count`` defaults to 5 because that is the smallest sample
    for which the relative-standard-deviation gate below is meaningful rather
    than dominated by a single outlier; it is a policy value, not a constant,
    and :mod:`tests.test_quota_equivalent_capacity` pins its behaviour at the
    boundary so the threshold cannot drift silently.

    ``maximum_relative_stddev`` rejects histories too erratic to extrapolate
    from. A model whose per-task cost varies by more than this fraction of its
    own mean produces a number that would move wildly between refreshes, which
    reads as instability in the *quota* rather than in our sample.
    """

    minimum_sample_count: int = Field(default=5, ge=2)
    maximum_relative_stddev: float = Field(default=0.75, gt=0.0)
    #: Below this many samples the estimate is published with a visible
    #: "small sample" qualifier rather than suppressed outright.
    high_confidence_sample_count: int = Field(default=12, ge=2)

    @model_validator(mode="after")
    def validate_policy(self) -> EquivalentCapacityPolicy:
        if self.high_confidence_sample_count < self.minimum_sample_count:
            raise ValueError("high_confidence_sample_count must be at least minimum_sample_count")
        return self


DEFAULT_EQUIVALENT_CAPACITY_POLICY = EquivalentCapacityPolicy()


class EquivalentCapacityEstimate(RegistryModel):
    """A derived, advisory figure. Never an official balance.

    ``confidence`` is constrained to ESTIMATED by the validator below — the
    type itself refuses to be marked EXACT, so no caller or future refactor can
    promote a guess into provider truth.
    """

    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    pool_id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    task_class: str = Field(default="default", min_length=1)

    estimated_remaining_tasks: float = Field(ge=0.0)
    mean_consumed_units: float = Field(gt=0.0)
    relative_stddev: float = Field(ge=0.0)
    sample_count: int = Field(ge=1)
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.UNKNOWN

    estimate_basis: EstimateBasis = EstimateBasis.OBSERVED_TASK_CONSUMPTION
    measurement_source: MeasurementSource = MeasurementSource.LOCAL_EXECUTION_HISTORY
    confidence: EvidenceConfidence = EvidenceConfidence.ESTIMATED
    #: True when the sample is above the publish threshold but below the
    #: high-confidence threshold; the UI shows this as a qualifier.
    small_sample: bool = False
    observed_at: datetime

    @model_validator(mode="after")
    def validate_estimate(self) -> EquivalentCapacityEstimate:
        if self.confidence is not EvidenceConfidence.ESTIMATED:
            raise ValueError("equivalent capacity is derived; it can only ever be ESTIMATED")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


class EquivalentCapacityUnavailable(RegistryModel):
    """The truthful "we cannot say yet" outcome.

    Distinct from an estimate of zero, and deliberately carrying no numeric
    field at all so it cannot be rendered as a quantity.
    """

    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    pool_id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    task_class: str = Field(default="default", min_length=1)
    reason: NoEstimateReason
    sample_count: int = Field(default=0, ge=0)
    observed_at: datetime


EquivalentCapacityResult = EquivalentCapacityEstimate | EquivalentCapacityUnavailable


def _relative_stddev(values: list[float], mean: float) -> float:
    if len(values) < 2 or mean <= 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance) / mean


def estimate_equivalent_capacity(
    *,
    provider_id: str,
    plan_id: str,
    pool_id: str,
    window_id: str,
    model_id: str,
    remaining_units: float | None,
    remaining_unit_kind: ConsumptionUnitKind,
    samples: list[TaskConsumptionSample],
    observed_at: datetime,
    task_class: str = "default",
    policy: EquivalentCapacityPolicy = DEFAULT_EQUIVALENT_CAPACITY_POLICY,
) -> EquivalentCapacityResult:
    """Estimate remaining comparable tasks, or explain why we cannot.

    The estimate is ``remaining_units / mean(consumed_units)`` over comparable
    samples. Every precondition failure returns
    :class:`EquivalentCapacityUnavailable` rather than a degraded number.

    ``remaining_unit_kind`` must match the samples' unit. This is the guard
    that stops GLM's token-denominated model usage from being divided into a
    credit-denominated pool remainder — a division that would produce a
    confident number with no meaning.
    """

    def unavailable(reason: NoEstimateReason, count: int = 0) -> EquivalentCapacityUnavailable:
        return EquivalentCapacityUnavailable(
            provider_id=provider_id,
            plan_id=plan_id,
            pool_id=pool_id,
            window_id=window_id,
            model_id=model_id,
            task_class=task_class,
            reason=reason,
            sample_count=count,
            observed_at=observed_at,
        )

    if remaining_units is None:
        return unavailable(NoEstimateReason.POOL_REMAINING_UNKNOWN)

    comparable = [
        sample
        for sample in samples
        if sample.model_id == model_id
        and sample.pool_id == pool_id
        and sample.task_class == task_class
    ]
    if not comparable:
        return unavailable(NoEstimateReason.NO_HISTORY)

    # Dividing a pool remainder by a cost measured in a different unit yields a
    # number that looks authoritative and means nothing.
    if remaining_unit_kind is ConsumptionUnitKind.UNKNOWN or any(
        sample.unit_kind is not remaining_unit_kind for sample in comparable
    ):
        return unavailable(NoEstimateReason.UNIT_MISMATCH, len(comparable))

    if len(comparable) < policy.minimum_sample_count:
        return unavailable(NoEstimateReason.INSUFFICIENT_SAMPLE, len(comparable))

    values = [sample.consumed_units for sample in comparable]
    mean = sum(values) / len(values)
    if mean <= 0 or not isfinite(mean):
        return unavailable(NoEstimateReason.NON_POSITIVE_CONSUMPTION, len(comparable))

    relative_stddev = _relative_stddev(values, mean)
    if relative_stddev > policy.maximum_relative_stddev:
        return unavailable(NoEstimateReason.UNSTABLE_HISTORY, len(comparable))

    estimated = remaining_units / mean
    if not isfinite(estimated):
        return unavailable(NoEstimateReason.NON_POSITIVE_CONSUMPTION, len(comparable))

    return EquivalentCapacityEstimate(
        provider_id=provider_id,
        plan_id=plan_id,
        pool_id=pool_id,
        window_id=window_id,
        model_id=model_id,
        task_class=task_class,
        estimated_remaining_tasks=estimated,
        mean_consumed_units=mean,
        relative_stddev=relative_stddev,
        sample_count=len(comparable),
        unit_kind=remaining_unit_kind,
        small_sample=len(comparable) < policy.high_confidence_sample_count,
        observed_at=observed_at,
    )


__all__ = [
    "DEFAULT_EQUIVALENT_CAPACITY_POLICY",
    "EquivalentCapacityEstimate",
    "EquivalentCapacityPolicy",
    "EquivalentCapacityResult",
    "EquivalentCapacityUnavailable",
    "EstimateBasis",
    "NoEstimateReason",
    "TaskConsumptionSample",
    "estimate_equivalent_capacity",
]
