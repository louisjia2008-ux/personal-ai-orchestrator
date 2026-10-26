"""Owner configured project acceptance and explicit manual failure recovery.

Profiles live in the host database, outside every worker worktree. A retry only
returns a task to READY; it never launches or deletes the previous attempt.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.verifier import VerifierCommand, VerifierProfile

if TYPE_CHECKING:
    from personal_ai_orchestrator.control_api import ControlPlaneService
    from personal_ai_orchestrator.safety_kernel import SafetyKernelStore

_probe_lock = threading.Lock()


def _runtime_executor(service: ControlPlaneService, runtime_id: str):
    executor = service.dispatch_executor
    resolver = getattr(executor, "executor_for_runtime", None)
    return resolver(runtime_id) if resolver else executor


def probe_execution_target(service: ControlPlaneService, target_id: str):
    """An explicit owner click makes one bounded, tool-confined real call."""
    service._validate_identifier("execution_target_id", target_id)
    if not service.owner_initiated_execution_enabled:
        raise _error("owner_initiated_execution_disabled", 403)
    registry = service._effective_registry()
    target = registry.execution_targets.get(target_id)
    if target is None or not target.enabled:
        raise _error("execution_target_not_registered", 400)
    executor = _runtime_executor(service, target.runtime_id)
    journal = service.execution_evidence_journal
    if executor is None or journal is None or not service._runtime_available(target_id):
        raise _error("execution_runtime_unavailable", 503)
    model = registry.models[target.model_sku_id]
    if not _probe_lock.acquire(blocking=False):
        raise _error("execution_probe_in_progress")
    try:
        root = journal.directory.parent
        with tempfile.TemporaryDirectory(prefix="model-probe-", dir=root) as directory:
            kwargs = dict(
                provider_id=model.provider_id,
                execution_target_id=target.id,
                model_sku_id=target.model_sku_id,
                cwd=Path(directory),
                evidence_root=root,
                timeout_seconds=90,
            )
            if target.runtime_id == "pi":
                from personal_ai_orchestrator.pi_execution_probe import run_pi_execution_probe

                result = run_pi_execution_probe(pi_bin=executor.pi_runtime.pi_bin, **kwargs)
            elif target.runtime_id == "opencode":
                from personal_ai_orchestrator.execution_probe import run_execution_probe

                result = run_execution_probe(opencode_bin=executor.config.opencode_bin, **kwargs)
            else:
                raise _error("execution_runtime_unavailable", 503)
        return {key: result[key] for key in ("execution_target_id", "result", "reason_code")}
    finally:
        _probe_lock.release()


def _error(code: str, status: int = 409):
    from personal_ai_orchestrator.control_api import ControlPlaneError

    return ControlPlaneError(status, code)


class ProjectVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_lines: list[str] = Field(max_length=8)
    preparation_command_lines: list[str] = Field(default_factory=list, max_length=8)
    allowed_paths: list[str] = Field(default_factory=list, max_length=64)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)

    @model_validator(mode="after")
    def validate_commands(self):
        for line in self.command_lines + self.preparation_command_lines:
            if not line.strip() or len(line) > 4096 or "\n" in line or "\x00" in line:
                raise ValueError("invalid command line")
            if not shlex.split(line):
                raise ValueError("empty argv")
        for path in self.allowed_paths:
            parsed = PurePosixPath(path)
            if (
                not path
                or len(path) > 256
                or parsed.is_absolute()
                or any(part in {"..", ".git", ".pao"} for part in parsed.parts)
                or "\x00" in path
                or "\n" in path
            ):
                raise ValueError("invalid allowed path")
        return self


def load_project_verifier(store: SafetyKernelStore, project_id: str | None):
    row = store.connection.execute(
        "SELECT profile_json FROM project_verifiers WHERE project_id=?", (project_id,)
    ).fetchone()
    return VerifierProfile.model_validate_json(row[0]) if row else None


def project_verification(service: ControlPlaneService, project_id: str, payload=None):
    service.get_project(project_id)
    if payload is not None:
        request = ProjectVerificationRequest.model_validate(payload)
        profile = VerifierProfile(
            name=f"project-{project_id}",
            preparation_commands=tuple(
                VerifierCommand(
                    name=f"prepare-{i + 1}",
                    argv=tuple(shlex.split(line)),
                    timeout_seconds=request.timeout_seconds,
                )
                for i, line in enumerate(request.preparation_command_lines)
            ),
            commands=tuple(
                VerifierCommand(
                    name=f"check-{i + 1}",
                    argv=tuple(shlex.split(line)),
                    timeout_seconds=request.timeout_seconds,
                )
                for i, line in enumerate(request.command_lines)
            ),
            allowed_paths=tuple(request.allowed_paths),
        )
        with service.store.connection:
            service.store.connection.execute(
                "INSERT INTO project_verifiers VALUES(?,?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
                (project_id, profile.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            service.store._audit(
                None,
                "PROJECT_VERIFIER_CONFIGURED",
                {
                    "project_id": project_id,
                    "profile_sha256": sha256(profile.model_dump_json().encode()).hexdigest(),
                },
            )
    profile = load_project_verifier(service.store, project_id)
    return {
        "project_id": project_id,
        "configured": bool(profile and profile.commands),
        "command_lines": [shlex.join(c.argv) for c in profile.commands] if profile else [],
        "preparation_command_lines": [shlex.join(c.argv) for c in profile.preparation_commands]
        if profile
        else [],
        "allowed_paths": list(profile.allowed_paths) if profile else [],
        "timeout_seconds": int(profile.commands[0].timeout_seconds)
        if profile and profile.commands
        else 300,
    }


def _retry_reason(
    service: ControlPlaneService, task_id: str, dispatch, *, interrupted=False, reopening=False
):
    store = service.store
    task = store.get_task(task_id)
    expected = TaskState.VERIFIED if reopening else TaskState.BLOCKED
    if task.state is not expected or task.scheduling_policy != "MANUAL":
        return (
            "reopen_requires_verified_manual_task"
            if reopening
            else "retry_requires_blocked_manual_task"
        )
    if dispatch is None or dispatch["authority"] != "OWNER_INITIATED_EXECUTION":
        return "retry_requires_owner_dispatch"
    if store.connection.execute(
        "SELECT 1 FROM task_preparations WHERE task_id=? AND cleanup_state!='CONFIRMED'",
        (task_id,),
    ).fetchone():
        return "preparation_cleanup_unconfirmed"
    if dispatch["failure_code"] == "STARTUP_RECOVERY_REQUIRED" and not interrupted:
        return "use_interrupted_dispatch_recovery"
    if reopening and (dispatch["status"] != "FINISHED" or dispatch["failure_code"]):
        return "reopen_requires_successful_owner_dispatch"
    if not reopening and (
        dispatch["status"] not in {"BLOCKED", "FINISHED"} or not dispatch["failure_code"]
    ):
        return "retry_prior_dispatch_not_terminal"
    if task.manual_execution_target_id != dispatch["execution_target_id"]:
        return "manual_execution_target_mismatch"
    supervisor = getattr(service.dispatch_executor, "execution_supervisor", None)
    if supervisor is not None and supervisor.get(task_id) is not None:
        return "recovery_active_execution"
    if (
        store.connection.execute(
            "SELECT 1 FROM owner_dispatches WHERE task_id=? AND status IN ('RESERVED','STARTED')",
            (task_id,),
        ).fetchone()
        or store.connection.execute(
            "SELECT 1 FROM runs WHERE task_id=? AND status='RUNNING'",
            (task_id,),
        ).fetchone()
    ):
        return "recovery_active_execution"
    workspace = store.connection.execute(
        "SELECT * FROM workspaces WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if workspace and workspace["writer_token"]:
        return "recovery_writer_held"
    if reopening:
        if workspace is None or workspace["base_sha"] != task.base_sha:
            return "reopen_workspace_unavailable"
        from personal_ai_orchestrator.worktree_manager import WorktreeManager

        root = Path(workspace["worktree_path"])
        try:
            WorktreeManager(root.parent).adopt(
                repo_path=Path(workspace["repo_path"]),
                task_id=task_id,
                worktree_path=root,
                branch=workspace["branch"],
                base_sha=workspace["base_sha"],
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return "reopen_workspace_unavailable"
    if interrupted:
        receipt = store.connection.execute(
            "SELECT * FROM owner_dispatch_interruptions WHERE dispatch_id=?",
            (dispatch["dispatch_id"],),
        ).fetchone()
        if (
            receipt is None
            or receipt["attempt_id"] is None
            or workspace is None
            or receipt["interrupted_task_state_version"] != task.state_version
            or receipt["worktree_path"] != workspace["worktree_path"]
        ):
            return "recovery_cleanup_unconfirmed"
    if store.connection.execute(
        "SELECT 1 FROM runs r WHERE r.task_id=? AND NOT EXISTS "
        "(SELECT 1 FROM worker_attempts a WHERE a.run_id=r.run_id AND a.task_id=r.task_id)",
        (task_id,),
    ).fetchone():
        return "recovery_cleanup_unconfirmed"
    # Every claim on this task OR its workspace needs the canonical positive
    # process/pipe cleanup receipt. Terminal task labels alone prove nothing.
    claims = store.connection.execute(
        "SELECT attempt_id FROM worker_attempts WHERE task_id=? OR worktree_path=?",
        (task_id, workspace["worktree_path"] if workspace else None),
    ).fetchall()
    for row in claims:
        attempt = store.get_worker_attempt(row[0])
        receipt = store.connection.execute(
            "SELECT payload_json FROM worker_cleanup_receipts WHERE attempt_id=? "
            "AND executor_id=? AND cleanup_state='CONFIRMED'",
            (attempt.attempt_id, attempt.executor_id),
        ).fetchone()
        if (
            not attempt.fenced
            or attempt.spawn_state == "LEGACY_UNKNOWN"
            or attempt.cleanup_state != "CONFIRMED"
            or receipt is None
        ):
            return "recovery_cleanup_unconfirmed"
        try:
            cleanup = json.loads(receipt[0])
            if cleanup.get("state") != "CONFIRMED":
                return "recovery_cleanup_unconfirmed"
            store._check_worker_cleanup_facts(attempt, cleanup)
        except (ValueError, RuntimeError, TypeError, AttributeError):
            return "recovery_cleanup_unconfirmed"
    if (
        store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='switch_leases'"
        ).fetchone()
        and store.connection.execute(
            "SELECT 1 FROM switch_leases WHERE task_id=? AND status='AUTHORIZED' "
            "AND expires_at_epoch>CAST(strftime('%s','now') AS INTEGER)",
            (task_id,),
        ).fetchone()
    ):
        return "recovery_active_switch_lease"
    return None


def manual_workflow(service: ControlPlaneService, task_id: str):
    task = service.get_task(task_id)
    dispatch = service.store.connection.execute(
        "SELECT * FROM owner_dispatches WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    interrupted = bool(dispatch and dispatch["failure_code"] == "STARTUP_RECOVERY_REQUIRED")
    reopening = task.state == "VERIFIED"
    reason = _retry_reason(service, task_id, dispatch, interrupted=interrupted, reopening=reopening)
    if reopening and reason is None:
        report = service.verification_report(task_id)
        if report.status not in {"VERIFIED", "VERIFICATION_STALE"}:
            reason = "reopen_verification_unavailable"
    return {
        "task_id": task_id,
        "prior_dispatch_id": dispatch["dispatch_id"] if dispatch else None,
        "failure_code": dispatch["failure_code"] if dispatch else None,
        "retry_available": reason is None and service.owner_initiated_execution_enabled,
        "recovery_operation": "REOPEN" if reopening else "RECOVER" if interrupted else "RETRY",
        "blocking_reason": reason,
        "launch_performed": False,
    }


def retry_manual_task(service: ControlPlaneService, request):
    if not service.owner_initiated_execution_enabled:
        raise _error("owner_initiated_execution_disabled", 403)
    store = service.store
    binding = {
        "request_id": request.request_id,
        "task_id": request.task_id,
        "prior_dispatch_id": request.prior_dispatch_id,
        "task_state_version": request.task_state_version,
        "store_id": request.expected_store_id,
        "process_epoch": request.expected_process_epoch,
    }
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        existing = store.connection.execute(
            "SELECT * FROM manual_task_retries WHERE request_id=?",
            (request.request_id,),
        ).fetchone()
        if existing:
            if any(existing[k] != v for k, v in binding.items()):
                raise _error("conflicting_retry_request_id")
            receipt = dict(existing)
        else:
            task = store.get_task(request.task_id)
            if task.state_version != request.task_state_version:
                raise _error("recovery_generation_mismatch")
            dispatch = store.connection.execute(
                "SELECT * FROM owner_dispatches WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                (request.task_id,),
            ).fetchone()
            if dispatch is None or dispatch["dispatch_id"] != request.prior_dispatch_id:
                raise _error("recovery_generation_mismatch")
            reason = _retry_reason(service, request.task_id, dispatch)
            if reason:
                raise _error(reason)
            if store.connection.execute(
                "SELECT 1 FROM manual_task_retries WHERE prior_dispatch_id=?",
                (request.prior_dispatch_id,),
            ).fetchone():
                raise _error("prior_dispatch_already_recovered")
            store._transition_task_tx(
                request.task_id,
                TaskState.READY,
                expected_version=request.task_state_version,
                reason="owner requested retry; workspace retained; explicit START required",
            )
            receipt = binding | {"recovered_task_state_version": request.task_state_version + 1}
            store.connection.execute(
                "INSERT INTO manual_task_retries VALUES(:request_id,:task_id,:prior_dispatch_id,"
                ":task_state_version,:recovered_task_state_version,:store_id,:process_epoch)",
                receipt,
            )
            store._audit(request.task_id, "MANUAL_TASK_RETRY_PREPARED", receipt)
        store.connection.execute("COMMIT")
    except Exception:
        store.connection.execute("ROLLBACK")
        raise
    return {"retry": receipt, "launch_performed": False, "next_step": "EXPLICIT_START_REQUIRED"}


def reopen_verified_task(service: ControlPlaneService, request):
    """Withdraw the current verdict atomically; retain every file and journal entry."""
    if not service.owner_initiated_execution_enabled:
        raise _error("owner_initiated_execution_disabled", 403)
    store = service.store
    binding = {
        key: getattr(request, attribute)
        for key, attribute in (
            ("request_id", "request_id"),
            ("task_id", "task_id"),
            ("prior_dispatch_id", "prior_dispatch_id"),
            ("task_state_version", "task_state_version"),
            ("store_id", "expected_store_id"),
            ("process_epoch", "expected_process_epoch"),
        )
    }
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        existing = store.connection.execute(
            "SELECT * FROM manual_task_reopens WHERE request_id=?", (request.request_id,)
        ).fetchone()
        if existing:
            if any(existing[key] != value for key, value in binding.items()):
                raise _error("conflicting_reopen_request_id")
            receipt = dict(existing)
        else:
            task = store.get_task(request.task_id)
            dispatch = store.connection.execute(
                "SELECT * FROM owner_dispatches WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                (request.task_id,),
            ).fetchone()
            if (
                task.state_version != request.task_state_version
                or dispatch is None
                or dispatch["dispatch_id"] != request.prior_dispatch_id
            ):
                raise _error("recovery_generation_mismatch")
            reason = _retry_reason(service, request.task_id, dispatch, reopening=True)
            if reason:
                raise _error(reason)
            report = service.verification_report(request.task_id)
            if (
                report.status not in {"VERIFIED", "VERIFICATION_STALE"}
                or report.result is None
                or report.result.patch_snapshot is None
            ):
                raise _error("reopen_verification_unavailable")
            if store.connection.execute(
                "SELECT 1 FROM manual_task_reopens WHERE prior_dispatch_id=?",
                (request.prior_dispatch_id,),
            ).fetchone():
                raise _error("prior_dispatch_already_reopened")
            from personal_ai_orchestrator.verification_history import historical_evidence

            _, digest, _ = historical_evidence(service, task.task_id, report.evidence_id)
            store._transition_task_tx(
                task.task_id,
                TaskState.BLOCKED,
                expected_version=task.state_version,
                reason="owner reopened accepted work for review; prior verification is historical",
            )
            store._transition_task_tx(
                task.task_id,
                TaskState.READY,
                expected_version=task.state_version + 1,
                reason=(
                    "owner requested re-verification; retained workspace; explicit START required"
                ),
            )
            receipt = binding | {
                "recovered_task_state_version": task.state_version + 2,
                "prior_evidence_id": report.evidence_id,
                "prior_evidence_sha256": digest,
            }
            store.connection.execute(
                "INSERT INTO manual_task_reopens VALUES(:request_id,:task_id,:prior_dispatch_id,"
                ":task_state_version,:recovered_task_state_version,:store_id,:process_epoch,"
                ":prior_evidence_id,:prior_evidence_sha256)",
                receipt,
            )
            store._audit(task.task_id, "MANUAL_TASK_REOPEN_PREPARED", receipt)
        store.connection.execute("COMMIT")
    except Exception:
        store.connection.execute("ROLLBACK")
        raise
    return {"reopen": receipt, "launch_performed": False, "next_step": "EXPLICIT_START_REQUIRED"}


def task_patch(service: ControlPlaneService, task_id: str, *, evidence_id=None):
    """Export accepted bytes, or explicitly unverified bytes for a blocked task."""
    import base64

    from personal_ai_orchestrator.patch_snapshot import PatchSnapshotError, capture_patch
    from personal_ai_orchestrator.result_delivery import _read_evidence, result_delivery

    task = service.store.get_task(task_id)
    if evidence_id is not None:
        from personal_ai_orchestrator.verification_history import historical_patch

        return historical_patch(service, task_id, evidence_id)
    if task.state not in {TaskState.BLOCKED, TaskState.VERIFIED, TaskState.COMPLETED}:
        raise _error("result_not_terminal")
    if task.state in {TaskState.VERIFIED, TaskState.COMPLETED}:
        report = result_delivery(
            service.store.connection,
            task={"task_id": task_id, "request_id": task.request_id, "state": task.state.value},
            store_id=service.store.store_id,
            journal=service.verification_journal,
        )
        evidence_id = report["verification"]["evidence_id"]
        if not evidence_id or service.verification_journal is None:
            raise _error("result_snapshot_unavailable")
        result, digest, error = _read_evidence(service.verification_journal, evidence_id)
        if (
            error
            or result is None
            or result.patch_snapshot is None
            or digest != report["binding"]["verification_evidence_sha256"]
            or report["binding"].get("patch_sha256") != result.patch_snapshot.sha256
        ):
            raise _error("result_snapshot_unavailable")
        snapshot = result.patch_snapshot
        raw_patch = base64.b64decode(snapshot.patch_base64, validate=True)
        base_sha = snapshot.base_sha
        source, status = "VERIFIED_SNAPSHOT", report["workspace_status"]
    else:
        try:
            workspace = service.store.get_workspace(task_id)
        except KeyError:
            raise _error("result_workspace_unavailable", 404) from None
        if workspace.writer_token:
            raise _error("result_workspace_busy")
        from personal_ai_orchestrator.worktree_manager import WorktreeManager

        root = Path(workspace.worktree_path)
        WorktreeManager(root.parent).adopt(
            repo_path=Path(workspace.repo_path),
            task_id=task_id,
            worktree_path=root,
            branch=workspace.branch,
            base_sha=workspace.base_sha,
        )
        try:
            raw_patch = capture_patch(root, workspace.base_sha)
        except PatchSnapshotError as error:
            raise _error(str(error)) from None
        base_sha, source, status = workspace.base_sha, "UNVERIFIED_WORKTREE", "NOT_VERIFIED"
    return {
        "task_id": task_id,
        "base_sha": base_sha,
        # Text is a preview only. Export uses the exact base64 bytes and validates the digest.
        "patch": raw_patch.decode("utf-8", errors="replace"),
        "patch_base64": base64.b64encode(raw_patch).decode("ascii"),
        "sha256": sha256(raw_patch).hexdigest(),
        "byte_count": len(raw_patch),
        "source": source,
        "workspace_status": status,
    }
