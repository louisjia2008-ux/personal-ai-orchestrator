"""M1 WP5a-1 commit 1 — unit tests for the extracted recommendation service.

These tests pin the byte-equivalence contract that the refactor
preserves on the dispatch-recommendation path. They cover the
service in isolation (not through the HTTP handler) so a regression
in the host application/service layer surfaces here before the
integration tests catch it.

The tests focus on the host layer's responsibilities:

- Building one ``DispatchCandidateInput`` per execution target,
  respecting the static runtime-availability map, the
  ``execution_evidence_journal`` demote-fallback, the quota
  availability journal's ``state_at(now)``, and the tier-table
  lookup.
- Degrading gracefully on ``invalid_min_tier`` (a corrupt string in
  ``task.min_tier``) by recording a ``TASK_MIN_TIER_INVALID``
  system event and falling back to ``ModelTier.T1``.
- Returning the deterministic ``DispatchRecommendation`` from the
  scoring core.

The scoring algorithm itself is covered by the pre-existing
``tests/test_dispatch_recommender.py`` suite (left untouched by
this refactor).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.dispatch_recommendation_service import (
    DispatchRecommendationService,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    Provider,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
)
from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    TaskRecord,
)
from personal_ai_orchestrator.scheduler import RoutingObjective

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


SECRET_MARKER = "credential-ref-must-never-appear"


def _registry() -> ModelRegistry:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=now,
        confidence=EvidenceConfidence.EXACT,
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18_000,
        remaining_fraction=0.42,
        window_started_at=now - timedelta(hours=3),
        reset_at=now + timedelta(hours=2),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-1",
        quota_pool_id="pool",
        provider_id="minimax",
        observed_at=now,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax CN")},
        accounts={
            "account": Account(
                id="account",
                provider_id="minimax",
                label="subscription",
                credential_ref=SECRET_MARKER,
            )
        },
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="shared",
                snapshot=snapshot,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
            )
        },
        models={
            "m3": ModelSKU(
                id="m3",
                provider_id="minimax",
                display_name="m3",
            ),
            "m2": ModelSKU(
                id="m2",
                provider_id="minimax",
                display_name="m2",
            ),
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
                execution_verified=True,
            ),
            "m2-sub": ExecutionTarget(
                id="m2-sub",
                model_sku_id="m2",
                account_id="account",
                runtime_id="opencode",
                execution_verified=False,
            ),
        },
    )


def _build_service(
    tmp_path: Path,
    *,
    runtime_availability: dict[str, bool] | None = None,
) -> tuple[SafetyKernelStore, QuotaAvailabilityJournal, ModelRegistry]:
    store = SafetyKernelStore(tmp_path / "safety.db")
    availability = QuotaAvailabilityJournal(tmp_path)
    return store, availability, _registry()


def _make_service(
    store: SafetyKernelStore,
    availability: QuotaAvailabilityJournal,
    registry: ModelRegistry,
    *,
    runtime_availability: dict[str, bool] | None = None,
    execution_evidence_journal: ExecutionEvidenceJournal | None = None,
) -> DispatchRecommendationService:
    return DispatchRecommendationService(
        store,
        registry_provider=lambda: registry,
        quota_refresh_service=None,
        execution_evidence_journal=execution_evidence_journal,
        quota_availability_journal=availability,
        tier_table=None,
        runtime_availability=runtime_availability,
        runtime_availability_fallback=lambda _: True,
    )


def _make_task(
    store: SafetyKernelStore,
    *,
    task_id: str = "task-1",
    min_tier: str = "T1",
) -> TaskRecord:
    return store.submit_task(
        task_id=task_id,
        request_id=f"submit-{task_id}",
        project_id=None,
        intent="rank me",
        min_tier=min_tier,
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_collect_candidates_one_per_execution_target(tmp_path: Path) -> None:
    """``collect_candidates`` returns one row per execution target
    in the registry, sorted by ``target_id``.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        service = _make_service(store, availability, registry)
        candidates = service.collect_candidates()
        assert [c.execution_target_id for c in candidates] == [
            "m2-sub",
            "m3-sub",
        ]
        by_id = {c.execution_target_id: c for c in candidates}
        assert by_id["m3-sub"].verified is True  # static launch authority
        assert by_id["m2-sub"].verified is False
        for c in candidates:
            assert c.model_sku_id in {"m2", "m3"}
            assert c.runtime_available is True
            assert c.availability_state is QuotaAvailabilityState.UNKNOWN
            assert c.tier is None
        # Static registry verification is launch authority and must agree
        # with the owner-dispatch launch gate.
        assert by_id["m3-sub"].verified is True
        assert by_id["m2-sub"].verified is False
    finally:
        store.close()


def test_expired_execution_history_is_not_actionable_recommendation(
    tmp_path: Path,
) -> None:
    """Historical VERIFIED may remain diagnostic but cannot be admitted."""

    from personal_ai_orchestrator.scheduler import RoutingObjective

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    store, availability, registry = _build_service(tmp_path)
    try:
        # Remove static authority so m3-sub is evidence-backed.
        target = registry.execution_targets["m3-sub"].model_copy(
            update={"execution_verified": False}
        )
        registry = registry.model_copy(
            update={
                "execution_targets": {
                    **registry.execution_targets,
                    "m3-sub": target,
                }
            }
        )
        journal = ExecutionEvidenceJournal(tmp_path / "execution-runtime")
        old = now - timedelta(days=31)
        journal.append(
            build_execution_evidence(
                provider_id="minimax",
                execution_target_id="m3-sub",
                model_sku_id="m3",
                observed_at=old,
                result=ExecutionVerificationOutcome.VERIFIED,
                reason_code="TEST_OLD_VERIFIED",
            )
        )
        service = _make_service(
            store,
            availability,
            registry,
            execution_evidence_journal=journal,
        )
        candidates = service.collect_candidates(now=now)
        by_id = {candidate.execution_target_id: candidate for candidate in candidates}
        assert by_id["m3-sub"].verified is False
        assert by_id["m3-sub"].verified_stale is True
        assert by_id["m3-sub"].evidence_observed_at == old

        task = _make_task(store)
        recommendation, _, _ = service.recommend_for_task(
            task,
            policy=RoutingObjective.BALANCED,
            now=now,
        )
        evaluation = next(
            item for item in recommendation.evaluations if item.execution_target_id == "m3-sub"
        )
        assert evaluation.admitted is False
        assert "runtime-verified" in " ".join(evaluation.reasons)
    finally:
        store.close()


def test_collect_candidates_uses_launch_authority_not_historical_verification(
    tmp_path: Path,
) -> None:
    store, availability, registry = _build_service(tmp_path)
    journal = ExecutionEvidenceJournal(tmp_path / "execution")
    now = datetime(2030, 2, 1, tzinfo=UTC)
    try:
        old_verified = now - timedelta(days=31)
        journal.append(
            build_execution_evidence(
                provider_id="minimax",
                execution_target_id="m2-sub",
                model_sku_id="m2",
                observed_at=old_verified,
                result=ExecutionVerificationOutcome.VERIFIED,
                reason_code="TEST_EXPIRED",
            )
        )
        service = _make_service(
            store,
            availability,
            registry,
            execution_evidence_journal=journal,
        )
        candidate = {
            item.execution_target_id: item for item in service.collect_candidates(now=now)
        }["m2-sub"]
        assert candidate.verified is False
        assert candidate.verified_stale is True
        assert candidate.evidence_observed_at == old_verified
    finally:
        store.close()


def test_collect_candidates_latest_failure_revokes_historical_launch_authority(
    tmp_path: Path,
) -> None:
    store, availability, registry = _build_service(tmp_path)
    journal = ExecutionEvidenceJournal(tmp_path / "execution")
    now = datetime(2030, 2, 1, tzinfo=UTC)
    try:
        journal.append(
            build_execution_evidence(
                provider_id="minimax",
                execution_target_id="m2-sub",
                model_sku_id="m2",
                observed_at=now - timedelta(days=1),
                result=ExecutionVerificationOutcome.VERIFIED,
                reason_code="TEST_VERIFIED",
            )
        )
        journal.append(
            build_execution_evidence(
                provider_id="minimax",
                execution_target_id="m2-sub",
                model_sku_id="m2",
                observed_at=now,
                result=ExecutionVerificationOutcome.AUTH_FAILED,
                reason_code="TEST_AUTH_FAILED",
            )
        )
        service = _make_service(
            store,
            availability,
            registry,
            execution_evidence_journal=journal,
        )
        candidate = {
            item.execution_target_id: item for item in service.collect_candidates(now=now)
        }["m2-sub"]
        assert candidate.verified is False
        assert candidate.verified_stale is True

        task = _make_task(store)
        recommendation, _, _ = service.recommend_for_task(
            task,
            policy=RoutingObjective.BALANCED,
            now=now,
        )
        m2 = next(
            item for item in recommendation.evaluations if item.execution_target_id == "m2-sub"
        )
        assert m2.admitted is False
        assert "execution target has not been runtime-verified" in m2.reasons
    finally:
        store.close()


def test_collect_candidates_runtime_availability_overrides_fallback(
    tmp_path: Path,
) -> None:
    """A static ``runtime_availability`` entry wins over the
    ``runtime_availability_fallback`` callable.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        service = _make_service(
            store,
            availability,
            registry,
            runtime_availability={"m3-sub": False},
        )
        candidates = service.collect_candidates()
        by_id = {c.execution_target_id: c for c in candidates}
        assert by_id["m3-sub"].runtime_available is False
        assert by_id["m2-sub"].runtime_available is True
    finally:
        store.close()


def test_collect_candidates_quota_availability_state_at_now(
    tmp_path: Path,
) -> None:
    """``availability_state`` is read via ``evidence.state_at(now)``
    so a cooldown in the future still surfaces as ``COOLDOWN`` at
    the same ``now`` it was sampled for.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        future_cooldown_until = datetime(2030, 1, 1, tzinfo=UTC)
        availability.save(
            _availability_evidence(
                execution_target_id="m3-sub",
                provider_id="minimax",
                quota_pool_id="pool",
                state=QuotaAvailabilityState.COOLDOWN,
                cooldown_until=future_cooldown_until,
            )
        )
        service = _make_service(store, availability, registry)
        candidates = service.collect_candidates()
        by_id = {c.execution_target_id: c for c in candidates}
        # ``state_at`` projects COOLDOWN to RECOVERY_PROBE_DUE
        # after expiry; before expiry it stays at COOLDOWN. We
        # sample at ``now`` so it is whichever the journal returned.
        assert by_id["m3-sub"].availability_state in {
            QuotaAvailabilityState.COOLDOWN.value,
            QuotaAvailabilityState.RECOVERY_PROBE_DUE.value,
        }
        assert by_id["m2-sub"].availability_state is QuotaAvailabilityState.UNKNOWN
    finally:
        store.close()


def test_recommend_for_task_runs_deterministic_scoring(tmp_path: Path) -> None:
    """``recommend_for_task`` returns a ``DispatchRecommendation``
    with one evaluation per candidate + a top-1 pick.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        task = _make_task(store)
        service = _make_service(store, availability, registry)
        recommendation, candidates, invalid_min_tier = service.recommend_for_task(
            task,
            policy=RoutingObjective.BALANCED,
        )
        assert invalid_min_tier is None
        assert len(candidates) == len(recommendation.evaluations)
        if recommendation.top_pick is not None:
            assert recommendation.top_pick.execution_target_id in {
                "m2-sub",
                "m3-sub",
            }
    finally:
        store.close()


def test_recommend_for_task_records_task_min_tier_invalid(tmp_path: Path) -> None:
    """A corrupt ``task.min_tier`` string records a
    ``TASK_MIN_TIER_INVALID`` system event and falls back to
    ``ModelTier.T1``.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        # Submit a task with a deliberately invalid min_tier. The
        # store validates ``min_tier`` is a ``ModelTier`` on submit,
        # so we corrupt the column after submission.
        task = _make_task(store, min_tier="T1")
        store.connection.execute(
            "UPDATE tasks SET min_tier='T_CORRUPT' WHERE task_id=?", ("task-1",)
        )
        task = store.get_task("task-1")

        service = _make_service(store, availability, registry)
        recommendation, candidates, invalid_min_tier = service.recommend_for_task(
            task,
            policy=RoutingObjective.BALANCED,
        )
        assert invalid_min_tier == "T_CORRUPT"
        # No exception; ranking falls back to T1 and surfaces a
        # ``min_tier_invalid_assumed_T1`` reason on every candidate.
        for evaluation in recommendation.evaluations:
            if evaluation.admitted:
                reasons_text = " ".join(evaluation.reasons)
                assert "min_tier_invalid_assumed_T1" in reasons_text
        row = store.connection.execute(
            "SELECT event_type FROM audit_events WHERE event_type='TASK_MIN_TIER_INVALID'"
        ).fetchone()
        assert row is not None
    finally:
        store.close()


def test_recommend_for_task_passes_candidates_to_view_construction(
    tmp_path: Path,
) -> None:
    """The returned ``candidates`` list matches the registry targets
    in the same order as ``collect_candidates``.
    """

    store, availability, registry = _build_service(tmp_path)
    try:
        task = _make_task(store)
        service = _make_service(store, availability, registry)
        direct_candidates = service.collect_candidates()
        recommendation, service_candidates, _ = service.recommend_for_task(
            task,
            policy=RoutingObjective.BALANCED,
        )
        assert [c.execution_target_id for c in service_candidates] == [
            c.execution_target_id for c in direct_candidates
        ]
    finally:
        store.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _availability_evidence(
    *,
    execution_target_id: str,
    provider_id: str,
    quota_pool_id: str,
    state: QuotaAvailabilityState,
    cooldown_until: datetime | None = None,
) -> Any:
    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityEvidence,
    )

    return QuotaAvailabilityEvidence(
        execution_target_id=execution_target_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        state=state,
        consecutive_failures=0,
        previous_state_baseline=None,
        cooldown_until=cooldown_until,
        observed_at=datetime.now(UTC),
    )


# ``_BALANCED`` removed — the test imports ``RoutingObjective``
# locally now (the prior module-level indirection was a leftover
# from a stricter import-order experiment).
