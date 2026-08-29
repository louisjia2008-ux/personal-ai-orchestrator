from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    record_worker_exit,
    reconcile_workspace_truth,
    start_worker_run,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verifier import VerificationResult


def _running_store(tmp_path: Path) -> SafetyKernelStore:
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
    start_worker_run(
        store,
        run_id="run-1",
        task_id="t1",
        worker_id="worker",
        writer_token="writer-1",
    )
    return store


def _finish_worker(store: SafetyKernelStore) -> None:
    record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    begin_verification(store, task_id="t1")


def test_worker_run_requires_writer_lock(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(tmp_path / "worktree"),
        branch="task/t1",
        base_sha="abc",
    )
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    with pytest.raises(RuntimeError, match="writer lock"):
        start_worker_run(
            store,
            run_id="run-1",
            task_id="t1",
            worker_id="worker",
            writer_token="not-owner",
        )


def test_task_cannot_have_two_active_worker_runs(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    with pytest.raises(RuntimeError, match="already has an active worker run"):
        start_worker_run(
            store,
            run_id="run-2",
            task_id="t1",
            worker_id="worker-2",
            writer_token="writer-1",
        )


def test_worker_exit_rejects_run_from_other_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    store.submit_task(task_id="t2", request_id="r2", intent="implement")
    store.register_workspace(
        task_id="t2",
        repo_path=str(tmp_path / "repo2"),
        worktree_path=str(tmp_path / "worktree2"),
        branch="task/t2",
        base_sha="def",
    )
    store.acquire_writer("t2", "writer-2")
    store.transition_task("t2", TaskState.READY)
    store.transition_task("t2", TaskState.RUNNING)
    start_worker_run(
        store,
        run_id="run-2",
        task_id="t2",
        worker_id="worker-2",
        writer_token="writer-2",
    )

    with pytest.raises(ValueError, match="does not belong"):
        record_worker_exit(
            store,
            task_id="t1",
            run_id="run-2",
            exit_code=0,
            worker_result={"status": "finished"},
        )
    assert store.get_task("t1").state is TaskState.RUNNING
    assert store.get_task("t2").state is TaskState.RUNNING


def test_unexpected_worker_exit_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=2,
        worker_result=None,
    )
    assert state is TaskState.BLOCKED


def test_invalid_worker_result_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result="COMPLETE",
    )
    assert state is TaskState.BLOCKED


def test_worker_success_stops_at_worker_finished_until_verifier_evidence_passes(
    tmp_path: Path,
) -> None:
    store = _running_store(tmp_path)
    state = record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    assert state is TaskState.WORKER_FINISHED
    begin_verification(store, task_id="t1")
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-fixture",
    )
    assert apply_verification_result(store, task_id="t1", result=result) is TaskState.VERIFIED


def test_missing_verifier_evidence_blocks_even_claimed_pass(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=True,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id=None,
    )
    assert apply_verification_result(store, task_id="t1", result=result) is TaskState.BLOCKED


def test_failing_verifier_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    _finish_worker(store)
    result = VerificationResult(
        profile="fixture",
        passed=False,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
        evidence_id="verify-failure",
        failure_reason="injected failing test",
    )
    assert apply_verification_result(store, task_id="t1", result=result) is TaskState.BLOCKED


def test_missing_worktree_blocks_and_clears_stale_writer_lock(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    missing = tmp_path / "does-not-exist"
    store.register_workspace(
        task_id="t1",
        repo_path=str(tmp_path / "repo"),
        worktree_path=str(missing),
        branch="task/t1",
        base_sha="abc",
    )
    store.acquire_writer("t1", "writer-1")
    assert reconcile_workspace_truth(store) == ("t1",)
    assert store.get_task("t1").state is TaskState.BLOCKED
    assert store.get_workspace("t1").writer_token is None
