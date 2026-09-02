from pathlib import Path

import pytest

from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchStatus,
    SafetyKernelStore,
    TaskState,
)


def _running_store(path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(path)
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    return store


def _run_status(store: SafetyKernelStore, run_id: str) -> str:
    row = store.connection.execute(
        "SELECT status FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row is not None
    return row["status"]


def test_durable_store_enforces_one_active_run_per_task_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    first = _running_store(db)
    second = SafetyKernelStore(db)
    first.start_run(run_id="run-1", task_id="t1", worker_id="worker-a")

    with pytest.raises(RuntimeError, match="active worker run"):
        second.start_run(run_id="run-2", task_id="t1", worker_id="worker-b")

    assert _run_status(first, "run-1") == "RUNNING"


def test_finish_run_rolls_back_state_if_audit_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = _running_store(tmp_path / "state.sqlite3")
    store.start_run(run_id="run-1", task_id="t1", worker_id="worker")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        store.finish_run(run_id="run-1", status="FINISHED", result={"ok": True})

    assert _run_status(store, "run-1") == "RUNNING"


def test_routing_decision_rolls_back_if_audit_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        store.record_routing_decision(
            decision_id="decision-1",
            request_id="request-1",
            task_id=None,
            payload={"decision_id": "decision-1"},
        )

    row = store.connection.execute(
        "SELECT decision_id FROM routing_decisions WHERE request_id='request-1'"
    ).fetchone()
    assert row is None


def test_owner_dispatched_worker_starts_run_and_running_state_atomically(
    tmp_path: Path,
) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch-1",
        request_id="dispatch-request-1",
        task_id="t1",
        task_state_version=0,
        execution_target_id="m3-sub",
        authority="OWNER_INITIATED_EXECUTION",
    )
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    store.acquire_writer("t1", "writer-1")
    ready = store.transition_task("t1", TaskState.READY, expected_version=0)

    running = store.start_dispatched_worker(
        dispatch_id="dispatch-1",
        task_id="t1",
        expected_task_version=ready.state_version,
        run_id="run-1",
        worker_id="m3-sub",
        writer_token="writer-1",
        pid=1234,
    )

    assert running.state is TaskState.RUNNING
    assert running.state_version == ready.state_version + 1
    assert _run_status(store, "run-1") == "RUNNING"
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request-1")
    assert dispatch.status is OwnerDispatchStatus.STARTED
    assert dispatch.started_at is not None
    store.assert_running_invariant("t1")


def test_owner_dispatched_worker_requires_writer_lock_before_running(
    tmp_path: Path,
) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.reserve_owner_dispatch(
        dispatch_id="dispatch-1",
        request_id="dispatch-request-1",
        task_id="t1",
        task_state_version=0,
        execution_target_id="m3-sub",
        authority="OWNER_INITIATED_EXECUTION",
    )
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    ready = store.transition_task("t1", TaskState.READY, expected_version=0)

    with pytest.raises(RuntimeError, match="active writer lock"):
        store.start_dispatched_worker(
            dispatch_id="dispatch-1",
            task_id="t1",
            expected_task_version=ready.state_version,
            run_id="run-1",
            worker_id="m3-sub",
            writer_token="writer-1",
        )

    assert store.get_task("t1").state is TaskState.READY
    row = store.connection.execute("SELECT run_id FROM runs WHERE run_id='run-1'").fetchone()
    assert row is None
    dispatch = store.get_owner_dispatch_by_request_id("dispatch-request-1")
    assert dispatch.status is OwnerDispatchStatus.RESERVED


def test_running_invariant_rejects_running_task_without_active_run(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="codex/t1",
        base_sha="abc123",
    )
    store.acquire_writer("t1", "writer-1")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)

    with pytest.raises(RuntimeError, match="exactly one active run"):
        store.assert_running_invariant("t1")


def test_startup_reconciliation_rolls_back_as_one_unit_on_audit_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _running_store(tmp_path / "state.sqlite3")
    store.start_run(run_id="run-1", task_id="t1", worker_id="worker")

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected reconciliation audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected reconciliation audit failure"):
        store.reconcile_startup()

    assert store.get_task("t1").state is TaskState.RUNNING
    assert _run_status(store, "run-1") == "RUNNING"
