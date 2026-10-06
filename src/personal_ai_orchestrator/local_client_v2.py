"""Bounded, owner-only manual execution boundary, separate from legacy clients.

This module translates an explicitly bound owner request into existing host
operations. It does not tick the scheduler, resolve approvals, grant permissions,
retry a dispatch, return worker answers, or turn missing evidence into success.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.control_api import ControlPlaneError, ControlPlaneService
from personal_ai_orchestrator.execution_controller import execution_target_has_launch_verification
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchRecord,
    OwnerDispatchStatus,
    TaskState,
    owner_dispatch_matches_expected,
)

CATALOG_LIMIT = 100
DETAIL_LIMIT = 50
CAPABILITIES = (
    "manual-submit-v2",
    "owner-start-v2",
    "safe-detail-v2",
    "dispatch-status-v2",
    "manual-cancel-v2",
    "host-identity-v2",
)
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_IDENTITY_PATTERN = r"^[a-f0-9]{32}$"
Operation = Literal[
    "CAPABILITIES", "CONTEXT", "SUBMIT_V2", "START", "DETAIL", "DISPATCH_STATUS", "CANCEL"
]


class LocalClientRequestV2(BaseModel):
    """Exact operation shapes prevent clients from importing new authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol_version: Literal[2]
    operation: Operation
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    task_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    project_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    intent: str | None = Field(default=None, min_length=1, max_length=8192)
    execution_target_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    task_state_version: int | None = Field(default=None, ge=0)
    expected_store_id: str | None = Field(default=None, pattern=_IDENTITY_PATTERN)
    expected_process_epoch: str | None = Field(default=None, pattern=_IDENTITY_PATTERN)

    @model_validator(mode="after")
    def exact_operation_shape(self) -> LocalClientRequestV2:
        identities = {"expected_store_id", "expected_process_epoch"}
        shapes = {
            "CAPABILITIES": set(),
            "CONTEXT": set(),
            "SUBMIT_V2": {"task_id", "project_id", "intent", "execution_target_id"} | identities,
            "START": {"task_id", "task_state_version", "execution_target_id"} | identities,
            "DETAIL": {"task_id"},
            "DISPATCH_STATUS": {"task_id", "task_state_version", "execution_target_id"}
            | identities,
            "CANCEL": {"task_id"} | identities,
        }
        expected = shapes[self.operation]
        optional = set(type(self).model_fields) - {"protocol_version", "operation", "request_id"}
        supplied = self.model_fields_set & optional
        if supplied != expected or any(getattr(self, name) is None for name in expected):
            raise ValueError("invalid_v2_operation_shape")
        return self


def _safe_id(value: str | None, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
        raise ControlPlaneError(503, "local_client_view_unavailable")
    return value


def _safe_stamp(value: str | None) -> str | None:
    # Only machine-generated timestamps; never return a free-form database cell.
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9T:.+Z-]{10,40}", value) is None:
        raise ControlPlaneError(503, "local_client_view_unavailable")
    return value


def _safe_token(value: str | None) -> str | None:
    if value is None:
        return None
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value) else "UNKNOWN"


@contextmanager
def _read_snapshot(service: ControlPlaneService):
    """All pieces of a detail/reconciliation response describe one DB snapshot."""
    connection = service.store.connection
    connection.execute("BEGIN")
    try:
        yield
    finally:
        connection.execute("ROLLBACK")


def _safe_task(service: ControlPlaneService, task_id: str) -> dict[str, Any]:
    # SELECT only allowlisted bounded columns. In particular, never load intent,
    # working_subpath, base_sha, audit payloads, or any provider result into a view.
    row = service.store.connection.execute(
        """SELECT substr(task_id,1,129) AS task_id,
            substr(request_id,1,129) AS request_id,
            substr(project_id,1,129) AS project_id,
            substr(state,1,80) AS state, state_version,
            substr(scheduling_policy,1,80) AS scheduling_policy,
            substr(manual_execution_target_id,1,129) AS manual_execution_target_id,
            substr(created_at,1,41) AS created_at, substr(updated_at,1,41) AS updated_at
            FROM tasks WHERE task_id=?""",
        (task_id,),
    ).fetchone()
    if row is None:
        raise ControlPlaneError(404, "task_not_found")
    return {
        "task_id": _safe_id(row["task_id"]),
        "request_id": _safe_id(row["request_id"]),
        "project_id": _safe_id(row["project_id"], nullable=True),
        "state": _safe_token(row["state"]),
        "state_version": row["state_version"],
        "scheduling_policy": _safe_token(row["scheduling_policy"]),
        "manual_execution_target_id": _safe_id(row["manual_execution_target_id"], nullable=True),
        "created_at": _safe_stamp(row["created_at"]),
        "updated_at": _safe_stamp(row["updated_at"]),
    }


def _require_manual_task(service: ControlPlaneService, task_id: str) -> dict[str, Any]:
    task = _safe_task(service, task_id)
    if (
        task["scheduling_policy"] != "MANUAL"
        or not task["manual_execution_target_id"]
        or task["state"] in {"AUTO_PLANNED", "AUTO_GRACE"}
    ):
        raise ControlPlaneError(409, "manual_task_required")
    return task


def _context(service: ControlPlaneService) -> dict[str, Any]:
    # No discovery/refresh or project filesystem probes: this operation is read-only.
    rows = service.store.connection.execute(
        """SELECT substr(project_id,1,129) AS project_id,
            substr(storage_availability,1,80) AS storage_availability
            FROM projects ORDER BY project_id LIMIT ?""",
        (CATALOG_LIMIT + 1,),
    ).fetchall()
    projects = [
        {
            "project_id": _safe_id(row["project_id"]),
            "storage_availability": _safe_token(row["storage_availability"]),
        }
        for row in rows[:CATALOG_LIMIT]
    ]
    registry = service._effective_registry()
    targets = sorted(registry.execution_targets)[: CATALOG_LIMIT + 1]
    execution_targets = [
        {
            "execution_target_id": _safe_id(target_id),
            "runtime_available": bool(service._runtime_available(target_id)),
            "launch_verified": execution_target_has_launch_verification(
                registry,
                execution_target_id=target_id,
                execution_evidence_journal=service.execution_evidence_journal,
            ),
        }
        for target_id in targets[:CATALOG_LIMIT]
    ]
    owner_enabled = service.owner_initiated_execution_enabled
    executor_available = service.dispatch_executor is not None
    reasons = []
    if not owner_enabled:
        reasons.append("owner_initiated_execution_disabled")
    if not executor_available:
        reasons.append("dispatch_executor_unavailable")
    return {
        "capabilities": list(CAPABILITIES),
        "owner_start": {
            "available": owner_enabled and executor_available,
            "owner_enabled": owner_enabled,
            "executor_available": executor_available,
            "blocking_reasons": reasons,
        },
        "projects": projects,
        "execution_targets": execution_targets,
        "truncated": {
            "projects": len(rows) > CATALOG_LIMIT,
            "execution_targets": len(targets) > CATALOG_LIMIT,
        },
    }


def _detail(service: ControlPlaneService, task_id: str) -> dict[str, Any]:
    with _read_snapshot(service):
        task = _safe_task(service, task_id)
        runs = service.store.connection.execute(
            """SELECT substr(run_id,1,129) AS run_id, substr(status,1,80) AS status,
                substr(started_at,1,41) AS started_at, substr(finished_at,1,41) AS finished_at
                FROM runs WHERE task_id=? ORDER BY rowid DESC LIMIT ?""",
            (task_id, DETAIL_LIMIT + 1),
        ).fetchall()
        approvals = service.store.connection.execute(
            """SELECT substr(approval_id,1,129) AS approval_id, substr(kind,1,80) AS kind,
                substr(status,1,80) AS status, substr(created_at,1,41) AS created_at,
                substr(resolved_at,1,41) AS resolved_at
                FROM approvals WHERE task_id=? ORDER BY rowid DESC LIMIT ?""",
            (task_id, DETAIL_LIMIT + 1),
        ).fetchall()
        # State is the only bounded verification observation in this contract.
        # Evidence/result files and audit payloads are deliberately not read.
        verification = "NOT_OBSERVED"
        if task["state"] == TaskState.VERIFYING.value:
            verification = "IN_PROGRESS"
        elif task["state"] in {TaskState.VERIFIED.value, TaskState.COMPLETED.value}:
            verification = "VERIFIED_EVIDENCE_UNAVAILABLE"
        return {
            "task": task,
            "runs": [
                {
                    "run_id": _safe_id(row["run_id"]),
                    "status": _safe_token(row["status"]),
                    "started_at": _safe_stamp(row["started_at"]),
                    "finished_at": _safe_stamp(row["finished_at"]),
                }
                for row in runs[:DETAIL_LIMIT]
            ],
            "approvals": [
                {
                    "approval_id": _safe_id(row["approval_id"]),
                    "kind": _safe_token(row["kind"]),
                    "status": _safe_token(row["status"]),
                    "created_at": _safe_stamp(row["created_at"]),
                    "resolved_at": _safe_stamp(row["resolved_at"]),
                }
                for row in approvals[:DETAIL_LIMIT]
            ],
            "verification": {"status": verification},
            "answer_completeness": "UNAVAILABLE",
            "truncated": {
                "runs": len(runs) > DETAIL_LIMIT,
                "approvals": len(approvals) > DETAIL_LIMIT,
            },
        }


def _dispatch_status(service: ControlPlaneService, request: LocalClientRequestV2) -> dict[str, Any]:
    with _read_snapshot(service):
        row = service.store.connection.execute(
            """SELECT substr(dispatch_id,1,145) AS dispatch_id,
                substr(request_id,1,129) AS request_id, substr(task_id,1,129) AS task_id,
                task_state_version, substr(execution_target_id,1,129) AS execution_target_id,
                substr(authority,1,80) AS authority, substr(status,1,80) AS status,
                substr(created_at,1,41) AS created_at,
                substr(failure_code,1,81) AS failure_code
                FROM owner_dispatches WHERE request_id=?""",
            (request.request_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneError(404, "dispatch_not_found")
        # Reuse the canonical tuple comparator without loading free-form
        # failure_reason or unbounded provider/executor error contents.
        record = OwnerDispatchRecord.model_validate(dict(row))
        if not owner_dispatch_matches_expected(
            record,
            dispatch_id=f"owner-dispatch-{request.request_id}",
            request_id=request.request_id,
            task_id=request.task_id,
            task_state_version=request.task_state_version,
            execution_target_id=request.execution_target_id,
            authority="OWNER_INITIATED_EXECUTION",
        ):
            raise ControlPlaneError(409, "conflicting_dispatch_request_id")
        return {
            "dispatch_id": record.dispatch_id,
            "request_id": record.request_id,
            "task_id": record.task_id,
            "task_state_version": record.task_state_version,
            "execution_target_id": record.execution_target_id,
            "authority": record.authority,
            "status": record.status.value,
            "accepted": record.failure_code is None,
            "failure_code": _safe_token(record.failure_code),
            "task": _safe_task(service, record.task_id),
            "recovery_required": request.expected_process_epoch != service.process_epoch
            and record.status in {OwnerDispatchStatus.RESERVED, OwnerDispatchStatus.STARTED},
        }


def handle_local_client_v2(service: ControlPlaneService, payload: dict[str, Any]) -> dict[str, Any]:
    request = LocalClientRequestV2.model_validate(payload)
    # These are read from the same handling host/connection used below. A
    # bridge-side preflight cannot substitute for this server-side binding.
    store_id = service.store.store_id
    epoch = service.process_epoch
    if request.expected_store_id is not None and request.expected_store_id != store_id:
        raise ControlPlaneError(409, "store_identity_mismatch")
    if (
        request.operation in {"SUBMIT_V2", "START", "CANCEL"}
        and request.expected_process_epoch != epoch
    ):
        raise ControlPlaneError(409, "process_epoch_mismatch")
    if request.operation in {"CAPABILITIES", "CONTEXT"}:
        result = _context(service)
    elif request.operation == "SUBMIT_V2":
        if request.execution_target_id not in service._effective_registry().execution_targets:
            raise ControlPlaneError(400, "execution_target_not_registered")
        service.submit_task(
            {
                "task_id": request.task_id,
                "request_id": request.request_id,
                "project_id": request.project_id,
                "intent": request.intent,
                "scheduling_policy": "MANUAL",
                "manual_execution_target_id": request.execution_target_id,
            }
        )
        result = {"task": _safe_task(service, request.task_id)}
    elif request.operation == "START":
        task = _require_manual_task(service, request.task_id)
        if task["manual_execution_target_id"] != request.execution_target_id:
            raise ControlPlaneError(409, "manual_execution_target_mismatch")
        if service.dispatch_executor is None:
            raise ControlPlaneError(503, "dispatch_executor_unavailable")
        service.dispatch_task(
            request.task_id,
            {
                "request_id": request.request_id,
                "task_state_version": request.task_state_version,
                "execution_target_id": request.execution_target_id,
            },
        )
        result = _dispatch_status(service, request)
    elif request.operation == "DISPATCH_STATUS":
        result = _dispatch_status(service, request)
    elif request.operation == "DETAIL":
        result = _detail(service, request.task_id)
    else:
        _require_manual_task(service, request.task_id)
        cancelled = service.cancel_task(request.task_id, {"request_id": request.request_id})
        result = {
            "task": _safe_task(service, request.task_id),
            "cancelled_now": cancelled.cancelled_now,
        }
    return {
        "protocol_version": 2,
        "request_id": request.request_id,
        "operation": request.operation,
        "store_id": store_id,
        "process_epoch": epoch,
        "payload": result,
    }
