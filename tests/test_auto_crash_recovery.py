"""M1 WP5a-2 closeout — crash-consistency of the AUTO lifecycle exits.

Independent-review merge blocker: the abort/veto/pre-worker closeouts
were multi-stage writes, so a crash between durable commits could leave
``state == READY`` with active-looking auto metadata and/or an orphan
pending shadow. These tests pin the repaired contract with deterministic
fault injection (a journal that raises, direct store-level commits, and
fresh-connection restarts) — never sleep, never wall-clock races.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_ai_orchestrator.control_api import ControlPlaneService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduling_settings import SchedulingSettings
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal
from personal_ai_orchestrator.supervised_auto_step import (
    drain_auto_shadow_cleanup_outbox,
    supervised_auto_decision_id,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from tests.test_supervised_auto_step import NOW, _env, _plan, _registry


class _FailingJournal:
    """Shadow journal stand-in whose discard always fails.

    Simulates an unavailable/malformed journal at the exact moment the
    lifecycle close commits — the SQLite authority must already be
    owner-safe and the cleanup must be recoverable later.
    """

    def discard_pending(self, pending_id: str) -> bool:
        raise OSError("journal unavailable (fault injection)")


def _write_dummy_pending(shadow: ShadowEvidenceJournal, pending_id: str) -> Path:
    path = shadow.pending_path_for(pending_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def _auto_task(store: SafetyKernelStore, *, task_id: str = "task-1") -> None:
    store.submit_task(
        task_id=task_id,
        request_id=f"submit-{task_id}",
        project_id=None,
        intent="crash fixture",
    )
    store.transition_task(task_id, TaskState.READY)
    store.transition_task(
        task_id,
        TaskState.AUTO_PLANNED,
        auto_decision_id=f"auto-{task_id}-v1",
        auto_reason="AUTO_PLANNED target=x",
    )
    store.transition_task(
        task_id,
        TaskState.AUTO_GRACE,
        auto_grace_deadline_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )


def _outbox_rows(store: SafetyKernelStore) -> list[tuple]:
    return [
        tuple(row)
        for row in store.connection.execute(
            "SELECT pending_id, task_id, reason, completed_at "
            "FROM auto_shadow_cleanup_outbox ORDER BY pending_id"
        ).fetchall()
    ]


def _restart_service(
    tmp_path: Path,
    *,
    executor,
    shadow,
    quota_observed_at: datetime = NOW,
) -> tuple[SafetyKernelStore, ControlPlaneService]:
    """Fresh store + service on the same durable files (daemon restart)."""

    from personal_ai_orchestrator.execution_evidence import (
        ExecutionEvidenceJournal,
        ExecutionVerificationOutcome,
        build_execution_evidence,
    )
    from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
    from tests.test_supervised_auto_step import OBSERVED_AT, _FakeQuotaRefresh, _observation

    store = SafetyKernelStore(tmp_path / "safety.db")
    evidence_journal = ExecutionEvidenceJournal(tmp_path)
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
    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityJournal,
        observe_success,
    )

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
    service = ControlPlaneService(
        registry=_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=quota_journal,
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json", initial=True
        ),
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling.json"),
        execution_evidence_journal=evidence_journal,
        quota_refresh_service=_FakeQuotaRefresh(_observation(now=quota_observed_at)),
        dispatch_executor=executor,
        shadow_journal=shadow,
        catalog_snapshot_id="catalog-test",
    )
    return store, service


# ---------------------------------------------------------------------------
# §19-A: veto crash window — atomic COMMIT lands, cleanup "lost"
# ---------------------------------------------------------------------------


def test_veto_crash_after_commit_leaves_owner_safe_sqlite(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    old_decision_id = task.auto_decision_id
    assert old_decision_id is not None

    # Exactly what _veto_auto_lifecycle does, MINUS the post-commit drain
    # — the process "dies" between the SQLite COMMIT and the discard.
    env.store.abort_auto_lifecycle(
        "task-1",
        expected_version=task.state_version,
        reason="owner_veto",
        event_type="AUTO_VETOED",
        request_id="veto-crash-1",
        target="m3-sub",
        force_manual=True,
    )

    # Immediately after the DB reopen the row is already owner-safe —
    # SQLite authority correctness never depended on the shadow fs.
    env.store.close()
    store2 = SafetyKernelStore(tmp_path / "safety.db")
    task2 = store2.get_task("task-1")
    assert task2.state is TaskState.READY
    assert task2.auto_decision_id is None
    assert task2.auto_grace_deadline_at is None
    assert task2.auto_acked_at is None
    assert task2.auto_reason is None
    row = store2.connection.execute(
        "SELECT scheduling_policy FROM tasks WHERE task_id='task-1'"
    ).fetchone()
    assert row["scheduling_policy"] == "MANUAL"

    # The outbox row survived the crash: the discard intent is durable.
    open_rows = store2.pending_shadow_cleanups()
    assert [r["pending_id"] for r in open_rows] == [old_decision_id]

    # Reconciliation drains the old pending and never re-plans (MANUAL).
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    assert old_decision_id in [p.pending_id for p in shadow2.load_pending_all()]
    drain_auto_shadow_cleanup_outbox(store2, shadow2)
    assert shadow2.load_pending_all() == ()
    assert store2.pending_shadow_cleanups() == ()
    store2.close()


# ---------------------------------------------------------------------------
# §19-B: mode change crash window
# ---------------------------------------------------------------------------


def test_mode_change_crash_before_abort_recovers_on_restart(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    old_decision_id = env.store.get_task("task-1").auto_decision_id
    assert old_decision_id is not None

    # The mode JSON persisted MANUAL but the handler crashed before any
    # task abort (direct file write bypasses the inline handler aborts).
    env.settings.set_mode("MANUAL")
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=None)
    service2.supervised_auto_tick(NOW + timedelta(seconds=301))

    task = store2.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert task.auto_grace_deadline_at is None
    assert task.auto_acked_at is None
    assert task.auto_reason is None
    aborted = [
        e for e in store2.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(e["payload"]["reason"] == "mode_changed" for e in aborted)
    # No dispatch, no worker — even though the grace deadline had passed.
    assert executor.unique_calls == []
    store2.close()


# ---------------------------------------------------------------------------
# §19-C: project disable crash window
# ---------------------------------------------------------------------------


def test_project_disable_crash_before_abort_recovers_on_restart(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    # Project flag flipped False durably; handler crashed before aborts.
    env.store.set_project_settings(
        "p1", supervised_auto_allowed=False, unattended_allowed=True, grace_seconds=300
    )
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=shadow2)
    assert service2.supervised_auto_tick is not None
    service2.supervised_auto_tick(NOW + timedelta(seconds=301))

    task = store2.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert task.auto_grace_deadline_at is None
    assert task.auto_acked_at is None
    aborted = [
        e for e in store2.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(e["payload"]["reason"] == "project_auto_disabled" for e in aborted)
    # ``auto_reason`` may carry the same tick's legal skip hint (the
    # planning scan runs after the abort and records WHY it will not
    # re-plan) — that is a hint, never lifecycle metadata.
    assert task.auto_reason in (None, "project_supervised_auto_disabled")
    # Pending eventually discarded via the tick's drain.
    assert shadow2.load_pending_all() == ()
    assert store2.pending_shadow_cleanups() == ()
    assert executor.unique_calls == []
    store2.close()


# ---------------------------------------------------------------------------
# §19-D: unacked-timeout crash window (journal unavailable at abort time)
# ---------------------------------------------------------------------------


def test_unacked_timeout_crash_recovers_and_new_cycle_is_fresh(tmp_path) -> None:
    env = _env(tmp_path, unattended=False, grace_seconds=300)
    _plan(env)
    old_decision_id = env.store.get_task("task-1").auto_decision_id
    assert old_decision_id is not None
    assert env.shadow.load_pending_all()

    # The journal is unavailable when the timeout abort commits: the
    # drain fails, the outbox row stays open, the pending file survives.
    env.service.shadow_journal = _FailingJournal()
    env.tick(NOW + timedelta(hours=25))
    task = env.store.get_task("task-1")
    assert task.state is TaskState.READY
    # SQLite authority already owner-safe despite the journal failure.
    assert task.auto_decision_id is None
    assert task.auto_grace_deadline_at is None
    assert task.auto_acked_at is None
    assert task.auto_reason is None
    assert env.store.pending_shadow_cleanups()
    assert old_decision_id in [p.pending_id for p in env.shadow.load_pending_all()]

    # Restart with a healthy journal: the old lifecycle is cleaned
    # exactly once and any new cycle derives a NEW decision id. The
    # restart tick runs near NOW so the fresh unacked cycle (whose
    # ``updated_at`` anchor is wall-clock, ~2h behind the fake NOW)
    # is not itself unacked-timeout'd by the fake/wall gap.
    env.store.close()
    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    restarted_at = NOW + timedelta(minutes=30)
    # This test isolates lifecycle recovery: the new cycle has a fresh quota
    # fixture, rather than bypassing the production observation-age gate.
    store2, service2 = _restart_service(
        tmp_path, executor=executor, shadow=shadow2, quota_observed_at=restarted_at,
    )
    service2.supervised_auto_tick(restarted_at)

    task2 = store2.get_task("task-1")
    new_decision_id = task2.auto_decision_id
    assert new_decision_id is not None  # re-planned (fresh cycle, allowed)
    assert new_decision_id != old_decision_id
    pending_ids = [p.pending_id for p in shadow2.load_pending_all()]
    assert old_decision_id not in pending_ids
    assert new_decision_id in pending_ids
    # The old pending id is never reused: one decision row per id.
    rows = store2.connection.execute(
        "SELECT COUNT(*) AS n FROM routing_decisions WHERE task_id='task-1'"
    ).fetchall()
    assert rows[0]["n"] == 2
    assert executor.unique_calls == []  # attended → no dispatch without ack
    store2.close()


# ---------------------------------------------------------------------------
# §19-E: pre-worker BLOCKED closeout crash window
# ---------------------------------------------------------------------------


def test_pre_worker_blocked_closeout_crash_recovers_atomically(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    task = env.store.get_task("task-1")
    old_decision_id = task.auto_decision_id
    # Admission fails before any worker: an EXACT current reservation
    # (round 7: correct live version + frozen target "m3-sub") whose
    # post-reservation validation fails — the same BLOCKED audit row
    # ``initiate_owner_dispatch`` writes via ``mark_owner_dispatch_blocked``
    # — then the executor's pre-worker path parks the task in BLOCKED.
    request_id = f"supervised-auto-dispatch-{old_decision_id}"
    env.store.reserve_owner_dispatch(
        dispatch_id=f"owner-dispatch-{request_id}",
        request_id=request_id,
        task_id="task-1",
        task_state_version=task.state_version,
        execution_target_id="m3-sub",
        authority="SUPERVISED_AUTO",
    )
    env.store.mark_owner_dispatch_blocked(
        request_id,
        failure_code="QUOTA_ADMISSION_FAILED",
        failure_reason="quota admission blocked the billable launch",
    )
    env.store.transition_task(
        "task-1", TaskState.BLOCKED, expected_version=task.state_version
    )
    # Process dies before the reconciliation tick.
    env.store.close()

    from tests.test_supervised_auto_step import _RecordingExecutor

    executor = _RecordingExecutor()
    shadow2 = ShadowEvidenceJournal(tmp_path / "shadow-root")
    store2, service2 = _restart_service(tmp_path, executor=executor, shadow=shadow2)
    service2.supervised_auto_tick(NOW + timedelta(seconds=301))

    after = store2.get_task("task-1")
    assert after.state is TaskState.READY
    assert after.auto_decision_id is None
    assert after.auto_grace_deadline_at is None
    assert after.auto_acked_at is None
    assert after.auto_reason is None
    aborted = [
        e for e in store2.audit_events("task-1") if e["event_type"] == "AUTO_ABORTED"
    ]
    assert any(
        e["payload"]["reason"].startswith("admission_failed:") for e in aborted
    )
    assert shadow2.load_pending_all() == ()
    assert executor.unique_calls == []
    store2.close()


# ---------------------------------------------------------------------------
# §19-F: legacy READY + stale auto metadata (old-build crash residue)
# ---------------------------------------------------------------------------


def test_legacy_ready_stale_metadata_is_recovered(tmp_path) -> None:
    env = _env(tmp_path, unattended=True, grace_seconds=300)
    _plan(env)
    old_decision_id = env.store.get_task("task-1").auto_decision_id
    # Rewind the row into the OLD build's crash-window state: READY with
    # all four lifecycle columns still set and the pending still on disk.
    stale_deadline = (NOW + timedelta(seconds=300)).isoformat()
    env.store.connection.execute(
        """
        UPDATE tasks SET state='READY',
            auto_grace_deadline_at=?, auto_acked_at=NULL,
            auto_reason='AUTO_ABORTED reason=mode_changed'
        WHERE task_id='task-1'
        """,
        (stale_deadline,),
    )
    assert env.shadow.load_pending_all()

    env.tick(NOW + timedelta(seconds=5))
    task = env.store.get_task("task-1")
    recovered = [
        e
        for e in env.store.audit_events("task-1")
        if e["event_type"] == "AUTO_METADATA_RECOVERED"
    ]
    assert recovered
    assert (
        recovered[0]["payload"]["reason"] == "ready_state_stale_auto_metadata"
    )
    assert recovered[0]["payload"]["cleared_decision_id"] == old_decision_id
    # The old lifecycle is gone: no old pending, outbox drained.
    pending_ids = [p.pending_id for p in env.shadow.load_pending_all()]
    assert old_decision_id not in pending_ids
    assert env.store.pending_shadow_cleanups() == ()
    # The same tick may legally start a NEW cycle — with a fresh id.
    if task.auto_decision_id is not None:
        assert task.auto_decision_id != old_decision_id
        assert task.state is TaskState.AUTO_GRACE


def test_ready_with_only_routing_decision_is_not_mistaken_as_stale(tmp_path) -> None:
    """Crash boundary A (frozen decision persisted, task still READY) is
    legal and must NOT be recovered/cleared — only the row's own
    lifecycle metadata marks staleness."""

    env = _env(tmp_path, unattended=True, grace_seconds=300)
    step = env.service._supervised_auto_step()  # noqa: SLF001 — test hook
    task = env.store.get_task("task-1")
    from personal_ai_orchestrator.supervised_auto_step import (
        supervised_auto_routing_request_id,
    )

    decision = step._freeze_decision(  # noqa: SLF001 — test hook
        task=task,
        request_id=supervised_auto_routing_request_id(
            supervised_auto_decision_id(task)
        ),
        target_id="m3-sub",
        decision_reason="crash boundary A",
        now=NOW,
    )
    assert decision is not None
    env.tick(NOW)
    after = env.store.get_task("task-1")
    # The frozen decision was reused (not a second decision row) and the
    # lifecycle advanced normally — no AUTO_METADATA_RECOVERED fired.
    assert after.state is TaskState.AUTO_GRACE
    assert after.auto_decision_id == supervised_auto_decision_id(task)
    assert not [
        e
        for e in env.store.audit_events("task-1")
        if e["event_type"] == "AUTO_METADATA_RECOVERED"
    ]


# ---------------------------------------------------------------------------
# §20: the shadow-cleanup outbox itself
# ---------------------------------------------------------------------------


def _outbox_fixture(tmp_path: Path):
    store = SafetyKernelStore(tmp_path / "outbox.db")
    shadow = ShadowEvidenceJournal(tmp_path / "shadow-root")
    _auto_task(store)
    return store, shadow


def test_outbox_enqueue_is_idempotent(tmp_path) -> None:
    store, _shadow = _outbox_fixture(tmp_path)
    store.abort_auto_lifecycle("task-1", reason="mode_changed")
    # A duplicate intent (recovery replay / concurrent writer) is a no-op.
    store.connection.execute(
        """
        INSERT OR IGNORE INTO auto_shadow_cleanup_outbox
            (pending_id, task_id, reason, created_at)
        VALUES('auto-task-1-v1', 'task-1', 'duplicate', '2026-01-01T00:00:00')
        """
    )
    rows = _outbox_rows(store)
    assert len(rows) == 1
    assert rows[0][0] == "auto-task-1-v1"
    assert rows[0][3] is None  # still open
    store.close()


def test_outbox_crash_after_commit_before_discard(tmp_path) -> None:
    store, shadow = _outbox_fixture(tmp_path)
    pending_path = _write_dummy_pending(shadow, "auto-task-1-v1")
    assert pending_path.exists()

    store.abort_auto_lifecycle("task-1", reason="mode_changed")
    # "Crash": no drain ran. SQLite is safe; the intent is durable.
    task = store.get_task("task-1")
    assert task.state is TaskState.READY and task.auto_decision_id is None
    assert pending_path.exists()
    assert [r["pending_id"] for r in store.pending_shadow_cleanups()] == [
        "auto-task-1-v1"
    ]

    # Restart: the incomplete row is loaded and the drain completes it.
    store.close()
    store2 = SafetyKernelStore(tmp_path / "outbox.db")
    assert [r["pending_id"] for r in store2.pending_shadow_cleanups()] == [
        "auto-task-1-v1"
    ]
    completed = drain_auto_shadow_cleanup_outbox(store2, shadow)
    assert completed == 1
    assert not pending_path.exists()
    assert store2.pending_shadow_cleanups() == ()
    store2.close()


def test_outbox_crash_after_discard_before_completed_marker(tmp_path) -> None:
    store, shadow = _outbox_fixture(tmp_path)
    pending_path = _write_dummy_pending(shadow, "auto-task-1-v1")
    store.abort_auto_lifecycle("task-1", reason="mode_changed")
    # "Crash" between the successful discard and the completed marker.
    assert shadow.discard_pending("auto-task-1-v1") is True
    assert not pending_path.exists()
    assert store.pending_shadow_cleanups()

    # Restart drain: the absent pending is a no-op success; the row is
    # then marked completed and a second drain is harmless.
    completed = drain_auto_shadow_cleanup_outbox(store, shadow)
    assert completed == 1
    assert store.pending_shadow_cleanups() == ()
    assert drain_auto_shadow_cleanup_outbox(store, shadow) == 0
    store.close()


def test_outbox_nonexistent_pending_is_successful_cleanup(tmp_path) -> None:
    store, shadow = _outbox_fixture(tmp_path)
    # No pending file was ever written; the intent still completes.
    store.abort_auto_lifecycle("task-1", reason="mode_changed")
    assert drain_auto_shadow_cleanup_outbox(store, shadow) == 1
    assert store.pending_shadow_cleanups() == ()
    # Completed rows are never reprocessed forever: the open query is
    # filtered on completed_at IS NULL.
    assert drain_auto_shadow_cleanup_outbox(store, shadow) == 0
    store.close()


def test_outbox_unavailable_journal_never_rolls_back_sqlite(tmp_path) -> None:
    store, shadow = _outbox_fixture(tmp_path)
    _write_dummy_pending(shadow, "auto-task-1-v1")
    store.abort_auto_lifecycle("task-1", reason="mode_changed")

    # Journal unavailable at drain time: the intent stays open but the
    # already-committed task truth is untouched (READY + clean).
    assert drain_auto_shadow_cleanup_outbox(store, _FailingJournal()) == 0
    task = store.get_task("task-1")
    assert task.state is TaskState.READY
    assert task.auto_decision_id is None
    assert task.auto_grace_deadline_at is None
    assert task.auto_acked_at is None
    assert task.auto_reason is None
    assert [r["pending_id"] for r in store.pending_shadow_cleanups()] == [
        "auto-task-1-v1"
    ]
    # A later healthy drain finishes the job.
    assert drain_auto_shadow_cleanup_outbox(store, shadow) == 1
    assert store.pending_shadow_cleanups() == ()
    store.close()


# ---------------------------------------------------------------------------
# §17: one logical close = one authoritative version bump
# ---------------------------------------------------------------------------


def test_abort_is_exactly_one_version_bump_and_one_audit(tmp_path) -> None:
    store, _shadow = _outbox_fixture(tmp_path)
    before = store.get_task("task-1")
    updated = store.abort_auto_lifecycle(
        "task-1",
        expected_version=before.state_version,
        reason="mode_changed",
    )
    assert updated.state is TaskState.READY
    assert updated.state_version == before.state_version + 1
    events = [
        e["event_type"] for e in store.audit_events("task-1")
    ]
    assert events.count("AUTO_ABORTED") == 1
    # No second authoritative mutation followed the close.
    assert store.get_task("task-1").state_version == updated.state_version
    store.close()


def test_veto_close_is_exactly_one_version_bump(tmp_path) -> None:
    store, _shadow = _outbox_fixture(tmp_path)
    before = store.get_task("task-1")
    updated = store.abort_auto_lifecycle(
        "task-1",
        expected_version=before.state_version,
        reason="owner_veto",
        event_type="AUTO_VETOED",
        request_id="veto-1",
        force_manual=True,
    )
    assert updated.state_version == before.state_version + 1
    policy = store.connection.execute(
        "SELECT scheduling_policy FROM tasks WHERE task_id='task-1'"
    ).fetchone()
    assert policy["scheduling_policy"] == "MANUAL"
    store.close()


def test_plain_owner_blocked_task_cannot_take_auto_abort_path(tmp_path) -> None:
    store, _shadow = _outbox_fixture(tmp_path)
    # A normal owner BLOCKED task (no auto metadata) must be rejected by
    # the atomic helper even when allow_blocked is mistakenly passed.
    store.submit_task(
        task_id="task-2",
        request_id="submit-2",
        project_id=None,
        intent="owner task",
    )
    store.transition_task("task-2", TaskState.READY)
    store.transition_task("task-2", TaskState.BLOCKED)
    try:
        store.abort_auto_lifecycle(
            "task-2", reason="mode_changed", allow_blocked=True
        )
    except ValueError:
        pass
    else:
        raise AssertionError("plain owner BLOCKED task must not auto-abort")
    assert store.get_task("task-2").state is TaskState.BLOCKED
    store.close()
