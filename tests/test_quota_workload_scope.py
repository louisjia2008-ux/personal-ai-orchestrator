"""P4.2.6.5.1 — MiniMax general-only coding quota projection.

The defect these tests pin
--------------------------
MiniMax's quota response describes two workload scopes on this account:
``general`` (coding and text) and ``video``. Because their remaining figures
differ, the collector concluded that no single plan figure was derivable and
reported MiniMax coding quota UNKNOWN — a *video* balance suppressing a
*coding* figure it says nothing about.

This orchestrator schedules coding, text, and agentic software-engineering
work. Video is therefore out of its workload scope: it is a real provider
observation, it is preserved, and it constrains nothing here.

Every test below asserts one half of that: what the coding projection reads,
and what it must never let another workload do to it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaState,
    QuotaWindowKind,
)
from personal_ai_orchestrator.quota_collectors.minimax import (
    MiniMaxQuotaReason,
    normalize_minimax_quota,
)
from personal_ai_orchestrator.quota_observability import build_pace_trace
from personal_ai_orchestrator.quota_plan import EquivalentScopeKind, PlanQuotaSemantics
from personal_ai_orchestrator.quota_workload_scope import (
    ACTIVE_SCHEDULING_WORKLOADS,
    QuotaWorkloadScope,
    classify_provider_scope,
    is_scheduled_workload,
)
from personal_ai_orchestrator.scheduler import (
    RoutingObjective,
    RoutingPolicy,
    TaskProfile,
    route_task,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)

#: The account's real coding/text models, as the catalog reports them.
CATALOG = frozenset({"MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2"})


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def scope_entry(
    name: str,
    *,
    interval: object = None,
    weekly: object = None,
    windows: bool = True,
) -> dict:
    """One ``model_remains`` entry as MiniMax returns it (Shape B)."""

    entry: dict = {"model": name}
    if interval is not None:
        entry["current_interval_remaining_percent"] = interval
    if weekly is not None:
        entry["current_weekly_remaining_percent"] = weekly
    if windows:
        entry.update(
            {
                "start_time": ms(NOW - timedelta(hours=1)),
                "end_time": ms(NOW + timedelta(hours=4)),
                "weekly_start_time": ms(NOW - timedelta(days=2)),
                "weekly_end_time": ms(NOW + timedelta(days=5)),
            }
        )
    return entry


def payload(*entries: dict) -> dict:
    return {"model_remains": list(entries), "base_resp": {"status_code": 0}}


def project(*entries: dict):
    return normalize_minimax_quota(payload(*entries), observed_at=NOW, known_model_ids=CATALOG)


def fractions(projection) -> tuple[float | None, float | None]:
    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None and weekly is not None
    return five_hour.remaining_fraction, weekly.remaining_fraction


# ---------------------------------------------------------------------------
# Scope classification — general and video are workloads, not models
# ---------------------------------------------------------------------------


def test_general_and_video_are_workload_scopes_not_models() -> None:
    """§4 — the provider's ``model_remains`` key does not make an entry a model."""

    assert classify_provider_scope("minimax", "general") is QuotaWorkloadScope.CODING_TEXT
    assert classify_provider_scope("minimax", "video") is QuotaWorkloadScope.VIDEO_GENERATION
    # A real model id is classified by the catalog, not by this table: nothing
    # here may claim "general" *is* MiniMax-M2.7.
    assert classify_provider_scope("minimax", "MiniMax-M2.7") is QuotaWorkloadScope.UNKNOWN


def test_every_minimax_surface_shares_one_scope_vocabulary() -> None:
    """Discovery exposes several MiniMax surfaces; they meter the same scopes."""

    for provider_id in ("minimax", "minimax-cn", "minimax-cn-coding-plan"):
        assert classify_provider_scope(provider_id, "general") is QuotaWorkloadScope.CODING_TEXT


def test_this_build_schedules_coding_text_only() -> None:
    assert ACTIVE_SCHEDULING_WORKLOADS == frozenset({QuotaWorkloadScope.CODING_TEXT})
    assert is_scheduled_workload(QuotaWorkloadScope.CODING_TEXT)
    assert not is_scheduled_workload(QuotaWorkloadScope.VIDEO_GENERATION)


def test_scope_entries_are_never_promoted_into_routable_models() -> None:
    """§16 — no per-model remaining balance may be invented from a scope name."""

    projection = project(
        scope_entry("general", interval=95, weekly=95),
        scope_entry("video", interval=60, weekly=60),
    )

    assert projection.covered_model_ids() == ()
    assert all(
        view.scope_kind is EquivalentScopeKind.PROVIDER_RESOURCE_SCOPE
        for view in projection.model_equivalents
    )
    # The shared-plan semantics are unchanged: one pool, no model-scoped pools.
    assert projection.plan.quota_semantics is PlanQuotaSemantics.SHARED_POOL
    assert projection.pool.shared_across_models is True


# ---------------------------------------------------------------------------
# §19 — the critical regression fixture
# ---------------------------------------------------------------------------


def test_general_95_beside_video_60_reads_as_95() -> None:
    """§19 — the exact fixture that used to collapse MiniMax to UNKNOWN.

    The two figures are not two views of one quantity. Coding quota is
    ``general``: 95%, EXACT, neither averaged with video (77.5%) nor limited
    by it (60%).
    """

    projection = project(
        scope_entry("general", interval=95, weekly=95),
        scope_entry("video", interval=60, weekly=60),
    )

    assert fractions(projection) == (pytest.approx(0.95), pytest.approx(0.95))
    assert projection.confidence is EvidenceConfidence.EXACT
    assert projection.state is QuotaState.AVAILABLE
    assert projection.unknown_reason is None
    assert projection.active_workload_scope is QuotaWorkloadScope.CODING_TEXT
    # Not the mean, and not the minimum.
    assert fractions(projection)[0] != pytest.approx(0.775)
    assert fractions(projection)[0] != pytest.approx(0.60)


def test_the_reason_for_a_coding_figure_is_never_a_model_disagreement() -> None:
    """§9 — ``QUOTA_VARIES_BY_MODEL`` was wrong, and cannot be reintroduced."""

    retired = {"QUOTA_VARIES_BY_MODEL", "SHARED_POOL_VIEWED_PER_MODEL"}
    for entries in (
        (scope_entry("general", interval=95, weekly=60), scope_entry("video", interval=100)),
        (scope_entry("video", interval=60, weekly=60),),
        (scope_entry("general"),),
    ):
        projection = project(*entries)
        assert projection.unknown_reason not in retired
    assert not (retired & {member.value for member in MiniMaxQuotaReason})


def test_both_general_windows_are_projected_separately_when_identified() -> None:
    """§7 — real 5-hour and weekly figures, each from its own provider field."""

    projection = project(
        scope_entry("general", interval=96, weekly=59),
        scope_entry("video", interval=100, weekly=100),
    )

    five_hour = projection.window(QuotaWindowKind.FIVE_HOUR)
    weekly = projection.window(QuotaWindowKind.WEEKLY)
    assert five_hour is not None and weekly is not None
    assert five_hour.remaining_fraction == pytest.approx(0.96)
    assert weekly.remaining_fraction == pytest.approx(0.59)
    assert five_hour.confidence is EvidenceConfidence.EXACT
    assert weekly.confidence is EvidenceConfidence.EXACT
    assert five_hour.duration_seconds != weekly.duration_seconds


def test_a_window_the_general_scope_does_not_identify_is_not_invented() -> None:
    """§7 — no fabricated "weekly = 95%" from a 5-hour figure."""

    projection = project(
        scope_entry("general", interval=95),
        scope_entry("video", interval=60, weekly=60),
    )

    five_hour, weekly = fractions(projection)
    assert five_hour == pytest.approx(0.95)
    assert weekly is None
    assert projection.unknown_reason == MiniMaxQuotaReason.GENERAL_WINDOW_SEMANTICS_UNKNOWN.value
    # ...and certainly not borrowed from video's weekly figure.
    assert weekly != pytest.approx(0.60)


# ---------------------------------------------------------------------------
# §20 — the projection table
# ---------------------------------------------------------------------------


def test_low_general_is_reported_even_when_video_is_full() -> None:
    projection = project(
        scope_entry("general", interval=20, weekly=20),
        scope_entry("video", interval=100, weekly=100),
    )

    assert fractions(projection) == (pytest.approx(0.20), pytest.approx(0.20))
    assert projection.state is QuotaState.AVAILABLE


def test_coding_stays_available_when_only_video_is_exhausted() -> None:
    projection = project(
        scope_entry("general", interval=100, weekly=100),
        scope_entry("video", interval=0, weekly=0),
    )

    assert fractions(projection) == (pytest.approx(1.0), pytest.approx(1.0))
    assert projection.state is QuotaState.AVAILABLE


def test_coding_is_exhausted_when_general_is_exhausted() -> None:
    """A full video balance cannot rescue an exhausted coding scope."""

    projection = project(
        scope_entry("general", interval=0, weekly=0),
        scope_entry("video", interval=100, weekly=100),
    )

    assert fractions(projection) == (pytest.approx(0.0), pytest.approx(0.0))
    assert projection.state is QuotaState.EXHAUSTED


def test_general_alone_is_enough() -> None:
    projection = project(scope_entry("general", interval=95, weekly=59))

    assert fractions(projection) == (pytest.approx(0.95), pytest.approx(0.59))
    assert projection.confidence is EvidenceConfidence.EXACT
    assert projection.workload_scope_notes == ()


def test_video_alone_leaves_coding_unknown_rather_than_borrowing_its_figure() -> None:
    """§18 — UNKNOWN for a precise reason, never a video number in disguise."""

    projection = project(scope_entry("video", interval=60, weekly=60))

    assert fractions(projection) == (None, None)
    assert projection.confidence is EvidenceConfidence.UNKNOWN
    assert projection.state is QuotaState.UNKNOWN
    assert projection.unknown_reason == MiniMaxQuotaReason.GENERAL_QUOTA_NOT_AVAILABLE.value
    # The video observation itself is still preserved.
    assert [view.scope_id for view in projection.model_equivalents] == ["video", "video"]


def test_malformed_general_values_fail_closed() -> None:
    """§20 — a figure we cannot read is not a figure, and is not replaced."""

    for bad in ("ninety-five", 420, -3, None):
        projection = project(
            scope_entry("general", interval=bad if bad is not None else "", weekly=bad),
            scope_entry("video", interval=60, weekly=60),
        )
        assert fractions(projection) == (None, None)
        assert projection.confidence is EvidenceConfidence.UNKNOWN
        assert projection.unknown_reason == MiniMaxQuotaReason.GENERAL_QUOTA_READ_FAILED.value


def test_a_general_scope_with_no_figures_does_not_fall_back_to_video() -> None:
    projection = project(
        scope_entry("general"),
        scope_entry("video", interval=60, weekly=60),
    )

    assert fractions(projection) == (None, None)
    assert projection.unknown_reason == MiniMaxQuotaReason.GENERAL_QUOTA_NOT_AVAILABLE.value


def test_several_coding_scopes_that_disagree_still_derive_nothing() -> None:
    """Agreement is still required *within* the coding workload."""

    projection = normalize_minimax_quota(
        payload(
            scope_entry("general", interval=95, weekly=95),
            scope_entry("text", interval=40, weekly=95),
        ),
        observed_at=NOW,
        known_model_ids=CATALOG,
    )

    five_hour, weekly = fractions(projection)
    assert five_hour is None
    assert weekly == pytest.approx(0.95)
    assert projection.unknown_reason == MiniMaxQuotaReason.CODING_SCOPE_VIEWS_DISAGREE.value


# ---------------------------------------------------------------------------
# §21 — evidence preservation
# ---------------------------------------------------------------------------


def test_video_evidence_is_preserved_and_classified_not_deleted() -> None:
    """§21/§22 — a future video-scheduling build must still find this data."""

    projection = project(
        scope_entry("general", interval=95, weekly=95),
        scope_entry("video", interval=60, weekly=60),
    )

    by_scope = {(view.scope_id, view.window_id): view for view in projection.model_equivalents}
    assert set(by_scope) == {
        ("general", "5h"),
        ("general", "weekly"),
        ("video", "5h"),
        ("video", "weekly"),
    }
    assert by_scope[("video", "5h")].remaining_fraction == pytest.approx(0.60)
    assert by_scope[("video", "5h")].workload_scope is QuotaWorkloadScope.VIDEO_GENERATION
    assert by_scope[("general", "5h")].workload_scope is QuotaWorkloadScope.CODING_TEXT
    # Separable by the UI without re-deriving the classification.
    assert [view.scope_id for view in projection.equivalents_in_active_workload()] == [
        "general",
        "general",
    ]
    assert [view.scope_id for view in projection.equivalents_outside_active_workload()] == [
        "video",
        "video",
    ]
    assert projection.workload_scope_notes == ("VIDEO_SCOPE_IGNORED_FOR_CODING",)


# ---------------------------------------------------------------------------
# §13/§20 — video must not reach the scheduler
# ---------------------------------------------------------------------------


def _minimax_snapshot(video_interval: float, video_weekly: float):
    projection = project(
        scope_entry("general", interval=95, weekly=59),
        scope_entry("video", interval=video_interval, weekly=video_weekly),
    )
    return projection


def test_video_never_becomes_the_coding_binding_window() -> None:
    """§13 — the scarcest *coding* window binds, whatever video reports."""

    scarce_video = _minimax_snapshot(1, 1)
    full_video = _minimax_snapshot(100, 100)

    for projection in (scarce_video, full_video):
        binding = projection.binding_window
        # weekly (59%) is scarcer than 5h (95%); video's 1% is not a candidate.
        assert binding.window_id == "weekly"
        assert binding.remaining_fraction == pytest.approx(0.59)
    assert scarce_video.binding_window == full_video.binding_window


def test_video_never_enters_the_snapshot_the_scheduler_reads() -> None:
    """The structural guarantee: excluded scopes are not windows."""

    scarce = _minimax_snapshot(1, 1).to_snapshot()
    full = _minimax_snapshot(100, 100).to_snapshot()

    assert [window.window_id for window in scarce.windows] == ["5h", "weekly"]
    assert [window.remaining_fraction for window in scarce.windows] == [
        window.remaining_fraction for window in full.windows
    ]


def test_video_never_contributes_to_temporal_scarcity() -> None:
    """§13 — a collapsing video balance is not a coding scarcity signal."""

    scarce = build_pace_trace(_minimax_snapshot(1, 1).to_snapshot(), at=NOW)
    full = build_pace_trace(_minimax_snapshot(100, 100).to_snapshot(), at=NOW)

    assert scarce.scarcity_class is full.scarcity_class
    assert scarce.effective_pace == full.effective_pace
    assert [window.window_id for window in scarce.windows] == ["5h", "weekly"]


def _source(confidence: EvidenceConfidence = EvidenceConfidence.EXACT) -> EvidenceSource:
    return EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://quota",
        confidence=confidence,
    )


def _registry_with(snapshot) -> ModelRegistry:
    """A one-target registry whose MiniMax pool is a real collector snapshot."""

    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax")},
        accounts={"minimax-a": Account(id="minimax-a", provider_id="minimax", label="MiniMax")},
        plans={
            "minimax-plan": Plan(
                id="minimax-plan",
                account_id="minimax-a",
                name="Token Plan",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "minimax-token-plan-cn": QuotaPool(
                id="minimax-token-plan-cn",
                plan_id="minimax-plan",
                name="MiniMax Token Plan",
                snapshot=snapshot,
                reserve_fraction=0.0,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR, QuotaWindowKind.WEEKLY),
            )
        },
        models={
            "minimax-m2.7": ModelSKU(
                id="minimax-m2.7",
                provider_id="minimax",
                display_name="MiniMax-M2.7",
                capabilities=CapabilityProfile(scores={"debugging": 0.9}),
            )
        },
        execution_targets={
            "minimax-sub": ExecutionTarget(
                id="minimax-sub",
                model_sku_id="minimax-m2.7",
                account_id="minimax-a",
                runtime_id="opencode",
                execution_verified=True,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="m27-binding",
                model_sku_id="minimax-m2.7",
                execution_target_id="minimax-sub",
                quota_pool_id="minimax-token-plan-cn",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=_source(),
            ),
        ),
        pool_memberships=(
            PoolMembership(pool=PoolKind.WORKER, model_sku_id="minimax-m2.7", priority=1),
        ),
    )


def _quota_saver_decision(video_interval: float, video_weekly: float):
    snapshot = _minimax_snapshot(video_interval, video_weekly).to_snapshot(
        quota_pool_id="minimax-token-plan-cn"
    )
    return route_task(
        _registry_with(snapshot),
        task=TaskProfile(
            task_id="coding-task",
            pool=PoolKind.WORKER,
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        ),
        now=NOW,
        known_at=NOW,
        runtime_availability={"minimax-sub": True},
        policy=RoutingPolicy(objective=RoutingObjective.QUOTA_SAVER),
    )


def test_quota_saver_does_not_penalize_coding_when_video_is_low() -> None:
    """§13 — QUOTA_SAVER must see the same coding task either way."""

    exhausted_video = _quota_saver_decision(0, 0)
    full_video = _quota_saver_decision(100, 100)

    assert exhausted_video.selected_execution_target_id == "minimax-sub"
    assert full_video.selected_execution_target_id == "minimax-sub"
    left = exhausted_video.evaluations[0]
    right = full_video.evaluations[0]
    assert left.admitted is right.admitted is True
    assert left.score == right.score
    assert left.scarcity_class is right.scarcity_class
    assert left.minimum_remaining_fraction == right.minimum_remaining_fraction


# ---------------------------------------------------------------------------
# §15 — equivalent capacity draws on the coding scope
# ---------------------------------------------------------------------------


def test_equivalent_capacity_reads_the_coding_binding_window_only() -> None:
    """§15 — the estimate's input is the coding window, never video's."""

    projection = _minimax_snapshot(1, 1)
    binding = projection.binding_window
    window = projection.window(QuotaWindowKind.WEEKLY)

    assert window is not None
    assert binding.window_id == window.window_id
    # Equivalent capacity is derived from this window; nothing in the windows
    # tuple came from a scope outside the projected workload.
    assert all(
        view.workload_scope is not QuotaWorkloadScope.VIDEO_GENERATION
        for view in projection.equivalents_in_active_workload()
    )
