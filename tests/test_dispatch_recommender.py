"""Owner-dispatch recommender: the gate that finally lets a scheduling
policy pick a target at owner-dispatch time.

The recommender is deliberately simpler than ``scheduler.route_task``:
no TaskProfile is available, so capability fit is uniformly 1.0 and the
honest answer is to redistribute the policy weight onto the signals
that ARE known (quota headroom, evidence freshness, runtime availability)
and report exactly that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.dispatch_recommender import (
    CandidateWindowInput,
    DispatchCandidateInput,
    _score,
    recommend_owner_dispatch,
    source_pressure_for,
)
from personal_ai_orchestrator.control_api import DispatchRecommendationCandidate
from personal_ai_orchestrator.model_registry import QuotaWindowKind
from personal_ai_orchestrator.model_tiers import ModelTier
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
from personal_ai_orchestrator.quota_burn import BurnPressure
from personal_ai_orchestrator.scheduler import RoutingObjective, objective_weights


# A single fixed instant the tests reason about. Plain constant rather
# than a pytest fixture: pytest-asyncio's strict mode treats any fixture
# with a matching name as reserved, and the value never varies here.
FIXED_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _candidate(
    target_id: str,
    *,
    remaining: tuple[float, ...] = (0.8, 0.9),
    verified: bool = True,
    runtime: bool = True,
    state: QuotaAvailabilityState = QuotaAvailabilityState.AVAILABLE_OBSERVED,
    age_days: int | None = 1,
    windows: tuple[CandidateWindowInput, ...] = (),
    tier: ModelTier | None = None,
    tier_match_reason: str | None = None,
) -> DispatchCandidateInput:
    evidence_at = FIXED_NOW - timedelta(days=age_days) if age_days is not None else None
    return DispatchCandidateInput(
        execution_target_id=target_id,
        model_sku_id=f"{target_id}/model",
        runtime_available=runtime,
        verified=verified,
        remaining_fractions=remaining,
        evidence_observed_at=evidence_at,
        availability_state=state,
        windows=windows,
        tier=tier,
        tier_match_reason=tier_match_reason,
    )


def test_quota_saver_prefers_target_with_more_headroom():
    a = _candidate("provider-A/m2.5", remaining=(0.4, 0.5))
    b = _candidate("provider-B/m3", remaining=(0.9, 0.95))
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.QUOTA_SAVER, now=FIXED_NOW
    )
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "provider-B/m3"
    # The admission reasoning names the policy and the binding-window
    # headroom; the owner can see exactly why B won. M1 WP3 renames
    # ``headroom=`` to ``headroom_min=`` (and adds ``headroom_mean=``).
    assert "policy=QUOTA_SAVER" in result.top_pick.reasons[0]
    assert "headroom_min=0.90" in result.top_pick.reasons[0]


def test_exhausted_target_is_excluded_with_explicit_reason():
    a = _candidate("provider-A/m2.5")
    b = _candidate(
        "provider-B/m3",
        remaining=(0.0, 0.9),
        state=QuotaAvailabilityState.EXHAUSTED_OBSERVED,
    )
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    eligible_ids = [c.execution_target_id for c in result.evaluations if c.admitted]
    assert eligible_ids == ["provider-A/m2.5"]
    blocked = next(
        c for c in result.evaluations if c.execution_target_id == "provider-B/m3"
    )
    assert blocked.admitted is False
    assert any("exhausted" in reason.lower() for reason in blocked.reasons)


def test_unverified_target_is_excluded():
    a = _candidate("provider-A/m2.5", verified=False)
    b = _candidate("provider-B/m3")
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    eligible_ids = [c.execution_target_id for c in result.evaluations if c.admitted]
    assert eligible_ids == ["provider-B/m3"]


def test_stale_evidence_does_not_demote_but_ranks_honesty():
    # A target with recent evidence should score above one with stale or
    # never-verified evidence, even when both are technically verified.
    fresh = _candidate("provider-A/m2.5", age_days=0)
    stale = _candidate("provider-B/m3", age_days=30)
    result = recommend_owner_dispatch(
        [fresh, stale], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "provider-A/m2.5"


def test_unknown_quota_state_with_observations_keeps_but_penalizes():
    # No journal entry at all: candidate still admissible if we have
    # window remaining fractions, but the policy score is lower than a
    # target whose journal agrees with the observation.
    fresh_known = _candidate(
        "provider-A/m2.5",
        remaining=(0.9, 0.9),
        state=QuotaAvailabilityState.AVAILABLE_OBSERVED,
    )
    observed_only = _candidate(
        "provider-B/m3",
        remaining=(0.9, 0.9),
        state=QuotaAvailabilityState.UNKNOWN,
    )
    result = recommend_owner_dispatch(
        [fresh_known, observed_only],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
    )
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "provider-A/m2.5"
    assert result.evaluations[0].execution_target_id == "provider-A/m2.5"


def test_no_candidates_admits_returns_no_top_pick():
    a = _candidate("provider-A/m2.5", verified=False)
    b = _candidate("provider-B/m3", runtime=False)
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    assert result.top_pick is None
    assert all(not c.admitted for c in result.evaluations)


def test_score_components_record_what_was_counted():
    a = _candidate("provider-A/m2.5", remaining=(0.9, 0.9), age_days=0)
    result = recommend_owner_dispatch(
        [a], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    components = {c.name for c in result.top_pick.score_components}
    # M1 WP3: 5-term shape (commit 2). The previous
    # ``quota_headroom_mean`` is gone — ``headroom_min`` is the binding
    # driver, ``quality_capability_fit`` folds in tier + freshness.
    assert "headroom_min" in components
    assert "quality_capability_fit" in components
    assert "pressure_term" in components
    # Capability fit is declared openly, not omitted: the
    # score_components array lists every input that shaped the total.
    assert any(
        c.name == "quality_capability_fit" for c in result.top_pick.score_components
    )


# ---------------------------------------------------------------------------
# M1 WP1 — CandidateWindowInput + source_pressure_for pipeline
# ---------------------------------------------------------------------------


def _weekly_window(
    *, used_fraction: float | None, reset_in_hours: float = 24.0
) -> CandidateWindowInput:
    return CandidateWindowInput(
        kind=QuotaWindowKind.WEEKLY,
        window_started_at=FIXED_NOW,
        reset_at=FIXED_NOW + timedelta(hours=reset_in_hours),
        used_fraction=used_fraction,
    )


def test_dispatch_candidate_input_windows_defaults_to_empty_tuple() -> None:
    """WP1 must not break existing 36 tests; ``windows`` defaults to ``()``."""

    candidate = _candidate("provider-A/m2.5")
    assert candidate.windows == ()


def test_dispatch_candidate_input_windows_round_trips_through_recommender() -> None:
    """The ``windows`` field reaches the recommender as an attribute on the input."""

    candidate = DispatchCandidateInput(
        execution_target_id="provider-A/m2.5",
        model_sku_id="provider-A/m2.5",
        runtime_available=True,
        verified=True,
        remaining_fractions=(0.8, 0.9),
        windows=(_weekly_window(used_fraction=0.5),),
        evidence_observed_at=FIXED_NOW - timedelta(days=1),
        availability_state=QuotaAvailabilityState.AVAILABLE_OBSERVED,
    )
    # Field is preserved verbatim on the dataclass; the recommender does
    # not mutate it (WP1 is pipeline-only, scoring is WP3's job).
    assert candidate.windows[0].kind is QuotaWindowKind.WEEKLY
    assert candidate.windows[0].used_fraction == 0.5


def test_source_pressure_for_returns_starved_for_weekly_close_to_reset() -> None:
    """80% remaining, 1 h to reset → STARVED (per the truth table)."""

    windows = (_weekly_window(used_fraction=0.20, reset_in_hours=1.0),)
    assert source_pressure_for(windows, now=FIXED_NOW) is BurnPressure.STARVED


def test_source_pressure_for_returns_unmetered_when_no_weekly_window() -> None:
    """No WEEKLY window → UNMETERED, not a guess from a 5h window."""

    windows = (
        CandidateWindowInput(
            kind=QuotaWindowKind.FIVE_HOUR,
            window_started_at=FIXED_NOW,
            reset_at=FIXED_NOW + timedelta(hours=5),
            used_fraction=0.5,
        ),
    )
    assert source_pressure_for(windows, now=FIXED_NOW) is BurnPressure.UNMETERED


def test_source_pressure_for_returns_unmetered_when_weekly_used_fraction_is_none() -> None:
    """``used_fraction=None`` short-circuits to UNMETERED via ``assess``."""

    windows = (_weekly_window(used_fraction=None),)
    assert source_pressure_for(windows, now=FIXED_NOW) is BurnPressure.UNMETERED


def test_source_pressure_for_infers_window_started_at_when_omitted() -> None:
    """No ``window_started_at`` → infer from kind, fall through to ``assess``."""

    inferred_window = CandidateWindowInput(
        kind=QuotaWindowKind.WEEKLY,
        window_started_at=None,
        reset_at=FIXED_NOW + timedelta(hours=24),
        used_fraction=0.5,
    )
    # Inferred start = reset_at - 7d = ~6 d before now, so 50% used at the
    # 6-day mark of a 7-day window is BEHIND (used less than ideal).
    assert source_pressure_for((inferred_window,), now=FIXED_NOW) is BurnPressure.BEHIND


# ---------------------------------------------------------------------------
# M1 WP2 — tier-aware capability_fit + hard tier floor
# ---------------------------------------------------------------------------


def _tier_candidate(
    target_id: str,
    *,
    tier: ModelTier | None = None,
    tier_match_reason: str | None = None,
    age_days: int | None = 1,
) -> DispatchCandidateInput:
    evidence_at = FIXED_NOW - timedelta(days=age_days) if age_days is not None else None
    return DispatchCandidateInput(
        execution_target_id=target_id,
        model_sku_id=f"{target_id}/model",
        runtime_available=True,
        verified=True,
        remaining_fractions=(0.8, 0.9),
        evidence_observed_at=evidence_at,
        availability_state=QuotaAvailabilityState.AVAILABLE_OBSERVED,
        tier=tier,
        tier_match_reason=tier_match_reason,
    )


def test_default_min_tier_is_T1_and_keeps_existing_rankings_unchanged() -> None:
    """Adding ``min_tier`` with a T1 default must not flip any existing test.

    Same shape as ``test_quota_saver_prefers_target_with_more_headroom`` —
    a T0 / T1 / T2 mix under default ``min_tier=T1`` produces the same
    ranking the old code produced, modulo the 0.1 capability-fit
    penalty on the T0 row and the hard floor eliminating the T2 row.
    """

    t0 = _tier_candidate("flagship/m9", tier=ModelTier.T0, tier_match_reason="exact")
    t1 = _tier_candidate("workhorse/m7", tier=ModelTier.T1, tier_match_reason="exact")
    t2 = _tier_candidate("fast/m5", tier=ModelTier.T2, tier_match_reason="exact")
    result = recommend_owner_dispatch(
        [t0, t1, t2], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    # T0 and T1 are at or above the default floor; T2 is below it.
    admitted_ids = {e.execution_target_id for e in result.evaluations if e.admitted}
    assert admitted_ids == {"flagship/m9", "workhorse/m7"}
    blocked = next(e for e in result.evaluations if e.execution_target_id == "fast/m5")
    assert blocked.admitted is False
    assert any(
        reason == "tier_below_minimum(tier=T2,min_tier=T1)" for reason in blocked.reasons
    )


def test_target_below_min_tier_is_hard_eliminated_with_explicit_reason() -> None:
    """A T2 target for a T0 task must NOT be admitted; the reason names both tiers."""

    t0 = _tier_candidate("flagship/m9", tier=ModelTier.T0, tier_match_reason="exact")
    t2 = _tier_candidate("fast/m5", tier=ModelTier.T2, tier_match_reason="exact")
    result = recommend_owner_dispatch(
        [t0, t2], policy=RoutingObjective.BALANCED, now=FIXED_NOW, min_tier=ModelTier.T0
    )
    t2_eval = next(
        e for e in result.evaluations if e.execution_target_id == "fast/m5"
    )
    assert t2_eval.admitted is False
    assert any(
        reason == "tier_below_minimum(tier=T2,min_tier=T0)" for reason in t2_eval.reasons
    )


def test_target_above_min_tier_pays_a_gentle_capability_fit_penalty() -> None:
    """A T0 target for a T1 task is mildly penalised (capability_fit = 0.9).

    The penalty shows up in the ``quality_capability_fit`` score
    component on the admitted evaluation. M1 WP3 fix (F2) makes
    ``capability_fit`` a pure tier function — the legacy
    ``freshness / 5.0`` folding is gone, so the numeric assertion
    pins the post-F2 arithmetic directly (``quality_weight ×
    capability_fit``; freshness rides on its own row in the same
    Σ-identity).
    """

    # Pre-F2 used ``age_days=30`` to make freshness deterministic
    # (clamped at -5.0). Post-F2 freshness rides on its own row
    # but the helper still returns 0.0 for missing or stale
    # evidence (V2 normalisation — see
    # ``scheduler._freshness_value``); the assertion below
    # targets only the
    # ``quality_capability_fit`` row so the freshness value is
    # irrelevant.
    t0 = _tier_candidate(
        "flagship/m9", tier=ModelTier.T0, tier_match_reason="exact", age_days=30,
    )
    t1 = _tier_candidate(
        "workhorse/m7", tier=ModelTier.T1, tier_match_reason="exact", age_days=30,
    )
    result = recommend_owner_dispatch(
        [t0, t1], policy=RoutingObjective.BALANCED, now=FIXED_NOW, min_tier=ModelTier.T1
    )
    components = {
        c.name: c.value
        for c in next(
            e for e in result.evaluations if e.execution_target_id == "flagship/m9"
        ).score_components
    }
    # quality_weight=0.7 for BALANCED; capability_fit=0.9 (T0
    # above T1 floor: 1.0 - 0.1 * 1); no freshness folding. The
    # raw value is 0.9 and ``weight`` carries the multiplier.
    # Pre-F2 the row read ``-0.07`` because freshness (-5.0) was
    # folded into the value at ``/5.0``; the F2 fix separates
    # them so the row reads ``0.9`` and the freshness row reads
    # ``-5.0`` on its own.
    assert abs(components["quality_capability_fit"] - 0.9) < 1e-9


def test_tier_unknown_is_treated_as_T1_and_recorded_in_reasons() -> None:
    """A target whose tier the table could not classify still admits.

    The score path treats ``None`` as T1 (no penalty); the reasons
    tuple records ``tier_unknown_assumed_T1`` so the UI can label it.
    """

    unknown = _tier_candidate("unknown/m1")  # tier defaults to None
    result = recommend_owner_dispatch(
        [unknown], policy=RoutingObjective.BALANCED, now=FIXED_NOW, min_tier=ModelTier.T1
    )
    eval_ = result.top_pick
    assert eval_ is not None
    assert "tier_unknown_assumed_T1" in eval_.reasons[0]
    assert "min_tier=T1" in eval_.reasons[0]
    assert "match=default" in eval_.reasons[0]


# ---------------------------------------------------------------------------
# M1 WP3 commit 3 — five-term score, headroom_min, 5h smoothing, MANUAL
# ---------------------------------------------------------------------------


def _weekly_window(
    *, used_fraction: float | None, reset_in_hours: float = 24.0
) -> CandidateWindowInput:
    return CandidateWindowInput(
        kind=QuotaWindowKind.WEEKLY,
        window_started_at=FIXED_NOW,
        reset_at=FIXED_NOW + timedelta(hours=reset_in_hours),
        used_fraction=used_fraction,
    )


def _five_hour_window(
    *, used_fraction: float | None, elapsed_hours: float = 2.0
) -> CandidateWindowInput:
    return CandidateWindowInput(
        kind=QuotaWindowKind.FIVE_HOUR,
        window_started_at=FIXED_NOW - timedelta(hours=elapsed_hours),
        reset_at=FIXED_NOW + timedelta(hours=5 - elapsed_hours),
        used_fraction=used_fraction,
    )


def test_ahead_source_ranks_below_on_track_under_burn_down() -> None:
    """BURN_DOWN ranks STARVED first, ON_TRACK second, AHEAD last."""

    a = _candidate(
        "provider-A/on-track",
        remaining=(0.6, 0.6),
        windows=(_weekly_window(used_fraction=0.4),),
    )
    b = _candidate(
        "provider-B/ahead",
        remaining=(0.6, 0.6),
        windows=(_weekly_window(used_fraction=0.7),),
    )
    starved = _candidate(
        "provider-C/starved",
        remaining=(0.6, 0.6),
        windows=(_weekly_window(used_fraction=0.2, reset_in_hours=1.0),),
    )
    result = recommend_owner_dispatch(
        [a, b, starved],
        policy=RoutingObjective.BURN_DOWN,
        now=FIXED_NOW,
    )
    # STARVED source must rank above ON_TRACK (its pressure_term=+1.0
    # gets weight 1.0; ON_TRACK's pressure_term=0 contributes nothing).
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "provider-C/starved"


def test_balanced_default_eliminates_t2_floor_and_burn_down_promotes_starved() -> None:
    """Two distinct behaviours pinned by the same pool.

    M1 WP3 fix (F3): the previous ``test_five_objective_each_determines_a_different_top_pick``
    name promised coverage the body did not deliver (the test
    only exercised BALANCED + BURN_DOWN on a T0/T1/T2 pool that
    was not the same as the BURN_DOWN pool). The new test makes
    the two assertions explicit and self-contained: ``BALANCED``
    with the default ``min_tier=T1`` hard-eliminates the T2 row
    (the tier-floor contract), and ``BURN_DOWN`` ranks a STARVED
    source above an ON_TRACK source (the WP3 pressure contract).
    """

    # BALANCED: T0 + T1 + T2 under default min_tier=T1. The T2
    # row is below the floor and is hard-eliminated; the T0 and
    # T1 rows admit.
    candidates = [
        _candidate("flagship", remaining=(0.9, 0.9), tier=ModelTier.T0,
                   tier_match_reason="exact"),
        _candidate("workhorse", remaining=(0.6, 0.6), tier=ModelTier.T1,
                   tier_match_reason="exact"),
        _candidate("fast", remaining=(0.4, 0.4), tier=ModelTier.T2,
                   tier_match_reason="exact"),
    ]
    result = recommend_owner_dispatch(
        candidates, policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    assert any(
        not e.admitted and "tier_below_minimum" in e.reasons[0]
        for e in result.evaluations if e.execution_target_id == "fast"
    )

    # BURN_DOWN: a STARVED WEEKLY source outranks an ON_TRACK
    # source for the same target. ``STARVED`` is a window state,
    # not an objective property — both windows carry it through
    # the same data path; what varies between presets is the
    # weight, not whether the source is STARVED.
    starved = _candidate(
        "starved", remaining=(0.6, 0.6),
        tier=ModelTier.T1, tier_match_reason="exact",
        windows=(_weekly_window(used_fraction=0.20, reset_in_hours=1.0),),
    )
    on_track = _candidate(
        "on-track", remaining=(0.6, 0.6),
        tier=ModelTier.T1, tier_match_reason="exact",
        windows=(_weekly_window(used_fraction=0.4),),
    )
    result = recommend_owner_dispatch(
        [starved, on_track], policy=RoutingObjective.BURN_DOWN, now=FIXED_NOW
    )
    assert result.top_pick.execution_target_id == "starved"


def test_5h_smoothing_eliminates_target_with_mismatched_tier() -> None:
    """A T0 target with rolling cap tripped is hard-eliminated."""

    # 0.9 used in 2h = 0.45/h > 0.35 cap → gate fires. T0 != T1.
    a = _candidate(
        "flagship/overkill",
        tier=ModelTier.T0,
        tier_match_reason="exact",
        windows=(_five_hour_window(used_fraction=0.9),),
    )
    # T1 target with same burn is exempt.
    b = _candidate(
        "workhorse/tight",
        tier=ModelTier.T1,
        tier_match_reason="exact",
        windows=(_five_hour_window(used_fraction=0.9),),
    )
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.BALANCED, now=FIXED_NOW, min_tier=ModelTier.T1
    )
    eliminated = next(
        e for e in result.evaluations if e.execution_target_id == "flagship/overkill"
    )
    assert eliminated.admitted is False
    assert any("rolling_window_smoothing" in r for r in eliminated.reasons)


def test_stale_source_pressure_term_is_zero_with_reason() -> None:
    """A STALE WEEKLY window keeps pressure_term=0 + carries
    ``burn_stale_ignored`` in the reasons tuple.
    """

    # WEEKLY that is at or past reset_at → STALE.
    stale = _weekly_window(used_fraction=0.5, reset_in_hours=0.0)
    a = _candidate(
        "provider-A/stale",
        windows=(stale,),
    )
    result = recommend_owner_dispatch(
        [a], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    top = result.top_pick
    assert top is not None
    assert "burn_stale_ignored" in top.reasons[0]


def test_score_identity_holds_for_recommender() -> None:
    """``Σ weight × value == core_score`` for one candidate.

    M1 WP3 fix (F2 + F4): the recommender emits the **same six**
    weight-named components the scheduler does, and each
    component row is the **raw unweighted value** the Σ identity
    is built from. The test iterates ``score_components`` and
    multiplies each row's ``value`` by its ``weight``; the sum
    must equal the rank score (modulo the UNKNOWN-availability
    penalty the recommender applies outside the Σ).
    """

    from personal_ai_orchestrator.scheduler import FRESHNESS_WEIGHT

    candidate = _candidate(
        "p/m",
        remaining=(0.6, 0.6),
        windows=(_weekly_window(used_fraction=0.4),),
        age_days=30,  # freshness=-5 so the value contribution is deterministic
    )
    result = recommend_owner_dispatch(
        [candidate],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
        min_tier=ModelTier.T1,
    )
    top = result.top_pick
    assert top is not None
    # The Σ-identity covers exactly the six weight-named rows. The
    # scheduler path appends ``legacy_nudge`` rows for shadow
    # compatibility (the recommender does not); either way the
    # identity is on the six named ones.
    core_names = {
        "quality_capability_fit",
        "pressure_term",
        "headroom_min",
        "latency_log",
        "cost_log",
        "freshness",
    }
    core_rows = [c for c in top.score_components if c.name in core_names]
    assert {c.name for c in core_rows} == core_names
    expected = sum(
        (c.value or 0.0) * (c.weight or 0.0) for c in core_rows
    )
    # The recommender subtracts ``8.0`` when ``availability_state
    # is UNKNOWN``; the fixture's candidate is
    # ``AVAILABLE_OBSERVED`` so the penalty does not fire.
    assert abs((top.score or 0.0) - expected) < 1e-9
    # Sanity: the freshness row carries ``FRESHNESS_WEIGHT`` and
    # the freshness value is the helper's 0.0 for stale evidence
    # (V2 normalisation — see scheduler._freshness_value).
    freshness_row = next(c for c in core_rows if c.name == "freshness")
    assert freshness_row.weight == FRESHNESS_WEIGHT
    assert freshness_row.value == 0.0


def test_recommend_owner_dispatch_is_deterministic() -> None:
    """Same inputs twice → identical score + components tuple."""

    candidate = _candidate(
        "p/m",
        remaining=(0.6, 0.6),
        windows=(_weekly_window(used_fraction=0.4),),
    )
    args = dict(
        candidates=[candidate],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
        min_tier=ModelTier.T1,
    )
    a = recommend_owner_dispatch(**args)
    b = recommend_owner_dispatch(**args)
    assert a == b
    assert a.top_pick is not None
    assert a.top_pick.score == b.top_pick.score
    assert a.top_pick.score_components == b.top_pick.score_components


def test_manual_policy_uses_balanced_with_explicit_reason() -> None:
    """MANUAL does not pick automatically; the recommender falls back to
    BALANCED weights and surfaces ``manual_policy_recommendation_uses_balanced``
    in the reasons.
    """

    candidate = _candidate(
        "p/m",
        tier=ModelTier.T0,
        tier_match_reason="exact",
    )
    result = recommend_owner_dispatch(
        [candidate],
        policy=RoutingObjective.MANUAL,
        now=FIXED_NOW,
        min_tier=ModelTier.T1,
    )
    top = result.top_pick
    assert top is not None
    # ``effective_policy`` is BALANCED; the reason tag fires.
    assert "policy=BALANCED" in top.reasons[0]
    assert "manual_policy_recommendation_uses_balanced" in top.reasons[0]
    assert result.policy is RoutingObjective.MANUAL  # wire label preserved


# ---------------------------------------------------------------------------
# M1 WP3 fix (F1) — headroom_mean rides on its own field; effective_pace
# stays None on the recommender path so the field keeps its
# ``QuotaSnapshot.effective_pace`` contract.
# ---------------------------------------------------------------------------


def test_recommender_headroom_mean_uses_explicit_field_not_effective_pace() -> None:
    """``headroom_mean`` must NOT be smuggled into ``effective_pace``.

    WP3 carried the headroom mean in ``effective_pace`` because no
    dedicated field existed. That broke the ``effective_pace``
    contract — on the recommender path the value was a headroom mean,
    on the scheduler path it was the ``QuotaSnapshot.effective_pace``
    scarcity-classifier input. The fix moves the mean to
    ``headroom_mean_fraction`` and leaves ``effective_pace`` at its
    default ``None`` on the recommender path.
    """

    candidate = _candidate(
        "provider-A/m3",
        remaining=(0.6, 0.9),
    )
    result = recommend_owner_dispatch(
        [candidate], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    top = result.top_pick
    assert top is not None
    # ``headroom_mean_fraction`` carries the arithmetic mean of
    # ``remaining_fractions`` — a real value, not None.
    assert top.headroom_mean_fraction is not None
    assert abs(top.headroom_mean_fraction - 0.75) < 1e-9
    # ``effective_pace`` stays at its default — the recommender has
    # no ``QuotaSnapshot`` so there is no value to smuggle in.
    assert top.effective_pace is None
    # The wire-shape candidate view mirrors the dedicated field so
    # the Swift UI can render both numbers alongside the score.
    view = DispatchRecommendationCandidate.model_validate(
        {
            "execution_target_id": top.execution_target_id,
            "model_sku_id": top.model_sku_id,
            "eligible": top.eligible,
            "admitted": top.admitted,
            "score": top.score,
            "headroom_mean": top.headroom_mean_fraction,
            "headroom_min": min((0.6, 0.9)),
            "evidence_fresh": True,
            "runtime_available": True,
            "verified": True,
            "quota_state": "AVAILABLE_OBSERVED",
            "score_components": [],
            "reasons": top.reasons,
        }
    )
    assert view.headroom_mean == 0.75


def test_recommender_scheduler_paths_agree_effective_pace_contract() -> None:
    """Both paths leave ``effective_pace`` as ``None`` for the same input.

    The scheduler path fills ``effective_pace`` from
    ``QuotaSnapshot.effective_pace``; the recommender path has no
    snapshot and so leaves the field at its default. A future
    observer must not see a headroom mean in either field.
    """

    candidate = _candidate("p/m", remaining=(0.6, 0.9))
    result = recommend_owner_dispatch(
        [candidate], policy=RoutingObjective.BALANCED, now=FIXED_NOW
    )
    top = result.top_pick
    assert top is not None
    assert top.effective_pace is None
    # Sanity: the headroom mean and min are two distinct numbers.
    assert top.headroom_mean_fraction is not None
    assert abs(top.headroom_mean_fraction - 0.75) < 1e-9


# -----------------------------------------------------------------------------
# M1 WP4 §4.4 — unmetered pool scoring and admission
# -----------------------------------------------------------------------------


def test_t3_task_picks_unmetered_target_when_it_is_top_candidate() -> None:
    """A T3 task under BALANCED can have an unmetered target as top pick.

    The unmetered target's ``tier`` is T3 (default glob); when the
    task's ``min_tier=T3`` is at-or-above the target tier, the
    capability fit is gentle (1.0 - 0.1 * 0 = 1.0), the headroom is
    1.0 (UNMETERED window has used_fraction=None which the
    recommender maps to headroom_min=1.0 via the unmetered
    convention), and the score is the highest. The unmetered path
    does not bump the target into COOLDOWN unless the worker
    classifier fires (handled by ``observe_rate_limited``).
    """

    unmetered = _candidate(
        "opencode-big-pickle",
        remaining=(1.0, 1.0),
        tier=ModelTier.T3,
        tier_match_reason="exact",
    )
    zai = _candidate(
        "zai-coding-plan-glm-5.3",
        remaining=(0.5, 0.5),
        tier=ModelTier.T1,
        tier_match_reason="glob",
    )
    result = recommend_owner_dispatch(
        [unmetered, zai],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
        min_tier=ModelTier.T3,
    )
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "opencode-big-pickle"


def test_cooldown_unmetered_target_is_eliminated_with_recovers_at_reason() -> None:
    """An unmetered target in COOLDOWN is rejected; the reason carries ``recovers_at``.

    The unmetered path's cooldown is short (15 minutes default) and
    expires back to ``AVAILABLE_UNMETERED`` directly via
    ``previous_state_baseline`` (commit 2). The dispatch panel
    surfaces ``recovers_at`` so the owner can see when the target
    re-opens.
    """

    target = _candidate(
        "opencode-big-pickle",
        remaining=(0.0, 0.0),
        tier=ModelTier.T3,
        tier_match_reason="exact",
        state=QuotaAvailabilityState.COOLDOWN,
    )
    result = recommend_owner_dispatch(
        [target],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
        min_tier=ModelTier.T3,
    )
    assert result.top_pick is None
    blocked = result.evaluations[0]
    assert blocked.admitted is False
    assert any("cooldown" in reason.lower() for reason in blocked.reasons)


def test_five_hour_smoothing_does_not_apply_to_unmetered_targets() -> None:
    """The 5-hour rolling smoothing gate requires a FIVE_HOUR window.

    An unmetered target has no FIVE_HOUR window — the gate must
    short-circuit to "no verdict" rather than eliminate the target
    on the (impossible) basis of a non-existent FIVE_HOUR window.
    """

    from personal_ai_orchestrator.dispatch_recommender import CandidateWindowInput
    from personal_ai_orchestrator.model_registry import QuotaWindowKind

    # A target whose only window is UNMETERED — no FIVE_HOUR window
    # exists for the rolling cap to read from.
    unmetered = _candidate(
        "opencode-big-pickle",
        remaining=(1.0, 1.0),
        tier=ModelTier.T0,
        tier_match_reason="exact",
        windows=(
            CandidateWindowInput(
                kind=QuotaWindowKind.UNMETERED,
                window_started_at=None,
                reset_at=None,
                used_fraction=None,
            ),
        ),
    )
    result = recommend_owner_dispatch(
        [unmetered],
        policy=RoutingObjective.BALANCED,
        now=FIXED_NOW,
        min_tier=ModelTier.T3,
    )
    assert result.top_pick is not None
    assert result.top_pick.admitted is True
    # No rolling-window reason should appear in the list.
    assert not any(
        "rolling" in reason.lower() for reason in result.top_pick.reasons
    )
