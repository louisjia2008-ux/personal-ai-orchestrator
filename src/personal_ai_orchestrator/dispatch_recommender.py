"""Owner-dispatch target recommendation.

Pure, deterministic ranking of execution targets by a scheduling policy,
with hard eligibility gates applied before scoring.

Distinct from :mod:`scheduler.route_task`: this recommender does NOT
require a :class:`TaskProfile`. Owner-dispatch has no natural-language-
to-capability inference path yet, so capability fit was uniformly 1.0
through P4. M1 WP2 makes ``capability_fit`` tier-aware: a flagship T0
target for a T1 task is mildly penalised ("overkill"); a T2 target for
the same T1 task is hard-eliminated ("below the task's floor"). The
penalty is gentle by design — WP3's pressure term can absorb it — and
the elimination is hard because a tier below the floor is the kind of
"this is the wrong model" decision no policy override should undo.

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
from personal_ai_orchestrator.model_tiers import (
    DEFAULT_TIER_ENTRY,
    ModelTier,
    TierTable,
    meets_minimum,
    tier_index,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityState,
)
from personal_ai_orchestrator.quota_burn import (
    BurnPressure,
    assess,
    infer_window_started_at,
)
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
    # M1 WP2: capability tier for this target, resolved from the host
    # tier table. ``None`` means the recommender could not classify the
    # target — capability_fit then assumes T1 (the default entry) and
    # the reasons tuple records ``tier_unknown_assumed_T1`` so the UI
    # can flag it. ``tier_match_reason`` is one of ``\"exact\"``,
    # ``\"glob\"``, ``\"default\"`` so the dashboard can label pattern
    # matches as such.
    tier: ModelTier | None = None
    tier_match_reason: str | None = None


def source_pressure_for(
    windows: Sequence[CandidateWindowInput], *, now: datetime
) -> BurnPressure:
    """Pick the WEEKLY window and return its ``assess`` pressure.

    No WEEKLY window → ``UNMETERED``. A WEEKLY window without the data
    ``assess`` needs (no canonical duration for the kind, missing
    ``used_fraction``, etc.) also surfaces as ``UNMETERED`` because
    ``QuotaWindowSnapshot.burn``'s short-circuits do. The function never
    raises on partial observation — the same fail-closed contract
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
        started_at = infer_window_started_at(
            reset_at=weekly.reset_at,
            duration_seconds=kind_duration,
        )
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
    min_tier: ModelTier,
) -> tuple[bool, str]:
    """Pre-score gates: verified, runtime-available, not exhausted, tier-met.

    M1 WP2 added the tier floor check. A target whose resolved tier
    is strictly below ``min_tier`` is hard-eliminated — the reason
    string carries both tiers so the UI can render it verbatim
    without a second lookup. A target with ``tier is None`` (the
    table could not classify it) is NOT eliminated; the score path
    treats ``None`` as the default ``ModelTier.T1`` and the reasons
    tuple records ``tier_unknown_assumed_T1``.
    """

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
    if candidate.tier is not None and not meets_minimum(candidate.tier, min_tier):
        return (
            False,
            f"tier_below_minimum(tier={candidate.tier.value},min_tier={min_tier.value})",
        )
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
    min_tier: ModelTier,
) -> tuple[float, tuple[tuple[str, float], ...]]:
    quality_weight, quota_weight, latency_weight, cost_weight = _objective_weights(policy)

    headroom = _headroom(candidate)
    freshness = _freshness_bonus(candidate, now=now)

    # M1 WP2 capability fit: gentle penalty for \"overkill\" (target tier
    # higher than min_tier), zero penalty for \"exact match\", and the
    # hard-eliminated \"below floor\" case is already filtered out by
    # ``_hard_eligibility``. ``tier is None`` is treated as T1 so an
    # unclassified target gets the same score a T1 target would.
    effective_tier = candidate.tier if candidate.tier is not None else ModelTier.T1
    capability_fit = 1.0 - 0.1 * (
        tier_index(min_tier) - tier_index(effective_tier)
    )
    # The penalty is clamped so a far-above-min tier cannot push
    # capability_fit below zero — the scoring math stays readable.
    capability_fit = max(0.0, min(1.0, capability_fit))

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
    min_tier: ModelTier = ModelTier.T1,
) -> DispatchRecommendation:
    """Rank :class:`DispatchCandidateInput` by ``policy`` and admit gates.

    ``min_tier`` defaults to ``ModelTier.T1`` (workhorse) so every
    existing call site that did not think about tier keeps producing
    the same ranking — a flagship T0 target for a T1-default task is
    mildly penalised, a T2 target is hard-eliminated.
    """

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        eligible, ineligible_reason = _hard_eligibility(
            candidate, now=now, min_tier=min_tier
        )
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
        score, components = _score(
            candidate, policy=policy, now=now, min_tier=min_tier
        )
        tier_reason = (
            "tier_unknown_assumed_T1"
            if candidate.tier is None
            else f"tier={candidate.tier.value}"
        )
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
                        f"runtime={candidate.runtime_available}, "
                        f"{tier_reason} min_tier={min_tier.value} "
                        f"match={candidate.tier_match_reason or 'default'}"
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
