"""Owner-dispatch target recommendation.

Pure, deterministic ranking of execution targets by a scheduling policy,
with hard eligibility gates applied before scoring.

Distinct from :mod:`scheduler.route_task`: this recommender does NOT
require a :class:`TaskProfile`. Owner-dispatch has no natural-language-
to-capability inference path yet, so capability fit is uniformly 1.0
and the policy weights redistribute into the signals it does know —
quota headroom, evidence freshness, runtime availability. The reasoning
is reported per candidate, so the owner can see what was and was not
considered.

M1 WP1 adds ``CandidateWindowInput`` and ``DispatchCandidateInput.windows``:
the per-window data the recommender (and the card-rendering
``source_pressure`` shim) needs to call ``quota_burn.assess`` for one
window at a time. ``source_pressure_for`` is the pure function both the
recommender and the dashboard will read; it sits here rather than in
``quota_burn`` so it can speak ``DispatchCandidateInput`` without
``quota_burn`` knowing what a dispatch candidate is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from personal_ai_orchestrator.model_registry import QuotaWindowKind
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityState,
)
from personal_ai_orchestrator.quota_burn import BurnPressure, assess
from personal_ai_orchestrator.scheduler import (
    CandidateEvaluation,
    RoutingObjective,
    _objective_weights,
    _score_candidate,
)


@dataclass(frozen=True)
class CandidateWindowInput:
    """One observed quota window for a dispatch candidate.

    Stripped of the ``duration_seconds`` / ``confidence`` /
    ``measurement_source`` metadata on :class:`QuotaWindowSnapshot` —
    only the three fields the burn truth table needs. Lives here rather
    than in :mod:`quota_burn` so the burn module never has to learn the
    dispatch shape.
    """

    kind: QuotaWindowKind
    window_started_at: datetime | None
    reset_at: datetime
    used_fraction: float | None


@dataclass(frozen=True)
class DispatchCandidateInput:
    """Inputs for one execution target, gathered by the control plane."""

    execution_target_id: str
    model_sku_id: str
    runtime_available: bool
    verified: bool
    # True when ``verified`` is the demote-fallback result (latest evidence
    # is non-VERIFIED while an older VERIFIED row still exists). Lets the
    # view model surface the staleness alongside the score; never affects
    # the ranking itself — the recommender still admits the target, the UI
    # just gets the warning.
    verified_stale: bool = False
    # Observed quota windows for this target (e.g. 5h + weekly remaining
    # fractions). Empty list means "no observation was reported".
    remaining_fractions: tuple[float, ...] = ()
    # M1 WP1: per-window observation the recommender / card can pass to
    # ``quota_burn.assess``. Empty tuple is the no-observation default.
    windows: tuple[CandidateWindowInput, ...] = ()
    # Evidence freshness: when the target last passed a real worker
    # invocation. ``None`` means never or stale beyond the cap.
    evidence_observed_at: datetime | None = None
    # Availability state from the quota admission journal. ``UNKNOWN``
    # when no journal entry exists.
    availability_state: QuotaAvailabilityState = QuotaAvailabilityState.UNKNOWN


def source_pressure_for(
    windows: Sequence[CandidateWindowInput], *, now: datetime
) -> BurnPressure:
    """Pick the WEEKLY window and return its ``assess`` pressure.

    No WEEKLY window → ``UNMETERED``. A WEEKLY window without the data
    ``assess`` needs (no ``reset_at`` for the kind, missing
    ``used_fraction``, etc.) also surfaces as ``UNMETERED`` because
    :class:`QuotaWindowSnapshot.burn`'s short-circuits do. The function
    never raises on partial observation — the same fail-closed contract
    ``assess`` honours at the lowest layer.
    """

    weekly = next((w for w in windows if w.kind is QuotaWindowKind.WEEKLY), None)
    if weekly is None:
        return BurnPressure.UNMETERED
    started_at = weekly.window_started_at
    if started_at is None:
        kind_duration = weekly.kind.duration_seconds()
        if kind_duration is None:
            return BurnPressure.UNMETERED
        # Mirror ``infer_window_started_at``'s logic without depending on
        # the helper — keeps this function standalone-testable.
        from datetime import timedelta as _timedelta

        started_at = weekly.reset_at - _timedelta(seconds=kind_duration)
    assessment = assess(
        window_started_at=started_at,
        reset_at=weekly.reset_at,
        used_fraction=weekly.used_fraction,
        now=now,
    )
    return assessment.pressure


@dataclass(frozen=True)
class DispatchRecommendation:
    """Ranked list of :class:`CandidateEvaluation`, top pick first."""

    policy: RoutingObjective
    evaluations: tuple[CandidateEvaluation, ...]

    @property
    def top_pick(self) -> CandidateEvaluation | None:
        for evaluation in self.evaluations:
            if evaluation.admitted:
                return evaluation
        return None


# A target seen at most this many days ago is treated as 'fresh evidence'.
_EVIDENCE_FRESH_DAYS = 7


def _hard_eligibility(
    candidate: DispatchCandidateInput,
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Pre-score gates: verified, runtime-available, not exhausted."""

    if not candidate.verified:
        return False, "execution target has not been runtime-verified"
    if not candidate.runtime_available:
        return False, "worker runtime is unavailable on this host"
    if candidate.availability_state is QuotaAvailabilityState.EXHAUSTED_OBSERVED:
        return False, "quota observed as exhausted for the current window"
    if candidate.availability_state is QuotaAvailabilityState.COOLDOWN:
        return False, "quota is in cooldown after exhaustion"
    if not candidate.remaining_fractions:
        return False, "no quota observation reported for this target"
    if min(candidate.remaining_fractions) <= 0.0:
        return False, "every quota window reports zero remaining"
    return True, ""


def _headroom(candidate: DispatchCandidateInput) -> float:
    """Average remaining fraction across all observed windows.

    The min is too punishing — one exhausted sub-window of a multi-window
    plan would zero out an otherwise-healthy candidate — and the max is
    too generous — one free window says nothing about the others. The
    mean is the conservative middle.
    """

    if not candidate.remaining_fractions:
        return 0.0
    return sum(candidate.remaining_fractions) / len(candidate.remaining_fractions)


def _freshness_bonus(candidate: DispatchCandidateInput, *, now: datetime) -> float:
    """Targets seen recently are more trustworthy than silent ones."""

    observed_at = candidate.evidence_observed_at
    if observed_at is None:
        return -5.0
    age_days = (now - observed_at).total_seconds() / 86400.0
    if age_days < 0 or age_days > _EVIDENCE_FRESH_DAYS:
        return -5.0
    return max(0.0, _EVIDENCE_FRESH_DAYS - age_days) * 1.0


def _score(
    candidate: DispatchCandidateInput,
    *,
    policy: RoutingObjective,
    now: datetime,
) -> tuple[float, tuple[tuple[str, float], ...]]:
    quality_weight, quota_weight, latency_weight, cost_weight = _objective_weights(policy)

    headroom = _headroom(candidate)
    freshness = _freshness_bonus(candidate, now=now)

    # Capability fit is unknown without a TaskProfile. Declare 1.0 openly
    # rather than inferring intent — the policy redistributes its weight
    # onto the signals it does know.
    capability_fit = 1.0

    components: list[tuple[str, float]] = [
        ("quality_capability_fit", quality_weight * capability_fit * 25.0),
        ("quota_headroom_mean", quota_weight * headroom * 40.0),
        ("evidence_freshness", quality_weight * freshness),
    ]
    score = sum(value for _, value in components)
    # Penalise UNKNOWN availability state, even when remaining fractions
    # were reported: a fresh collector result and stale journal disagree.
    if candidate.availability_state is QuotaAvailabilityState.UNKNOWN:
        score -= 8.0

    return round(score, 8), tuple(components)


def recommend_owner_dispatch(
    candidates: Iterable[DispatchCandidateInput],
    *,
    policy: RoutingObjective,
    now: datetime,
) -> DispatchRecommendation:
    """Rank :class:`DispatchCandidateInput` by ``policy`` and admit gates."""

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        eligible, ineligible_reason = _hard_eligibility(candidate, now=now)
        if not eligible:
            evaluations.append(
                CandidateEvaluation(
                    execution_target_id=candidate.execution_target_id,
                    model_sku_id=candidate.model_sku_id,
                    eligible=False,
                    admitted=False,
                    score=None,
                    reasons=(ineligible_reason,),
                )
            )
            continue
        score, components = _score(candidate, policy=policy, now=now)
        evaluations.append(
            CandidateEvaluation(
                execution_target_id=candidate.execution_target_id,
                model_sku_id=candidate.model_sku_id,
                eligible=True,
                admitted=True,
                score=score,
                score_components=tuple(
                    _component(name=name, contribution=value)
                    for name, value in components
                ),
                reasons=(
                    (
                        f"policy={policy.value}, "
                        f"headroom={_headroom(candidate):.2f}, "
                        f"verified={candidate.verified}, "
                        f"runtime={candidate.runtime_available}"
                    ),
                ),
            )
        )
    evaluations.sort(
        key=lambda item: (
            -float(item.score) if item.score is not None else math.inf,
            item.execution_target_id,
        )
    )
    return DispatchRecommendation(policy=policy, evaluations=tuple(evaluations))


def _component(name: str, contribution: float) -> "ScoreComponent":
    # Local re-export shim so this module stays decoupled from
    # scheduler's exported ScoreComponent. Keeps the recommendation
    # value object identical to the rest of the scheduler's output.
    from personal_ai_orchestrator.scheduler import ScoreComponent

    return ScoreComponent(name=name, value=round(contribution, 8))


__all__ = [
    "CandidateWindowInput",
    "DispatchCandidateInput",
    "DispatchRecommendation",
    "recommend_owner_dispatch",
    "source_pressure_for",
]
