"""Host-owned transition helpers joining worker lifecycle and deterministic verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verifier import VerificationResult


_TERMINAL_STATES = {TaskState.FAILED, TaskState.CANCELLED, TaskState.COMPLETED}


def start_worker_run(
    store: SafetyKernelStore,
    *,
    task_id: str,
    run_id: str,
    worker_id: str,
    writer_token: str,
    pid: int | None = None,
) -> None:
    """Start one worker only when the host-owned task worktree lock is held."""

    task = store.get_task(task_id)
    if task.state is not TaskState.RUNNING:
        raise ValueError("worker run can only start for a RUNNING task")
    workspace = store.get_workspace(task_id)
    if workspace.writer_token != writer_token:
        raise RuntimeError("worker run requires ownership of the task worktree writer lock")
    active = store.connection.execute(
        "SELECT run_id FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
        (task_id,),
    ).fetchone()
    if active is not None:
        raise RuntimeError("task already has an active worker run")
    store.start_run(run_id=run_id, task_id=task_id, worker_id=worker_id, pid=pid)


def _require_active_run_for_task(
    store: SafetyKernelStore,
    *,
    task_id: str,
    run_id: str,
) -> None:
    row = store.connection.execute(
        "SELECT task_id,status FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(run_id)
    if row["task_id"] != task_id:
        raise ValueError("run_id does not belong to task_id")
    if row["status"] != "RUNNING":
        raise RuntimeError("worker run is not active")


def record_worker_exit(
    store: SafetyKernelStore,
    *,
    task_id: str,
    run_id: str,
    exit_code: int,
    worker_result: Any,
) -> TaskState:
    """Record a worker process outcome without ever granting VERIFIED authority."""

    task = store.get_task(task_id)
    if task.state is not TaskState.RUNNING:
        raise ValueError("worker exit can only be recorded for a RUNNING task")
    _require_active_run_for_task(store, task_id=task_id, run_id=run_id)

    if exit_code != 0:
        store.finish_run(run_id, status="FAILED", result={"exit_code": exit_code})
        return store.transition_task(
            task_id,
            TaskState.BLOCKED,
            expected_version=task.state_version,
            reason=f"worker exited unexpectedly with code {exit_code}",
        ).state

    if not isinstance(worker_result, dict):
        store.finish_run(run_id, status="INVALID_RESULT", result={"exit_code": exit_code})
        return store.transition_task(
            task_id,
            TaskState.BLOCKED,
            expected_version=task.state_version,
            reason="worker returned an invalid structured result",
        ).state

    store.finish_run(run_id, status="FINISHED", result=worker_result)
    return store.transition_task(
        task_id,
        TaskState.WORKER_FINISHED,
        expected_version=task.state_version,
        reason="worker process finished; deterministic verification still required",
    ).state


def begin_verification(store: SafetyKernelStore, *, task_id: str) -> None:
    task = store.get_task(task_id)
    if task.state is not TaskState.WORKER_FINISHED:
        raise ValueError("verification can only begin after WORKER_FINISHED")
    store.transition_task(
        task_id,
        TaskState.VERIFYING,
        expected_version=task.state_version,
        reason="host deterministic verification started",
    )


def apply_verification_result(
    store: SafetyKernelStore,
    *,
    task_id: str,
    result: VerificationResult,
) -> TaskState:
    """Only a passing host result with immutable evidence can advance to VERIFIED."""

    task = store.get_task(task_id)
    if task.state is not TaskState.VERIFYING:
        raise ValueError("verification result requires VERIFYING state")

    has_authoritative_pass = result.passed and result.evidence_id is not None
    target = TaskState.VERIFIED if has_authoritative_pass else TaskState.BLOCKED
    if result.passed and result.evidence_id is None:
        reason = "passing verifier result is missing immutable host evidence"
    elif result.passed:
        reason = f"deterministic verification passed: {result.evidence_id}"
    else:
        reason = result.failure_reason or "deterministic verification failed"
    return store.transition_task(
        task_id,
        target,
        expected_version=task.state_version,
        reason=reason,
    ).state


def reconcile_workspace_truth(store: SafetyKernelStore) -> tuple[str, ...]:
    """Fail closed tasks whose registered worktree has disappeared.

    Stale writer locks on already-blocked/terminal tasks are released by the host using the exact
    stored token. No worker is trusted to repair its own isolation boundary.
    """

    blocked: list[str] = []
    rows = store.connection.execute(
        "SELECT task_id,worktree_path,writer_token FROM workspaces ORDER BY task_id"
    ).fetchall()
    for row in rows:
        task = store.get_task(row["task_id"])
        worktree_exists = Path(row["worktree_path"]).exists()
        if not worktree_exists and task.state not in _TERMINAL_STATES | {TaskState.BLOCKED}:
            store.transition_task(
                task.task_id,
                TaskState.BLOCKED,
                expected_version=task.state_version,
                reason="registered task worktree is missing",
            )
            blocked.append(task.task_id)
            task = store.get_task(task.task_id)
        writer_token = row["writer_token"]
        if writer_token and task.state in _TERMINAL_STATES | {TaskState.BLOCKED}:
            store.release_writer(task.task_id, writer_token)
    return tuple(blocked)


__all__ = [
    "apply_verification_result",
    "begin_verification",
    "record_worker_exit",
    "reconcile_workspace_truth",
    "start_worker_run",
]
