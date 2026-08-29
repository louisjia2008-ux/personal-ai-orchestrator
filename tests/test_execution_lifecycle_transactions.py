from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import record_worker_exit, start_worker_run
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState


def _prepared_store(tmp_path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "writer-1")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    return store


def test_worker_start_rolls_back_if_audit_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = _prepared_store(tmp_path)

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        start_worker_run(
            store,
            task_id="t1",
            run_id="run-1",
            worker_id="worker",
            writer_token="writer-1",
        )

    row = store.connection.execute("SELECT run_id FROM runs WHERE run_id='run-1'").fetchone()
    assert row is None
    assert store.get_task("t1").state is TaskState.RUNNING


def test_worker_exit_rolls_back_run_and_task_if_audit_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _prepared_store(tmp_path)
    start_worker_run(
        store,
        task_id="t1",
        run_id="run-1",
        worker_id="worker",
        writer_token="writer-1",
    )

    original_audit = store._audit
    calls = 0

    def fail_second_audit(*args, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second audit failure")
        original_audit(*args, **kwargs)

    monkeypatch.setattr(store, "_audit", fail_second_audit)
    with pytest.raises(RuntimeError, match="injected second audit failure"):
        record_worker_exit(
            store,
            task_id="t1",
            run_id="run-1",
            exit_code=0,
            worker_result={"status": "finished"},
        )

    run = store.connection.execute(
        "SELECT status,finished_at,result_json FROM runs WHERE run_id='run-1'"
    ).fetchone()
    assert run is not None
    assert run["status"] == "RUNNING"
    assert run["finished_at"] is None
    assert run["result_json"] is None
    task = store.get_task("t1")
    assert task.state is TaskState.RUNNING
    assert task.state_version == 2


def test_worker_exit_commits_run_and_task_together(tmp_path: Path) -> None:
    store = _prepared_store(tmp_path)
    start_worker_run(
        store,
        task_id="t1",
        run_id="run-1",
        worker_id="worker",
        writer_token="writer-1",
    )

    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )

    assert state is TaskState.WORKER_FINISHED
    assert store.get_task("t1").state is TaskState.WORKER_FINISHED
    run = store.connection.execute(
        "SELECT status FROM runs WHERE run_id='run-1'"
    ).fetchone()
    assert run is not None
    assert run["status"] == "FINISHED"
