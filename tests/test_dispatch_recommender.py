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
    DispatchCandidateInput,
    recommend_owner_dispatch,
)
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
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
