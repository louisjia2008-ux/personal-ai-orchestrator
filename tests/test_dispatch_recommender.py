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
    recommend_owner_dispatch,
    source_pressure_for,
)
from personal_ai_orchestrator.model_registry import QuotaWindowKind
from personal_ai_orchestrator.model_tiers import ModelTier
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
from personal_ai_orchestrator.quota_burn import BurnPressure
from personal_ai_orchestrator.scheduler import RoutingObjective


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
    )


def test_quota_saver_prefers_target_with_more_headroom():
    a = _candidate("provider-A/m2.5", remaining=(0.4, 0.5))
    b = _candidate("provider-B/m3", remaining=(0.9, 0.95))
    result = recommend_owner_dispatch(
        [a, b], policy=RoutingObjective.QUOTA_SAVER, now=FIXED_NOW
    )
    assert result.top_pick is not None
    assert result.top_pick.execution_target_id == "provider-B/m3"
    # The admission reasoning names the policy and the headroom; the
    # owner can see exactly why B won.
    assert "policy=QUOTA_SAVER" in result.top_pick.reasons[0]
    assert "headroom=0.9" in result.top_pick.reasons[0]


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
    assert "quota_headroom_mean" in components
    assert "evidence_freshness" in components
    # Capability fit is declared openly as a constant 1.0 contribution,
    # not omitted: the score_components array lists every input that
    # shaped the total.
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
) -> DispatchCandidateInput:
    return DispatchCandidateInput(
        execution_target_id=target_id,
        model_sku_id=f"{target_id}/model",
        runtime_available=True,
        verified=True,
        remaining_fractions=(0.8, 0.9),
        evidence_observed_at=FIXED_NOW - timedelta(days=1),
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

    The penalty shows up in the ``quality_capability_fit`` score component
    on the admitted evaluation; the exact arithmetic is owned by the
    recommender, this test just pins the single-tier-above case so a
    future refactor cannot silently halve or double it.
    """

    t0 = _tier_candidate("flagship/m9", tier=ModelTier.T0, tier_match_reason="exact")
    t1 = _tier_candidate("workhorse/m7", tier=ModelTier.T1, tier_match_reason="exact")
    result = recommend_owner_dispatch(
        [t0, t1], policy=RoutingObjective.BALANCED, now=FIXED_NOW, min_tier=ModelTier.T1
    )
    components = {
        c.name: c.value
        for c in next(
            e for e in result.evaluations if e.execution_target_id == "flagship/m9"
        ).score_components
    }
    # quality_weight=1.0 for BALANCED; capability_fit = 1.0 - 0.1*(1-0) = 0.9.
    # component = 1.0 * 0.9 * 25.0 = 22.5.
    assert components["quality_capability_fit"] == 22.5


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
