"""Host-owned transition helpers joining worker lifecycle and deterministic verification."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.process_supervisor import ProcessSupervisor, SupervisedProcess
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.shadow_evidence import (
    ShadowEvidenceJournal,
    ShadowFailureClass,
    ShadowFailureStage,
    ShadowQualityOutcome,
)
from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult


_TERMINAL_STATES = {TaskState.FAILED, TaskState.CANCELLED, TaskState.COMPLETED}


def _render_result(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def start_worker_run(
    store: SafetyKernelStore,
    *,
    task_id: str,
    run_id: str,
    worker_id: str,
    writer_token: str,
    pid: int | None = None,
) -> None:
    """Atomically authorize and persist one worker run for a host-owned task worktree."""

    store.connection.execute("BEGIN IMMEDIATE")
    try:
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
        started_at = datetime.now(UTC).isoformat()
        store.connection.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
            (run_id, task_id, worker_id, pid, "RUNNING", started_at, None, None),
        )
        store._audit(
            task_id,
            "RUN_STARTED",
            {"run_id": run_id, "worker_id": worker_id, "pid": pid},
        )
        store.connection.execute("COMMIT")
    except sqlite3.IntegrityError as error:
        store.connection.execute("ROLLBACK")
        raise RuntimeError("worker run violates durable run ownership constraints") from error
    except Exception:
        store.connection.execute("ROLLBACK")
        raise


def record_worker_exit(
    store: SafetyKernelStore,
    *,
    task_id: str,
    run_id: str,
    exit_code: int,
    worker_result: Any,
) -> TaskState:
    """Atomically record worker exit and its corresponding task-state transition.

    The run row, audit entries and task transition commit as one SQLite transaction. A crash or
    audit failure therefore cannot leave a FINISHED run paired with a still-RUNNING task.
    """

    store.connection.execute("BEGIN IMMEDIATE")
    try:
        task = store.get_task(task_id)
        if task.state is not TaskState.RUNNING:
            raise ValueError("worker exit can only be recorded for a RUNNING task")
        run = store.connection.execute(
            "SELECT task_id,status FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        if run["task_id"] != task_id:
            raise ValueError("run_id does not belong to task_id")
        if run["status"] != "RUNNING":
            raise RuntimeError("worker run is not active")

        if exit_code != 0:
            run_status = "FAILED"
            persisted_result: Any = {"exit_code": exit_code}
            next_state = TaskState.BLOCKED
            reason = f"worker exited unexpectedly with code {exit_code}"
        elif not isinstance(worker_result, dict):
            run_status = "INVALID_RESULT"
            persisted_result = {"exit_code": exit_code}
            next_state = TaskState.BLOCKED
            reason = "worker returned an invalid structured result"
        else:
            run_status = "FINISHED"
            persisted_result = worker_result
            next_state = TaskState.WORKER_FINISHED
            reason = "worker process finished; deterministic verification still required"

        stamp = datetime.now(UTC).isoformat()
        updated_run = store.connection.execute(
            """
            UPDATE runs SET status=?, finished_at=?, result_json=?
            WHERE run_id=? AND status='RUNNING'
            """,
            (run_status, stamp, _render_result(persisted_result), run_id),
        )
        if updated_run.rowcount != 1:
            raise RuntimeError("worker exit lost run-state concurrency race")

        next_version = task.state_version + 1
        updated_task = store.connection.execute(
            """
            UPDATE tasks SET state=?, state_version=?, updated_at=?
            WHERE task_id=? AND state_version=? AND state=?
            """,
            (
                next_state.value,
                next_version,
                stamp,
                task_id,
                task.state_version,
                TaskState.RUNNING.value,
            ),
        )
        if updated_task.rowcount != 1:
            raise RuntimeError("worker exit lost task-state concurrency race")

        store._audit(task_id, "RUN_FINISHED", {"run_id": run_id, "status": run_status})
        store._audit(
            task_id,
            "TASK_STATE_CHANGED",
            {
                "from": TaskState.RUNNING.value,
                "to": next_state.value,
                "state_version": next_version,
                "reason": reason,
            },
        )
        store.connection.execute("COMMIT")
        return next_state
    except Exception:
        store.connection.execute("ROLLBACK")
        raise


async def cancel_worker_run(
    store: SafetyKernelStore,
    supervisor: ProcessSupervisor,
    supervised: SupervisedProcess,
    *,
    task_id: str,
    run_id: str,
    writer_token: str,
    grace_seconds: float = 2.0,
) -> TaskState:
    """Cancel one exact owned process, then atomically close run/task/writer state.

    Any active model-switch lease is aborted first so cancellation cannot deadlock behind the
    bounded state freeze. The persisted run PID must match the exact process owned by the process
    supervisor; no broad process lookup or signalling is permitted.
    """

    task = store.get_task(task_id)
    if task.state is not TaskState.RUNNING:
        raise ValueError("worker cancellation requires a RUNNING task")
    run = store.connection.execute(
        "SELECT task_id,status,pid FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise KeyError(run_id)
    if run["task_id"] != task_id or run["status"] != "RUNNING":
        raise RuntimeError("worker cancellation requires the active run owned by the task")
    if run["pid"] != supervised.pid:
        raise RuntimeError("persisted run PID does not match the supervised process")
    workspace = store.get_workspace(task_id)
    if workspace.writer_token != writer_token:
        raise RuntimeError("worker cancellation requires the exact writer token")

    SwitchLeaseAuthority(store).abort_for_task(task_id, reason="worker cancellation")
    exit_code = await supervisor.cancel(supervised, grace_seconds=grace_seconds)

    store.connection.execute("BEGIN IMMEDIATE")
    try:
        task = store.get_task(task_id)
        if task.state is not TaskState.RUNNING:
            raise RuntimeError("task state changed while cancellation was in progress")
        run = store.connection.execute(
            "SELECT task_id,status,pid FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or run["task_id"] != task_id
            or run["status"] != "RUNNING"
            or run["pid"] != supervised.pid
        ):
            raise RuntimeError("run state changed while cancellation was in progress")
        workspace = store.get_workspace(task_id)
        if workspace.writer_token != writer_token:
            raise RuntimeError("writer ownership changed while cancellation was in progress")

        stamp = datetime.now(UTC).isoformat()
        updated_run = store.connection.execute(
            "UPDATE runs SET status='CANCELLED',finished_at=?,result_json=? "
            "WHERE run_id=? AND status='RUNNING'",
            (stamp, _render_result({"exit_code": exit_code}), run_id),
        )
        if updated_run.rowcount != 1:
            raise RuntimeError("worker cancellation lost run-state concurrency race")

        next_version = task.state_version + 1
        updated_task = store.connection.execute(
            "UPDATE tasks SET state=?,state_version=?,updated_at=? "
            "WHERE task_id=? AND state_version=? AND state=?",
            (
                TaskState.CANCELLED.value,
                next_version,
                stamp,
                task_id,
                task.state_version,
                TaskState.RUNNING.value,
            ),
        )
        if updated_task.rowcount != 1:
            raise RuntimeError("worker cancellation lost task-state concurrency race")

        released = store.connection.execute(
            "UPDATE workspaces SET writer_token=NULL,writer_acquired_at=NULL "
            "WHERE task_id=? AND writer_token=?",
            (task_id, writer_token),
        )
        if released.rowcount != 1:
            raise RuntimeError("worker cancellation lost writer ownership race")

        store._audit(task_id, "RUN_FINISHED", {"run_id": run_id, "status": "CANCELLED"})
        store._audit(
            task_id,
            "TASK_STATE_CHANGED",
            {
                "from": TaskState.RUNNING.value,
                "to": TaskState.CANCELLED.value,
                "state_version": next_version,
                "reason": "host cancelled the exact supervised worker process",
            },
        )
        store._audit(task_id, "WRITER_RELEASED", {"writer_token": writer_token})
        store.connection.execute("COMMIT")
        return TaskState.CANCELLED
    except Exception:
        store.connection.execute("ROLLBACK")
        raise


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


def _persisted_evidence_matches(
    journal: VerificationEvidenceJournal,
    result: VerificationResult,
) -> bool:
    if result.evidence_id is None:
        return False
    try:
        persisted = journal.load(result.evidence_id)
    except Exception:
        return False
    return persisted == result


def apply_verification_result(
    store: SafetyKernelStore,
    *,
    task_id: str,
    result: VerificationResult,
    evidence_journal: VerificationEvidenceJournal,
    shadow_journal: ShadowEvidenceJournal | None = None,
    shadow_pending_id: str | None = None,
    shadow_reset_cycle_ids: tuple[str, ...] = (),
    shadow_quota_after_snapshot_ids: tuple[str, ...] = (),
    shadow_observed_burn_fraction: float | None = None,
    shadow_execution_success: bool = True,
    shadow_verification_success: bool | None = None,
    shadow_quality_outcome: ShadowQualityOutcome | None = None,
    shadow_failure_class: ShadowFailureClass | None = None,
    shadow_failure_stage: ShadowFailureStage | None = None,
    shadow_regression_detected: bool = False,
    shadow_attempts_to_green: int | None = None,
    shadow_time_to_green_seconds: float | None = None,
    shadow_handoff_count: int = 0,
) -> TaskState:
    """Advance to VERIFIED only when the exact host result is durably journaled.

    A non-null ``evidence_id`` is only an identifier, not authority. The immutable journal must
    already contain the exact result before this transition is allowed; forged or mismatched
    in-memory results fail closed to BLOCKED.
    """

    task = store.get_task(task_id)
    if task.state is not TaskState.VERIFYING:
        raise ValueError("verification result requires VERIFYING state")

    evidence_matches = _persisted_evidence_matches(evidence_journal, result)
    has_authoritative_pass = result.passed and evidence_matches
    target = TaskState.VERIFIED if has_authoritative_pass else TaskState.BLOCKED
    if result.passed and result.evidence_id is None:
        reason = "passing verifier result is missing immutable host evidence"
    elif result.passed and not evidence_matches:
        reason = "passing verifier result is not backed by matching persisted host evidence"
    elif result.passed:
        reason = f"deterministic verification passed: {result.evidence_id}"
    else:
        reason = result.failure_reason or "deterministic verification failed"
    next_state = store.transition_task(
        task_id,
        target,
        expected_version=task.state_version,
        reason=reason,
    ).state
    if shadow_journal is not None and shadow_pending_id is not None:
        shadow_journal.finalize_pending(
            shadow_pending_id,
            reset_cycle_ids=shadow_reset_cycle_ids,
            quota_after_snapshot_ids=shadow_quota_after_snapshot_ids,
            observed_burn_fraction=shadow_observed_burn_fraction,
            execution_success=shadow_execution_success,
            verification_success=shadow_verification_success,
            quality_outcome=shadow_quality_outcome,
            failure_class=shadow_failure_class,
            failure_stage=shadow_failure_stage,
            verified=has_authoritative_pass,
            regression_detected=shadow_regression_detected,
            attempts_to_green=shadow_attempts_to_green,
            time_to_green_seconds=shadow_time_to_green_seconds,
            handoff_count=shadow_handoff_count,
            observed_at=datetime.now(UTC),
        )
    return next_state


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
    "cancel_worker_run",
    "record_worker_exit",
    "reconcile_workspace_truth",
    "start_worker_run",
]
