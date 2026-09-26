"""M1 WP5a-2 — the supervised-auto tick's full safety lifecycle.

Every test runs on a fake clock (``NOW`` + explicit offsets); nothing
sleeps. The supervisor cadence, the executor thread spawn and the real
worker/verifier surfaces are covered by their own test modules — this
file pins the tick's planning gates, grace semantics, dispatch
idempotence and every fail-closed abort path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_ai_orchestrator.control_api import ControlPlaneService
from personal_ai_orchestrator.execution_evidence import (
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    mark_recovery_probe_due,
    observe_exhaustion,
    observe_success,
)
from personal_ai_orchestrator.quota_refresh import (
    QuotaObservationState,
    QuotaProviderObservation,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduling_settings import SchedulingSettings
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal
from personal_ai_orchestrator.supervised_auto_step import (
    SUPERVISED_AUTO_STEP_NAME,
    supervised_auto_decision_id,
    supervised_auto_dispatch_request_id,
    supervised_auto_routing_request_id,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from tests.quota_identity_fixtures import bind
from tests.test_dispatch_initiator import _make_repo
from tests.test_dispatch_initiator import _registry as _base_registry

NOW = datetime.now(UTC) + timedelta(hours=2)
#: Timestamps for evidence / quota observations: recent past relative to
#: the fake NOW anchor so freshness gates pass deterministically.
OBSERVED_AT = NOW - timedelta(minutes=1)


def _registry():
    registry = _base_registry()
    for model_id in registry.models:
        registry = bind(registry, model_id, "pool", NOW - timedelta(days=1))
    return registry


class _FakeQuotaRefresh:
    """Minimal ``observations()`` provider — windows only, no I/O."""

    def __init__(self, observation: QuotaProviderObservation | None) -> None:
        self._observation = observation

    def observations(self) -> tuple[QuotaProviderObservation, ...]:
        if self._observation is None:
            return ()
        return (self._observation,)

    def snapshot_for_pool(self, pool):
        if self._observation is not None and self._observation.quota_pool_id == pool:
            return self._observation.snapshot
        return None


class _FakeExecutionSupervisor:
    """Mirrors ``OwnerDispatchExecutor.execution_supervisor`` semantics."""

    def __init__(self) -> None:
        self._live: set[str] = set()

    def register(self, task_id: str) -> None:
        self._live.add(task_id)

    def get(self, task_id: str):
        return True if task_id in self._live else None


class _RecordingExecutor:
    """DispatchExecutor stand-in that records ``execute`` invocations.

    ``simulate_live`` mirrors the real executor registering its live
    execution: with it on, a dispatched task looks alive and the tick's
    recovery path never re-enters. Crash-boundary tests turn it off to
    emulate an executor thread that died before starting a worker.
    """

    def __init__(self, *, simulate_live: bool = True) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._simulate_live = simulate_live
        self.execution_supervisor = _FakeExecutionSupervisor()

    def execute(self, request_id: str) -> None:
        with self._lock:
            self.calls.append(request_id)
        if self._simulate_live:
            self.execution_supervisor.register("task-1")

    @property
    def unique_calls(self) -> list[str]:
        with self._lock:
            return list(self.calls)


def _observation(
    now: datetime = OBSERVED_AT, *, remaining: float = 0.42
) -> QuotaProviderObservation:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=now,
        confidence=EvidenceConfidence.EXACT,
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18_000,
        remaining_fraction=remaining,
        used_fraction=1 - remaining,
        window_started_at=now - timedelta(hours=1),
        reset_at=now + timedelta(hours=4),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        quota_pool_id="pool", observed_at=now, source=source,
        state=QuotaState.AVAILABLE, confidence=EvidenceConfidence.EXACT,
        windows=(window,),
    )
    return QuotaProviderObservation(
        provider_id="minimax",
        quota_pool_id="pool",
        plan_id="plan",
        observation_state=QuotaObservationState.OBSERVED,
        confidence="EXACT",
        snapshot=snapshot,
        collector_available=True,
        last_refresh_status="SUCCESS",
        last_refresh_at=now.isoformat(),
        failure_reason=None,
    )


@dataclass
class _Env:
    service: ControlPlaneService
    store: SafetyKernelStore
    shadow: ShadowEvidenceJournal
    executor: _RecordingExecutor | None
    settings: SchedulingSettings
    quota_journal: QuotaAvailabilityJournal
    project_id: str
    task_id: str = "task-1"

    def tick(self, now: datetime) -> None:
        self.service.supervised_auto_tick(now)


def _env(
    tmp_path: Path,
    *,
    mode: str = "SUPERVISED_AUTO",
    unattended: bool = True,
    grace_seconds: int = 120,
    supervised_allowed: bool = True,
    with_executor: bool = True,
    quota_observation: _FakeQuotaRefresh | None = None,
    evidence_verified: bool = True,
) -> _Env:
    store = SafetyKernelStore(tmp_path / "safety.db")
    repo = _make_repo(tmp_path / "project")
    from personal_ai_orchestrator.control_api import ControlPlaneService as _Svc
    from tests.test_dispatch_initiator import _git

    head = _git(repo, "rev-parse", "HEAD")
    store.register_project(
        project_id="p1",
        display_name="Fixture",
        canonical_repo_root=str(repo),
        git_root=str(repo),
        default_branch="main",
        last_known_head=head,
    )
    store.set_project_settings(
        "p1",
        supervised_auto_allowed=supervised_allowed,
        unattended_allowed=unattended,
        grace_seconds=grace_seconds,
    )
    store.submit_task(
        task_id="task-1",
        request_id="submit-1",
        project_id="p1",
        intent="fix bug",
        base_sha=head,
    )
    store.transition_task("task-1", TaskState.READY)

    quota_journal = QuotaAvailabilityJournal(tmp_path)
    quota_journal.save(
        observe_success(
            None,
            execution_target_id="m3-sub",
            provider_id="minimax",
            quota_pool_id="pool",
            observed_at=OBSERVED_AT,
        )
    )
    from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal

    evidence_journal = ExecutionEvidenceJournal(tmp_path)
    if evidence_verified:
        evidence_journal.append(
            build_execution_evidence(
                provider_id="minimax",
                execution_target_id="m3-sub",
                model_sku_id="m3",
                observed_at=NOW,
                result=ExecutionVerificationOutcome.VERIFIED,
                reason_code="TEST_REAL_WORKER",
            )
        )
    settings = SchedulingSettings(tmp_path / "scheduling.json")
    settings.set_mode(mode)
    shadow = ShadowEvidenceJournal(tmp_path / "shadow-root")
    executor = _RecordingExecutor() if with_executor else None
    service = _Svc(
        registry=_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=quota_journal,
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json", initial=True
        ),
        scheduling_settings=settings,
        execution_evidence_journal=evidence_journal,
        quota_refresh_service=quota_observation
        if quota_observation is not None
        else _FakeQuotaRefresh(_observation()),
        dispatch_executor=executor,
        shadow_journal=shadow,
        catalog_snapshot_id="catalog-test",
    )
    return _Env(
        service=service,
        store=store,
        shadow=shadow,
        executor=executor,
        settings=settings,
        quota_journal=quota_journal,
        project_id="p1",
    )


def _audit_types(env: _Env, task_id: str = "task-1") -> list[str]:
    return [
        event["event_type"]
        for event in env.store.audit_events(task_id)
    ]


# ---------------------------------------------------------------------------
# 1–4: skip gates (no side effect, deduped reason)
# ---------------------------------------------------------------------------


def test_manual_mode_skips_planning(tmp_path) -> None:
    env = _env(tmp_path, mode="MANUAL")
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert task.auto_reason is None


def test_project_opt_out_skips_planning(tmp_path) -> None:
    env = _env(tmp_path, supervised_allowed=False)
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_reason == "project_supervised_auto_disabled"
    # Dedup: second tick must not write a second AUTO_SKIPPED audit row.
    before = _audit_types(env).count("AUTO_SKIPPED")
    env.tick(NOW + timedelta(seconds=5))
    after = _audit_types(env).count("AUTO_SKIPPED")
    assert before == 1 and after == 1


def test_non_ready_task_is_not_planned(tmp_path) -> None:
    env = _env(tmp_path)
    # Move the task out of READY before the tick.
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, reason="owner blocked for test"
    )
    env.tick(NOW)
    assert env.store.get_task("task-1").state is TaskState.BLOCKED


def test_hard_gate_quota_unknown_skips(tmp_path) -> None:
    # No quota observation at all → candidate has no remaining fractions
    # and UNKNOWN availability → gate fails with no_admitted_target.
    env = _env(tmp_path, quota_observation=_FakeQuotaRefresh(None))
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_reason == "no_admitted_target"


def test_hard_gate_quota_state_not_auto_ok_skips(tmp_path) -> None:
    from personal_ai_orchestrator.quota_availability import observe_exhaustion

    env = _env(tmp_path)
    # Flip the availability journal to a cooldown-state blocker after env
    # construction (observe_exhaustion → EXHAUSTED_OBSERVED).
    env.quota_journal.save(
        observe_exhaustion(
            env.quota_journal.load("m3-sub"),
            execution_target_id="m3-sub",
            provider_id="minimax",
            quota_pool_id="pool",
            observed_at=NOW - timedelta(hours=1),
            sanitized_reason_code="TEST",
        )
    )
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    # The recommender only hard-eliminates EXHAUSTED/COOLDOWN; a
    # RECOVERY_PROBE_DUE projection passes admission — the tick's own
    # quota-state gate is what fails closed here.
    assert task.auto_reason is not None
    assert task.auto_reason.startswith("quota_state_")


def test_recovered_observed_quota_is_auto_admissible(tmp_path) -> None:
    """Observed exhaustion followed by a strong recovery may plan again.

    The recovery state remains explicit evidence (RECOVERED_OBSERVED); the
    autonomous gate must not require a second ordinary-success observation
    before resuming normal routing.
    """

    env = _env(tmp_path)
    baseline = env.quota_journal.load("m3-sub")
    exhausted = observe_exhaustion(
        baseline,
        execution_target_id="m3-sub",
        provider_id="minimax",
        quota_pool_id="pool",
        observed_at=NOW - timedelta(hours=2),
        sanitized_reason_code="TEST_EXHAUSTED",
    )
    due = mark_recovery_probe_due(
        exhausted,
        observed_at=NOW - timedelta(minutes=2),
    )
    recovered = observe_success(
        due,
        execution_target_id="m3-sub",
        provider_id="minimax",
        quota_pool_id="pool",
        observed_at=NOW - timedelta(minutes=1),
    )
    env.quota_journal.save(recovered)

    env.tick(NOW)

    task = env.store.get_task("task-1")
    assert recovered.state.value == "RECOVERED_OBSERVED"
    assert task.state is TaskState.AUTO_GRACE
    assert task.auto_decision_id is not None
    assert "AUTO_PLANNED" in _audit_types(env)


def test_hard_gate_evidence_stale_skips(tmp_path) -> None:
    env = _env(tmp_path, evidence_verified=False)
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_reason == "no_admitted_target"


def test_owner_execution_disabled_skips(tmp_path) -> None:
    env = _env(tmp_path)
    env.service.owner_execution.set_enabled(False)
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_reason == "owner_initiated_execution_disabled"


def test_task_policy_manual_skips(tmp_path) -> None:
    env = _env(tmp_path)
    env.store.force_task_scheduling_policy("task-1", scheduling_policy="MANUAL")
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_reason == "task_policy_manual"


# ---------------------------------------------------------------------------
# 5–7: successful planning + frozen decision contract
# ---------------------------------------------------------------------------


def _plan(env: _Env) -> None:
    env.tick(NOW)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.AUTO_GRACE
    assert task.auto_decision_id is not None


def test_successful_planning_reaches_auto_grace(tmp_path) -> None:
    env = _env(tmp_path)
    _plan(env)
    task = env.store.get_task("task-1")
    assert task.state is TaskState.AUTO_GRACE
    assert task.auto_decision_id == supervised_auto_decision_id(
        env.store.get_task("task-1")
    ) or task.auto_decision_id.startswith("auto-task-1-")
    assert "AUTO_PLANNED" in _audit_types(env)


def test_frozen_decision_reused_across_ticks(tmp_path) -> None:
    env = _env(tmp_path)
    _plan(env)
    first = env.store.routing_decision_by_request_id(
        supervised_auto_routing_request_id(env.store.get_task("task-1").auto_decision_id)
    )
    assert first is not None
    payload_before = first["payload_json"]
    # Re-tick well within the grace window: the same frozen decision row,
    # unchanged, and no second routing decision for the task.
    env.tick(NOW + timedelta(seconds=5))
    env.tick(NOW + timedelta(seconds=10))
    rows = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE task_id='task-1'"
    ).fetchone()
    assert rows["n"] == 1
    again = env.store.routing_decision_by_request_id(first["request_id"])
    assert again["payload_json"] == payload_before


def test_routing_request_id_is_the_stable_supervised_auto_id(tmp_path) -> None:
    env = _env(tmp_path)
    _plan(env)
    task = env.store.get_task("task-1")
    row = env.store.routing_decision_by_request_id(
        supervised_auto_routing_request_id(task.auto_decision_id)
    )
    assert row is not None
    assert row["request_id"] == f"supervised-auto-{task.auto_decision_id}"
    # request_id stays UNIQUE — one decision per cycle id.
    dup = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE request_id=?",
        (row["request_id"],),
    ).fetchone()
    assert dup["n"] == 1


def test_frozen_decision_survives_crash_before_transition(tmp_path) -> None:
    """Crash boundary A: decision persisted, task still READY → the next
    tick reconciles onto the SAME decision instead of writing a second."""

    env = _env(tmp_path)
    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    task = env.store.get_task("task-1")
    request_id = supervised_auto_routing_request_id(
        supervised_auto_decision_id(task)
    )
    decision = step._freeze_decision(  # noqa: SLF001 — test hook
        task=task,
        request_id=request_id,
        target_id="m3-sub",
        decision_reason="test frozen crash boundary",
        now=NOW,
    )
    assert decision is not None
    # Next tick must complete the lifecycle on the frozen decision.
    env.tick(NOW)
    after = env.store.get_task("task-1")
    assert after.state is TaskState.AUTO_GRACE
    assert after.auto_decision_id is not None
    rows = env.store.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE task_id='task-1'"
    ).fetchone()
    assert rows["n"] == 1


# ---------------------------------------------------------------------------
# 8–11: grace semantics + dispatch exactly once
# ---------------------------------------------------------------------------


def test_unattended_project_gets_deadline_immediately(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    assert task.auto_grace_deadline_at is not None
    assert task.auto_acked_at is None
    assert datetime.fromisoformat(task.auto_grace_deadline_at) == NOW + timedelta(
        seconds=300
    )


def test_attended_project_waits_for_ack(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    assert task.auto_grace_deadline_at is None
    # No amount of ticking dispatches an unacked attended task.
    env.tick(NOW + timedelta(hours=1))
    env.tick(NOW + timedelta(hours=2))
    assert env.store.get_task("task-1").state is TaskState.AUTO_GRACE
    assert env.executor is not None and env.executor.unique_calls == []


def test_ack_sets_deadline_from_ack_time(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    ack_at = NOW + timedelta(minutes=10)
    env.store.ack_auto_grace(
        "task-1",
        acked_at=ack_at.isoformat(),
        grace_deadline=(ack_at + timedelta(seconds=300)).isoformat(),
        expected_version=env.store.get_task("task-1").state_version,
    )
    task = env.store.get_task("task-1")
    assert task.auto_acked_at == ack_at.isoformat()
    # Before the deadline: no dispatch. After: exactly one.
    env.tick(ack_at + timedelta(seconds=299))
    assert env.executor is not None and env.executor.unique_calls == []
    env.tick(ack_at + timedelta(seconds=301))
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1


def test_ack_retry_does_not_extend_deadline(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    first = env.store.get_task("task-1")
    ack_at = NOW + timedelta(minutes=10)
    deadline = ack_at + timedelta(seconds=300)
    env.store.ack_auto_grace(
        "task-1",
        acked_at=ack_at.isoformat(),
        grace_deadline=deadline.isoformat(),
        expected_version=first.state_version,
    )
    # Retry 30s later with the NEW (stale) version — idempotent path
    # returns the task unchanged; the deadline keeps its original value.
    second = env.store.get_task("task-1")
    result = env.store.ack_auto_grace(
        "task-1",
        acked_at=(ack_at + timedelta(seconds=30)).isoformat(),
        grace_deadline=(ack_at + timedelta(seconds=330)).isoformat(),
        expected_version=second.state_version,
    )
    assert result.auto_acked_at == ack_at.isoformat()
    assert result.auto_grace_deadline_at == deadline.isoformat()
    # And the dispatch still fires at the ORIGINAL deadline.
    env.tick(deadline + timedelta(seconds=1))
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1


def test_grace_countdown_does_not_dispatch_early(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.tick(NOW + timedelta(seconds=299))
    assert env.store.get_task("task-1").state is TaskState.AUTO_GRACE
    assert env.executor is not None and env.executor.unique_calls == []


def test_grace_expiry_dispatches_exactly_once(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    expiry = NOW + timedelta(seconds=301)
    env.tick(expiry)
    env.tick(expiry + timedelta(seconds=5))
    env.tick(expiry + timedelta(seconds=10))
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1
    # One durable dispatch row, authority SUPERVISED_AUTO, stable id.
    task = env.store.get_task("task-1")
    row = env.store.get_owner_dispatch_by_request_id(
        supervised_auto_dispatch_request_id(task.auto_decision_id)
    )
    assert row.authority == "SUPERVISED_AUTO"
    assert row.execution_target_id == "m3-sub"
    dispatched = [
        e
        for e in env.store.audit_events("task-1")
        if e["event_type"] == "AUTO_DISPATCHED"
    ]
    assert len(dispatched) == 1
    assert dispatched[0]["payload"]["trigger"] == "grace_expired"


# ---------------------------------------------------------------------------
# 12–13: switch lease interplay
# ---------------------------------------------------------------------------


def _insert_lease(
    env: _Env, *, expires_at_epoch: int, decision_id: str = "lease-decision-1"
) -> None:
    """Insert a switch-lease row directly for gate testing.

    ``SwitchLeaseAuthority.authorize`` requires a READY/RUNNING task, so a
    lease coexisting with AUTO_GRACE is constructed at the schema level —
    the point under test is the tick's ``has_active_lease`` gate at
    dispatch time, not the authorize path.
    """

    import json

    from personal_ai_orchestrator.opencode_contract import (
        ModelRef,
        RoutingDecision,
        RoutingMode,
    )

    decision = RoutingDecision(
        decision_id=decision_id,
        request_id=f"lease-request-{decision_id}",
        mode=RoutingMode.ACTIVE,
        switch_requested=True,
        selected_model=ModelRef(provider_id="minimax", model_id="m3"),
        task_state_version=env.store.get_task("task-1").state_version,
    )
    env.store.record_routing_decision(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="task-1",
        payload=json.loads(decision.model_dump_json()),
    )
    env.store.connection.execute(
        "INSERT INTO switch_leases VALUES(?,?,?,?,?,?,?,?,?,NULL)",
        (
            f"switch-{decision_id}",
            decision_id,
            decision.request_id,
            "task-1",
            env.store.get_task("task-1").state_version,
            "session-lease",
            "AUTHORIZED",
            expires_at_epoch,
            datetime.now(UTC).isoformat(),
        ),
    )


def test_active_lease_blocks_dispatch(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # A lease that is still live at the (fake) dispatch moment.
    _insert_lease(env, expires_at_epoch=int((NOW + timedelta(hours=2)).timestamp()))
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.AUTO_GRACE


def test_expired_lease_does_not_block_dispatch(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # A lease whose expiry is long past at the (fake) dispatch moment.
    _insert_lease(
        env,
        decision_id="lease-decision-2",
        expires_at_epoch=int((NOW - timedelta(hours=1)).timestamp()),
    )
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1


# ---------------------------------------------------------------------------
# 14–16: fail-closed aborts
# ---------------------------------------------------------------------------


def test_mode_change_to_manual_aborts_auto_grace(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # Owner flips the mode via the settings handler (same-transaction abort).
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert task.auto_grace_deadline_at is None
    assert task.auto_acked_at is None
    # §23/§32: every auto column clears — the abort truth lives in the
    # audit trail, not on the active task row.
    assert task.auto_reason is None
    aborted = [
        e for e in env.store.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(e["payload"]["reason"] == "mode_changed" for e in aborted)
    # Pending shadow discarded.
    assert env.shadow.load_pending_all() == ()


def test_mode_change_aborts_before_dispatch_even_after_deadline(tmp_path) -> None:
    """TOCTOU: deadline already passed, mode flips MANUAL in the same
    window → NO DISPATCH, lifecycle aborted."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.service.update_scheduling_settings(
        {"default_scheduling_policy": "BALANCED", "mode": "MANUAL"}
    )
    env.tick(NOW + timedelta(seconds=301))
    assert env.executor is not None and env.executor.unique_calls == []
    assert env.store.get_task("task-1").state is TaskState.READY


def test_mode_revoked_while_daemon_down_aborts_on_next_tick(tmp_path) -> None:
    """Crash-recovery sweep: the mode file changed while no tick ran."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # Simulate the settings write happening outside the handler (e.g. the
    # abort write was lost): flip the persisted mode directly.
    env.settings.set_mode("MANUAL")
    env.tick(NOW + timedelta(seconds=5))
    assert env.store.get_task("task-1").state is TaskState.READY


def test_project_disable_aborts_auto_grace(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.service.set_project_supervised_auto_settings(
        "p1",
        {"supervised_auto_allowed": False, "unattended_allowed": True, "grace_seconds": 300},
    )
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    aborted = [
        e for e in env.store.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(e["payload"]["reason"] == "project_auto_disabled" for e in aborted)


def test_project_revoked_while_daemon_down_aborts_on_next_tick(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    env.store.set_project_settings(
        "p1", supervised_auto_allowed=False, unattended_allowed=True, grace_seconds=300
    )
    env.tick(NOW + timedelta(seconds=5))
    assert env.store.get_task("task-1").state is TaskState.READY


def test_unacked_timeout_after_24h_aborts(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    # The grace-entry timestamp is wall-clock (``updated_at``); the fake
    # NOW anchor runs 2h ahead of it. 20h after NOW the task is ~22h old
    # — still inside the 24h cap.
    env.tick(NOW + timedelta(hours=20))
    assert env.store.get_task("task-1").state is TaskState.AUTO_GRACE
    # 25h after NOW the task is ~27h old — past the cap: aborted back to
    # READY with the timeout reason + cleared metadata.
    env.tick(NOW + timedelta(hours=25))
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    aborted = [
        e for e in env.store.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(e["payload"]["reason"] == "unacked_timeout" for e in aborted)
    assert env.shadow.load_pending_all() == ()


# ---------------------------------------------------------------------------
# 17–24: recovery, idempotence, metadata
# ---------------------------------------------------------------------------


def test_auto_planned_crash_recovery_completes_grace(tmp_path) -> None:
    """Crash boundary between the two planning transitions: the task sits
    in AUTO_PLANNED; the next tick completes the promotion."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    task = env.store.get_task("task-1")
    planned = env.store.transition_task(
        "task-1",
        TaskState.AUTO_PLANNED,
        expected_version=task.state_version,
        auto_decision_id="auto-task-1-crash",
    )
    env.tick(NOW + timedelta(seconds=5))
    after = env.store.get_task("task-1")
    assert planned.state is TaskState.AUTO_PLANNED
    assert after.state is TaskState.AUTO_GRACE
    assert after.auto_decision_id == "auto-task-1-crash"


def test_reserved_dispatch_crash_recovery_reenters_executor_once(tmp_path) -> None:
    """Crash boundary C: dispatch RESERVED, the executor thread died
    before registering a live execution → the next tick re-enters the
    executor (bounded: one re-entry per tick, each re-entry idempotent at
    the durable ``start_dispatched_worker`` gate)."""

    env = _env(
        tmp_path,
        unattended=True,
        grace_seconds=300,
        with_executor=False,
    )
    env.executor = _RecordingExecutor(simulate_live=False)
    env.service.dispatch_executor = env.executor
    _plan(env)
    task = env.store.get_task("task-1")
    from personal_ai_orchestrator.dispatch_initiator import (
        initiate_owner_dispatch,
    )

    initiate_owner_dispatch(
        env.store,
        env.executor,
        task=task,
        request_id=supervised_auto_dispatch_request_id(task.auto_decision_id),
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
        project_provider=env.service.get_project,
        registry_provider=env.service._effective_registry,  # noqa: SLF001
        provider_registry_manager=None,
        runtime_available_provider=env.service._runtime_available,  # noqa: SLF001
        execution_evidence_journal=env.service.execution_evidence_journal,
        expected_state=TaskState.AUTO_GRACE,
    )
    assert env.executor is not None
    assert len(env.executor.unique_calls) == 1
    # The executor thread "died" (nothing live, no run row) → the tick's
    # recovery path re-enters exactly once per tick.
    env.tick(NOW + timedelta(seconds=301))
    assert len(env.executor.unique_calls) == 2
    env.tick(NOW + timedelta(seconds=306))
    assert len(env.executor.unique_calls) == 3


def test_restart_round_trip_is_idempotent(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task_before = env.store.get_task("task-1")
    # Simulate a daemon restart: fresh store on the same SQLite file,
    # fresh service, same settings file.
    env.store.close()
    store2 = SafetyKernelStore(tmp_path / "safety.db")
    service2 = ControlPlaneService(
        registry=_registry(),
        store=store2,
        runtime_availability={"m3-sub": True},
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling.json"),
        shadow_journal=ShadowEvidenceJournal(tmp_path / "shadow-root"),
        catalog_snapshot_id="catalog-test",
    )
    service2.supervised_auto_tick(NOW + timedelta(seconds=5))
    task_after = store2.get_task("task-1")
    assert task_after.state is TaskState.AUTO_GRACE
    assert task_after.auto_decision_id == task_before.auto_decision_id
    assert task_after.auto_grace_deadline_at == task_before.auto_grace_deadline_at
    store2.close()


def test_pre_worker_blocked_dispatch_returns_task_to_ready(tmp_path) -> None:
    """§3.4 step 4 fail branch: the executor's pre-worker BLOCKED outcome
    (e.g. admission failed) returns the task to READY + discards the
    pending shadow, with the sanitized reason audited."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    from personal_ai_orchestrator.control_api import ControlPlaneError
    from personal_ai_orchestrator.dispatch_initiator import (
        initiate_owner_dispatch,
    )

    request_id = supervised_auto_dispatch_request_id(task.auto_decision_id)
    # Force a validation failure AFTER reservation: stale version.
    try:
        initiate_owner_dispatch(
            env.store,
            env.executor,
            task=task,
            request_id=request_id,
            task_state_version=task.state_version + 99,
            execution_target_id="m3-sub",
            authority="SUPERVISED_AUTO",
            project_provider=env.service.get_project,
            registry_provider=env.service._effective_registry,  # noqa: SLF001
            provider_registry_manager=None,
            runtime_available_provider=env.service._runtime_available,  # noqa: SLF001
            execution_evidence_journal=env.service.execution_evidence_journal,
            expected_state=TaskState.AUTO_GRACE,
        )
    except ControlPlaneError:
        pass
    # The dispatch row is BLOCKED with no run; the tick reconciles the
    # task back to READY and closes the lifecycle.
    env.tick(NOW + timedelta(seconds=301))
    after = env.store.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert env.shadow.load_pending_all() == ()


def test_terminal_state_sweep_clears_stale_metadata(tmp_path) -> None:
    """Crash boundary D: the executor died after the task reached a
    terminal state but before clearing the metadata → the sweep closes."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    # Drive the lifecycle to a post-RUNNING terminal state manually
    # (simulating a completed run whose executor crashed at close-out).
    running = env.store.transition_task(
        "task-1", TaskState.RUNNING, expected_version=task.state_version
    )
    finished = env.store.transition_task(
        "task-1", TaskState.WORKER_FINISHED, expected_version=running.state_version
    )
    verifying = env.store.transition_task(
        "task-1", TaskState.VERIFYING, expected_version=finished.state_version
    )
    env.store.transition_task(
        "task-1", TaskState.VERIFIED, expected_version=verifying.state_version
    )
    assert env.store.get_task("task-1").auto_decision_id is not None
    env.tick(NOW + timedelta(seconds=5))
    after = env.store.get_task("task-1")
    assert after.state is TaskState.VERIFIED
    assert after.auto_decision_id is None
    assert after.auto_grace_deadline_at is None
    assert after.auto_acked_at is None
    assert after.auto_reason is None


def test_running_task_is_never_touched_by_sweeps(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    running = env.store.transition_task(
        "task-1", TaskState.RUNNING, expected_version=task.state_version
    )
    env.tick(NOW + timedelta(seconds=5))
    after = env.store.get_task("task-1")
    assert after.state is TaskState.RUNNING
    assert after.state_version == running.state_version
    assert after.auto_decision_id is not None  # executor owns the finalize


# ---------------------------------------------------------------------------
# supervisor registration (§19)
# ---------------------------------------------------------------------------


def test_supervisor_registration_control_only_excluded() -> None:
    from personal_ai_orchestrator.daemon_supervisor import build_default_supervisor

    # Without the step: heartbeat only (the control-only contract).
    supervisor = build_default_supervisor()
    names = [s.name for s in supervisor.snapshot().steps]
    assert SUPERVISED_AUTO_STEP_NAME not in names
    assert "heartbeat" in names
    # With the step: registered under the public name.
    supervisor2 = build_default_supervisor(supervised_auto_step=lambda now: None)
    names2 = [s.name for s in supervisor2.snapshot().steps]
    assert SUPERVISED_AUTO_STEP_NAME in names2
