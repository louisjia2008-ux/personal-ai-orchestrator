"""Startup closeout is truthful, generation-scoped and never a retry (#70)."""

from pathlib import Path

import pytest

from personal_ai_orchestrator.dispatch_initiator import initiate_owner_dispatch
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.safety_kernel import (
    AUTHORITY_OWNER_INITIATED_EXECUTION,
    STARTUP_RECOVERY_REQUIRED,
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
)
from tests.test_cleanup_quarantine import _claim, _complete, _observe, _receipt, _start


def _reserved(tmp_path: Path, *, ready: bool = True) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.db")
    task = store.submit_task(
        task_id="task",
        request_id="submit",
        intent="fixture",
        scheduling_policy="MANUAL",
        manual_execution_target_id="fixture",
    )
    store.register_workspace(
        task_id="task",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="fixture",
        base_sha="abc",
    )
    if ready:
        task = store.transition_task("task", TaskState.READY)
    store.acquire_writer("task", "writer")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch",
        request_id="dispatch-request",
        task_id="task",
        task_state_version=task.state_version,
        execution_target_id="fixture",
        authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
    )
    return store


def _interruption(store: SafetyKernelStore) -> dict:
    row = store.connection.execute("SELECT * FROM owner_dispatch_interruptions").fetchone()
    return dict(row)


@pytest.mark.parametrize("stage", ["reserved", "claimed", "entered", "observed", "started"])
def test_startup_closes_each_crash_boundary_without_inventing_cleanup(tmp_path, stage):
    store = _reserved(tmp_path)
    if stage != "reserved":
        _claim(store)
    if stage == "entered":
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    elif stage in {"observed", "started"}:
        _observe(store)
    if stage == "started":
        _start(store)
    before = store.get_task("task")
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    assert store.reconcile_startup() == ("task",)
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.get_task("task").state_version == before.state_version + 1
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request")
    assert dispatch.status is OwnerDispatchStatus.BLOCKED
    assert dispatch.failure_code == STARTUP_RECOVERY_REQUIRED
    assert "explicit recovery" in dispatch.failure_reason
    assert store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    assert (
        store.connection.execute("SELECT count(*) FROM worker_cleanup_receipts").fetchone()[0] == 0
    )
    record = _interruption(store)
    assert record["dispatch_id"] == "dispatch"
    assert record["attempt_id"] == (None if stage == "reserved" else "attempt")
    assert record["interrupted_task_state_version"] == before.state_version + 1
    assert record["run_id"] == ("run" if stage == "started" else None)
    if stage == "started":
        assert store.connection.execute("SELECT status FROM runs").fetchone()[0] == "INTERRUPTED"
    original_events = store.audit_events("task")
    assert store.reconcile_startup() == ()
    assert store.get_owner_dispatch_by_request_id("dispatch-request") == dispatch
    assert store.audit_events("task") == original_events
    assert _interruption(store) == record


def test_unstarted_submitted_reservation_is_blocked_and_quarantined(tmp_path):
    store = _reserved(tmp_path, ready=False)
    assert store.reconcile_startup() == ("task",)
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.has_cleanup_quarantine("task")
    assert _interruption(store)["attempt_id"] is None


@pytest.mark.parametrize("state", [TaskState.READY, TaskState.CANCELLED, TaskState.FAILED])
def test_old_reservation_does_not_mutate_newer_or_terminal_task(tmp_path, state):
    store = _reserved(tmp_path)
    _claim(store)
    store.transition_task("task", TaskState.BLOCKED)
    store.transition_task("task", state)
    task = store.get_task("task")
    assert store.reconcile_startup() == ()
    assert store.get_task("task") == task
    assert _interruption(store)["interrupted_task_state_version"] is None
    assert store.get_owner_dispatch_by_request_id("dispatch-request").failure_code == (
        STARTUP_RECOVERY_REQUIRED
    )


def test_multiple_old_reservations_block_source_generation_only_once(tmp_path):
    store = _reserved(tmp_path)
    store.reserve_owner_dispatch(
        dispatch_id="second",
        request_id="second-request",
        task_id="task",
        task_state_version=1,
        execution_target_id="fixture",
        authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
    )
    assert store.reconcile_startup() == ("task",)
    assert store.get_task("task").state_version == 2
    assert (
        store.connection.execute("SELECT count(*) FROM owner_dispatch_interruptions").fetchone()[0]
        == 2
    )


def test_confirmed_cleanup_keeps_truthful_interruption_and_never_restarts(tmp_path):
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    _complete(store)
    receipt = store.connection.execute(
        "SELECT payload_json FROM worker_cleanup_receipts"
    ).fetchone()[0]
    store.reconcile_startup()
    assert not store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    assert store.get_owner_dispatch_by_request_id("dispatch-request").failure_code == (
        STARTUP_RECOVERY_REQUIRED
    )
    assert (
        store.connection.execute("SELECT payload_json FROM worker_cleanup_receipts").fetchone()[0]
        == receipt
    )
    assert _interruption(store)["run_id"] == "run"


def test_startup_audit_failure_rolls_back_dispatch_task_run_and_fence(tmp_path, monkeypatch):
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    _start(store)
    original = store._audit

    def fail_dispatch(task_id, event_type, payload):
        if event_type == "OWNER_DISPATCH_INTERRUPTED":
            raise OSError("injected final audit failure")
        return original(task_id, event_type, payload)

    monkeypatch.setattr(store, "_audit", fail_dispatch)
    with pytest.raises(OSError, match="injected"):
        store.reconcile_startup()
    assert store.get_task("task").state is TaskState.RUNNING
    assert store.get_owner_dispatch_by_request_id("dispatch-request").status is (
        OwnerDispatchStatus.STARTED
    )
    assert store.connection.execute("SELECT status FROM runs").fetchone()[0] == "RUNNING"
    assert not store.get_worker_attempt("attempt").fenced
    assert (
        store.connection.execute("SELECT count(*) FROM owner_dispatch_interruptions").fetchone()[0]
        == 0
    )


def test_late_receipt_does_not_reopen_interrupted_dispatch(tmp_path):
    store = _reserved(tmp_path)
    _claim(store)
    _observe(store)
    store.reconcile_startup()
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request")
    store.complete_worker_attempt(attempt_id="attempt", executor_id="executor", cleanup=_receipt())
    assert store.get_owner_dispatch_by_request_id("dispatch-request") == dispatch
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.get_workspace("task").writer_token == "writer"


def test_same_request_and_runtime_reentry_after_restart_cannot_spawn(tmp_path):
    store = _reserved(tmp_path)
    # Use the canonical initiation identity for the replay below.
    store.connection.execute(
        "UPDATE owner_dispatches SET dispatch_id='owner-dispatch-replay', request_id='replay'"
    )
    version = store.get_task("task").state_version
    store.reconcile_startup()

    def forbidden(*args, **kwargs):
        pytest.fail("startup-interrupted replay entered runtime admission")

    dispatch, created, transitioned = initiate_owner_dispatch(
        store,
        object(),
        task=store.get_task("task"),
        request_id="replay",
        task_state_version=version,
        execution_target_id="fixture",
        project_provider=forbidden,
        registry_provider=forbidden,
        provider_registry_manager=None,
        runtime_available_provider=forbidden,
        execution_evidence_journal=None,
    )
    assert not created and transitioned is None
    assert dispatch.failure_code == STARTUP_RECOVERY_REQUIRED
    RuntimeDispatchExecutor(state_db=store.path, registry_provider=forbidden, executors={}).execute(
        "replay"
    )
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def _recoverable(tmp_path: Path, *, stage: str = "started", release: bool = True):
    from personal_ai_orchestrator.execution_controller import (
        begin_verification,
        reconcile_workspace_truth,
        record_worker_exit,
    )

    store = _reserved(tmp_path)
    _claim(store)
    if stage == "claimed":
        store.fence_worker_attempt(attempt_id="attempt", executor_id="executor")
        store.complete_worker_attempt(
            attempt_id="attempt",
            executor_id="executor",
            cleanup={"state": "CONFIRMED", "spawn_state": "NOT_STARTED"},
        )
    elif stage == "entered":
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
        store.fence_worker_attempt(attempt_id="attempt", executor_id="executor")
        store.complete_worker_attempt(
            attempt_id="attempt",
            executor_id="executor",
            cleanup={
                "state": "CONFIRMED",
                "spawn_state": "SPAWN_FAILED_NO_CHILD",
                "child_created": False,
                "process_create_failed": True,
            },
        )
    else:
        _observe(store)
        if stage in {"started", "finished", "verifying"}:
            _start(store)
        _complete(store)
        if stage in {"finished", "verifying"}:
            record_worker_exit(store, task_id="task", run_id="run", exit_code=0, worker_result={})
        if stage == "verifying":
            begin_verification(store, task_id="task")
    store.reconcile_startup()
    if release:
        reconcile_workspace_truth(store)
    return store


def _recover(store: SafetyKernelStore, **changes):
    return store.recover_owner_dispatch(
        **{
            "request_id": "recover-request",
            "task_id": "task",
            "expected_task_version": store.get_task("task").state_version,
            "prior_dispatch_id": "dispatch",
            "expected_store_id": store.store_id,
            "expected_process_epoch": "test-epoch",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "stage", ["claimed", "entered", "observed", "started", "finished", "verifying"]
)
def test_explicit_manual_recovery_requires_proof_and_only_prepares_new_start(tmp_path, stage):
    store = _recoverable(tmp_path, stage=stage)
    before = store.get_task("task")
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request")
    attempt = store.get_worker_attempt("attempt")
    receipt = _recover(store)
    assert receipt.task_state_version == before.state_version
    assert receipt.recovered_task_state_version == before.state_version + 1
    assert receipt.prior_attempt_id == "attempt"
    assert store.get_task("task").state is TaskState.READY
    assert store.get_task("task").scheduling_policy == "MANUAL"
    assert store.get_task("task").manual_execution_target_id == "fixture"
    assert store.get_owner_dispatch_by_request_id("dispatch-request") == dispatch
    assert store.get_worker_attempt("attempt") == attempt
    assert store.get_workspace("task").writer_token is None
    assert store.get_owner_dispatch_recovery("recover-request") == receipt
    assert store.connection.execute("SELECT count(*) FROM owner_dispatches").fetchone()[0] == 1


def test_recovery_never_releases_confirmed_retained_writer(tmp_path):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path, release=False)
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_writer_held"):
        _recover(store)
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.get_workspace("task").writer_token == "writer"


@pytest.mark.parametrize("stage", ["reserved", "claimed", "entered", "observed", "started"])
def test_unknown_or_missing_cleanup_denies_manual_recovery(tmp_path, stage):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _reserved(tmp_path)
    if stage != "reserved":
        _claim(store)
    if stage == "entered":
        store.permit_worker_spawn(attempt_id="attempt", executor_id="executor")
    elif stage in {"observed", "started"}:
        _observe(store)
    if stage == "started":
        _start(store)
    store.reconcile_startup()
    before = store.get_task("task")
    with pytest.raises(OwnerDispatchRecoveryError):
        _recover(store)
    assert store.get_task("task") == before
    assert store.has_cleanup_quarantine("task")
    assert store.get_workspace("task").writer_token == "writer"
    with pytest.raises(KeyError):
        store.get_owner_dispatch_recovery("recover-request")


@pytest.mark.parametrize("policy", [None, "BALANCED", "QUOTA_SAVER", "SUPERVISED_AUTO"])
def test_recovery_denies_nonmanual_without_changing_policy(tmp_path, policy):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    store.connection.execute("UPDATE tasks SET scheduling_policy=?", (policy,))
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_requires_manual_task"):
        _recover(store)
    assert store.get_task("task").state is TaskState.BLOCKED
    assert store.get_task("task").scheduling_policy == policy


def test_recovered_manual_task_cannot_enter_supervised_auto(tmp_path):
    from tests.test_supervised_auto_step import NOW, _env

    env_path = tmp_path / "auto"
    env_path.mkdir()
    env = _env(env_path)
    store = _recoverable(tmp_path)
    _recover(store)
    env.service.store = store
    env.tick(NOW)
    assert store.get_task("task").state is TaskState.READY
    assert store.get_task("task").auto_reason == "task_policy_manual"
    assert not env.executor.calls
    assert store.connection.execute("SELECT count(*) FROM owner_dispatches").fetchone()[0] == 1


@pytest.mark.parametrize("target", [None, "another-target"])
def test_recovery_denies_missing_or_changed_target(tmp_path, target):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    store.connection.execute("UPDATE tasks SET manual_execution_target_id=?", (target,))
    with pytest.raises(OwnerDispatchRecoveryError, match="manual_execution_target_mismatch"):
        _recover(store)
    assert store.get_task("task").state is TaskState.BLOCKED


def test_recovery_denies_changed_workspace(tmp_path):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    store.connection.execute("UPDATE workspaces SET worktree_path='another-worktree'")
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_workspace_changed"):
        _recover(store)


@pytest.mark.parametrize("state", [TaskState.READY, TaskState.CANCELLED, TaskState.FAILED])
def test_recovery_denies_changed_generation_or_terminal_task(tmp_path, state):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    previous_version = store.get_task("task").state_version
    store.transition_task("task", state)
    if state is TaskState.READY:
        store.transition_task("task", TaskState.BLOCKED)
    before = store.get_task("task")
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_generation_mismatch"):
        _recover(store)
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_generation_mismatch"):
        _recover(store, expected_task_version=previous_version)
    assert store.get_task("task") == before


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "other"},
        {"prior_dispatch_id": "other"},
        {"expected_task_version": 999},
        {"expected_process_epoch": "new-host"},
    ],
)
def test_recovery_request_id_is_immutable_for_whole_tuple(tmp_path, change):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    receipt = _recover(store)
    with pytest.raises(OwnerDispatchRecoveryError, match="conflicting_recovery_request_id"):
        _recover(store, **{"expected_task_version": receipt.task_state_version, **change})
    assert store.get_owner_dispatch_recovery(receipt.request_id) == receipt


def test_recovery_lost_reply_repeated_click_and_restart_never_repeat_reset(tmp_path):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    receipt = _recover(store)
    store.transition_task("task", TaskState.CANCELLED)
    task = store.get_task("task")
    for _ in range(3):
        assert _recover(store, expected_task_version=receipt.task_state_version) == receipt
    assert store.get_task("task") == task
    with pytest.raises(OwnerDispatchRecoveryError, match="prior_dispatch_already_recovered"):
        _recover(store, request_id="second-recovery")
    store.close()
    store = SafetyKernelStore(tmp_path / "state.db")
    store.reconcile_startup()
    assert store.get_owner_dispatch_recovery("recover-request") == receipt
    assert store.get_task("task") == task
    with pytest.raises(OwnerDispatchRecoveryError, match="conflicting_recovery_request_id"):
        _recover(
            store,
            expected_task_version=receipt.task_state_version,
            expected_process_epoch="new-host",
        )


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_connections_recover_once(tmp_path, same_request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    version = store.get_task("task").state_version
    barrier = Barrier(2)

    def run(index):
        connection = SafetyKernelStore(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                return _recover(
                    connection,
                    expected_task_version=version,
                    request_id="recovery" if same_request else f"recovery-{index}",
                )
            except OwnerDispatchRecoveryError as error:
                return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert store.get_task("task").state_version == version + 1
    assert (
        store.connection.execute("SELECT count(*) FROM owner_dispatch_recoveries").fetchone()[0]
        == 1
    )
    if same_request:
        assert results[0] == results[1]
    else:
        assert "prior_dispatch_already_recovered" in results


def test_concurrent_cancel_and_recovery_cannot_resurrect_cancelled_task(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    version = store.get_task("task").state_version
    barrier = Barrier(2)

    def run(cancel):
        connection = SafetyKernelStore(store.path)
        try:
            barrier.wait(timeout=5)
            if cancel:
                return connection.transition_task("task", TaskState.CANCELLED)
            try:
                return _recover(connection, expected_task_version=version)
            except OwnerDispatchRecoveryError as error:
                return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [False, True]))
    assert store.get_task("task").state is TaskState.CANCELLED


@pytest.mark.parametrize("corruption", ["missing", "malformed", "wrong-attempt", "false-eof"])
def test_recovery_requires_immutable_matching_receipt_not_cached_state(tmp_path, corruption):
    import json

    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    # Simulate damaged durable evidence; public APIs cannot mutate receipts.
    if corruption == "missing":
        store.connection.execute("DROP TRIGGER immutable_worker_cleanup_receipt_delete")
        store.connection.execute("DELETE FROM worker_cleanup_receipts")
    else:
        store.connection.execute("DROP TRIGGER immutable_worker_cleanup_receipt_update")
        cleanup = _receipt()
        if corruption == "wrong-attempt":
            cleanup["attempt_id"] = "unrelated"
        elif corruption == "false-eof":
            cleanup["stdout"]["eof"] = False
        payload = "not json" if corruption == "malformed" else json.dumps(cleanup)
        store.connection.execute("UPDATE worker_cleanup_receipts SET payload_json=?", (payload,))
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_cleanup_unconfirmed"):
        _recover(store)
    assert store.get_task("task").state is TaskState.BLOCKED


def test_recovery_audit_failure_rolls_back_state_and_receipt(tmp_path, monkeypatch):
    store = _recoverable(tmp_path)
    before = store.get_task("task")
    original = store._audit

    def fail(task_id, event_type, payload):
        if event_type == "OWNER_DISPATCH_RECOVERED":
            raise OSError("recovery audit failure")
        return original(task_id, event_type, payload)

    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(OSError, match="recovery audit failure"):
        _recover(store)
    assert store.get_task("task") == before
    with pytest.raises(KeyError):
        store.get_owner_dispatch_recovery("recover-request")


@pytest.mark.parametrize("reason", ["owner initiated execution dispatch reserved", "unrelated"])
def test_prethread_submitted_ready_generation_needs_exact_admission_audit(tmp_path, reason):
    store = _reserved(tmp_path, ready=False)
    before = store.transition_task("task", TaskState.READY, reason=reason)
    store.reconcile_startup()
    if reason == "owner initiated execution dispatch reserved":
        assert store.get_task("task").state is TaskState.BLOCKED
        assert _interruption(store)["interrupted_task_state_version"] == before.state_version + 1
    else:
        assert store.get_task("task") == before
        assert _interruption(store)["interrupted_task_state_version"] is None
    # Even exact admission is not proof of no child: legacy absence of attempt
    # stays quarantined and cannot qualify for explicit recovery.
    assert _interruption(store)["attempt_id"] is None
    assert store.has_cleanup_quarantine("task")


@pytest.mark.parametrize("kind", ["run", "dispatch"])
def test_recovery_denies_another_active_execution_claim(tmp_path, kind):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    if kind == "run":
        store.start_run(run_id="other-run", task_id="task", worker_id="other", pid=999)
    else:
        store.reserve_owner_dispatch(
            dispatch_id="other",
            request_id="other-request",
            task_id="task",
            task_state_version=store.get_task("task").state_version,
            execution_target_id="fixture",
            authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
        )
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_active_execution"):
        _recover(store)


@pytest.mark.parametrize("scope", ["task", "path", "unrelated"])
def test_recovery_checks_every_task_and_workspace_claim(tmp_path, scope):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    store.submit_task(task_id="other", request_id="other", intent="other")
    path = store.get_workspace("task").worktree_path if scope == "path" else "unrelated-path"
    task_id = "task" if scope == "task" else "other"
    store.connection.execute(
        """INSERT INTO worker_attempts(
           attempt_id,task_id,task_state_version,authority,executor_id,worktree_path,
           spawn_state,fenced,cleanup_state,created_at,updated_at)
           VALUES('uncertain',?,0,'LEGACY_UNKNOWN','legacy-unknown',?,
                  'LEGACY_UNKNOWN',1,'UNKNOWN','fixture','fixture')""",
        (task_id, path),
    )
    if scope == "unrelated":
        _recover(store)
        assert store.get_task("task").state is TaskState.READY
    else:
        with pytest.raises(OwnerDispatchRecoveryError, match="recovery_cleanup_unconfirmed"):
            _recover(store)
        assert store.get_task("task").state is TaskState.BLOCKED


def test_recovery_then_fresh_start_uses_new_attempt_and_old_receipt_is_readonly(tmp_path):
    store = _recoverable(tmp_path)
    receipt = _recover(store)
    store.acquire_writer("task", "new-writer")
    version = store.get_task("task").state_version
    store.reserve_owner_dispatch(
        dispatch_id="new-dispatch",
        request_id="new-request",
        task_id="task",
        task_state_version=version,
        execution_target_id="fixture",
        authority=AUTHORITY_OWNER_INITIATED_EXECUTION,
    )
    store.begin_worker_attempt(
        dispatch_id="new-dispatch",
        task_id="task",
        attempt_id="new-attempt",
        executor_id="new-executor",
        writer_token="new-writer",
        expected_task_version=version,
    )
    store.permit_worker_spawn(attempt_id="new-attempt", executor_id="new-executor")
    store.record_worker_spawn(
        attempt_id="new-attempt",
        executor_id="new-executor",
        spawn_ticket="new-ticket",
        pid=456,
    )
    current = store.start_dispatched_worker(
        dispatch_id="new-dispatch",
        task_id="task",
        expected_task_version=version,
        run_id="new-run",
        worker_id="fixture",
        writer_token="new-writer",
        pid=456,
        attempt_id="new-attempt",
        executor_id="new-executor",
    )
    assert _recover(store, expected_task_version=receipt.task_state_version) == receipt
    assert store.get_task("task") == current
    assert current.state is TaskState.RUNNING
    assert store.get_workspace("task").writer_token == "new-writer"
    assert store.get_owner_dispatch_by_request_id("dispatch-request").status is (
        OwnerDispatchStatus.BLOCKED
    )


@pytest.mark.parametrize("table", ["owner_dispatch_interruptions", "owner_dispatch_recoveries"])
def test_interruption_and_recovery_receipts_are_immutable(tmp_path, table):
    import sqlite3

    store = _recoverable(tmp_path)
    _recover(store)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute(f"UPDATE {table} SET task_id='task'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute(f"DELETE FROM {table}")


def test_recovery_rechecks_exact_prior_run_identity(tmp_path):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    store.connection.execute("UPDATE runs SET pid=999 WHERE run_id='run'")
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_attempt_identity_mismatch"):
        _recover(store)


def test_prethread_admission_audit_must_follow_matching_reservation(tmp_path):
    store = _reserved(tmp_path, ready=False)
    store.transition_task(
        "task", TaskState.READY, reason="owner initiated execution dispatch reserved"
    )
    # A damaged/imported audit cannot establish a generation solely by its text.
    store.connection.execute("DELETE FROM audit_events WHERE event_type='OWNER_DISPATCH_RESERVED'")
    before = store.get_task("task")
    store.reconcile_startup()
    assert store.get_task("task") == before
    assert _interruption(store)["interrupted_task_state_version"] is None


def test_recovery_requires_current_store_identity(tmp_path):
    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError

    store = _recoverable(tmp_path)
    before = store.get_task("task")
    with pytest.raises(OwnerDispatchRecoveryError, match="store_identity_mismatch"):
        _recover(store, expected_store_id="unrelated-store")
    assert store.get_task("task") == before


def test_recovery_cannot_bypass_active_switch_lease(tmp_path):
    from datetime import UTC, datetime, timedelta

    from personal_ai_orchestrator.safety_kernel import OwnerDispatchRecoveryError
    from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority

    store = _recoverable(tmp_path)
    SwitchLeaseAuthority(store)
    store.record_routing_decision(
        decision_id="decision", request_id="route", task_id="task", payload={}
    )
    expires = int((datetime.now(UTC) + timedelta(minutes=1)).timestamp())
    store.connection.execute(
        "INSERT INTO switch_leases VALUES(?,?,?,?,?,?,?,?,?,NULL)",
        (
            "lease",
            "decision",
            "route",
            "task",
            store.get_task("task").state_version,
            "session",
            "AUTHORIZED",
            expires,
            datetime.now(UTC).isoformat(),
        ),
    )
    with pytest.raises(OwnerDispatchRecoveryError, match="recovery_active_switch_lease"):
        _recover(store)
    assert store.get_task("task").state is TaskState.BLOCKED
