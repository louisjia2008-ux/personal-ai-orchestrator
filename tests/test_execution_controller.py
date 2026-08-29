from pathlib import Path

from personal_ai_orchestrator.execution_controller import (
    apply_verification_result,
    begin_verification,
    record_worker_exit,
    reconcile_workspace_truth,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verifier import VerificationResult


def _running_store(tmp_path: Path) -> SafetyKernelStore:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="r1", intent="implement")
    store.transition_task("t1", TaskState.READY)
    store.transition_task("t1", TaskState.RUNNING)
    store.start_run(run_id="run-1", task_id="t1", worker_id="worker")
    return store


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


def test_worker_success_stops_at_worker_finished_until_verifier_passes(tmp_path: Path) -> None:
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
    )
    assert apply_verification_result(store, task_id="t1", result=result) is TaskState.VERIFIED


def test_failing_verifier_blocks_task(tmp_path: Path) -> None:
    store = _running_store(tmp_path)
    record_worker_exit(
        store,
        task_id="t1",
        run_id="run-1",
        exit_code=0,
        worker_result={"status": "finished"},
    )
    begin_verification(store, task_id="t1")
    result = VerificationResult(
        profile="fixture",
        passed=False,
        changed_paths=(),
        unexpected_paths=(),
        stages=(),
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
