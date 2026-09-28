"""Host-owned transition helpers joining worker lifecycle and deterministic verification."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.process_supervisor import ProcessSupervisor, SupervisedProcess
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.safety_kernel import (
    SafetyKernelStore,
    ShadowFinalizationIntent,
    TaskState,
    shadow_identity_payload,
)
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

# Execution-verification evidence older than this no longer authorizes
# launch; the runtime surface must be re-proven by a fresh real worker
# invocation.
EXECUTION_EVIDENCE_MAX_AGE_SECONDS = 30 * 24 * 3600.0


@dataclass(frozen=True)
class ExecutionVerificationProjection:
    """Separate diagnostic verification history from current launch authority."""

    historical_verified: bool
    launch_verified: bool
    verified_stale: bool
    verified_observed_at: datetime | None


def project_execution_target_verification(
    registry: ModelRegistry,
    *,
    execution_target_id: str,
    execution_evidence_journal: Any = None,
    now: datetime | None = None,
) -> ExecutionVerificationProjection:
    """Project one canonical verification view for clients and recommenders.

    Static ``ExecutionTarget.execution_verified`` remains authoritative. For
    dynamic evidence, diagnostic history keeps the existing demote-fallback,
    while ``launch_verified`` uses the exact same latest-row + 30-day rule as
    the launch boundary. Age expiry therefore never leaves an actionable UI
    target merely because an older VERIFIED row is still useful history.
    """

    try:
        target = registry.execution_targets[execution_target_id]
    except KeyError:
        raise RuntimeError("execution target is not in the registry") from None

    if target.execution_verified:
        return ExecutionVerificationProjection(
            historical_verified=True,
            launch_verified=True,
            verified_stale=False,
            verified_observed_at=None,
        )

    journal = execution_evidence_journal
    if journal is None:
        return ExecutionVerificationProjection(
            historical_verified=False,
            launch_verified=False,
            verified_stale=False,
            verified_observed_at=None,
        )

    historical = None
    stale_since = None
    launch_verified = False
    try:
        historical, stale_since = journal.latest_verified_for_target(execution_target_id)
        launch_verified = journal.target_has_verified_evidence(
            execution_target_id,
            max_age_seconds=EXECUTION_EVIDENCE_MAX_AGE_SECONDS,
            now=now,
        )
    except Exception:
        historical, stale_since, launch_verified = None, None, False

    return ExecutionVerificationProjection(
        historical_verified=historical is not None,
        launch_verified=bool(launch_verified),
        verified_stale=(
            historical is not None and (stale_since is not None or not launch_verified)
        ),
        verified_observed_at=(historical.observed_at if historical is not None else None),
    )


def execution_target_has_launch_verification(
    registry: ModelRegistry,
    *,
    execution_target_id: str,
    execution_evidence_journal: Any = None,
    now: datetime | None = None,
) -> bool:
    """Return the exact verification authority used by the launch gate.

    Historical VERIFIED evidence remains useful for diagnostics, but it only
    authorizes launch when it is the latest target evidence and is within the
    canonical age cap. A registry target explicitly marked execution_verified
    remains authoritative by design.
    """

    target = registry.execution_targets.get(execution_target_id)
    if target is None:
        return False
    if target.execution_verified:
        return True
    journal = execution_evidence_journal
    if journal is None:
        return False
    try:
        latest = journal.latest_for_target(execution_target_id)
    except Exception:
        return False
    if latest is None or not latest.establishes_verified:
        return False
    observed_now = now or datetime.now(UTC)
    age = (observed_now - latest.observed_at).total_seconds()
    return 0 <= age <= EXECUTION_EVIDENCE_MAX_AGE_SECONDS


def _render_result(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _failure_result_payload(
    *,
    exit_code: int | None,
    signal: int | None,
    worker_result: Any,
) -> dict[str, Any]:
    """Build a fail-closed run-result payload from the worker's sanitized narration.

    Used by both the normal failure path (non-zero exit code) and the
    emergency-repair path (process died, no envelope, possibly killed by a
    signal). The returned dict carries only what the owner needs to make
    sense of the death: exit code (or signal name), and the worker's own
    ``stdout_tail`` / ``stderr_tail`` if it had a chance to write them.
    Host-derived metadata (hashes, byte counts, ``timed_out``) is dropped
    here on purpose — it carries zero authority and would only obscure the
    real cause.
    """

    payload: dict[str, Any] = {}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if signal is not None:
        payload["signal"] = signal
    if isinstance(worker_result, dict):
        if isinstance(worker_result.get("stderr_tail"), str):
            payload["stderr_tail"] = worker_result["stderr_tail"]
        if isinstance(worker_result.get("stdout_tail"), str):
            payload["stdout_tail"] = worker_result["stdout_tail"]
    return payload


def _human_reason_for_failure(*, exit_code: int | None, signal: int | None) -> str:
    """One-line reason a human can read off the run-row.

    Mirrors the audit-reason style so the run row's stored ``reason`` and
    the persisted run-row's ``result_json`` agree on what happened.
    """

    if signal is not None:
        return f"worker exited unexpectedly (signal {signal})"
    if exit_code is not None:
        return f"worker exited unexpectedly with code {exit_code}"
    return "worker exited unexpectedly"


def validate_execution_target_launch(
    registry: ModelRegistry,
    *,
    execution_target_id: str,
    runtime_available: bool,
    execution_evidence_journal: Any = None,
    now: datetime | None = None,
) -> None:
    """Fail closed unless the selected target is actually launchable.

    ``execution_verified`` defaults to False everywhere; the only
    launch-authorizing alternative is durable evidence that a REAL worker
    invocation on this exact target previously succeeded.
    """

    try:
        target = registry.execution_targets[execution_target_id]
    except KeyError:
        raise RuntimeError("execution target is not in the registry") from None
    if not target.enabled:
        raise RuntimeError("execution target is disabled")
    if not execution_target_has_launch_verification(
        registry,
        execution_target_id=execution_target_id,
        execution_evidence_journal=execution_evidence_journal,
    ):
        raise RuntimeError("execution target has not been runtime-verified")
    if not runtime_available:
        raise RuntimeError("execution runtime is unavailable")


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
            # Keep the worker's own sanitized narration on failure so the
            # owner can see WHY the worker died ("Usage limit reached for
            # 5 hour" and similar). Drop everything else: a failed worker's
            # host-derived metadata (hashes, byte counts) has no authority
            # and no value, so the failure record stays fail-closed apart
            # from the narration itself.
            persisted_result = _failure_result_payload(
                exit_code=exit_code, signal=None, worker_result=worker_result
            )
            next_state = TaskState.BLOCKED
            reason = _human_reason_for_failure(exit_code=exit_code, signal=None)
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
    shadow_dispatch_id: str = "",
    shadow_reset_cycle_ids: tuple[str, ...] = (),
    shadow_quota_after_snapshot_ids: tuple[str, ...] = (),
    shadow_observed_burn_fraction: float | None = None,
    shadow_execution_success: bool = True,
    shadow_main_repo_unchanged: bool = True,
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

    Round 3 (crash-consistency): when a shadow pending is supplied, the
    terminal transition and a DURABLE shadow-finalization intent commit
    in ONE SQLite transaction (``apply_verification_outcome``); the
    filesystem finalize is then replayed post-COMMIT through the
    idempotent finalize-outbox drain. A crash between the terminal
    COMMIT and the filesystem finalize can no longer lose the real
    observation — the intent replays it byte-identically (``observed_at``
    is pinned durably at enqueue time). The pending identity is loaded
    BEFORE any mutation: a missing/malformed pending fails closed while
    the task is still VERIFYING.

    Round 4 (final outcome ordering): the immutable intent freezes the
    FINAL HOST-AUTHORITATIVE outcome, not the intermediate verifier
    verdict. The caller supplies the main-repo immutability result
    (computed BEFORE this call) and this helper composes both facts into
    ONE terminal commit: verifier PASS + main repo mutated ⇒ BLOCKED
    with a truthful ``verified=False`` / ``verification_success=True``
    shadow — there is no normal VERIFIED → BLOCKED downgrade after the
    intent exists.
    """

    task = store.get_task(task_id)
    if task.state is not TaskState.VERIFYING:
        raise ValueError("verification result requires VERIFYING state")

    evidence_matches = _persisted_evidence_matches(evidence_journal, result)
    has_authoritative_pass = result.passed and evidence_matches
    main_repo_mutated = has_authoritative_pass and not shadow_main_repo_unchanged
    final_verified = has_authoritative_pass and not main_repo_mutated
    target = TaskState.VERIFIED if final_verified else TaskState.BLOCKED
    if result.passed and result.evidence_id is None:
        reason = "passing verifier result is missing immutable host evidence"
    elif result.passed and not evidence_matches:
        reason = "passing verifier result is not backed by matching persisted host evidence"
    elif main_repo_mutated:
        reason = "main repository mutated during owner dispatch"
    elif result.passed:
        reason = f"deterministic verification passed: {result.evidence_id}"
    else:
        reason = result.failure_reason or "deterministic verification failed"

    finalize_intent = None
    if shadow_journal is not None and shadow_pending_id is not None:
        # Load the pending identity BEFORE mutating: the observation's
        # identity keys (routing request id + frozen decision id) must
        # be captured durably, and a missing pending must fail closed
        # while the task is still VERIFYING.
        pending = shadow_journal.load_pending(shadow_pending_id)
        finalize_intent = ShadowFinalizationIntent(
            pending_id=shadow_pending_id,
            task_id=task_id,
            dispatch_id=shadow_dispatch_id,
            request_id=pending.request_id,
            decision_id=pending.decision_id,
            verified=final_verified,
            execution_success=shadow_execution_success,
            verification_success=(
                # The verifier's OWN authoritative result — distinct
                # from the composed final verdict: a passed verifier
                # plus a mutated main repo keeps verification_success
                # True while verified becomes False (round 4 §6).
                shadow_verification_success
                if shadow_verification_success is not None
                else has_authoritative_pass
            ),
            quality_outcome=(
                shadow_quality_outcome.value
                if shadow_quality_outcome is not None
                # Host-safety failure, not a model/verifier quality
                # failure: the most accurate existing taxonomy for a
                # mutated main repo (round 4 §6).
                else (ShadowQualityOutcome.OPERATIONAL_FAILED.value if main_repo_mutated else None)
            ),
            failure_class=(
                shadow_failure_class.value
                if shadow_failure_class is not None
                else (ShadowFailureClass.INFRA_FAILURE.value if main_repo_mutated else None)
            ),
            failure_stage=(
                shadow_failure_stage.value
                if shadow_failure_stage is not None
                else (ShadowFailureStage.INFRASTRUCTURE.value if main_repo_mutated else None)
            ),
            regression_detected=shadow_regression_detected,
            attempts_to_green=shadow_attempts_to_green,
            time_to_green_seconds=shadow_time_to_green_seconds,
            handoff_count=shadow_handoff_count,
            reset_cycle_ids=shadow_reset_cycle_ids,
            quota_after_snapshot_ids=shadow_quota_after_snapshot_ids,
            observed_burn_fraction=shadow_observed_burn_fraction,
            observed_at=datetime.now(UTC).isoformat(),
            identity_json=shadow_identity_payload(pending),
        )
    next_state = store.apply_verification_outcome(
        task_id,
        expected_version=task.state_version,
        target=target,
        reason=reason,
        finalize=finalize_intent,
    )
    if finalize_intent is not None:
        # Best-effort inline drain: replays the durable intent so the
        # common case finalizes + discards immediately; a failure only
        # leaves the intent open for the next drain (tick / restart).
        from personal_ai_orchestrator.supervised_auto_step import (
            drain_auto_shadow_finalize_outbox,
        )

        drain_auto_shadow_finalize_outbox(store, shadow_journal)
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
    "validate_execution_target_launch",
]
