"""M1 WP1 — burn wiring through ``QuotaWindowSnapshot.burn`` and
``PlanQuotaProjection.source_pressure``.

The dashboard asks two related questions:

- per window: how fast am I burning this quota relative to the ideal
  line (``burn`` on each ``QuotaPlanWindowView``);
- per provider: how urgent is the WEEKLY window that drives the
  recommender (``source_pressure`` on ``QuotaProviderCardView``).

Both pieces of information come from the same ``assess`` primitive, so
the tests assert the *wiring*: the projection surfaces carry the right
enum values and the right ``inferred`` flag, without duplicating the
truth-table assertions that already live in ``test_quota_burn.py``.

The view-model projection (the ``QuotaBurnView`` and the
``source_pressure`` string on the card) is tested at the model layer
rather than through a full ``refresh_quota`` round-trip: the
``_burn_view_for_window`` and ``_quota_provider_card`` helpers are
small shims over the methods under test here, and a separate
end-to-end test lives in ``test_quota_refresh.py`` for the broader
provider-card pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_burn import BurnPressure
from personal_ai_orchestrator.quota_plan import (
    ConsumptionUnitKind,
    BindingWindow,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
)


def _evidence(now: datetime) -> EvidenceSource:
    return EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API, observed_at=now
    )


def test_snapshot_burn_returns_unmetered_when_no_reset_at() -> None:
    """No ``reset_at`` → UNMETERED, no inference flag."""

    now = datetime.now(UTC)
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=5 * 3600.0,
        remaining_fraction=0.5,
        used_fraction=0.5,
        window_started_at=now - timedelta(hours=1),
        reset_at=None,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    assessment, inferred = window.burn(now=now)
    assert assessment.pressure is BurnPressure.UNMETERED
    assert inferred is False


def test_snapshot_burn_returns_unmetered_for_unknown_kind() -> None:
    """``UNKNOWN`` / ``CUSTOM`` kinds have no canonical duration → UNMETERED."""

    now = datetime.now(UTC)
    window = QuotaWindowSnapshot(
        window_id="custom",
        window_kind=QuotaWindowKind.UNKNOWN,
        duration_seconds=None,
        remaining_fraction=0.5,
        used_fraction=0.5,
        window_started_at=now - timedelta(hours=1),
        reset_at=now + timedelta(hours=1),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    assessment, inferred = window.burn(now=now)
    assert assessment.pressure is BurnPressure.UNMETERED
    assert inferred is False


def test_snapshot_burn_marks_inferred_when_collector_omits_started_at() -> None:
    """No ``window_started_at`` + a kind with a duration → infer it.

    The inferred start is ``reset_at - 7d`` for WEEKLY, so for a reset
    24h from ``now`` the inferred start is ~6 d before ``now`` and the
    actual-vs-expected deviation lands in BEHIND territory (we have
    used less than the 6-day elapsed fraction would predict).
    """

    now = datetime.now(UTC)
    window = QuotaWindowSnapshot(
        window_id="weekly",
        window_kind=QuotaWindowKind.WEEKLY,
        duration_seconds=7 * 24 * 3600.0,
        remaining_fraction=0.5,
        used_fraction=0.5,
        window_started_at=None,  # omitted by collector
        reset_at=now + timedelta(hours=24),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    assessment, inferred = window.burn(now=now)
    assert inferred is True
    assert assessment.pressure is BurnPressure.BEHIND


def test_snapshot_burn_does_not_infer_when_collector_supplies_started_at() -> None:
    now = datetime.now(UTC)
    window = QuotaWindowSnapshot(
        window_id="weekly",
        window_kind=QuotaWindowKind.WEEKLY,
        duration_seconds=7 * 24 * 3600.0,
        remaining_fraction=0.5,
        used_fraction=0.5,
        window_started_at=now - timedelta(hours=24),
        reset_at=now + timedelta(hours=24),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    _, inferred = window.burn(now=now)
    assert inferred is False


def _projection(
    *, weekly_used_fraction: float | None, weekly_reset_offset_hours: float = 24.0
) -> PlanQuotaProjection:
    now = datetime.now(UTC)
    weekly = QuotaWindowSnapshot(
        window_id="weekly",
        window_kind=QuotaWindowKind.WEEKLY,
        duration_seconds=7 * 24 * 3600.0,
        remaining_fraction=(
            (1.0 - weekly_used_fraction) if weekly_used_fraction is not None else None
        ),
        used_fraction=weekly_used_fraction,
        window_started_at=now,
        reset_at=now + timedelta(hours=weekly_reset_offset_hours),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    plan = PlanQuota(
        provider_id="minimax-cn-coding-plan",
        plan_id="coding-plan",
        display_name="Coding Plan",
        quota_semantics=PlanQuotaSemantics.SHARED_POOL,
        observed_at=now,
    )
    pool = SharedQuotaPool(
        provider_id="minimax-cn-coding-plan",
        plan_id="coding-plan",
        pool_id="coding-plan",
        shared_across_models=True,
        unit_kind=ConsumptionUnitKind.TOKENS,
        resource_kind=QuotaResourceKind.TOKEN_PLAN_INCLUDED_QUOTA,
    )
    return PlanQuotaProjection(
        plan=plan,
        pool=pool,
        windows=(weekly,),
        binding_window=BindingWindow(),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )


def test_projection_source_pressure_reads_weekly_window_assessment() -> None:
    """80% remaining, 1h to reset → STARVED (per the truth table)."""

    projection = _projection(weekly_used_fraction=0.20, weekly_reset_offset_hours=1.0)
    now = datetime.now(UTC)
    assert projection.source_pressure(now=now) is BurnPressure.STARVED


def test_projection_source_pressure_unmetered_when_weekly_window_has_no_used_fraction() -> None:
    """``used_fraction=None`` → UNMETERED (no quota observed)."""

    projection = _projection(weekly_used_fraction=None)
    now = datetime.now(UTC)
    assert projection.source_pressure(now=now) is BurnPressure.UNMETERED


def test_projection_source_pressure_unmetered_when_no_weekly_window() -> None:
    """No WEEKLY window at all → UNMETERED, not a guess from another window."""

    now = datetime.now(UTC)
    five_h = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=5 * 3600.0,
        remaining_fraction=0.5,
        used_fraction=0.5,
        window_started_at=now,
        reset_at=now + timedelta(hours=5),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    plan = PlanQuota(
        provider_id="minimax-cn-coding-plan",
        plan_id="coding-plan",
        display_name="Coding Plan",
        quota_semantics=PlanQuotaSemantics.SHARED_POOL,
        observed_at=now,
    )
    pool = SharedQuotaPool(
        provider_id="minimax-cn-coding-plan",
        plan_id="coding-plan",
        pool_id="coding-plan",
        shared_across_models=True,
        unit_kind=ConsumptionUnitKind.TOKENS,
        resource_kind=QuotaResourceKind.TOKEN_PLAN_INCLUDED_QUOTA,
    )
    projection = PlanQuotaProjection(
        plan=plan,
        pool=pool,
        windows=(five_h,),
        binding_window=BindingWindow(),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=_evidence(now),
    )
    assert projection.source_pressure(now=now) is BurnPressure.UNMETERED