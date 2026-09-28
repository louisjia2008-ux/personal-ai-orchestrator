"""Durable host-owned approval records for bounded safety decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.safety_kernel import SafetyKernelStore


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalKind(StrEnum):
    PRODUCTION_ACTIVE_ROUTING = "PRODUCTION_ACTIVE_ROUTING"
    PAID_OVERAGE = "PAID_OVERAGE"
    HIGH_RISK_EXECUTION = "HIGH_RISK_EXECUTION"


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    task_id: str
    kind: ApprovalKind
    status: ApprovalStatus
    created_at: datetime
    resolved_at: datetime | None = None


class ApprovalAuthority:
    """Read/write approvals without exposing arbitrary state changes to workers."""

    def __init__(self, store: SafetyKernelStore) -> None:
        self.store = store

    def request(self, *, approval_id: str, task_id: str, kind: ApprovalKind) -> ApprovalRecord:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self.store.get_task(task_id)
            existing = self.store.connection.execute(
                "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if existing is not None:
                record = self._from_row(existing)
                if record.task_id != task_id or record.kind is not kind:
                    raise ValueError("approval_id already belongs to a different request")
                self.store.connection.execute("COMMIT")
                return record

            created_at = datetime.now(UTC).isoformat()
            self.store.connection.execute(
                "INSERT INTO approvals VALUES(?,?,?,?,?,?)",
                (
                    approval_id,
                    task_id,
                    kind.value,
                    ApprovalStatus.PENDING.value,
                    created_at,
                    None,
                ),
            )
            self.store._audit(
                task_id,
                "APPROVAL_REQUESTED",
                {"approval_id": approval_id, "kind": kind.value},
            )
            self.store.connection.execute("COMMIT")
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        request_id: str | None = None,
    ) -> ApprovalRecord:
        target = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get(approval_id)
            if current.status is target:
                self.store.connection.execute("COMMIT")
                return current
            if current.status is not ApprovalStatus.PENDING:
                raise ValueError("resolved approval is immutable")

            resolved_at = datetime.now(UTC).isoformat()
            updated = self.store.connection.execute(
                "UPDATE approvals SET status=?,resolved_at=? WHERE approval_id=? AND status=?",
                (
                    target.value,
                    resolved_at,
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("approval resolution lost concurrency race")
            self.store._audit(
                current.task_id,
                "APPROVAL_RESOLVED",
                {
                    "approval_id": approval_id,
                    "kind": current.kind.value,
                    "status": target.value,
                    **({"request_id": request_id} if request_id is not None else {}),
                },
            )
            self.store.connection.execute("COMMIT")
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def get(self, approval_id: str) -> ApprovalRecord:
        row = self.store.connection.execute(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return self._from_row(row)

    def is_approved(self, approval_id: str, *, kind: ApprovalKind) -> bool:
        try:
            record = self.get(approval_id)
        except KeyError:
            return False
        return record.kind is kind and record.status is ApprovalStatus.APPROVED

    @staticmethod
    def _from_row(row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            kind=ApprovalKind(row["kind"]),
            status=ApprovalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                None if row["resolved_at"] is None else datetime.fromisoformat(row["resolved_at"])
            ),
        )


__all__ = ["ApprovalAuthority", "ApprovalKind", "ApprovalRecord", "ApprovalStatus"]
