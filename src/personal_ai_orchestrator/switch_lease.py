"""Bounded host-owned authorization lease for production model switches.

The scheduler decision is not sufficient authority to mutate an OpenCode session. Immediately
before switching, the adapter must obtain a short-lived lease that revalidates the durable task
state version. While that lease is live, a SQLite trigger freezes task state transitions. The
adapter must complete or abort the lease after the local switch attempt; a crashed adapter releases
the freeze automatically when the lease expires.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.opencode_contract import RoutingDecision, RoutingMode
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState

DEFAULT_SWITCH_LEASE_SECONDS = 10
_MAX_SWITCH_LEASE_SECONDS = 30
_ROUTABLE_STATES = {TaskState.READY, TaskState.RUNNING}


class SwitchLeaseStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"


class SwitchLeaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_state_version: int = Field(ge=0)
    session_id: str = Field(min_length=1)
    status: SwitchLeaseStatus
    expires_at_epoch: int
    created_at: datetime
    resolved_at: datetime | None = None


class SwitchLeaseAuthority:
    """Issue and resolve one bounded state-freezing lease per ACTIVE switch decision."""

    def __init__(self, store: SafetyKernelStore) -> None:
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.store.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS switch_leases (
                lease_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL UNIQUE REFERENCES routing_decisions(decision_id),
                request_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                task_state_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at_epoch INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_authorized_switch_lease_per_task
                ON switch_leases(task_id) WHERE status = 'AUTHORIZED';
            CREATE TRIGGER IF NOT EXISTS block_task_transition_during_switch_lease
            BEFORE UPDATE OF state, state_version ON tasks
            WHEN EXISTS (
                SELECT 1 FROM switch_leases
                WHERE task_id = OLD.task_id
                  AND status = 'AUTHORIZED'
                  AND expires_at_epoch > CAST(strftime('%s','now') AS INTEGER)
            )
            BEGIN
                SELECT RAISE(ABORT, 'task state frozen by active model switch lease');
            END;
            """
        )

    @staticmethod
    def _epoch_now() -> int:
        return int(datetime.now(UTC).timestamp())

    def _expire_stale(self, *, task_id: str | None = None) -> None:
        now_epoch = self._epoch_now()
        if task_id is None:
            rows = self.store.connection.execute(
                "SELECT lease_id,task_id FROM switch_leases "
                "WHERE status='AUTHORIZED' AND expires_at_epoch<=?",
                (now_epoch,),
            ).fetchall()
        else:
            rows = self.store.connection.execute(
                "SELECT lease_id,task_id FROM switch_leases "
                "WHERE task_id=? AND status='AUTHORIZED' AND expires_at_epoch<=?",
                (task_id, now_epoch),
            ).fetchall()
        for row in rows:
            self.store.connection.execute(
                "UPDATE switch_leases SET status='EXPIRED',resolved_at=? "
                "WHERE lease_id=? AND status='AUTHORIZED'",
                (datetime.now(UTC).isoformat(), row["lease_id"]),
            )
            self.store._audit(
                row["task_id"],
                "SWITCH_LEASE_EXPIRED",
                {"lease_id": row["lease_id"]},
            )

    def authorize(
        self,
        *,
        decision_id: str,
        request_id: str,
        task_id: str,
        task_state_version: int,
        session_id: str,
        ttl_seconds: int = DEFAULT_SWITCH_LEASE_SECONDS,
    ) -> SwitchLeaseRecord:
        if ttl_seconds <= 0 or ttl_seconds > _MAX_SWITCH_LEASE_SECONDS:
            raise ValueError("switch lease ttl is outside the bounded safety range")

        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale(task_id=task_id)
            decision_row = self.store.connection.execute(
                "SELECT request_id,task_id,payload_json FROM routing_decisions WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if decision_row is None:
                raise KeyError(decision_id)
            decision = RoutingDecision.model_validate(json.loads(decision_row["payload_json"]))
            if decision.mode is not RoutingMode.ACTIVE or not decision.switch_requested:
                raise ValueError("routing decision does not authorize an ACTIVE model switch")
            if decision_row["request_id"] != request_id or decision.request_id != request_id:
                raise ValueError("switch authorization request_id does not match decision")
            if decision_row["task_id"] != task_id:
                raise ValueError("switch authorization task_id does not match decision")
            if decision.task_state_version != task_state_version:
                raise ValueError("switch authorization state version does not match decision")

            task = self.store.get_task(task_id)
            if task.state not in _ROUTABLE_STATES:
                raise RuntimeError("task is no longer eligible for an ACTIVE model switch")
            if task.state_version != task_state_version:
                raise RuntimeError("task state changed after the routing decision")

            existing = self.store.connection.execute(
                "SELECT * FROM switch_leases WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                record = self._from_row(existing)
                if (
                    record.request_id != request_id
                    or record.task_id != task_id
                    or record.task_state_version != task_state_version
                    or record.session_id != session_id
                ):
                    raise ValueError("decision already belongs to a different switch lease request")
                if record.status is SwitchLeaseStatus.AUTHORIZED:
                    self.store.connection.execute("COMMIT")
                    return record
                raise RuntimeError("switch decision lease is no longer reusable")

            active = self.store.connection.execute(
                "SELECT lease_id FROM switch_leases "
                "WHERE task_id=? AND status='AUTHORIZED' AND expires_at_epoch>? LIMIT 1",
                (task_id, self._epoch_now()),
            ).fetchone()
            if active is not None:
                raise RuntimeError("task already has an authorized model switch lease")

            lease_id = f"switch-{secrets.token_urlsafe(18)}"
            created_at = datetime.now(UTC)
            expires_at_epoch = int(created_at.timestamp()) + ttl_seconds
            self.store.connection.execute(
                "INSERT INTO switch_leases VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                (
                    lease_id,
                    decision_id,
                    request_id,
                    task_id,
                    task_state_version,
                    session_id,
                    SwitchLeaseStatus.AUTHORIZED.value,
                    expires_at_epoch,
                    created_at.isoformat(),
                ),
            )
            self.store._audit(
                task_id,
                "SWITCH_LEASE_AUTHORIZED",
                {
                    "lease_id": lease_id,
                    "decision_id": decision_id,
                    "task_state_version": task_state_version,
                    "expires_at_epoch": expires_at_epoch,
                },
            )
            self.store.connection.execute("COMMIT")
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise
        return self.get(lease_id)

    def resolve(
        self,
        *,
        lease_id: str,
        decision_id: str,
        session_id: str,
        completed: bool,
    ) -> SwitchLeaseRecord:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.store.connection.execute(
                "SELECT * FROM switch_leases WHERE lease_id=?",
                (lease_id,),
            ).fetchone()
            if row is None:
                raise KeyError(lease_id)
            record = self._from_row(row)
            if record.decision_id != decision_id or record.session_id != session_id:
                raise ValueError("switch lease resolution does not match its authorization")
            if record.status is not SwitchLeaseStatus.AUTHORIZED:
                expected = (
                    SwitchLeaseStatus.COMPLETED if completed else SwitchLeaseStatus.ABORTED
                )
                if record.status is expected:
                    self.store.connection.execute("COMMIT")
                    return record
                raise RuntimeError("switch lease is no longer active")
            if record.expires_at_epoch <= self._epoch_now():
                self.store.connection.execute(
                    "UPDATE switch_leases SET status='EXPIRED',resolved_at=? WHERE lease_id=?",
                    (datetime.now(UTC).isoformat(), lease_id),
                )
                self.store._audit(record.task_id, "SWITCH_LEASE_EXPIRED", {"lease_id": lease_id})
                self.store.connection.execute("COMMIT")
                raise RuntimeError("switch lease expired before completion")

            target = SwitchLeaseStatus.COMPLETED if completed else SwitchLeaseStatus.ABORTED
            resolved_at = datetime.now(UTC).isoformat()
            updated = self.store.connection.execute(
                "UPDATE switch_leases SET status=?,resolved_at=? "
                "WHERE lease_id=? AND status='AUTHORIZED'",
                (target.value, resolved_at, lease_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("switch lease resolution lost concurrency race")
            self.store._audit(
                record.task_id,
                "SWITCH_LEASE_RESOLVED",
                {"lease_id": lease_id, "decision_id": decision_id, "status": target.value},
            )
            self.store.connection.execute("COMMIT")
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.execute("ROLLBACK")
            raise
        return self.get(lease_id)

    def has_active_lease(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """M1 WP5a-2: does this task hold a live, unexpired AUTHORIZED lease?

        A lease only counts as active when ALL of:

        - ``task_id`` matches, AND
        - ``status == AUTHORIZED``, AND
        - ``expires_at > now``

        Expired leases are invisible (an expired freeze must not permanently
        block the supervised-auto tick). COMPLETED / ABORTED / EXPIRED rows
        never block. The optional ``now`` keeps the check fake-clock friendly;
        wall-clock is only read when ``now`` is omitted.
        """

        reference = datetime.now(UTC) if now is None else now
        now_epoch = int(reference.timestamp())
        row = self.store.connection.execute(
            "SELECT 1 FROM switch_leases "
            "WHERE task_id=? AND status=? AND expires_at_epoch>? LIMIT 1",
            (task_id, SwitchLeaseStatus.AUTHORIZED.value, now_epoch),
        ).fetchone()
        return row is not None

    def abort_for_task(self, task_id: str, *, reason: str) -> tuple[str, ...]:
        """Release any live switch freeze before a host cancellation/state transition."""

        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale(task_id=task_id)
            rows = self.store.connection.execute(
                "SELECT lease_id FROM switch_leases WHERE task_id=? AND status='AUTHORIZED'",
                (task_id,),
            ).fetchall()
            lease_ids = tuple(row["lease_id"] for row in rows)
            for lease_id in lease_ids:
                self.store.connection.execute(
                    "UPDATE switch_leases SET status='ABORTED',resolved_at=? "
                    "WHERE lease_id=? AND status='AUTHORIZED'",
                    (datetime.now(UTC).isoformat(), lease_id),
                )
                self.store._audit(
                    task_id,
                    "SWITCH_LEASE_RESOLVED",
                    {"lease_id": lease_id, "status": "ABORTED", "reason": reason},
                )
            self.store.connection.execute("COMMIT")
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise
        return lease_ids

    def get(self, lease_id: str) -> SwitchLeaseRecord:
        row = self.store.connection.execute(
            "SELECT * FROM switch_leases WHERE lease_id=?",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise KeyError(lease_id)
        return self._from_row(row)

    @staticmethod
    def _from_row(row) -> SwitchLeaseRecord:
        return SwitchLeaseRecord(
            lease_id=row["lease_id"],
            decision_id=row["decision_id"],
            request_id=row["request_id"],
            task_id=row["task_id"],
            task_state_version=row["task_state_version"],
            session_id=row["session_id"],
            status=SwitchLeaseStatus(row["status"]),
            expires_at_epoch=row["expires_at_epoch"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                None if row["resolved_at"] is None else datetime.fromisoformat(row["resolved_at"])
            ),
        )


__all__ = [
    "DEFAULT_SWITCH_LEASE_SECONDS",
    "SwitchLeaseAuthority",
    "SwitchLeaseRecord",
    "SwitchLeaseStatus",
]
