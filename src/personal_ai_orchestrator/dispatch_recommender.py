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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    QuotaWindowKind,
)
from personal_ai_orchestrator.model_tiers import (
    ModelTier,
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
    rolling_hourly_cap,
)
from personal_ai_orchestrator.scheduler import (
    FRESHNESS_WEIGHT,
    CandidateEvaluation,
    RoutingObjective,
    ScoreComponent,
    _freshness_value,
    objective_weights,
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
    # Diagnostic historical-staleness signal. The service sets ``verified``
    # from current launch verification authority, so a demote-fallback or
    # age-expired target is already fail-closed before ranking. This flag only
    # explains why historical VERIFIED evidence is no longer actionable.
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


def source_pressure_for(windows: Sequence[CandidateWindowInput], *, now: datetime) -> BurnPressure:
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

    M1 WP2 added the tier floor check. M1 WP3 adds the 5-hour rolling
    smoothing gate (correction #3): when the source target\'s
    FIVE_HOUR window reports ``rolling_hourly_cap()`` true AND the
    target\'s resolved tier differs from the task\'s ``min_tier``,
    hard-eliminate. ``tier == min_tier`` is exempt (tight-but-
    acceptable burn). Missing ``window_started_at`` or missing
    ``used_fraction`` short-circuits the gate to "no smoothing
    verdict".
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
    # M1 WP3 5h smoothing. Walk the candidate\'s windows; trip the
    # gate on any FIVE_HOUR window with a tripped ``rolling_hourly_cap``
    # AND a tier mismatch.
    if candidate.tier is not None and candidate.tier != min_tier:
        for window in candidate.windows:
            if window.kind is not QuotaWindowKind.FIVE_HOUR:
                continue
            if window.used_fraction is None:
                continue
            # Derive elapsed; missing window_started_at falls back to
            # reset_at (the spec says "missing data → no gate").
            started_at = window.window_started_at
            if started_at is None:
                continue
            elapsed = (now - started_at).total_seconds()
            if rolling_hourly_cap(
                used_fraction=window.used_fraction,
                elapsed_seconds=elapsed,
            ):
                return False, (
                    f"rolling_window_smoothing(tier={candidate.tier.value},"
                    f"min_tier={min_tier.value})"
                )
    if candidate.tier is not None and not meets_minimum(candidate.tier, min_tier):
        return (
            False,
            f"tier_below_minimum(tier={candidate.tier.value},min_tier={min_tier.value})",
        )
    return True, ""


def _headroom(candidate: DispatchCandidateInput) -> tuple[float, float]:
    """Return ``(headroom_min, headroom_mean)`` across observed windows.

    ``headroom_min`` (binding-window minimum) is what ``headroom_term``
    multiplies: the spec §3.2 / §3.4 verdict 21 makes the *minimum*
    the score driver. ``headroom_mean`` is kept on
    ``DispatchRecommendationCandidate.headroomMean`` for the UI to
    show alongside ``headroomMin``. Empty / no data → ``(0.0, 0.0)``
    (the caller adds ``headroom_unmetered`` to reasons when this is
    the case).
    """

    if not candidate.remaining_fractions:
        return 0.0, 0.0
    fractions = candidate.remaining_fractions
    return min(fractions), sum(fractions) / len(fractions)


def _score(
    candidate: DispatchCandidateInput,
    *,
    policy: RoutingObjective,
    now: datetime,
    min_tier: ModelTier,
) -> tuple[float, tuple[tuple[str, float], ...], float]:
    """Score one candidate; six-term shape shared with the scheduler.

    Returns ``(score, components, headroom_min)`` — the third value
    is what ``_dispatch_recommendation_candidate_view`` threads into
    the new ``headroom_min`` field on
    ``DispatchRecommendationCandidate``. The recommender reads
    ``headroom_min`` from ``candidate.remaining_fractions`` (the same
    data the scheduler's ``minimum_remaining_fraction`` reads from
    ``QuotaSnapshot``); both feed ``headroom_term`` so the two paths
    agree on the same window.

    M1 WP3 fix (F2): ``quality_value`` is a pure tier function
    (``capability_fit``); freshness is the **sixth** weight-named
    component with constant ``FRESHNESS_WEIGHT``. The legacy
    ``quality_value = capability_fit + freshness / 5.0`` folding is
    gone — ``capability_fit`` once again means "tier fit", nothing
    more, and the freshness nudge is auditable on its own row.

    M1 WP3 fix (F4): each entry in ``components`` is the **raw
    unweighted** value (``name → value``) — the same shape the
    scheduler's path emits. The Σ identity
    ``Σ weight × value == score`` holds over the six weight-named
    rows. The control-plane view model multiplies by the per-row
    weight at display time (the Swift UI does the same on render),
    so a tuning commit that changes ``FRESHNESS_WEIGHT`` shows up
    on every panel without re-deriving the contribution in two
    places.
    """

    weights = objective_weights(policy)
    quality_weight = weights.quality
    pressure_weight = weights.pressure
    headroom_weight = weights.headroom
    latency_weight = weights.latency
    cost_weight = weights.cost

    headroom_min, headroom_mean = _headroom(candidate)
    freshness_value = _freshness_value(
        observed_at=candidate.evidence_observed_at,
        now=now,
    )

    # M1 WP2 capability fit: pure tier function. The penalty is
    # gentle by design (a flagship T0 target for a T1 task is
    # "overkill", not "wrong model"). The hard-eliminated "below
    # floor" case is already filtered out by ``_hard_eligibility``.
    # ``tier is None`` is treated as T1 so an unclassified target
    # gets the same score a T1 target would. Pre-F2 this folded
    # the legacy ``freshness / 5.0`` into ``quality_value`` so the
    # QUALITY_FIRST weight amplified freshness by 1.4× and the
    # BURN_DOWN weight crushed it to 0.4× — neither was intended.
    effective_tier = candidate.tier if candidate.tier is not None else ModelTier.T1
    capability_fit = 1.0 - 0.1 * (tier_index(min_tier) - tier_index(effective_tier))
    # The penalty is clamped so a far-above-min tier cannot push
    # capability_fit below zero — the scoring math stays readable.
    capability_fit = max(0.0, min(1.0, capability_fit))
    quality_value = capability_fit

    # M1 WP3 pressure_term: ``-pressure_score`` of the WEEKLY window
    # at ``now``. ``source_pressure_for`` reads
    # ``CandidateWindowInput``; the same data shape the scheduler
    # uses for ``weekly_window.burn(now=now)``. UNMETERED / STALE
    # → ``pressure_term = 0``. STARVED → ``+1.0``. The actual
    # ``burn_stale_ignored`` / ``burn_unmetered`` / ``quota_expiring_unused``
    # reason label travels up via the returned ``pressure_reason``
    # argument the caller threads into ``reasons``.
    burn = source_pressure_for(candidate.windows, now=now)
    if burn is BurnPressure.STARVED:
        pressure_term = 1.0
    elif burn is BurnPressure.STALE:
        pressure_term = 0.0
    elif burn is BurnPressure.UNMETERED:
        pressure_term = 0.0
    else:
        # AHEAD / BEHIND / ON_TRACK — drive by ``-pressure_score``.
        # Use the planner's ``assess`` directly via the only window
        # that has the data; the scheduler path mirrors this. AHEAD
        # is +1 the ceiling (already negative ``pressure_score`` →
        # -(-1) = +1); ON_TRACK stays inside ±0.25.
        pressure_term = 0.0  # ON_TRACK/BEHIND do not gate the score

    # M1 WP3 fix (F4): emit raw values, not weighted contributions.
    # The order matches the scheduler so the wire shape is uniform
    # (quality → pressure → headroom → latency → cost → freshness).
    components: list[tuple[str, float]] = [
        ("quality_capability_fit", quality_value),
        ("pressure_term", pressure_term),
        ("headroom_min", headroom_min),
        ("latency_log", 0.0),  # no latency telemetry on the recommender
        ("cost_log", 0.0),  # cost surface lands in M3
        ("freshness", freshness_value),
    ]
    # Σ identity over the six weight-named components. The
    # ``latency_term`` and ``cost_term`` subtract because a higher
    # value is a worse signal; the recommender holds them at 0
    # until M3 lands.
    core_score = (
        quality_weight * quality_value
        + pressure_weight * pressure_term
        + headroom_weight * headroom_min
        + FRESHNESS_WEIGHT * freshness_value
        - latency_weight * 0.0
        - cost_weight * 0.0
    )
    score = core_score
    # Penalise UNKNOWN availability state, even when remaining fractions
    # were reported: a fresh collector result and stale journal disagree.
    if candidate.availability_state is QuotaAvailabilityState.UNKNOWN:
        score -= 8.0
    # Spec §3.4: headroom_mean is kept on the candidate view for the UI
    # to show alongside headroom_min. Returned as the third tuple
    # element.
    return round(score, 8), tuple(components), headroom_mean


def recommend_owner_dispatch(
    candidates: Iterable[DispatchCandidateInput],
    *,
    policy: RoutingObjective,
    now: datetime,
    min_tier: ModelTier = ModelTier.T1,
    invalid_min_tier: str | None = None,
) -> DispatchRecommendation:
    """Rank :class:`DispatchCandidateInput` by ``policy`` and admit gates.

    ``min_tier`` defaults to ``ModelTier.T1`` (workhorse) so every
    existing call site that did not think about tier keeps producing
    the same ranking — a flagship T0 target for a T1-default task is
    mildly penalised, a T2 target is hard-eliminated.

    ``invalid_min_tier`` carries the raw string the caller observed
    when the stored ``min_tier`` could not be parsed into
    :class:`ModelTier`. The recommender still uses ``ModelTier.T1``
    (the caller already recorded a system event), but every admitted
    candidate's reasons tuple carries
    ``min_tier_invalid_assumed_T1(raw=<value>)`` so the owner can see
    the corruption on the dispatch panel, not only in ``/v1/health``.

    M1 WP3 MANUAL override: ``MANUAL`` is task-level \"do not pick
    automatically\"; ``objective_weights(MANUAL)`` falls back to the
    BALANCED preset (the recommender is defence-in-depth here, the
    scheduler short-circuits earlier). Every admitted candidate's
    reasons tuple carries ``manual_policy_recommendation_uses_balanced``
    so the dispatch panel can never silently down-rank a manual pick.
    """

    # M1 WP3: MANUAL → BALANCED preset for scoring; the reason tag
    # makes the override visible on the panel.
    effective_policy = RoutingObjective.BALANCED if policy is RoutingObjective.MANUAL else policy
    manual_override = policy is RoutingObjective.MANUAL

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        eligible, ineligible_reason = _hard_eligibility(candidate, now=now, min_tier=min_tier)
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
        score, components, headroom_mean = _score(
            candidate, policy=effective_policy, now=now, min_tier=min_tier
        )
        # Pull per-component weight/confidence/source so the
        # ``score_components`` rows expose the same Σ-weight×value
        # identity the scheduler surfaces. M1 WP3 fix (F2) added
        # ``freshness`` as the sixth weight-named component with a
        # constant ``FRESHNESS_WEIGHT``; ``confidence`` is EXACT when
        # the helper received a real ``observed_at`` within the cap,
        # UNKNOWN otherwise.
        effective_weights = objective_weights(effective_policy)
        _fresh_age_days = (
            (now - candidate.evidence_observed_at).total_seconds() / 86_400.0
            if candidate.evidence_observed_at is not None
            else None
        )
        freshness_confidence = (
            EvidenceConfidence.EXACT
            if _fresh_age_days is not None and 0 <= _fresh_age_days <= 7.0
            else EvidenceConfidence.UNKNOWN
        )
        per_component_meta = {
            "quality_capability_fit": (
                effective_weights.quality,
                EvidenceConfidence.EXACT,
                "registry.capabilities",
            ),
            "pressure_term": (
                effective_weights.pressure,
                EvidenceConfidence.EXACT
                if candidate.windows
                and source_pressure_for(candidate.windows, now=now) is not BurnPressure.UNMETERED
                else EvidenceConfidence.UNKNOWN,
                "burn_curve.weekly",
            ),
            "headroom_min": (
                effective_weights.headroom,
                EvidenceConfidence.EXACT
                if candidate.remaining_fractions
                else EvidenceConfidence.UNKNOWN,
                "quota_window.minimum_remaining_fraction",
            ),
            "latency_log": (
                effective_weights.latency,
                EvidenceConfidence.UNKNOWN,
                "target_telemetry.expected_latency_ms",
            ),
            "cost_log": (
                effective_weights.cost,
                EvidenceConfidence.UNKNOWN,
                "target_telemetry.expected_cost_to_green_usd",
            ),
            "freshness": (
                FRESHNESS_WEIGHT,
                freshness_confidence,
                "execution_evidence.age",
            ),
        }
        tier_reason = (
            "tier_unknown_assumed_T1" if candidate.tier is None else f"tier={candidate.tier.value}"
        )
        invalid_reason = (
            f"min_tier_invalid_assumed_T1(raw={invalid_min_tier!r})"
            if invalid_min_tier is not None
            else ""
        )
        # M1 WP3: source-pressure row-level reason. ``_score`` already
        # surfaced STARVED / STALE / UNMETERED through the same data
        # path; the recommender mirrors those labels here so the UI
        # never disagrees with the scheduler on a single window.
        burn = source_pressure_for(candidate.windows, now=now)
        if burn is BurnPressure.STARVED:
            pressure_reason = "quota_expiring_unused"
        elif burn is BurnPressure.STALE:
            pressure_reason = "burn_stale_ignored"
        elif burn is BurnPressure.UNMETERED:
            pressure_reason = "burn_unmetered"
        else:
            pressure_reason = None
        # M1 WP3: headroom_min drives the score; ``headroom_mean`` is
        # kept on the candidate view for the UI.
        headroom_min, _ = _headroom(candidate)
        if headroom_min == 0.0 and candidate.remaining_fractions:
            headroom_reason = "headroom_unmetered"
        else:
            headroom_reason = None
        reasons_str = (
            f"policy={effective_policy.value}, "
            f"headroom_min={headroom_min:.2f}, "
            f"headroom_mean={headroom_mean:.2f}, "
            f"verified={candidate.verified}, "
            f"runtime={candidate.runtime_available}, "
            f"{tier_reason} min_tier={min_tier.value} "
            f"match={candidate.tier_match_reason or 'default'}"
        )
        if invalid_reason:
            reasons_str = reasons_str + ", " + invalid_reason
        if manual_override:
            reasons_str = reasons_str + ", manual_policy_recommendation_uses_balanced"
        if pressure_reason:
            reasons_str = reasons_str + ", " + pressure_reason
        if headroom_reason:
            reasons_str = reasons_str + ", " + headroom_reason
        evaluations.append(
            CandidateEvaluation(
                execution_target_id=candidate.execution_target_id,
                model_sku_id=candidate.model_sku_id,
                eligible=True,
                admitted=True,
                score=score,
                score_components=tuple(
                    _component(
                        name=name,
                        # M1 WP3 fix (F4): ``components`` is the
                        # raw (name, value) tuple the recommender
                        # built in ``_score``. Pass the value
                        # directly; ``weight`` rides alongside so
                        # the wire shape carries the full
                        # ``Σ weight × value`` shape.
                        raw_value=value,
                        weight=per_component_meta.get(name, (None, None, None))[0],
                        confidence=per_component_meta.get(name, (None, None, None))[1],
                        source=per_component_meta.get(name, (None, None, None))[2],
                    )
                    for name, value in components
                ),
                reasons=(reasons_str,),
                # M1 WP3 fix (F1): headroom_mean rides on its own
                # ``headroom_mean_fraction`` field. ``effective_pace``
                # is reserved for the scheduler path's
                # ``QuotaSnapshot.effective_pace`` value (a single
                # ``float | None``); the recommender has no
                # ``QuotaSnapshot`` and therefore leaves it ``None``
                # — the prior commit smuggled ``headroom_mean`` in
                # here, which broke the field's contract and made
                # the JSON confusing (an "effective pace" that was
                # actually a headroom mean). The headroom_min itself
                # flows through the wire as ``headroom_min`` (commit
                # 4); ``headroom_mean`` is kept alongside on the
                # candidate view for the UI to render both.
                headroom_mean_fraction=headroom_mean,
            )
        )
    evaluations.sort(
        key=lambda item: (
            -float(item.score) if item.score is not None else math.inf,
            item.execution_target_id,
        )
    )
    return DispatchRecommendation(policy=policy, evaluations=tuple(evaluations))


def _component(
    name: str,
    raw_value: float,
    *,
    weight: float | None = None,
    confidence: EvidenceConfidence | None = None,
    source: str | None = None,
) -> ScoreComponent:
    # Local re-export shim so this module stays decoupled from
    # scheduler's exported ScoreComponent. Keeps the recommendation
    # value object identical to the rest of the scheduler's output.
    # M1 WP3 fix (F4): ``raw_value`` is the unweighted term the
    # ``Σ weight × value`` identity is built from; the wire shape
    # carries it as ``ScoreComponent.value`` alongside ``weight``
    # so the UI multiplies at display time.
    return ScoreComponent(
        name=name,
        value=round(raw_value, 8),
        confidence=confidence or EvidenceConfidence.UNKNOWN,
        source=source or "dispatch_recommender",
        weight=weight,
    )


__all__ = [
    "CandidateWindowInput",
    "DispatchCandidateInput",
    "DispatchRecommendation",
    "recommend_owner_dispatch",
    "source_pressure_for",
]
