"""Durable cleanup evidence, restart fencing and exact writer safety (#71)."""

import sqlite3
from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import (
    reconcile_workspace_truth,
    record_worker_exit,
    start_worker_run,
)
from personal_ai_orchestrator.safety_kernel import (
    AUTHORITY_OWNER_INITIATED_EXECUTION,
    SafetyKernelStore,
    TaskState,
)


def _reserved(tmp_path: Path, *, writer: bool = True) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.db")
    store.submit_task(task_id="task", request_id="request", intent="fixture")
    (tmp_path / "worktree").mkdir(exist_ok=True)
    store.register_workspace(
        task_id="task",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="fixture",
        base_sha="abc",
    )
    store.transition_task("task", TaskState.READY)
    if writer:
        store.acquire_writer("task", "writer")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch",
        request_id="dispatch-request",
        task_id="task",
        task_state_version=store.get_task("task").state_version,
        execution_target_id="fixture",
        authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
    )
    return store


def _claim(store: SafetyKernelStore, **changes):
    return store.begin_worker_attempt(
        **{
            "dispatch_id": "dispatch",
            "task_id": "task",
            "attempt_id": "attempt",
            "executor_id": "executor",
            "writer_token": "writer",
            **changes,
        }
    )


def _observe(store: SafetyKernelStore) -> None:
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    store.record_worker_spawn(
        attempt_id="attempt",
        executor_id="executor",
        spawn_ticket="ticket",
        pid=123,
        run_id="run",
    )


def _start(store: SafetyKernelStore, **changes) -> None:
    store.start_dispatched_worker(
        **{
            "dispatch_id": "dispatch",
            "task_id": "task",
            "expected_task_version": store.get_task("task").state_version,
            "run_id": "run",
            "worker_id": "fixture",
            "writer_token": "writer",
            "pid": 123,
            "attempt_id": "attempt",
            "executor_id": "executor",
            **changes,
        }
    )


def _receipt(**changes):
    return {
        "state": "CONFIRMED",
        "spawn_state": "PROCESS_OBSERVED",
        "spawn_ticket": "ticket",
        "attempt_id": "attempt",
        "executor_id": "executor",
        "task_id": "task",
        "dispatch_id": "dispatch",
        "writer_token": "writer",
        "scope": "CREATED_PROCESS_GROUP",
        "capability": "WAITID_WNOWAIT",
        "exit_code": 0,
        "stdout": {"eof": True, "error": None, "forced_closed": False, "collector_done": True},
        "stderr": {"eof": True, "error": None, "forced_closed": False, "collector_done": True},
        "leader_exit_observed": True,
        "child_reaped": True,
        "scope_empty": True,
        **changes,
    }


def _complete(store: SafetyKernelStore, **changes):
    return store.complete_worker_attempt(
        attempt_id="attempt", executor_id="executor", cleanup=_receipt(**changes)
    )


def test_pre_spawn_claim_is_unique_across_connections(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    second = SafetyKernelStore(store.path)
    with pytest.raises(RuntimeError):
        _claim(second, attempt_id="another-attempt", executor_id="another-executor")
    with pytest.raises(RuntimeError):
        _claim(second)
    assert store.connection.execute("SELECT COUNT(*) FROM worker_attempts").fetchone()[0] == 1
    # A normal connection is not daemon startup and cannot fence an active executor.
    assert not store.get_worker_attempt("attempt").fenced
    second.close()
    store.close()


def test_claim_requires_exact_current_authority_and_writer(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    with pytest.raises(RuntimeError, match="active writer"):
        _claim(store, writer_token="other")
    store.transition_task("task", TaskState.BLOCKED)
    with pytest.raises(RuntimeError, match="task state authority"):
        _claim(store)
    assert store.get_worker_attempt_for_dispatch("dispatch") is None


def test_spawn_permit_is_one_shot_and_rechecks_authority(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    with pytest.raises(RuntimeError, match="already consumed"):
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    store.fence_worker_attempt(attempt_id="attempt", executor_id="executor")
    with pytest.raises(RuntimeError, match="fenced"):
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")


@pytest.mark.parametrize("terminal", [TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED])
def test_unresolved_cleanup_survives_terminal_restart(tmp_path: Path, terminal: TaskState) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.transition_task("task", TaskState.BLOCKED)
    if terminal is not TaskState.BLOCKED:
        store.transition_task("task", terminal)
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    reconcile_workspace_truth(store)
    assert store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    with pytest.raises(RuntimeError, match="quarantine"):
        store.release_writer("task", "writer")
    with pytest.raises(RuntimeError, match="quarantine"):
        store.acquire_writer("task", "other")


def test_legacy_reserved_without_run_and_writer_cannot_reacquire(tmp_path: Path) -> None:
    store = _reserved(tmp_path, writer=False)
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    assert store.has_cleanup_quarantine("task")
    assert store.get_worker_attempt_for_dispatch("dispatch").spawn_state == "LEGACY_UNKNOWN"
    with pytest.raises(RuntimeError, match="quarantine"):
        store.acquire_writer("task", "writer")
    with pytest.raises(RuntimeError, match="identity"):
        store.complete_worker_attempt(
            attempt_id="legacy-dispatch:dispatch",
            executor_id="legacy-unknown",
            cleanup={"state": "CONFIRMED", "spawn_state": "NOT_STARTED"},
        )


def test_legacy_running_migration_retains_writer(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    store.start_dispatched_worker(
        dispatch_id="dispatch",
        task_id="task",
        expected_task_version=1,
        run_id="run",
        worker_id="fixture",
        writer_token="writer",
        pid=123,
    )
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    assert store.reconcile_startup() == ("task",)
    reconcile_workspace_truth(store)
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.get_workspace("task").writer_token == "writer"
    assert store.has_cleanup_quarantine("task")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE workspaces SET writer_token=NULL WHERE task_id='task'",
        "UPDATE workspaces SET writer_token='other' WHERE task_id='task'",
        "UPDATE workspaces SET worktree_path='other' WHERE task_id='task'",
        "DELETE FROM workspaces WHERE task_id='task'",
        "INSERT OR REPLACE INTO workspaces(task_id,repo_path,worktree_path,branch,base_sha) "
        "SELECT task_id,repo_path,worktree_path,branch,base_sha FROM workspaces "
        "WHERE task_id='task'",
    ],
)
def test_direct_sql_cannot_erase_or_release_quarantine(tmp_path: Path, sql: str) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    with pytest.raises(sqlite3.IntegrityError, match="quarantine"):
        store.connection.execute(sql)
    assert store.get_workspace("task").writer_token == "writer"


def test_restart_fences_old_start_but_accepts_matching_late_facts(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    with pytest.raises(RuntimeError, match="fenced"):
        _start(store)
    _complete(store)
    assert not store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    # Facts do not renew launch authority, even if task and dispatch remain READY/RESERVED.
    with pytest.raises(RuntimeError, match="fenced"):
        _start(store)


def test_unknown_then_confirmed_receipts_are_separate_and_immutable(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _complete(store, state="UNKNOWN", scope_empty=False)
    assert store.has_cleanup_quarantine("task")
    _complete(store)
    _complete(store)
    assert not store.has_cleanup_quarantine("task")
    assert (
        store.connection.execute("SELECT COUNT(*) FROM worker_cleanup_receipts").fetchone()[0] == 2
    )
    with pytest.raises(RuntimeError, match="immutable"):
        _complete(store, reason="different")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute("UPDATE worker_cleanup_receipts SET payload_json='{}'")


@pytest.mark.parametrize(
    "changes",
    [
        {"executor_id": "stale"},
        {"attempt_id": "stale"},
        {"writer_token": "new-owner"},
        {"spawn_ticket": "stale"},
        {"leader_exit_observed": False},
        {"child_reaped": False},
        {"scope_empty": False},
    ],
)
def test_mismatched_or_unproven_receipt_cannot_clear_gate(tmp_path: Path, changes: dict) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    with pytest.raises(RuntimeError):
        _complete(store, **changes)
    assert store.has_cleanup_quarantine("task")


def test_old_receipt_cannot_clear_later_attempt_or_release_new_writer(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _complete(store)
    store.release_writer("task", "writer")
    store.acquire_writer("task", "new-writer")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch-2",
        request_id="dispatch-request-2",
        task_id="task",
        task_state_version=1,
        execution_target_id="fixture",
        authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
    )
    _claim(
        store,
        dispatch_id="dispatch-2",
        attempt_id="attempt-2",
        executor_id="executor-2",
        writer_token="new-writer",
    )
    _complete(store)
    assert store.get_workspace("task").writer_token == "new-writer"
    assert store.has_cleanup_quarantine("task")
    with pytest.raises(RuntimeError):
        store.release_writer("task", "writer")


def test_receipt_audit_failure_rolls_back_gate_change(tmp_path: Path, monkeypatch) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)

    def fail(*args, **kwargs):
        raise OSError("audit fixture failed")

    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(OSError):
        _complete(store)
    assert store.has_cleanup_quarantine("task")
    assert store.get_worker_attempt("attempt").cleanup_state == "UNRESOLVED"
    assert (
        store.connection.execute("SELECT COUNT(*) FROM worker_cleanup_receipts").fetchone()[0] == 0
    )


def test_confirmed_receipt_before_terminal_commit_survives_restart(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    _complete(store)
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    assert not store.has_cleanup_quarantine("task")
    assert store.get_worker_attempt("attempt").cleanup_state == "CONFIRMED"
    reconcile_workspace_truth(store)
    assert store.get_workspace("task").writer_token is None
    assert store.get_task("task").state is TaskState.BLOCKED


def test_exit_zero_with_unknown_cleanup_is_blocked(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    assert (
        record_worker_exit(
            store, task_id="task", run_id="run", exit_code=0, worker_result={"ok": True}
        )
        is TaskState.BLOCKED
    )
    reconcile_workspace_truth(store)
    assert store.get_workspace("task").writer_token == "writer"


def test_direct_start_helpers_cannot_bypass_attempt_gate(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    with pytest.raises(RuntimeError, match="exact attempt"):
        _start(store, attempt_id=None, executor_id=None)
    with pytest.raises(RuntimeError, match="identity"):
        _start(store, executor_id="stale")
    with pytest.raises(RuntimeError, match="quarantine"):
        store.start_run(task_id="task", run_id="other", worker_id="fixture")
    store.connection.execute("UPDATE tasks SET state='RUNNING' WHERE task_id='task'")
    with pytest.raises(RuntimeError, match="quarantine"):
        start_worker_run(
            store, task_id="task", run_id="other", worker_id="fixture", writer_token="writer"
        )


def test_not_started_requires_fence_and_unconsumed_permit(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    with pytest.raises(RuntimeError, match="fenced"):
        _complete(store, spawn_state="NOT_STARTED")
    store.fence_worker_attempt(attempt_id="attempt", executor_id="executor")
    _complete(store, spawn_state="NOT_STARTED")
    assert not store.has_cleanup_quarantine("task")
    with pytest.raises(RuntimeError, match="fenced"):
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")


def test_entered_spawn_cannot_be_inferred_not_started(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    store.fence_worker_attempt(attempt_id="attempt", executor_id="executor")
    with pytest.raises(RuntimeError, match="never-entered"):
        _complete(store, spawn_state="NOT_STARTED")
    _complete(
        store, spawn_state="SPAWN_FAILED_NO_CHILD", child_created=False, process_create_failed=True
    )
    assert not store.has_cleanup_quarantine("task")


def test_creation_guard_serializes_startup_fence_with_os_boundary(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    other = SafetyKernelStore(store.path, timeout_seconds=0.0)
    with store.worker_spawn_guard(
        attempt_id="attempt", executor_id="executor", spawn_ticket="ticket"
    ) as report_created:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.reconcile_startup()
        report_created(123)
    assert store.get_worker_attempt("attempt").spawn_state == "SPAWN_OBSERVED"
    other.reconcile_startup()
    assert store.get_worker_attempt("attempt").fenced
    other.close()


def test_creation_guard_rollback_cannot_authorize_second_child(tmp_path: Path, monkeypatch) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    real_audit = store._audit

    def fail_observation(task_id, event_type, payload):
        if event_type == "WORKER_SPAWN_OBSERVED":
            raise OSError("fixture write failed after OS spawn")
        real_audit(task_id, event_type, payload)

    monkeypatch.setattr(store, "_audit", fail_observation)
    with pytest.raises(OSError):
        with store.worker_spawn_guard(
            attempt_id="attempt", executor_id="executor", spawn_ticket="ticket"
        ) as report_created:
            report_created(123)
    attempt = store.get_worker_attempt("attempt")
    assert attempt.spawn_state == "SPAWN_ENTERED"
    assert attempt.spawn_creation_claimed
    assert store.has_cleanup_quarantine("task")
    with pytest.raises(RuntimeError, match="unused spawn permit"):
        with store.worker_spawn_guard(
            attempt_id="attempt", executor_id="executor", spawn_ticket="another"
        ):
            pytest.fail("second child could be spawned")


def test_creation_guard_fenced_executor_never_enters_os_boundary(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    store.reconcile_startup()
    with pytest.raises(RuntimeError, match="fenced"):
        with store.worker_spawn_guard(
            attempt_id="attempt", executor_id="executor", spawn_ticket="ticket"
        ):
            pytest.fail("fenced executor reached OS boundary")


def test_other_task_cannot_reregister_or_move_into_quarantined_worktree(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    store.submit_task(task_id="other", request_id="other-request", intent="fixture")
    with pytest.raises(RuntimeError, match="quarantine"):
        store.register_workspace(
            task_id="other",
            repo_path=str(tmp_path / "repo"),
            worktree_path=str(tmp_path / "worktree"),
            branch="other",
            base_sha="abc",
        )
    with pytest.raises(sqlite3.IntegrityError, match="quarantine"):
        store.connection.execute(
            "INSERT INTO workspaces(task_id,repo_path,worktree_path,branch,base_sha) "
            "VALUES('other','repo',?,'branch','sha')",
            (str(tmp_path / "worktree"),),
        )
    store.register_workspace(
        task_id="other",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "other-tree"),
        branch="other",
        base_sha="abc",
    )
    with pytest.raises(sqlite3.IntegrityError, match="quarantine"):
        store.connection.execute(
            "UPDATE workspaces SET worktree_path=? WHERE task_id='other'",
            (str(tmp_path / "worktree"),),
        )
    assert store.get_workspace("task").writer_token == "writer"


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "LEADER_ONLY"},
        {"capability": "UNKNOWN"},
        {"spawn_state": "UNKNOWN"},
        {"stdout": None},
        {"stderr": {}},
        {"exit_code": None},
        {"stdout": {"eof": False, "error": None, "forced_closed": False, "collector_done": True}},
        {
            "stderr": {
                "eof": True,
                "error": "ReaderError",
                "forced_closed": False,
                "collector_done": True,
            }
        },
        {"stdout": {"eof": True, "error": None, "forced_closed": True, "collector_done": True}},
        {"stderr": {"eof": True, "error": None, "forced_closed": False, "collector_done": False}},
        {"stdout": {"eof": True, "forced_closed": False, "collector_done": True}},
    ],
)
def test_confirmed_cleanup_requires_complete_scope_and_pipe_evidence(
    tmp_path: Path, changes
) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    with pytest.raises(RuntimeError):
        _complete(store, **changes)
    assert store.has_cleanup_quarantine("task")
    assert (
        store.connection.execute("SELECT COUNT(*) FROM worker_cleanup_receipts").fetchone()[0] == 0
    )


@pytest.mark.parametrize("state", ["BLOCKED", "FAILED", "CANCELLED", "VERIFIED", "COMPLETED"])
def test_legacy_finished_run_with_retained_writer_stays_quarantined(tmp_path: Path, state) -> None:
    store = _reserved(tmp_path)
    store.start_dispatched_worker(
        dispatch_id="dispatch",
        task_id="task",
        expected_task_version=1,
        run_id="run",
        worker_id="fixture",
        writer_token="writer",
        pid=123,
    )
    record_worker_exit(store, task_id="task", run_id="run", exit_code=2, worker_result={})
    # Snapshot from the old implementation after state repair but before writer
    # release. Terminal status and leader exit do not prove group/pipe cleanup.
    store.connection.execute("UPDATE tasks SET state=? WHERE task_id='task'", (state,))
    store.connection.execute("UPDATE owner_dispatches SET status='FINISHED'")
    assert store.connection.execute("SELECT status FROM runs").fetchone()[0] == "FAILED"
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    reconcile_workspace_truth(store)
    assert store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    with pytest.raises(RuntimeError, match="quarantine"):
        store.release_writer("task", "writer")


def test_emergency_repair_matches_current_attempt_atomically(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    assert store.fail_worker_attempt(
        task_id="task",
        run_id="run",
        writer_token="writer",
        pid=123,
        result={"reason": "fixture"},
        reason="fixture failure",
    )
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.connection.execute("SELECT status FROM runs").fetchone()[0] == "CLEANUP_UNKNOWN"
    assert store.get_workspace("task").writer_token == "writer"
    assert store.has_cleanup_quarantine("task")


@pytest.mark.parametrize(
    "changes", [{"writer_token": "other"}, {"pid": 456}, {"run_id": "old-run"}]
)
def test_stale_emergency_identity_cannot_change_active_task(tmp_path: Path, changes) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    before = store.audit_events("task")
    assert not store.fail_worker_attempt(
        **{
            "task_id": "task",
            "run_id": "run",
            "writer_token": "writer",
            "pid": 123,
            "result": {},
            "reason": "stale failure",
            **changes,
        }
    )
    assert store.get_task("task").state is TaskState.RUNNING
    assert store.audit_events("task") == before


def test_terminal_old_run_emergency_cannot_block_new_running_generation(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    _complete(store)
    record_worker_exit(store, task_id="task", run_id="run", exit_code=2, worker_result={})
    store.release_writer("task", "writer")
    store.transition_task("task", TaskState.READY)
    store.acquire_writer("task", "writer-2")
    store.transition_task("task", TaskState.RUNNING)
    store.start_run(task_id="task", run_id="run-2", worker_id="fixture", pid=456)
    before = store.audit_events("task")
    assert not store.fail_worker_attempt(
        task_id="task",
        run_id="run",
        writer_token="writer",
        pid=123,
        result={},
        reason="stale old callback",
    )
    assert store.get_task("task").state is TaskState.RUNNING
    assert store.get_workspace("task").writer_token == "writer-2"
    assert store.audit_events("task") == before


def test_emergency_repair_audit_failure_rolls_back_run_and_task(
    tmp_path: Path, monkeypatch
) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)

    def fail(*args, **kwargs):
        raise OSError("audit fixture")

    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(OSError):
        store.fail_worker_attempt(
            task_id="task",
            run_id="run",
            writer_token="writer",
            pid=123,
            result={},
            reason="fixture",
        )
    assert store.get_task("task").state is TaskState.RUNNING
    assert store.connection.execute("SELECT status FROM runs").fetchone()[0] == "RUNNING"
    assert store.has_cleanup_quarantine("task")


def test_old_confirmed_attempt_does_not_cover_different_writer_at_restart(tmp_path: Path) -> None:
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _complete(store)
    store.release_writer("task", "writer")
    store.acquire_writer("task", "new-writer")
    store.transition_task("task", TaskState.BLOCKED)
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    reconcile_workspace_truth(store)
    assert store.get_worker_attempt("attempt").cleanup_state == "CONFIRMED"
    assert store.get_worker_attempt("legacy-task:task").writer_token == "new-writer"
    assert store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "new-writer"
    _complete(store)
    assert store.has_cleanup_quarantine("task")
    with pytest.raises(RuntimeError, match="quarantine"):
        store.release_writer("task", "new-writer")


def test_workspace_reconcile_yields_to_concurrent_startup_quarantine(tmp_path, monkeypatch):
    store = _reserved(tmp_path)
    store.transition_task("task", TaskState.BLOCKED)
    original_release = store.release_writer

    def startup_before_release(task_id, writer_token):
        other = SafetyKernelStore(tmp_path / "state.db")
        try:
            other.reconcile_startup()
        finally:
            other.close()
        return original_release(task_id, writer_token)

    monkeypatch.setattr(store, "release_writer", startup_before_release)
    try:
        assert reconcile_workspace_truth(store) == ()
        assert store.has_cleanup_quarantine("task")
        assert store.get_workspace("task").writer_token == "writer"
    finally:
        store.close()


def test_second_cancel_does_not_disguise_cleanup_unknown_as_cancelled(tmp_path):
    from personal_ai_orchestrator.control_api import ControlPlaneError, ControlPlaneService
    from personal_ai_orchestrator.model_registry import ModelRegistry
    from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
    from personal_ai_orchestrator.scheduling_settings import SchedulingSettings

    store = _reserved(tmp_path)
    _claim(store)
    store.transition_task("task", TaskState.BLOCKED)
    service = ControlPlaneService(
        registry=ModelRegistry(),
        store=store,
        owner_execution=OwnerExecutionSettings(tmp_path / "owner.json"),
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling.json"),
    )
    before = store.audit_events("task")
    try:
        with pytest.raises(ControlPlaneError) as error:
            service.cancel_task("task", {"request_id": "cancel-again"})
        assert error.value.status == 409
        assert error.value.code == "worker_cleanup_unknown"
        assert store.get_task("task").state is TaskState.BLOCKED
        assert store.get_workspace("task").writer_token == "writer"
        assert store.get_worker_attempt("attempt").cleanup_state == "UNRESOLVED"
        assert store.audit_events("task") == before
    finally:
        store.close()
