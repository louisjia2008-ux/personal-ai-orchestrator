"""Host-owned durable task state and writer-lock authority.

This module intentionally owns safety state outside coding agents. Worker prose never advances a
Task directly to VERIFIED/COMPLETED; deterministic verification and explicit host transitions do.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskState(StrEnum):
    SUBMITTED = "SUBMITTED"
    READY = "READY"
    RUNNING = "RUNNING"
    WORKER_FINISHED = "WORKER_FINISHED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    #: M1 WP5a-1: host-owned planning tick wrote a frozen
    #: ``RoutingDecision`` for this task. The task is now sitting in
    #: ``AUTO_GRACE`` (or — for ``unattended_allowed`` projects —
    #: holding an explicit ``auto_grace_deadline_at``).
    AUTO_PLANNED = "AUTO_PLANNED"
    #: M1 WP5a-1: grace window. Owner can veto (→ READY), explicit
    #: ack (``POST /v1/tasks/{id}/auto/ack``) starts the countdown,
    #: or expiry dispatches (WP5a-2). No autonomous
    #: ``AUTO_GRACE → RUNNING`` transition in WP5a-1 — the tick
    #: lands in WP5a-2.
    AUTO_GRACE = "AUTO_GRACE"


_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.SUBMITTED: frozenset({TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.READY: frozenset(
        {
            TaskState.RUNNING,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
            # M1 WP5a-1: the host-owned tick can promote READY →
            # AUTO_PLANNED when the project's supervised-auto toggle
            # is on and the recommendations pass the six hard
            # gates (frozen in docs/M1_WP5_SPEC.md).
            TaskState.AUTO_PLANNED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {TaskState.WORKER_FINISHED, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.WORKER_FINISHED: frozenset(
        {TaskState.VERIFYING, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.VERIFYING: frozenset(
        {TaskState.VERIFIED, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.VERIFIED: frozenset({TaskState.COMPLETED, TaskState.BLOCKED}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.COMPLETED: frozenset(),
    # M1 WP5a-1: AUTO_PLANNED is the planning-tick's terminal state
    # until the task is vetoed back to READY or promoted into
    # AUTO_GRACE. M1 WP5a-2 adds the autonomous execution edge:
    # AUTO_GRACE → RUNNING is legal ONLY through the host dispatch
    # pathway (``start_dispatched_worker(expected_state=AUTO_GRACE)``);
    # there is no public "set state RUNNING" API, and every caller
    # must hold a durable dispatch reservation first.
    TaskState.AUTO_PLANNED: frozenset(
        {TaskState.AUTO_GRACE, TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}
    ),
    TaskState.AUTO_GRACE: frozenset(
        {TaskState.RUNNING, TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}
    ),
}


# ---------------------------------------------------------------------------
# Round 6 §5 — authority-owned source-state contract.
#
# The legal source state for a worker start is a property of the DURABLE
# DISPATCH AUTHORITY, never of whatever state the task happens to be in
# when a (possibly stale) executor thread runs:
#
#     OWNER_INITIATED_EXECUTION → READY
#     SUPERVISED_AUTO           → AUTO_GRACE
#
# The string values live here — the lowest-level host-owned module — so
# ``dispatch_initiator``, the executor and the store share ONE mapping
# with no circular imports (every layer already imports this module).
# ---------------------------------------------------------------------------

AUTHORITY_OWNER_INITIATED_EXECUTION = "OWNER_INITIATED_EXECUTION"
AUTHORITY_SUPERVISED_AUTO = "SUPERVISED_AUTO"
AUTHORITY_DELEGATED_CHILD = "DELEGATED_CHILD"


def expected_source_state_for_dispatch_authority(authority: str) -> TaskState:
    """Round 6 §5 — the ONE authority → legal-source-state mapping.

    ``OWNER_INITIATED_EXECUTION`` and ``DELEGATED_CHILD`` start only from READY;
    ``SUPERVISED_AUTO`` may only start from AUTO_GRACE. Any other
    authority value raises :class:`ValueError` — callers must fail
    closed (no worker spawn) rather than guessing a default.
    """

    if authority in (AUTHORITY_OWNER_INITIATED_EXECUTION, AUTHORITY_DELEGATED_CHILD):
        return TaskState.READY
    if authority == AUTHORITY_SUPERVISED_AUTO:
        return TaskState.AUTO_GRACE
    raise ValueError(f"unknown dispatch authority: {authority!r}")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskRecord(FrozenModel):
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    project_id: str | None = None
    base_sha: str | None = None
    working_subpath: str | None = None
    state: TaskState
    state_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    scheduling_policy: str | None = None
    manual_execution_target_id: str | None = None
    # M1 WP2: minimum capability tier required for the dispatch target.
    # Always a valid ModelTier string; the storage layer normalises
    # None → "T1" on insert so old rows still type-check.
    min_tier: str = "T1"
    # M1 WP5a-1: AUTO_PLANNED / AUTO_GRACE bookkeeping. All four
    # fields default to ``None`` so a pre-WP5a-1 task reads cleanly.
    # Round 7 §21 — identifier contract (do not conflate):
    # ``auto_decision_id`` is the lifecycle/cycle id derived by the
    # planning tick as ``auto-{task_id}-v{state_version-at-planning}``
    # (one per cycle, fresh per state_version, idempotent on replay).
    # It is NOT the ``RoutingDecision.decision_id`` — that is an
    # independent durable routing-decision id (``route-{digest}``).
    # The chain is: auto_decision_id → routing request id
    # ``supervised-auto-{auto_decision_id}`` → the durable
    # RoutingDecision (``route-{digest}``, looked up by request id).
    # A ``PendingShadowObservation`` carries ``pending_id ==
    # auto_decision_id`` and ``decision_id ==
    # RoutingDecision.decision_id``.
    # ``auto_grace_deadline_at`` is the ISO timestamp at which the
    # grace window expires (set on entry to AUTO_GRACE for
    # ``unattended_allowed`` projects, or on owner ack otherwise).
    # ``auto_acked_at`` is the ISO timestamp the owner acknowledged
    # the planning decision (the deadline is then
    # ``acked_at + grace_seconds``). ``auto_reason`` is the
    # ``AUTO_SKIPPED{reason}`` / ``AUTO_PLANNED{decision_id,target}``
    # audit summary text exposed back to the panel.
    auto_decision_id: str | None = None
    auto_grace_deadline_at: str | None = None
    auto_acked_at: str | None = None
    auto_reason: str | None = None


class DelegatedTaskLineageRule(StrEnum):
    RESOLVED = "DELEGATED_LINEAGE_RESOLVED"
    CHILD_TASK_NOT_FOUND = "DELEGATED_LINEAGE_CHILD_TASK_NOT_FOUND"
    TASK_SUBMISSION_MISSING = "DELEGATED_LINEAGE_TASK_SUBMISSION_MISSING"
    TASK_SUBMISSION_AMBIGUOUS = "DELEGATED_LINEAGE_TASK_SUBMISSION_AMBIGUOUS"
    METADATA_MISSING = "DELEGATED_LINEAGE_METADATA_MISSING"
    METADATA_PARTIAL = "DELEGATED_LINEAGE_METADATA_PARTIAL"
    METADATA_INVALID = "DELEGATED_LINEAGE_METADATA_INVALID"
    PARENT_TASK_NOT_FOUND = "DELEGATED_LINEAGE_PARENT_TASK_NOT_FOUND"
    PARENT_RUN_NOT_FOUND = "DELEGATED_LINEAGE_PARENT_RUN_NOT_FOUND"
    PARENT_RUN_TASK_MISMATCH = "DELEGATED_LINEAGE_PARENT_RUN_TASK_MISMATCH"
    TASK_CONTEXT_MISMATCH = "DELEGATED_LINEAGE_TASK_CONTEXT_MISMATCH"


class DelegatedTaskLineage(FrozenModel):
    """Canonical child-to-parent relationship recovered from submission audit."""

    child_task_id: str = Field(min_length=1)
    parent_task_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)


class DelegatedTaskLineageError(ValueError):
    """Stable, transcript-free failure from canonical lineage resolution."""

    def __init__(
        self,
        rule_id: DelegatedTaskLineageRule,
        *,
        failure_category: str,
        parent_found: bool = False,
    ) -> None:
        self.rule_id = rule_id
        self.failure_category = failure_category
        self.parent_found = parent_found
        super().__init__(rule_id.value)


class ProjectAvailability(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    MISSING = "MISSING"
    INVALID_REPOSITORY = "INVALID_REPOSITORY"


class ProjectRecord(FrozenModel):
    project_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    canonical_repo_root: str = Field(min_length=1)
    git_root: str = Field(min_length=1)
    default_branch: str = Field(min_length=1)
    last_known_head: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    working_subpath: str | None = None
    remote_url: str | None = None
    last_opened_at: datetime | None = None
    storage_availability: ProjectAvailability = ProjectAvailability.ONLINE
    security_bookmark_b64: str | None = None
    scheduling_policy: str | None = None
    manual_execution_target_id: str | None = None
    #: M1 WP5a-1: project-level supervised-auto toggle. When ``True``,
    #: tasks in this project are eligible for ``SUPERVISED_AUTO``
    #: planning (host-owned tick path). Owned by the project so the
    #: owner can opt-in per project; default ``False`` keeps the
    #: pre-WP5a-1 manual-only behavior.
    supervised_auto_allowed: bool = False
    #: M1 WP5a-1: project-level unattended toggle. When ``True``, the
    #: ``AUTO_GRACE`` countdown starts immediately on planning.
    #: ``False`` means the grace countdown waits for an explicit
    #: owner ack via ``POST /v1/tasks/{id}/auto/ack``.
    unattended_allowed: bool = False
    #: M1 WP5a-1: project-level grace window in seconds (default 120).
    #: Bounds how long ``AUTO_GRACE`` may sit before ``AUTO_DISPATCHED``
    #: or ``AUTO_ABORTED`` fires. Owner can lower it per project but
    #: never raise it past the safety kernel default.
    grace_seconds: int = 120


class OwnerDispatchStatus(StrEnum):
    RESERVED = "RESERVED"
    STARTED = "STARTED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    FINISHED = "FINISHED"


class OwnerDispatchRecord(FrozenModel):
    dispatch_id: str
    request_id: str
    task_id: str
    task_state_version: int = Field(ge=0)
    execution_target_id: str
    authority: str
    status: OwnerDispatchStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_code: str | None = None
    failure_reason: str | None = None


def owner_dispatch_matches_expected(
    record: OwnerDispatchRecord,
    *,
    dispatch_id: str,
    request_id: str,
    task_id: str,
    task_state_version: int,
    execution_target_id: str,
    authority: str,
) -> bool:
    """Round 5 §7 — the ONE definition of an exact dispatch reservation.

    A durable ``owner_dispatches`` row is the reservation expected for a
    request id only when EVERY identity dimension matches exactly —
    ``request_id``, ``dispatch_id``, ``task_id``, ``task_state_version``,
    ``execution_target_id`` and ``authority``. Both
    :meth:`SafetyKernelStore.reserve_owner_dispatch` (duplicate
    idempotency) and the SUPERVISED_AUTO crash-recovery path (existing
    row re-admission) must use THIS comparison so the two can never
    drift into different equality rules. No partial match exists.
    """

    return (
        record.dispatch_id == dispatch_id
        and record.request_id == request_id
        and record.task_id == task_id
        and record.task_state_version == task_state_version
        and record.execution_target_id == execution_target_id
        and record.authority == authority
    )


class WorkerAttemptRecord(FrozenModel):
    """Durable, single-use spawn authority and cleanup projection for one dispatch."""

    attempt_id: str
    dispatch_id: str | None
    task_id: str
    task_state_version: int
    dispatch_task_state_version: int | None = None
    authority: str
    executor_id: str
    writer_token: str | None
    worktree_path: str | None
    spawn_state: str
    spawn_creation_claimed: bool = False
    fenced: bool
    cleanup_state: str
    spawn_ticket: str | None
    pid: int | None
    run_id: str | None


class WorkspaceRecord(FrozenModel):
    task_id: str
    repo_path: str
    worktree_path: str
    branch: str
    base_sha: str
    project_id: str | None = None
    working_subpath: str | None = None
    writer_token: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ShadowFinalizationIntent:
    """Immutable durable payload for a post-worker shadow finalization.

    Every input ``finalize_pending`` needs must be reconstructable from
    this intent alone (round 3 crash-consistency): no executor process
    state, no transient Python objects and no re-derived wall clock.
    ``observed_at`` is pinned at enqueue time — replays reuse the exact
    stored value so the deterministic ``observation_id`` byte-content
    stays identical. ``request_id`` / ``decision_id`` are the PENDING's
    routing identity (the observation's identity keys), not the
    dispatch request id.

    Round 4 (§17): ``identity_json`` freezes the pending's remaining
    immutable identity (execution targets, snapshot ids, provider /
    pool, task family, quota confidence, collector status, predicted
    burn). With it the intent is SELF-SUFFICIENT — a restart can build
    the exact expected :class:`ShadowObservation` and prove an existing
    observation byte-for-byte even after the pending file is gone.
    """

    pending_id: str
    task_id: str
    request_id: str
    decision_id: str
    dispatch_id: str = ""
    verified: bool = False
    execution_success: bool = False
    verification_success: bool | None = None
    quality_outcome: str | None = None
    failure_class: str | None = None
    failure_stage: str | None = None
    regression_detected: bool = False
    attempts_to_green: int | None = None
    time_to_green_seconds: float | None = None
    handoff_count: int = 0
    reset_cycle_ids: tuple[str, ...] = ()
    quota_after_snapshot_ids: tuple[str, ...] = ()
    observed_burn_fraction: float | None = None
    observed_at: str = ""
    identity_json: str = "{}"

    @property
    def finalization_id(self) -> str:
        return f"shadow-finalize-{self.pending_id}"

    def payload_json(self) -> str:
        return _json(
            {
                "pending_id": self.pending_id,
                "task_id": self.task_id,
                "dispatch_id": self.dispatch_id,
                "request_id": self.request_id,
                "decision_id": self.decision_id,
                "verified": self.verified,
                "execution_success": self.execution_success,
                "verification_success": self.verification_success,
                "quality_outcome": self.quality_outcome,
                "failure_class": self.failure_class,
                "failure_stage": self.failure_stage,
                "regression_detected": self.regression_detected,
                "attempts_to_green": self.attempts_to_green,
                "time_to_green_seconds": self.time_to_green_seconds,
                "handoff_count": self.handoff_count,
                "reset_cycle_ids": list(self.reset_cycle_ids),
                "quota_after_snapshot_ids": list(self.quota_after_snapshot_ids),
                "observed_burn_fraction": self.observed_burn_fraction,
                "observed_at": self.observed_at,
                "identity_json": self.identity_json,
            }
        )


def shadow_identity_payload(pending: Any) -> str:
    """Freeze a pending shadow's immutable identity for an intent.

    Duck-typed against ``PendingShadowObservation`` so this module stays
    decoupled from ``shadow_evidence``. Enums are stored by value,
    tuples as lists — canonical JSON makes the payload immutable
    conflict-detection material (round 4 §17).
    """

    collector_status = getattr(pending, "collector_status", None)
    quota_confidence = getattr(pending, "quota_confidence", None)
    return _json(
        {
            "manual_execution_target_id": pending.manual_execution_target_id,
            "scheduler_execution_target_id": pending.scheduler_execution_target_id,
            "catalog_snapshot_id": pending.catalog_snapshot_id,
            "policy_snapshot_id": pending.policy_snapshot_id,
            "quota_snapshot_ids": list(pending.quota_snapshot_ids),
            "provider_id": pending.provider_id,
            "quota_pool_id": pending.quota_pool_id,
            "task_family": pending.task_family,
            "quota_confidence": (None if quota_confidence is None else quota_confidence.value),
            "collector_status": (None if collector_status is None else collector_status.value),
            "predicted_burn_fraction": pending.predicted_burn_fraction,
        }
    )


#: M1 WP2: the set of min_tier values the schema accepts. Mirrors
#: :class:`personal_ai_orchestrator.model_tiers.ModelTier`; we
#: deliberately inline the four strings here so this module does not
#: import model_tiers (which is fine to import but not required at
#: the storage layer — the recompute happens in
#: dispatch_recommender).
_VALID_TIER_VALUES = frozenset({"T0", "T1", "T2", "T3"})


class SafetyKernelStore:
    """SQLite/WAL source of truth for P0 task, run, workspace, audit and routing state."""

    def __init__(self, path: str | Path = ":memory:", *, timeout_seconds: float = 5.0) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(
            self.path, isolation_level=None, timeout=max(0.0, timeout_seconds)
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()
        # Identity is additive metadata, not execution authority. A single
        # INSERT OR IGNORE atomically initializes old/new stores across hosts.
        self.connection.execute(
            "INSERT OR IGNORE INTO store_metadata(key,value) VALUES ('store_id',?)",
            (uuid4().hex,),
        )

    @property
    def store_id(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM store_metadata WHERE key='store_id'"
        ).fetchone()
        value = row["value"] if row is not None else None
        if (
            not isinstance(value, str)
            or len(value) != 32
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise RuntimeError("store identity unavailable")
        return value

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                intent TEXT NOT NULL,
                project_id TEXT,
                base_sha TEXT,
                working_subpath TEXT,
                state TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                canonical_repo_root TEXT NOT NULL,
                git_root TEXT NOT NULL,
                default_branch TEXT NOT NULL,
                last_known_head TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                working_subpath TEXT,
                remote_url TEXT,
                last_opened_at TEXT,
                storage_availability TEXT NOT NULL,
                security_bookmark_b64 TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                worker_id TEXT NOT NULL,
                pid INTEGER,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                result_json TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_task
                ON runs(task_id) WHERE status = 'RUNNING';
            CREATE TABLE IF NOT EXISTS workspaces (
                task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                project_id TEXT,
                repo_path TEXT NOT NULL,
                worktree_path TEXT NOT NULL UNIQUE,
                branch TEXT NOT NULL,
                base_sha TEXT NOT NULL,
                working_subpath TEXT,
                writer_token TEXT,
                writer_acquired_at TEXT
            );
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routing_decisions (
                decision_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                task_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_dispatches (
                dispatch_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                task_state_version INTEGER NOT NULL,
                execution_target_id TEXT NOT NULL,
                authority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                failure_code TEXT,
                failure_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS worker_attempts (
                attempt_id TEXT PRIMARY KEY,
                dispatch_id TEXT UNIQUE REFERENCES owner_dispatches(dispatch_id),
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                task_state_version INTEGER NOT NULL,
                dispatch_task_state_version INTEGER,
                authority TEXT NOT NULL,
                executor_id TEXT NOT NULL,
                writer_token TEXT,
                worktree_path TEXT,
                spawn_state TEXT NOT NULL DEFAULT 'CLAIMED',
                spawn_creation_claimed INTEGER NOT NULL DEFAULT 0,
                fenced INTEGER NOT NULL DEFAULT 0,
                cleanup_state TEXT NOT NULL DEFAULT 'UNRESOLVED',
                spawn_ticket TEXT UNIQUE,
                pid INTEGER,
                run_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS worker_attempt_quarantine
                ON worker_attempts(task_id) WHERE cleanup_state != 'CONFIRMED';
            CREATE TABLE IF NOT EXISTS worker_cleanup_receipts (
                attempt_id TEXT NOT NULL REFERENCES worker_attempts(attempt_id),
                cleanup_state TEXT NOT NULL CHECK(cleanup_state IN ('UNKNOWN', 'CONFIRMED')),
                executor_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(attempt_id, cleanup_state)
            );
            CREATE TRIGGER IF NOT EXISTS immutable_worker_cleanup_receipt_update
                BEFORE UPDATE ON worker_cleanup_receipts BEGIN
                SELECT RAISE(ABORT, 'worker cleanup receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_worker_cleanup_receipt_delete
                BEFORE DELETE ON worker_cleanup_receipts BEGIN
                SELECT RAISE(ABORT, 'worker cleanup receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_worker_attempt_identity
                BEFORE UPDATE ON worker_attempts
                WHEN NEW.attempt_id IS NOT OLD.attempt_id
                  OR NEW.dispatch_id IS NOT OLD.dispatch_id
                  OR NEW.task_id IS NOT OLD.task_id
                  OR NEW.task_state_version IS NOT OLD.task_state_version
                  OR NEW.dispatch_task_state_version IS NOT OLD.dispatch_task_state_version
                  OR NEW.authority IS NOT OLD.authority
                  OR NEW.executor_id IS NOT OLD.executor_id
                  OR NEW.writer_token IS NOT OLD.writer_token
                  OR NEW.worktree_path IS NOT OLD.worktree_path
                  OR (OLD.fenced = 1 AND NEW.fenced != 1)
                  OR (OLD.spawn_creation_claimed = 1 AND NEW.spawn_creation_claimed != 1)
                BEGIN SELECT RAISE(ABORT, 'worker attempt identity and fence are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_worker_attempt_delete
                BEFORE DELETE ON worker_attempts
                BEGIN SELECT RAISE(ABORT, 'worker attempts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS confirmed_cleanup_requires_receipt
                BEFORE UPDATE OF cleanup_state ON worker_attempts
                WHEN NEW.cleanup_state = 'CONFIRMED' AND NOT EXISTS (
                    SELECT 1 FROM worker_cleanup_receipts r WHERE r.attempt_id=NEW.attempt_id
                    AND r.executor_id=NEW.executor_id AND r.cleanup_state='CONFIRMED')
                BEGIN SELECT RAISE(ABORT, 'confirmed cleanup requires immutable receipt'); END;
            CREATE TRIGGER IF NOT EXISTS quarantined_workspace_update
                BEFORE UPDATE ON workspaces
                WHEN (NEW.writer_token IS NOT OLD.writer_token
                      OR NEW.writer_acquired_at IS NOT OLD.writer_acquired_at
                      OR NEW.task_id IS NOT OLD.task_id
                      OR NEW.worktree_path IS NOT OLD.worktree_path)
                 AND EXISTS (SELECT 1 FROM worker_attempts a
                     WHERE a.cleanup_state != 'CONFIRMED'
                     AND (a.task_id = OLD.task_id OR a.task_id = NEW.task_id
                          OR a.worktree_path = OLD.worktree_path
                          OR a.worktree_path = NEW.worktree_path))
                BEGIN SELECT RAISE(ABORT, 'worker cleanup quarantine'); END;
            CREATE TRIGGER IF NOT EXISTS quarantined_workspace_delete
                BEFORE DELETE ON workspaces
                WHEN EXISTS (SELECT 1 FROM worker_attempts a
                     WHERE a.cleanup_state != 'CONFIRMED'
                     AND (a.task_id = OLD.task_id OR a.worktree_path = OLD.worktree_path))
                BEGIN SELECT RAISE(ABORT, 'worker cleanup quarantine'); END;
            CREATE TRIGGER IF NOT EXISTS quarantined_workspace_insert
                BEFORE INSERT ON workspaces
                WHEN EXISTS (SELECT 1 FROM worker_attempts a
                     WHERE a.cleanup_state != 'CONFIRMED'
                     AND (a.task_id = NEW.task_id OR a.worktree_path = NEW.worktree_path))
                BEGIN SELECT RAISE(ABORT, 'worker cleanup quarantine'); END;
            CREATE TABLE IF NOT EXISTS quota_observation_history (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                quota_pool_id TEXT NOT NULL,
                window_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                remaining_fraction REAL,
                confidence TEXT NOT NULL,
                measurement_source TEXT NOT NULL,
                reset_at TEXT,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auto_shadow_cleanup_outbox (
                pending_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS auto_shadow_cleanup_outbox_open
                ON auto_shadow_cleanup_outbox(created_at)
                WHERE completed_at IS NULL;
            CREATE TABLE IF NOT EXISTS auto_shadow_finalize_outbox (
                finalization_id TEXT PRIMARY KEY,
                pending_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                dispatch_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                verified INTEGER NOT NULL,
                execution_success INTEGER NOT NULL,
                verification_success INTEGER,
                quality_outcome TEXT,
                failure_class TEXT,
                failure_stage TEXT,
                regression_detected INTEGER NOT NULL DEFAULT 0,
                attempts_to_green INTEGER,
                time_to_green_seconds REAL,
                handoff_count INTEGER NOT NULL DEFAULT 0,
                reset_cycle_ids TEXT NOT NULL DEFAULT '[]',
                quota_after_snapshot_ids TEXT NOT NULL DEFAULT '[]',
                observed_burn_fraction REAL,
                observed_at TEXT NOT NULL,
                identity_json TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS auto_shadow_finalize_outbox_open
                ON auto_shadow_finalize_outbox(created_at)
                WHERE completed_at IS NULL;
            CREATE INDEX IF NOT EXISTS auto_shadow_finalize_outbox_pending
                ON auto_shadow_finalize_outbox(pending_id);
            """
        )
        self._ensure_column("tasks", "project_id", "TEXT")
        self._ensure_column("tasks", "base_sha", "TEXT")
        self._ensure_column("tasks", "working_subpath", "TEXT")
        self._ensure_column("tasks", "scheduling_policy", "TEXT")
        self._ensure_column("tasks", "manual_execution_target_id", "TEXT")
        # M1 WP2: tier floor for the dispatch target. Default "T1" so
        # every existing row satisfies the new constraint without a
        # migration script. New submits may pass any of T0/T1/T2/T3.
        self._ensure_column("tasks", "min_tier", "TEXT NOT NULL DEFAULT 'T1'")
        # M1 WP5a-1: AUTO_PLANNED / AUTO_GRACE bookkeeping. All four
        # columns default to NULL so a pre-WP5a-1 task row reads
        # cleanly and ``AUTO_*`` state machines never trip on legacy
        # data. WP5a-1 commit 3 does NOT add an ``AUTO_* → RUNNING``
        # transition — that lives in WP5a-2's tick.
        self._ensure_column("tasks", "auto_decision_id", "TEXT")
        self._ensure_column("tasks", "auto_grace_deadline_at", "TEXT")
        self._ensure_column("tasks", "auto_acked_at", "TEXT")
        self._ensure_column("tasks", "auto_reason", "TEXT")
        # Round 4 §17: the finalize outbox freezes the pending's full
        # immutable identity so restarts can prove observations without
        # the pending file.
        self._ensure_column(
            "auto_shadow_finalize_outbox", "identity_json", "TEXT NOT NULL DEFAULT '{}'"
        )
        self._ensure_column("projects", "scheduling_policy", "TEXT")
        self._ensure_column("projects", "manual_execution_target_id", "TEXT")
        # M1 WP5a-1: project-level supervised-auto settings. The
        # defaults mirror the dataclass defaults (False / False / 120)
        # so an existing project keeps the pre-WP5a-1 manual-only
        # behavior. Old DBs migrate silently via the column defaults.
        self._ensure_column(
            "projects",
            "supervised_auto_allowed",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "projects",
            "unattended_allowed",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "projects",
            "grace_seconds",
            "INTEGER NOT NULL DEFAULT 120",
        )
        self._ensure_column("workspaces", "project_id", "TEXT")
        self._ensure_column("workspaces", "working_subpath", "TEXT")
        self._ensure_column("worker_attempts", "dispatch_task_state_version", "INTEGER")
        self._ensure_column(
            "worker_attempts", "spawn_creation_claimed", "INTEGER NOT NULL DEFAULT 0"
        )

    def _fence_previous_worker_attempts_tx(self) -> None:
        """Startup never turns absence of a run into proof that no child exists.

        Opening the host store fences old executors. A matching late receipt can
        add cleanup facts, but cannot authorize a start or release any writer.
        Legacy reservations and live runs receive permanent UNKNOWN tombstones.
        """
        stamp = _now()
        previous = self.connection.execute(
            "SELECT attempt_id,task_id FROM worker_attempts WHERE fenced=0"
        ).fetchall()
        self.connection.execute(
            "UPDATE worker_attempts SET fenced=1,updated_at=? WHERE fenced=0", (stamp,)
        )
        for row in previous:
            self._audit(
                row["task_id"],
                "WORKER_ATTEMPT_FENCED",
                {"attempt_id": row["attempt_id"], "reason": "host startup"},
            )
        legacy = self.connection.execute(
            """
            SELECT d.*, w.writer_token, w.worktree_path FROM owner_dispatches d
            LEFT JOIN workspaces w ON w.task_id=d.task_id
            WHERE d.status IN ('RESERVED','STARTED')
              AND NOT EXISTS (SELECT 1 FROM worker_attempts a
                              WHERE a.dispatch_id=d.dispatch_id)
            """
        ).fetchall()
        for row in legacy:
            self.connection.execute(
                """INSERT INTO worker_attempts(
                    attempt_id,dispatch_id,task_id,task_state_version,authority,
                    executor_id,writer_token,worktree_path,spawn_state,fenced,
                    cleanup_state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?, 'LEGACY_UNKNOWN',1,'UNKNOWN',?,?)""",
                (
                    f"legacy-dispatch:{row['dispatch_id']}",
                    row["dispatch_id"],
                    row["task_id"],
                    row["task_state_version"],
                    row["authority"],
                    "legacy-unknown",
                    row["writer_token"],
                    row["worktree_path"],
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                row["task_id"],
                "WORKER_CLEANUP_QUARANTINED",
                {"dispatch_id": row["dispatch_id"], "reason": "legacy attempt not observed"},
            )
        runs = self.connection.execute(
            """SELECT r.*, t.state_version, w.writer_token, w.worktree_path
            FROM runs r JOIN tasks t ON t.task_id=r.task_id
            LEFT JOIN workspaces w ON w.task_id=r.task_id
            WHERE r.status='RUNNING'
              AND NOT EXISTS (SELECT 1 FROM worker_attempts a WHERE a.run_id=r.run_id)
              AND NOT EXISTS (SELECT 1 FROM worker_attempts a
                   WHERE a.task_id=r.task_id AND a.spawn_state='LEGACY_UNKNOWN')"""
        ).fetchall()
        for row in runs:
            self.connection.execute(
                """INSERT INTO worker_attempts(
                    attempt_id,task_id,task_state_version,authority,executor_id,
                    writer_token,worktree_path,spawn_state,fenced,cleanup_state,
                    pid,run_id,created_at,updated_at
                ) VALUES(?,?,?,'LEGACY_UNKNOWN','legacy-unknown',?,?,
                         'LEGACY_UNKNOWN',1,'UNKNOWN',?,?,?,?)""",
                (
                    f"legacy-run:{row['run_id']}",
                    row["task_id"],
                    row["state_version"],
                    row["writer_token"],
                    row["worktree_path"],
                    row["pid"],
                    row["run_id"],
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                row["task_id"],
                "WORKER_CLEANUP_QUARANTINED",
                {"run_id": row["run_id"], "reason": "legacy active run without cleanup evidence"},
            )

        uncertain_tasks = self.connection.execute(
            """SELECT t.*,w.writer_token,w.worktree_path FROM tasks t
            LEFT JOIN workspaces w ON w.task_id=t.task_id
            WHERE (t.state IN ('RUNNING','WORKER_FINISHED','VERIFYING')
                   OR w.writer_token IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM worker_attempts a WHERE a.task_id=t.task_id
                  AND (w.writer_token IS NULL OR a.writer_token IS w.writer_token))"""
        ).fetchall()
        for row in uncertain_tasks:
            self.connection.execute(
                """INSERT INTO worker_attempts(
                    attempt_id,task_id,task_state_version,authority,executor_id,writer_token,
                    worktree_path,spawn_state,fenced,cleanup_state,created_at,updated_at
                ) VALUES(?,?,?,'LEGACY_UNKNOWN','legacy-unknown',?,?,
                         'LEGACY_UNKNOWN',1,'UNKNOWN',?,?)""",
                (
                    f"legacy-task:{row['task_id']}",
                    row["task_id"],
                    row["state_version"],
                    row["writer_token"],
                    row["worktree_path"],
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                row["task_id"],
                "WORKER_CLEANUP_QUARANTINED",
                {"reason": "legacy task or retained writer without cleanup evidence"},
            )

    def has_cleanup_quarantine(self, task_id: str) -> bool:
        return (
            self.connection.execute(
                """SELECT 1 FROM worker_attempts a WHERE a.cleanup_state!='CONFIRMED'
                AND (a.task_id=? OR a.worktree_path=(
                    SELECT worktree_path FROM workspaces WHERE task_id=?)) LIMIT 1""",
                (task_id, task_id),
            ).fetchone()
            is not None
        )

    def assert_cleanup_clear(self, task_id: str) -> None:
        if self.has_cleanup_quarantine(task_id):
            raise RuntimeError("task worktree has unresolved worker cleanup quarantine")

    def get_worker_attempt(self, attempt_id: str) -> WorkerAttemptRecord:
        row = self.connection.execute(
            "SELECT * FROM worker_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return WorkerAttemptRecord(**{name: row[name] for name in WorkerAttemptRecord.model_fields})

    def get_worker_attempt_for_dispatch(self, dispatch_id: str) -> WorkerAttemptRecord | None:
        row = self.connection.execute(
            "SELECT attempt_id FROM worker_attempts WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
        return self.get_worker_attempt(row["attempt_id"]) if row else None

    def fence_worker_attempt(self, *, attempt_id: str, executor_id: str) -> WorkerAttemptRecord:
        """Revoke unused spawn/start authority without asserting cleanup."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            if not attempt.fenced:
                self.connection.execute(
                    "UPDATE worker_attempts SET fenced=1,updated_at=? WHERE attempt_id=?",
                    (_now(), attempt_id),
                )
                self._audit(attempt.task_id, "WORKER_ATTEMPT_FENCED", {"attempt_id": attempt_id})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_worker_attempt(attempt_id)

    def _check_attempt_executor(self, attempt_id: str, executor_id: str) -> WorkerAttemptRecord:
        attempt = self.get_worker_attempt(attempt_id)
        if attempt.executor_id != executor_id or attempt.spawn_state == "LEGACY_UNKNOWN":
            raise RuntimeError("worker attempt executor identity mismatch")
        return attempt

    @staticmethod
    def _check_manual_target_binding(task: TaskRecord, execution_target_id: str) -> None:
        # Vetoed AUTO tasks intentionally retain targetless MANUAL semantics.
        # Only an explicit binding constrains legacy owner dispatch paths.
        if (
            task.manual_execution_target_id
            and task.manual_execution_target_id != execution_target_id
        ):
            raise RuntimeError("manual execution target mismatch")

    def _check_attempt_launch_authority(self, attempt: WorkerAttemptRecord) -> None:
        if attempt.fenced or attempt.cleanup_state != "UNRESOLVED":
            raise RuntimeError("worker attempt executor is fenced or already completed")
        dispatch = self.connection.execute(
            "SELECT * FROM owner_dispatches WHERE dispatch_id=?", (attempt.dispatch_id,)
        ).fetchone()
        if (
            dispatch is None
            or dispatch["task_id"] != attempt.task_id
            or dispatch["status"] != OwnerDispatchStatus.RESERVED.value
            or dispatch["task_state_version"] != attempt.dispatch_task_state_version
            or dispatch["authority"] != attempt.authority
        ):
            raise RuntimeError("worker attempt lost dispatch authority")
        task = self.get_task(attempt.task_id)
        self._check_manual_target_binding(task, dispatch["execution_target_id"])
        expected = expected_source_state_for_dispatch_authority(attempt.authority)
        if task.state is not expected or task.state_version != attempt.task_state_version:
            raise RuntimeError("worker attempt lost task state authority")
        workspace = self.get_workspace(attempt.task_id)
        if (
            workspace.writer_token != attempt.writer_token
            or workspace.worktree_path != attempt.worktree_path
        ):
            raise RuntimeError("worker attempt lost exact writer ownership")
        if (
            self.connection.execute(
                "SELECT 1 FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
                (attempt.task_id,),
            ).fetchone()
            is not None
        ):
            raise RuntimeError("task already has an active worker run")
        other = self.connection.execute(
            """SELECT 1 FROM worker_attempts WHERE task_id=? AND attempt_id!=?
               AND cleanup_state!='CONFIRMED' LIMIT 1""",
            (attempt.task_id, attempt.attempt_id),
        ).fetchone()
        if other is not None:
            raise RuntimeError("task worktree has unresolved worker cleanup quarantine")

    def begin_worker_attempt(
        self,
        *,
        dispatch_id: str,
        task_id: str,
        attempt_id: str,
        executor_id: str,
        writer_token: str,
        expected_task_version: int | None = None,
    ) -> WorkerAttemptRecord:
        """Claim a dispatch once, before the adapter may enter process creation."""
        if not all((dispatch_id, task_id, attempt_id, executor_id, writer_token)):
            raise ValueError("worker attempt requires complete identity")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            dispatch = self.connection.execute(
                "SELECT * FROM owner_dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if dispatch is None or dispatch["task_id"] != task_id:
                raise ValueError("dispatch_id does not belong to task_id")
            self.assert_cleanup_clear(task_id)
            task = self.get_task(task_id)
            if expected_task_version is not None and task.state_version != expected_task_version:
                raise RuntimeError("stale task state_version")
            workspace = self.get_workspace(task_id)
            if workspace.writer_token != writer_token:
                raise RuntimeError("worker attempt requires the active writer lock")
            stamp = _now()
            self.connection.execute(
                """INSERT INTO worker_attempts(
                    attempt_id,dispatch_id,task_id,task_state_version,dispatch_task_state_version,
                    authority,executor_id,writer_token,worktree_path,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    dispatch_id,
                    task_id,
                    task.state_version,
                    dispatch["task_state_version"],
                    dispatch["authority"],
                    executor_id,
                    writer_token,
                    workspace.worktree_path,
                    stamp,
                    stamp,
                ),
            )
            self._check_attempt_launch_authority(self.get_worker_attempt(attempt_id))
            self._audit(
                task_id,
                "WORKER_ATTEMPT_CLAIMED",
                {"attempt_id": attempt_id, "dispatch_id": dispatch_id, "executor_id": executor_id},
            )
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self.connection.execute("ROLLBACK")
            raise RuntimeError("dispatch already has an immutable worker attempt") from error
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_worker_attempt(attempt_id)

    def permit_worker_spawn(self, *, attempt_id: str, executor_id: str) -> WorkerAttemptRecord:
        """Consume the one-shot permit immediately before crossing the OS boundary."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            self._check_attempt_launch_authority(attempt)
            if attempt.spawn_state != "CLAIMED":
                raise RuntimeError("worker spawn permit was already consumed")
            self.connection.execute(
                "UPDATE worker_attempts SET spawn_state='SPAWN_ENTERED',updated_at=? "
                "WHERE attempt_id=?",
                (_now(), attempt_id),
            )
            self._audit(attempt.task_id, "WORKER_SPAWN_ENTERED", {"attempt_id": attempt_id})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_worker_attempt(attempt_id)

    @contextmanager
    def worker_spawn_guard(
        self,
        *,
        attempt_id: str,
        executor_id: str,
        spawn_ticket: str,
    ) -> Iterator[Callable[[int], None]]:
        """Hold the fence lock across synchronous OS creation and PID capture.

        The adapter must not await inside this guard. The earlier durable spawn
        permit and a one-shot creation claim survive a rollback after OS entry,
        so a receipt failure cannot authorize a second creation or NOT_STARTED.
        Startup fencing cannot interleave between validation and child creation.
        """
        if not spawn_ticket:
            raise ValueError("spawn_ticket is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            self._check_attempt_launch_authority(attempt)
            if attempt.spawn_state != "SPAWN_ENTERED" or attempt.spawn_creation_claimed:
                raise RuntimeError("worker OS creation requires an unused spawn permit")
            self.connection.execute(
                "UPDATE worker_attempts SET spawn_creation_claimed=1,updated_at=? "
                "WHERE attempt_id=?",
                (_now(), attempt_id),
            )
            self._audit(attempt.task_id, "WORKER_CREATION_CLAIMED", {"attempt_id": attempt_id})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            self._check_attempt_launch_authority(attempt)
            if attempt.spawn_state != "SPAWN_ENTERED" or attempt.spawn_ticket is not None:
                raise RuntimeError("worker OS creation already observed")
            recorded = False

            def report_created(pid: int) -> None:
                nonlocal recorded
                if recorded or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                    raise RuntimeError("worker creation requires one exact positive child PID")
                recorded = True
                self.connection.execute(
                    "UPDATE worker_attempts SET spawn_state='SPAWN_OBSERVED',spawn_ticket=?, "
                    "pid=?,updated_at=? WHERE attempt_id=?",
                    (spawn_ticket, pid, _now(), attempt_id),
                )
                self._audit(
                    attempt.task_id,
                    "WORKER_SPAWN_OBSERVED",
                    {
                        "attempt_id": attempt_id,
                        "spawn_ticket": spawn_ticket,
                        "pid": pid,
                    },
                )

            yield report_created
            if not recorded:
                raise RuntimeError("worker creation guard exited without child observation")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def record_worker_spawn(
        self,
        *,
        attempt_id: str,
        executor_id: str,
        spawn_ticket: str,
        pid: int | None = None,
        run_id: str | None = None,
    ) -> WorkerAttemptRecord:
        """Persist exact child facts; late observations never restore launch authority."""
        if not spawn_ticket:
            raise ValueError("spawn_ticket is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            if attempt.spawn_state not in {"SPAWN_ENTERED", "SPAWN_OBSERVED"}:
                raise RuntimeError("worker spawn was not permitted")
            if attempt.spawn_ticket is not None:
                if (attempt.spawn_ticket, attempt.pid, attempt.run_id) != (
                    spawn_ticket,
                    pid,
                    run_id,
                ):
                    raise RuntimeError("worker spawn identity is immutable")
            else:
                if attempt.cleanup_state == "CONFIRMED":
                    raise RuntimeError("completed no-child attempt cannot observe a new spawn")
                self.connection.execute(
                    "UPDATE worker_attempts SET spawn_state='SPAWN_OBSERVED',spawn_ticket=?, "
                    "pid=?,run_id=?,updated_at=? WHERE attempt_id=?",
                    (spawn_ticket, pid, run_id, _now(), attempt_id),
                )
                self._audit(
                    attempt.task_id,
                    "WORKER_SPAWN_OBSERVED",
                    {
                        "attempt_id": attempt_id,
                        "spawn_ticket": spawn_ticket,
                        "pid": pid,
                        "run_id": run_id,
                    },
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_worker_attempt(attempt_id)

    def complete_worker_attempt(
        self,
        *,
        attempt_id: str,
        executor_id: str,
        cleanup: dict[str, Any],
    ) -> WorkerAttemptRecord:
        """Append immutable same-attempt cleanup facts without releasing a writer.

        UNKNOWN may later receive a matching CONFIRMED receipt. Neither missing
        run rows nor a legacy PID can establish cleanup or NOT_STARTED.
        """
        state = cleanup.get("state")
        if state not in {"CONFIRMED", "UNKNOWN"}:
            raise ValueError("cleanup state must be CONFIRMED or UNKNOWN")
        if "status" in cleanup and cleanup["status"] != state:
            raise ValueError("cleanup state conflicts with supervisor status")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self._check_attempt_executor(attempt_id, executor_id)
            for key in ("attempt_id", "executor_id", "dispatch_id", "task_id", "writer_token"):
                if key in cleanup and cleanup[key] != getattr(attempt, key):
                    raise RuntimeError("cleanup receipt does not match exact worker attempt")
            if cleanup.get("run_id") is not None and cleanup["run_id"] != attempt.run_id:
                raise RuntimeError("cleanup receipt does not match exact worker run")
            if (
                attempt.spawn_ticket is not None
                and "spawn_ticket" in cleanup
                and cleanup["spawn_ticket"] != attempt.spawn_ticket
            ):
                raise RuntimeError("cleanup receipt does not match exact spawn ticket")
            if state == "CONFIRMED":
                spawn_state = cleanup.get("spawn_state")
                if spawn_state == "NOT_STARTED":
                    if attempt.spawn_state != "CLAIMED" or not attempt.fenced:
                        raise RuntimeError("NOT_STARTED requires fenced, never-entered spawn")
                elif spawn_state == "SPAWN_FAILED_NO_CHILD":
                    if (
                        attempt.spawn_state != "SPAWN_ENTERED"
                        or not attempt.fenced
                        or attempt.spawn_ticket is not None
                        or attempt.pid is not None
                        or cleanup.get("child_created") is not False
                        or cleanup.get("process_create_failed") is not True
                    ):
                        raise RuntimeError("spawn failure lacks positive no-child evidence")
                else:
                    if spawn_state != "PROCESS_OBSERVED":
                        raise RuntimeError(
                            "confirmed cleanup requires explicit observed spawn state"
                        )
                    if cleanup.get("scope") != "CREATED_PROCESS_GROUP" or cleanup.get(
                        "capability"
                    ) not in {"WAITID_WNOWAIT", "WAITPID_REAP_ON_OBSERVE"}:
                        raise RuntimeError("confirmed cleanup lacks supported ownership scope")
                    if attempt.pid is None or attempt.pid <= 0:
                        raise RuntimeError("confirmed cleanup requires an observed exact child PID")
                    if not isinstance(cleanup.get("exit_code"), int) or isinstance(
                        cleanup["exit_code"], bool
                    ):
                        raise RuntimeError("confirmed cleanup requires an observed exit code")
                    for name in ("stdout", "stderr"):
                        pipe = cleanup.get(name)
                        if not isinstance(pipe, dict) or not (
                            pipe.get("eof") is True
                            and pipe.get("collector_done") is True
                            and pipe.get("forced_closed") is False
                            and "error" in pipe
                            and pipe["error"] is None
                        ):
                            raise RuntimeError("confirmed cleanup lacks complete pipe evidence")
                    if (
                        not attempt.spawn_ticket
                        or cleanup.get("spawn_ticket") != attempt.spawn_ticket
                    ):
                        raise RuntimeError(
                            "confirmed cleanup requires the exact observed spawn ticket"
                        )
                    if not all(
                        cleanup.get(key) is True
                        for key in ("leader_exit_observed", "child_reaped", "scope_empty")
                    ):
                        raise RuntimeError("confirmed cleanup lacks exit, reap or scope evidence")
            payload = _json(cleanup)
            existing = self.connection.execute(
                "SELECT payload_json,executor_id FROM worker_cleanup_receipts "
                "WHERE attempt_id=? AND cleanup_state=?",
                (attempt_id, state),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload or existing["executor_id"] != executor_id:
                    raise RuntimeError("worker cleanup receipt is immutable")
            else:
                self.connection.execute(
                    "INSERT INTO worker_cleanup_receipts VALUES(?,?,?,?,?)",
                    (attempt_id, state, executor_id, payload, _now()),
                )
                if attempt.cleanup_state != "CONFIRMED":
                    self.connection.execute(
                        "UPDATE worker_attempts SET cleanup_state=?,fenced=1,updated_at=? "
                        "WHERE attempt_id=?",
                        (state, _now(), attempt_id),
                    )
                self._audit(
                    attempt.task_id,
                    "WORKER_CLEANUP_RECORDED",
                    {
                        "attempt_id": attempt_id,
                        "executor_id": executor_id,
                        "cleanup": cleanup,
                    },
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_worker_attempt(attempt_id)

    def fail_worker_attempt(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_token: str,
        pid: int,
        result: Any,
        reason: str,
    ) -> bool:
        """Atomically repair only the still-active exact run/attempt generation.

        An old callback cannot block a newer run or release its writer. Missing
        cleanup remains quarantined even when emergency repair closes task/run
        presentation. The caller must separately request any writer release.
        """
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT * FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            workspace = self.connection.execute(
                "SELECT writer_token FROM workspaces WHERE task_id=?",
                (task_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT * FROM worker_attempts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            matches = (
                run is not None
                and run["task_id"] == task_id
                and run["status"] == "RUNNING"
                and run["pid"] == pid
                and workspace is not None
                and bool(writer_token)
                and workspace["writer_token"] == writer_token
                and (
                    attempt is None
                    or (
                        attempt["task_id"] == task_id
                        and attempt["writer_token"] == writer_token
                        and attempt["pid"] == pid
                    )
                )
            )
            if not matches:
                self.connection.execute("COMMIT")
                return False
            task = self.get_task(task_id)
            if task.state is not TaskState.RUNNING:
                self.connection.execute("COMMIT")
                return False
            run_status = "CLEANUP_UNKNOWN" if self.has_cleanup_quarantine(task_id) else "FAILED"
            stamp = _now()
            updated = self.connection.execute(
                "UPDATE runs SET status=?,finished_at=?,result_json=? "
                "WHERE run_id=? AND task_id=? AND status='RUNNING' AND pid IS ?",
                (run_status, stamp, _json(result), run_id, task_id, pid),
            )
            if updated.rowcount != 1:
                raise RuntimeError("worker emergency repair lost exact run ownership")
            next_version = task.state_version + 1
            updated = self.connection.execute(
                "UPDATE tasks SET state='BLOCKED',state_version=?,updated_at=? "
                "WHERE task_id=? AND state='RUNNING' AND state_version=?",
                (next_version, stamp, task_id, task.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("worker emergency repair lost task state ownership")
            self._audit(task_id, "RUN_FINISHED", {"run_id": run_id, "status": run_status})
            self._audit(
                task_id,
                "TASK_STATE_CHANGED",
                {
                    "from": TaskState.RUNNING.value,
                    "to": TaskState.BLOCKED.value,
                    "state_version": next_version,
                    "reason": reason,
                },
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _column_names(self, table: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _ensure_column(self, table: str, name: str, definition: str) -> None:
        if name in self._column_names(table):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _audit(self, task_id: str | None, event_type: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO audit_events(task_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (task_id, event_type, _json(payload), _now()),
        )

    def record_system_event(self, event_type: str, payload: Any) -> None:
        """Public entry for non-task host events (daemon supervisor, etc.).

        The audit_events table accepts ``task_id = NULL`` for events that
        do not belong to any one task (e.g. supervisor step failures, backoff
        transitions, periodic reconciliation sweeps). Cross-module callers
        must use this wrapper instead of reaching into the private
        ``_audit`` directly so the system-event contract stays in one place.
        """
        self._audit(task_id=None, event_type=event_type, payload=payload)

    def register_project(
        self,
        *,
        project_id: str,
        display_name: str,
        canonical_repo_root: str,
        git_root: str,
        default_branch: str,
        last_known_head: str,
        working_subpath: str | None = None,
        remote_url: str | None = None,
        storage_availability: ProjectAvailability = ProjectAvailability.ONLINE,
        security_bookmark_b64: str | None = None,
    ) -> ProjectRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT * FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            stamp = _now()
            if existing is not None:
                record = self._project_from_row(existing)
                same_identity = (
                    record.canonical_repo_root == canonical_repo_root
                    and record.git_root == git_root
                    and record.working_subpath == working_subpath
                )
                if not same_identity:
                    raise ValueError("project_id already belongs to a different repository")
                self.connection.execute(
                    """
                    UPDATE projects
                    SET display_name=?, default_branch=?, last_known_head=?, updated_at=?,
                        remote_url=?, storage_availability=?, security_bookmark_b64=?
                    WHERE project_id=?
                    """,
                    (
                        display_name,
                        default_branch,
                        last_known_head,
                        stamp,
                        remote_url,
                        storage_availability.value,
                        security_bookmark_b64,
                        project_id,
                    ),
                )
                self._audit(
                    None,
                    "PROJECT_UPDATED",
                    {
                        "project_id": project_id,
                        "git_root": git_root,
                        "storage_availability": storage_availability.value,
                    },
                )
                self.connection.execute("COMMIT")
                return self.get_project(project_id)

            duplicate = self.connection.execute(
                """
                SELECT project_id FROM projects
                WHERE canonical_repo_root=? AND git_root=?
                  AND COALESCE(working_subpath, '')=COALESCE(?, '')
                """,
                (canonical_repo_root, git_root, working_subpath),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("repository is already registered")
            self.connection.execute(
                """
                INSERT INTO projects(
                    project_id,display_name,canonical_repo_root,git_root,default_branch,
                    last_known_head,created_at,updated_at,working_subpath,remote_url,
                    last_opened_at,storage_availability,security_bookmark_b64
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    project_id,
                    display_name,
                    canonical_repo_root,
                    git_root,
                    default_branch,
                    last_known_head,
                    stamp,
                    stamp,
                    working_subpath,
                    remote_url,
                    None,
                    storage_availability.value,
                    security_bookmark_b64,
                ),
            )
            self._audit(
                None,
                "PROJECT_REGISTERED",
                {
                    "project_id": project_id,
                    "git_root": git_root,
                    "storage_availability": storage_availability.value,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_project(project_id)

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        rows = self.connection.execute(
            "SELECT * FROM projects ORDER BY last_opened_at DESC, updated_at DESC, display_name"
        ).fetchall()
        return tuple(self._project_from_row(row) for row in rows)

    def get_project(self, project_id: str) -> ProjectRecord:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return self._project_from_row(row)

    def set_project_scheduling_policy(
        self,
        project_id: str,
        *,
        scheduling_policy: str | None,
        manual_execution_target_id: str | None = None,
    ) -> ProjectRecord:
        """Persist an owner-chosen project scheduling override.

        ``scheduling_policy=None`` means the project defers to the global default. The
        override is durable so that Settings changes never rewrite it implicitly.
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_project(project_id)
            stamp = _now()
            self.connection.execute(
                """
                UPDATE projects
                SET scheduling_policy=?, manual_execution_target_id=?, updated_at=?
                WHERE project_id=?
                """,
                (scheduling_policy, manual_execution_target_id, stamp, project_id),
            )
            self._audit(
                None,
                "PROJECT_SCHEDULING_POLICY_SET",
                {
                    "project_id": project_id,
                    "scheduling_policy": scheduling_policy,
                    "manual_execution_target_id": manual_execution_target_id,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_project(project_id)

    def set_project_settings(
        self,
        project_id: str,
        *,
        supervised_auto_allowed: bool,
        unattended_allowed: bool,
        grace_seconds: int,
    ) -> ProjectRecord:
        """M1 WP5a-1: persist the project-level supervised-auto settings.

        Validates ``grace_seconds`` (1 ≤ grace_seconds ≤ 86_400, i.e.
        between 1 second and 24 hours) so a malformed payload fails
        closed with ``ValueError`` — the caller (the control-plane
        facade) maps it to 400.

        Returns the updated ``ProjectRecord``.
        """

        if not isinstance(grace_seconds, int) or grace_seconds < 1 or grace_seconds > 86_400:
            raise ValueError("grace_seconds must be an integer between 1 and 86400 (24h)")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_project(project_id)
            stamp = _now()
            self.connection.execute(
                """
                UPDATE projects
                SET supervised_auto_allowed=?, unattended_allowed=?,
                    grace_seconds=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    1 if supervised_auto_allowed else 0,
                    1 if unattended_allowed else 0,
                    int(grace_seconds),
                    stamp,
                    project_id,
                ),
            )
            self._audit(
                None,
                "PROJECT_SUPERVISED_AUTO_SETTINGS_SET",
                {
                    "project_id": project_id,
                    "supervised_auto_allowed": supervised_auto_allowed,
                    "unattended_allowed": unattended_allowed,
                    "grace_seconds": int(grace_seconds),
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_project(project_id)

    def update_project_availability(
        self,
        project_id: str,
        *,
        storage_availability: ProjectAvailability,
        last_known_head: str | None = None,
        default_branch: str | None = None,
    ) -> ProjectRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            project = self.get_project(project_id)
            stamp = _now()
            self.connection.execute(
                """
                UPDATE projects
                SET storage_availability=?, last_known_head=?, default_branch=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    storage_availability.value,
                    last_known_head or project.last_known_head,
                    default_branch or project.default_branch,
                    stamp,
                    project_id,
                ),
            )
            self._audit(
                None,
                "PROJECT_AVAILABILITY_UPDATED",
                {
                    "project_id": project_id,
                    "storage_availability": storage_availability.value,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_project(project_id)

    def mark_project_opened(self, project_id: str) -> ProjectRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_project(project_id)
            stamp = _now()
            self.connection.execute(
                "UPDATE projects SET last_opened_at=?, updated_at=? WHERE project_id=?",
                (stamp, stamp, project_id),
            )
            self._audit(None, "PROJECT_OPENED", {"project_id": project_id})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_project(project_id)

    def remove_project(self, project_id: str) -> ProjectRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            project = self.get_project(project_id)
            self.connection.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
            self._audit(
                None,
                "PROJECT_REMOVED_FROM_ORCHESTRATOR",
                {"project_id": project_id, "git_root": project.git_root},
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return project

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"],
            display_name=row["display_name"],
            canonical_repo_root=row["canonical_repo_root"],
            git_root=row["git_root"],
            default_branch=row["default_branch"],
            last_known_head=row["last_known_head"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            working_subpath=row["working_subpath"],
            remote_url=row["remote_url"],
            last_opened_at=(
                None
                if row["last_opened_at"] is None
                else datetime.fromisoformat(row["last_opened_at"])
            ),
            storage_availability=ProjectAvailability(row["storage_availability"]),
            security_bookmark_b64=row["security_bookmark_b64"],
            scheduling_policy=row["scheduling_policy"],
            manual_execution_target_id=row["manual_execution_target_id"],
            # SQLite stores booleans as integers; ``bool(int)`` round-trips.
            supervised_auto_allowed=bool(row["supervised_auto_allowed"]),
            unattended_allowed=bool(row["unattended_allowed"]),
            grace_seconds=int(row["grace_seconds"]),
        )

    def task_count_for_project(self, project_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?", (project_id,)
        ).fetchone()
        return int(row["n"])

    def record_quota_observation(
        self,
        *,
        provider_id: str,
        quota_pool_id: str,
        window_id: str,
        observed_at: str,
        remaining_fraction: float | None,
        confidence: str,
        measurement_source: str,
        reset_at: str | None,
        state: str,
        retention: int = 500,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            duplicate = self.connection.execute(
                """
                SELECT observation_id FROM quota_observation_history
                WHERE provider_id=? AND quota_pool_id=? AND window_id=? AND observed_at=?
                  AND confidence=? AND measurement_source=? AND state=?
                  AND COALESCE(reset_at, '')=COALESCE(?, '')
                  AND COALESCE(remaining_fraction, -1.0)=COALESCE(?, -1.0)
                """,
                (
                    provider_id,
                    quota_pool_id,
                    window_id,
                    observed_at,
                    confidence,
                    measurement_source,
                    state,
                    reset_at,
                    remaining_fraction,
                ),
            ).fetchone()
            if duplicate is None:
                self.connection.execute(
                    """
                    INSERT INTO quota_observation_history(
                        provider_id,quota_pool_id,window_id,observed_at,
                        remaining_fraction,confidence,measurement_source,reset_at,state
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        provider_id,
                        quota_pool_id,
                        window_id,
                        observed_at,
                        remaining_fraction,
                        confidence,
                        measurement_source,
                        reset_at,
                        state,
                    ),
                )
            self.connection.execute(
                """
                DELETE FROM quota_observation_history
                WHERE observation_id NOT IN (
                    SELECT observation_id FROM quota_observation_history
                    ORDER BY observed_at DESC, observation_id DESC
                    LIMIT ?
                )
                """,
                (retention,),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def quota_observation_history(self, *, limit: int = 200) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT * FROM quota_observation_history
                ORDER BY observed_at DESC, observation_id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        )

    def submit_task(
        self,
        *,
        task_id: str,
        request_id: str,
        intent: str,
        project_id: str | None = None,
        base_sha: str | None = None,
        working_subpath: str | None = None,
        scheduling_policy: str | None = None,
        manual_execution_target_id: str | None = None,
        min_tier: str = "T1",
        delegated_parent: tuple[str, str] | None = None,
    ) -> TaskRecord:
        if project_id is not None:
            self.get_project(project_id)
            if base_sha is None:
                raise ValueError("base_sha is required for registered project tasks")
        # Storage-level validation: the schema allows any TEXT but
        # the dispatch recommender expects one of the four known
        # ModelTier values. Failing here is cheaper than failing
        # later inside the recommender with a KeyError.
        if min_tier not in _VALID_TIER_VALUES:
            raise ValueError(
                f"min_tier must be one of {sorted(_VALID_TIER_VALUES)}, got {min_tier!r}"
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if delegated_parent is not None:
                parent_id, parent_run_id = delegated_parent
                parent = self.get_task(parent_id)
                run = self.connection.execute(
                    "SELECT task_id,status FROM runs WHERE run_id=?", (parent_run_id,)
                ).fetchone()
                if (
                    run is None
                    or run["task_id"] != parent_id
                    or run["status"] != "RUNNING"
                    or parent.state is not TaskState.RUNNING
                    or parent.project_id != project_id
                    or parent.base_sha != base_sha
                    or parent.working_subpath != working_subpath
                    or parent_id == task_id
                    or parent.request_id.startswith("pi5-child-submit-")
                    or parent.scheduling_policy == "MANUAL"
                ):
                    raise ValueError("delegated parent is not an active matching run")
            existing = self.connection.execute(
                "SELECT * FROM tasks WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing is not None:
                record = self._task_from_row(existing)
                if (
                    record.task_id != task_id
                    or record.intent != intent
                    or record.project_id != project_id
                    or record.base_sha != base_sha
                    or record.working_subpath != working_subpath
                    or record.scheduling_policy != scheduling_policy
                    or record.manual_execution_target_id != manual_execution_target_id
                    or record.min_tier != min_tier
                ):
                    raise ValueError("request_id already belongs to a different task submission")
                self.connection.execute("COMMIT")
                return record

            stamp = _now()
            self.connection.execute(
                """
                INSERT INTO tasks(
                    task_id,request_id,intent,project_id,base_sha,working_subpath,
                    state,state_version,created_at,updated_at,
                    scheduling_policy,manual_execution_target_id,min_tier
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    request_id,
                    intent,
                    project_id,
                    base_sha,
                    working_subpath,
                    TaskState.SUBMITTED.value,
                    0,
                    stamp,
                    stamp,
                    scheduling_policy,
                    manual_execution_target_id,
                    min_tier,
                ),
            )
            self._audit(
                task_id,
                "TASK_SUBMITTED",
                {
                    "request_id": request_id,
                    "intent": intent,
                    "project_id": project_id,
                    "base_sha": base_sha,
                    "working_subpath": working_subpath,
                    "scheduling_policy": scheduling_policy,
                    "manual_execution_target_id": manual_execution_target_id,
                    **(
                        {
                            "delegated_parent_task_id": delegated_parent[0],
                            "delegated_parent_run_id": delegated_parent[1],
                        }
                        if delegated_parent is not None
                        else {}
                    ),
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        row = self.connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_from_row(row)

    def resolve_delegated_task_lineage(self, child_task_id: str) -> DelegatedTaskLineage:
        """Resolve one delegated child through canonical submission metadata.

        ``TaskRecord`` intentionally does not duplicate relationship metadata.
        Delegation is established atomically by :meth:`submit_task` in the
        child's append-only ``TASK_SUBMITTED`` audit payload. Resolution is
        fail-closed: missing, malformed, partial, or duplicate submission
        evidence is never guessed or inferred from identifier conventions.
        """

        try:
            child = self.get_task(child_task_id)
        except KeyError:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.CHILD_TASK_NOT_FOUND,
                failure_category="CHILD_TASK",
            ) from None
        try:
            submissions = tuple(
                event
                for event in self.audit_events(child_task_id)
                if event["event_type"] == "TASK_SUBMITTED"
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.METADATA_INVALID,
                failure_category="LINEAGE_METADATA",
            ) from None
        if not submissions:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.TASK_SUBMISSION_MISSING,
                failure_category="TASK_SUBMISSION",
            )
        if len(submissions) != 1:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.TASK_SUBMISSION_AMBIGUOUS,
                failure_category="TASK_SUBMISSION",
            )

        payload = submissions[0]["payload"]
        if not isinstance(payload, dict):
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.METADATA_INVALID,
                failure_category="LINEAGE_METADATA",
            )
        parent_key = "delegated_parent_task_id"
        run_key = "delegated_parent_run_id"
        has_parent = parent_key in payload
        has_run = run_key in payload
        if not has_parent and not has_run:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.METADATA_MISSING,
                failure_category="NOT_DELEGATED_CHILD",
            )
        if has_parent != has_run:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.METADATA_PARTIAL,
                failure_category="LINEAGE_METADATA",
            )
        parent_task_id = payload[parent_key]
        parent_run_id = payload[run_key]
        if (
            not isinstance(parent_task_id, str)
            or not parent_task_id
            or not isinstance(parent_run_id, str)
            or not parent_run_id
            or parent_task_id == child_task_id
        ):
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.METADATA_INVALID,
                failure_category="LINEAGE_METADATA",
            )
        try:
            parent = self.get_task(parent_task_id)
        except KeyError:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.PARENT_TASK_NOT_FOUND,
                failure_category="PARENT_TASK",
            ) from None
        run = self.connection.execute(
            "SELECT task_id FROM runs WHERE run_id=?",
            (parent_run_id,),
        ).fetchone()
        if run is None:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.PARENT_RUN_NOT_FOUND,
                failure_category="PARENT_RUN",
                parent_found=True,
            )
        if run["task_id"] != parent_task_id:
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.PARENT_RUN_TASK_MISMATCH,
                failure_category="PARENT_RUN",
                parent_found=True,
            )
        if (
            child.project_id != parent.project_id
            or child.base_sha != parent.base_sha
            or child.working_subpath != parent.working_subpath
        ):
            raise DelegatedTaskLineageError(
                DelegatedTaskLineageRule.TASK_CONTEXT_MISMATCH,
                failure_category="TASK_CONTEXT",
                parent_found=True,
            )
        return DelegatedTaskLineage(
            child_task_id=child_task_id,
            parent_task_id=parent_task_id,
            parent_run_id=parent_run_id,
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        # ``min_tier`` column was added in M1 WP2; older SQLite files
        # may predate the schema bump. ``dict.get`` lets the loader
        # fall back to "T1" on legacy stores; new stores have the
        # column with the DEFAULT clause.
        min_tier = row["min_tier"] if "min_tier" in row.keys() else "T1"
        # M1 WP5a-1: AUTO_PLANNED / AUTO_GRACE columns are nullable
        # so a pre-WP5a-1 task row reads cleanly. ``row[]`` returns
        # ``None`` for missing keys on SQLite's default cursor (no
        # ``row.keys()`` gate needed once ``_ensure_column`` has run).
        return TaskRecord(
            task_id=row["task_id"],
            request_id=row["request_id"],
            intent=row["intent"],
            project_id=row["project_id"],
            base_sha=row["base_sha"],
            working_subpath=row["working_subpath"],
            state=TaskState(row["state"]),
            state_version=row["state_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            scheduling_policy=row["scheduling_policy"],
            manual_execution_target_id=row["manual_execution_target_id"],
            min_tier=min_tier,
            auto_decision_id=row["auto_decision_id"],
            auto_grace_deadline_at=row["auto_grace_deadline_at"],
            auto_acked_at=row["auto_acked_at"],
            auto_reason=row["auto_reason"],
        )

    def transition_task(
        self,
        task_id: str,
        new_state: TaskState,
        *,
        expected_version: int | None = None,
        reason: str | None = None,
        # M1 WP5a-1: AUTO_PLANNED / AUTO_GRACE bookkeeping fields.
        # All four are optional so the pre-WP5a-1 call sites (which
        # do not pass them) keep working unchanged. The fields are
        # written only when the call site supplies them, so a
        # AUTO_PLANNED transition that does not pass
        # ``auto_decision_id`` still reads ``None`` on the row
        # (the WP5a-1 contract pins that the tick writes a real
        # ``auto_decision_id``; this default-to-None behaviour is
        # the safety net).
        auto_decision_id: str | None = None,
        auto_grace_deadline_at: str | None = None,
        auto_acked_at: str | None = None,
        auto_reason: str | None = None,
    ) -> TaskRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._transition_task_tx(
                task_id,
                new_state,
                expected_version=expected_version,
                reason=reason,
                auto_decision_id=auto_decision_id,
                auto_grace_deadline_at=auto_grace_deadline_at,
                auto_acked_at=auto_acked_at,
                auto_reason=auto_reason,
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def _transition_task_tx(
        self,
        task_id: str,
        new_state: TaskState,
        *,
        expected_version: int | None,
        reason: str | None,
        auto_decision_id: str | None = None,
        auto_grace_deadline_at: str | None = None,
        auto_acked_at: str | None = None,
        auto_reason: str | None = None,
    ) -> None:
        """Transition internals for an ALREADY-OPEN transaction."""

        current = self.get_task(task_id)
        if expected_version is not None and current.state_version != expected_version:
            raise RuntimeError("stale task state_version")
        if new_state not in _ALLOWED_TRANSITIONS[current.state]:
            raise ValueError(f"invalid task transition {current.state} -> {new_state}")
        if new_state in {
            TaskState.RUNNING,
            TaskState.WORKER_FINISHED,
            TaskState.VERIFYING,
            TaskState.VERIFIED,
            TaskState.COMPLETED,
        }:
            self.assert_cleanup_clear(task_id)
        stamp = _now()
        next_version = current.state_version + 1
        # Build the UPDATE column list dynamically so a pre-WP5a-1
        # call that does not pass the new fields stays byte-equivalent
        # (the SQL only touches the columns the call site touched).
        update_columns = "state=?, state_version=?, updated_at=?"
        update_values: list[object] = [
            new_state.value,
            next_version,
            stamp,
        ]
        if auto_decision_id is not None:
            update_columns += ", auto_decision_id=?"
            update_values.append(auto_decision_id)
        if auto_grace_deadline_at is not None:
            update_columns += ", auto_grace_deadline_at=?"
            update_values.append(auto_grace_deadline_at)
        if auto_acked_at is not None:
            update_columns += ", auto_acked_at=?"
            update_values.append(auto_acked_at)
        if auto_reason is not None:
            update_columns += ", auto_reason=?"
            update_values.append(auto_reason)
        update_values.extend([task_id, current.state_version])
        updated = self.connection.execute(
            f"""
            UPDATE tasks SET {update_columns}
            WHERE task_id=? AND state_version=?
            """,
            update_values,
        )
        if updated.rowcount != 1:
            raise RuntimeError("task transition lost optimistic concurrency race")
        audit_payload: dict[str, object] = {
            "from": current.state.value,
            "to": new_state.value,
            "state_version": next_version,
            "reason": reason,
        }
        if auto_decision_id is not None:
            audit_payload["auto_decision_id"] = auto_decision_id
        if auto_grace_deadline_at is not None:
            audit_payload["auto_grace_deadline_at"] = auto_grace_deadline_at
        if auto_acked_at is not None:
            audit_payload["auto_acked_at"] = auto_acked_at
        if auto_reason is not None:
            audit_payload["auto_reason"] = auto_reason
        self._audit(
            task_id,
            "TASK_STATE_CHANGED",
            audit_payload,
        )

    def apply_verification_outcome(
        self,
        task_id: str,
        *,
        expected_version: int,
        target: TaskState,
        reason: str,
        finalize: ShadowFinalizationIntent | None = None,
    ) -> TaskState:
        """OPTION A (round 3): terminal truth + shadow-intent in ONE tx.

        The authoritative VERIFYING → VERIFIED/BLOCKED transition and the
        durable shadow-finalization intent commit atomically: a crash can
        never leave terminal SQLite truth whose pending observation is
        only reachable through a lost filesystem side effect. The
        filesystem finalize itself happens post-COMMIT via the idempotent
        finalize-outbox drain.
        """

        if target not in (TaskState.VERIFIED, TaskState.BLOCKED):
            raise ValueError("verification outcome target must be VERIFIED or BLOCKED")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.state is not TaskState.VERIFYING:
                raise ValueError("verification result requires VERIFYING state")
            self._transition_task_tx(
                task_id,
                target,
                expected_version=expected_version,
                reason=reason,
            )
            if finalize is not None:
                self._insert_shadow_finalize_locked(finalize)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id).state

    # ------------------------------------------------------------------
    # M1 WP5a-2: AUTO lifecycle metadata helpers.
    #
    # ``transition_task``'s ``auto_*`` parameters treat ``None`` as
    # "do not modify the column", which made it impossible for an
    # ``AUTO_* → READY`` transition to clear stale metadata (the
    # WP5a-1 debt). The helpers below own explicit clear / ack /
    # reason semantics with optimistic concurrency so the active task
    # row never carries misleading active-auto control state after a
    # lifecycle terminal exit (§32).
    # ------------------------------------------------------------------

    def clear_auto_state_metadata(
        self,
        task_id: str,
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> TaskRecord:
        """Atomically NULL all four ``auto_*`` columns with a version bump.

        Idempotent: when the columns are already NULL the row is returned
        unchanged (no version bump, no audit) so crash-recovery replays and
        duplicate veto/abort calls stay cheap. Raises ``RuntimeError`` when
        ``expected_version`` does not match the authoritative row — the
        caller must re-read and re-decide, never clobber.
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.state_version != expected_version:
                raise RuntimeError("stale task state_version")
            if (
                current.auto_decision_id is None
                and current.auto_grace_deadline_at is None
                and current.auto_acked_at is None
                and current.auto_reason is None
            ):
                self.connection.execute("COMMIT")
                return current
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks
                SET auto_decision_id=NULL, auto_grace_deadline_at=NULL,
                    auto_acked_at=NULL, auto_reason=NULL,
                    state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (next_version, stamp, task_id, current.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("auto metadata clear lost optimistic concurrency race")
            if current.auto_decision_id is not None:
                # Durable cleanup intent: the pending shadow whose id equals
                # the cleared decision id (裁决 15) must eventually be
                # discarded even if this process dies right after COMMIT.
                self._enqueue_cleanup_intent_locked(
                    current.auto_decision_id, task_id, reason or "metadata_cleared", stamp
                )
            self._audit(
                task_id,
                "AUTO_METADATA_CLEARED",
                {
                    "state_version": next_version,
                    "cleared_decision_id": current.auto_decision_id,
                    "reason": reason,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def ack_auto_grace(
        self,
        task_id: str,
        *,
        acked_at: str,
        grace_deadline: str,
        expected_version: int,
    ) -> TaskRecord:
        """Record the owner's one-time acknowledgement of an ``AUTO_GRACE`` task.

        Fail-closed contract (§22):

        - the task must currently be ``AUTO_GRACE``;
        - ``expected_version`` must match exactly (stale clients get
          ``RuntimeError`` and must re-read);
        - a task that is already acked is returned **unchanged** — an ACK
          retry never rewrites ``auto_acked_at`` and never extends the
          deadline (``auto_grace_deadline_at`` keeps its original value).
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.state is not TaskState.AUTO_GRACE:
                raise ValueError(f"auto ack requires AUTO_GRACE, task is {current.state.value}")
            if current.auto_acked_at is not None:
                self.connection.execute("COMMIT")
                return current
            if current.state_version != expected_version:
                raise RuntimeError("stale task state_version")
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks
                SET auto_acked_at=?, auto_grace_deadline_at=?,
                    state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (acked_at, grace_deadline, next_version, stamp, task_id, expected_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("auto ack lost optimistic concurrency race")
            self._audit(
                task_id,
                "AUTO_ACKED",
                {
                    "auto_decision_id": current.auto_decision_id,
                    "acked_at": acked_at,
                    "auto_grace_deadline_at": grace_deadline,
                    "state_version": next_version,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def set_auto_reason(self, task_id: str, *, reason: str) -> None:
        """Write the ``AUTO_SKIPPED`` reason hint with per-(task, reason) dedup.

        ``auto_reason`` is a display hint, never authority. The write (and the
        matching ``AUTO_SKIPPED`` audit event) fires only when the reason
        actually changed, so a failing gate evaluated every tick does not
        produce one audit row per tick (§3.4 step 1 dedup). Bumps
        ``state_version`` so clients holding a stale version re-read.
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.auto_reason == reason:
                self.connection.execute("COMMIT")
                return
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks SET auto_reason=?, state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (reason, next_version, stamp, task_id, current.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("auto reason write lost optimistic concurrency race")
            self._audit(
                task_id,
                "AUTO_SKIPPED",
                {"reason": reason, "state_version": next_version},
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def force_task_scheduling_policy(self, task_id: str, *, scheduling_policy: str) -> None:
        """Force a task's scheduling policy (veto locks the task to MANUAL)."""

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.scheduling_policy == scheduling_policy:
                self.connection.execute("COMMIT")
                return
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks SET scheduling_policy=?, state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (scheduling_policy, next_version, stamp, task_id, current.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("task policy force lost optimistic concurrency race")
            self._audit(
                task_id,
                "TASK_SCHEDULING_POLICY_FORCED",
                {
                    "scheduling_policy": scheduling_policy,
                    "state_version": next_version,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def abort_auto_lifecycle(
        self,
        task_id: str,
        *,
        expected_version: int | None = None,
        reason: str,
        event_type: str = "AUTO_ABORTED",
        request_id: str | None = None,
        request_id_explicit: bool | None = None,
        target: str | None = None,
        force_manual: bool = False,
        allow_blocked: bool = False,
    ) -> TaskRecord:
        """Crash-atomic fail-closed close of one AUTO lifecycle.

        ONE ``BEGIN IMMEDIATE`` transaction performs every authoritative
        SQLite mutation of the close (§ crash-consistency closeout):

        - re-read + exact ``expected_version`` check;
        - source-state gate: ``AUTO_PLANNED`` / ``AUTO_GRACE`` always;
          ``BLOCKED`` only with ``allow_blocked`` **and** a non-NULL
          ``auto_decision_id`` (pre-worker supervised-auto recovery — a
          plain owner BLOCKED task can never take this path);
        - state → ``READY`` + all four ``auto_*`` columns → NULL +
          optional ``scheduling_policy = MANUAL`` (veto lock);
        - exactly ONE ``state_version`` increment for the whole logical
          close (no transition bump + policy bump + clear bump);
        - one durable audit event (``AUTO_ABORTED`` / ``AUTO_VETOED``)
          in the same transaction;
        - one durable shadow-cleanup intent row in the same transaction.

        The pending-shadow journal is a separate filesystem store and can
        never share this SQLite transaction — the outbox row is the
        durable promise that the discard will eventually happen; the
        supervised-auto tick drains it idempotently on every sweep.
        """

        if event_type not in {"AUTO_ABORTED", "AUTO_VETOED"}:
            raise ValueError(f"unsupported auto lifecycle event {event_type}")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if expected_version is not None and current.state_version != expected_version:
                raise RuntimeError("stale task state_version")
            allowed_source = current.state in {
                TaskState.AUTO_PLANNED,
                TaskState.AUTO_GRACE,
            }
            if not allowed_source and allow_blocked:
                allowed_source = (
                    current.state is TaskState.BLOCKED and current.auto_decision_id is not None
                )
            if not allowed_source:
                raise ValueError(
                    "auto lifecycle abort requires AUTO_PLANNED/AUTO_GRACE"
                    f" (or supervised-auto BLOCKED), task is {current.state.value}"
                )
            previous_decision_id = current.auto_decision_id
            stamp = _now()
            next_version = current.state_version + 1
            update_columns = (
                "state=?, auto_decision_id=NULL, auto_grace_deadline_at=NULL,"
                " auto_acked_at=NULL, auto_reason=NULL, state_version=?,"
                " updated_at=?"
            )
            update_values: list[object] = [TaskState.READY.value, next_version, stamp]
            if force_manual:
                update_columns += ", scheduling_policy='MANUAL'"
            update_values.extend([task_id, current.state_version])
            updated = self.connection.execute(
                f"UPDATE tasks SET {update_columns} WHERE task_id=? AND state_version=?",
                update_values,
            )
            if updated.rowcount != 1:
                raise RuntimeError("auto lifecycle abort lost optimistic concurrency race")
            payload: dict[str, object] = {
                "from": current.state.value,
                "to": TaskState.READY.value,
                "state_version": next_version,
                "reason": reason,
                "auto_decision_id": previous_decision_id,
                "target": target,
                "request_id": request_id,
                "force_manual": force_manual,
            }
            if request_id_explicit is not None:
                # Issue #72: distinguish a stable caller key from older
                # generated cancel IDs in the same atomic veto evidence.
                payload["request_id_explicit"] = request_id_explicit
            self._audit(task_id, event_type, payload)
            if previous_decision_id is not None:
                self._enqueue_cleanup_intent_locked(previous_decision_id, task_id, reason, stamp)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def recover_ready_auto_metadata(self, task_id: str) -> TaskRecord | None:
        """Atomic recovery of a ``READY`` row that still carries auto metadata.

        An old build's multi-stage abort (or a torn downgrade) can leave
        ``state == READY`` with ``auto_*`` columns set — that is stale,
        active-looking control state no runtime path may interpret as a
        live lifecycle. This closeout detects the inconsistency and, in
        ONE transaction: captures the stale decision id, NULLs all four
        columns, bumps the version exactly once, audits
        ``AUTO_METADATA_RECOVERED`` and enqueues the stale pending
        shadow for durable cleanup. The state stays ``READY``; no worker
        side effect ever runs. Returns ``None`` when the row raced out
        of READY (a concurrent writer owns it) — never clobbers.
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current.state is not TaskState.READY:
                self.connection.execute("COMMIT")
                return None
            if (
                current.auto_decision_id is None
                and current.auto_grace_deadline_at is None
                and current.auto_acked_at is None
            ):
                # ``auto_reason`` alone is the tick's legal skip hint on a
                # READY row — not lifecycle metadata, never recovered.
                self.connection.execute("COMMIT")
                return current
            stale_decision_id = current.auto_decision_id
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks
                SET auto_decision_id=NULL, auto_grace_deadline_at=NULL,
                    auto_acked_at=NULL, auto_reason=NULL,
                    state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (next_version, stamp, task_id, current.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("auto metadata recovery lost optimistic concurrency race")
            self._audit(
                task_id,
                "AUTO_METADATA_RECOVERED",
                {
                    "reason": "ready_state_stale_auto_metadata",
                    "cleared_decision_id": stale_decision_id,
                    "state_version": next_version,
                },
            )
            if stale_decision_id is not None:
                self._enqueue_cleanup_intent_locked(
                    stale_decision_id,
                    task_id,
                    "ready_state_stale_auto_metadata",
                    stamp,
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def pending_shadow_cleanups(self, *, limit: int = 64) -> tuple[sqlite3.Row, ...]:
        """Open (not yet completed) shadow-cleanup intents, oldest first."""

        return tuple(
            self.connection.execute(
                """
                SELECT pending_id, task_id, reason, created_at
                FROM auto_shadow_cleanup_outbox
                WHERE completed_at IS NULL
                ORDER BY created_at, pending_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def complete_shadow_cleanup(self, pending_id: str) -> None:
        """Mark one shadow-cleanup intent completed (idempotent)."""

        self.connection.execute(
            "UPDATE auto_shadow_cleanup_outbox SET completed_at=? "
            "WHERE pending_id=? AND completed_at IS NULL",
            (_now(), pending_id),
        )

    def _enqueue_cleanup_intent_locked(
        self, pending_id: str, task_id: str, reason: str, stamp: str
    ) -> None:
        """Insert a cleanup intent INSIDE an open transaction (§16 guard).

        Round-3 conflict rule: a pending with a real-execution
        finalization intent (open OR completed — a completed intent
        already discarded the pending) is never promised to the discard
        path — finalize outranks discard, so the insert is suppressed
        (no-op) whenever a finalize intent exists for the same pending
        id.
        """

        self.connection.execute(
            """
            INSERT OR IGNORE INTO auto_shadow_cleanup_outbox
                (pending_id, task_id, reason, created_at)
            SELECT ?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1 FROM auto_shadow_finalize_outbox
                WHERE pending_id=?
            )
            """,
            (pending_id, task_id, reason, stamp, pending_id),
        )

    def _insert_shadow_finalize_locked(self, intent: ShadowFinalizationIntent) -> None:
        """Insert a finalize intent INSIDE an open transaction.

        Collision semantics (§9): a second enqueue with an IDENTICAL
        payload is idempotent; a different payload for the same
        finalization id fails closed — the first durable intent owns the
        truth. A still-open cleanup intent for the same pending is
        superseded (completed) in the same transaction: real execution
        evidence outranks a discard promise.
        """

        stamp = _now()
        existing = self.connection.execute(
            "SELECT payload_json FROM auto_shadow_finalize_outbox WHERE finalization_id=?",
            (intent.finalization_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != intent.payload_json():
                raise RuntimeError(
                    "shadow finalization id reused with different payload: "
                    f"{intent.finalization_id}"
                )
        else:
            self.connection.execute(
                """
                INSERT INTO auto_shadow_finalize_outbox (
                    finalization_id, pending_id, task_id, dispatch_id,
                    request_id, decision_id, verified, execution_success,
                    verification_success, quality_outcome, failure_class,
                    failure_stage, regression_detected, attempts_to_green,
                    time_to_green_seconds, handoff_count, reset_cycle_ids,
                    quota_after_snapshot_ids, observed_burn_fraction,
                    observed_at, identity_json, payload_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    intent.finalization_id,
                    intent.pending_id,
                    intent.task_id,
                    intent.dispatch_id,
                    intent.request_id,
                    intent.decision_id,
                    int(intent.verified),
                    int(intent.execution_success),
                    None
                    if intent.verification_success is None
                    else int(intent.verification_success),
                    intent.quality_outcome,
                    intent.failure_class,
                    intent.failure_stage,
                    int(intent.regression_detected),
                    intent.attempts_to_green,
                    intent.time_to_green_seconds,
                    intent.handoff_count,
                    _json(list(intent.reset_cycle_ids)),
                    _json(list(intent.quota_after_snapshot_ids)),
                    intent.observed_burn_fraction,
                    intent.observed_at,
                    intent.identity_json,
                    intent.payload_json(),
                    stamp,
                ),
            )
        # Finalize outranks discard: close any open cleanup intent for
        # the same pending in this same transaction.
        self.connection.execute(
            "UPDATE auto_shadow_cleanup_outbox SET completed_at=? "
            "WHERE pending_id=? AND completed_at IS NULL",
            (stamp, intent.pending_id),
        )

    def enqueue_shadow_finalization(self, intent: ShadowFinalizationIntent) -> None:
        """Durably record a post-worker shadow finalization intent."""

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._insert_shadow_finalize_locked(intent)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def pending_shadow_finalizations(self, *, limit: int = 64) -> tuple[sqlite3.Row, ...]:
        """Open (not yet completed) shadow-finalize intents, oldest first."""

        return tuple(
            self.connection.execute(
                """
                SELECT finalization_id, pending_id, task_id, dispatch_id,
                       request_id, decision_id, verified, execution_success,
                       verification_success, quality_outcome, failure_class,
                       failure_stage, regression_detected, attempts_to_green,
                       time_to_green_seconds, handoff_count, reset_cycle_ids,
                       quota_after_snapshot_ids, observed_burn_fraction,
                       observed_at, identity_json, created_at
                FROM auto_shadow_finalize_outbox
                WHERE completed_at IS NULL
                ORDER BY created_at, finalization_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def complete_shadow_finalization(self, finalization_id: str) -> None:
        """Mark one shadow-finalize intent completed (idempotent)."""

        self.connection.execute(
            "UPDATE auto_shadow_finalize_outbox SET completed_at=? "
            "WHERE finalization_id=? AND completed_at IS NULL",
            (_now(), finalization_id),
        )

    def open_shadow_finalize_intent(self, pending_id: str) -> bool:
        """True when an OPEN finalize intent exists for the pending."""

        row = self.connection.execute(
            "SELECT 1 FROM auto_shadow_finalize_outbox "
            "WHERE pending_id=? AND completed_at IS NULL LIMIT 1",
            (pending_id,),
        ).fetchone()
        return row is not None

    def shadow_finalize_intent_exists(self, pending_id: str) -> bool:
        """True when ANY finalize intent (open or completed) exists."""

        row = self.connection.execute(
            "SELECT 1 FROM auto_shadow_finalize_outbox WHERE pending_id=? LIMIT 1",
            (pending_id,),
        ).fetchone()
        return row is not None

    def routing_decision_by_request_id(self, request_id: str) -> Any | None:
        """Return the raw durable routing-decision row for a request id.

        WP5a-2's tick uses this to prove a frozen decision already exists
        before re-planning (crash recovery: decision persisted, task not yet
        transitioned). Returns ``None`` when no decision exists.
        """

        row = self.connection.execute(
            "SELECT decision_id,request_id,task_id,payload_json FROM routing_decisions "
            "WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return row

    def register_workspace(
        self,
        *,
        task_id: str,
        repo_path: str,
        worktree_path: str,
        branch: str,
        base_sha: str,
        project_id: str | None = None,
        working_subpath: str | None = None,
    ) -> WorkspaceRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_task(task_id)
            existing = self.connection.execute(
                "SELECT * FROM workspaces WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing is not None:
                record = self._workspace_from_row(existing)
                expected = (repo_path, worktree_path, branch, base_sha, project_id, working_subpath)
                actual = (
                    record.repo_path,
                    record.worktree_path,
                    record.branch,
                    record.base_sha,
                    record.project_id,
                    record.working_subpath,
                )
                if actual != expected:
                    raise ValueError("task already has a different workspace")
                self.connection.execute("COMMIT")
                return record
            quarantined = self.connection.execute(
                """SELECT 1 FROM worker_attempts WHERE cleanup_state!='CONFIRMED'
                AND (task_id=? OR worktree_path=?) LIMIT 1""",
                (task_id, worktree_path),
            ).fetchone()
            if quarantined is not None:
                raise RuntimeError("worktree has unresolved worker cleanup quarantine")
            self.connection.execute(
                """
                INSERT INTO workspaces(
                    task_id,project_id,repo_path,worktree_path,branch,base_sha,working_subpath
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    project_id,
                    repo_path,
                    worktree_path,
                    branch,
                    base_sha,
                    working_subpath,
                ),
            )
            self._audit(
                task_id,
                "WORKSPACE_REGISTERED",
                {
                    "project_id": project_id,
                    "worktree_path": worktree_path,
                    "base_sha": base_sha,
                    "working_subpath": working_subpath,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_workspace(task_id)

    def get_workspace(self, task_id: str) -> WorkspaceRecord:
        row = self.connection.execute(
            "SELECT * FROM workspaces WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._workspace_from_row(row)

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            task_id=row["task_id"],
            repo_path=row["repo_path"],
            worktree_path=row["worktree_path"],
            branch=row["branch"],
            base_sha=row["base_sha"],
            project_id=row["project_id"],
            working_subpath=row["working_subpath"],
            writer_token=row["writer_token"],
        )

    def acquire_writer(self, task_id: str, writer_token: str) -> WorkspaceRecord:
        if not writer_token:
            raise ValueError("writer_token is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_cleanup_clear(task_id)
            workspace = self.get_workspace(task_id)
            if workspace.writer_token not in (None, writer_token):
                raise RuntimeError("task worktree already has an active writer")
            if workspace.writer_token is None:
                self.connection.execute(
                    "UPDATE workspaces SET writer_token=?, writer_acquired_at=? WHERE task_id=?",
                    (writer_token, _now(), task_id),
                )
                self._audit(task_id, "WRITER_ACQUIRED", {"writer_token": writer_token})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_workspace(task_id)

    def release_writer(self, task_id: str, writer_token: str) -> WorkspaceRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_cleanup_clear(task_id)
            workspace = self.get_workspace(task_id)
            if workspace.writer_token != writer_token:
                raise RuntimeError("writer token does not own the task worktree")
            self.connection.execute(
                "UPDATE workspaces SET writer_token=NULL, writer_acquired_at=NULL WHERE task_id=?",
                (task_id,),
            )
            self._audit(task_id, "WRITER_RELEASED", {"writer_token": writer_token})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_workspace(task_id)

    def start_run(
        self,
        *,
        run_id: str,
        task_id: str,
        worker_id: str,
        pid: int | None = None,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_task(task_id)
            self.assert_cleanup_clear(task_id)
            active = self.connection.execute(
                "SELECT run_id FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
                (task_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("task already has an active worker run")
            self.connection.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, task_id, worker_id, pid, "RUNNING", _now(), None, None),
            )
            self._audit(
                task_id,
                "RUN_STARTED",
                {"run_id": run_id, "worker_id": worker_id, "pid": pid},
            )
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self.connection.execute("ROLLBACK")
            raise RuntimeError("worker run violates durable run ownership constraints") from error
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reserve_owner_dispatch(
        self,
        *,
        dispatch_id: str,
        request_id: str,
        task_id: str,
        task_state_version: int,
        execution_target_id: str,
        authority: str,
    ) -> tuple[OwnerDispatchRecord, bool]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.get_task(task_id)
            existing = self.connection.execute(
                "SELECT * FROM owner_dispatches WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                record = self._owner_dispatch_from_row(existing)
                same = owner_dispatch_matches_expected(
                    record,
                    dispatch_id=dispatch_id,
                    request_id=request_id,
                    task_id=task_id,
                    task_state_version=task_state_version,
                    execution_target_id=execution_target_id,
                    authority=authority,
                )
                if not same:
                    raise ValueError("request_id already has a conflicting owner dispatch")
                self.connection.execute("COMMIT")
                return record, False

            stamp = _now()
            self.connection.execute(
                """
                INSERT INTO owner_dispatches(
                    dispatch_id,request_id,task_id,task_state_version,execution_target_id,
                    authority,status,created_at,started_at,finished_at,failure_code,failure_reason
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dispatch_id,
                    request_id,
                    task_id,
                    task_state_version,
                    execution_target_id,
                    authority,
                    OwnerDispatchStatus.RESERVED.value,
                    stamp,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            self._audit(
                task_id,
                "OWNER_DISPATCH_RESERVED",
                {
                    "dispatch_id": dispatch_id,
                    "request_id": request_id,
                    "execution_target_id": execution_target_id,
                    "authority": authority,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_owner_dispatch_by_request_id(request_id), True

    def get_owner_dispatch_by_request_id(self, request_id: str) -> OwnerDispatchRecord:
        row = self.connection.execute(
            "SELECT * FROM owner_dispatches WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._owner_dispatch_from_row(row)

    def mark_owner_dispatch_blocked(
        self,
        request_id: str,
        *,
        failure_code: str,
        failure_reason: str,
    ) -> OwnerDispatchRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_owner_dispatch_by_request_id(request_id)
            if current.status in {
                OwnerDispatchStatus.FINISHED,
                OwnerDispatchStatus.CANCELLED,
                OwnerDispatchStatus.BLOCKED,
            }:
                self.connection.execute("COMMIT")
                return current
            stamp = _now()
            updated = self.connection.execute(
                """
                UPDATE owner_dispatches
                SET status=?, finished_at=?, failure_code=?, failure_reason=?
                WHERE request_id=? AND status IN (?,?)
                """,
                (
                    OwnerDispatchStatus.BLOCKED.value,
                    stamp,
                    failure_code,
                    failure_reason,
                    request_id,
                    OwnerDispatchStatus.RESERVED.value,
                    OwnerDispatchStatus.STARTED.value,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("owner dispatch block lost concurrency race")
            self._audit(
                current.task_id,
                "OWNER_DISPATCH_BLOCKED",
                {
                    "dispatch_id": current.dispatch_id,
                    "request_id": request_id,
                    "execution_target_id": current.execution_target_id,
                    "failure_code": failure_code,
                    "failure_reason": failure_reason,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_owner_dispatch_by_request_id(request_id)

    def finish_owner_dispatch(
        self,
        request_id: str,
        *,
        failure_code: str | None = None,
        failure_reason: str | None = None,
    ) -> OwnerDispatchRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_owner_dispatch_by_request_id(request_id)
            if current.status in {
                OwnerDispatchStatus.FINISHED,
                OwnerDispatchStatus.CANCELLED,
                OwnerDispatchStatus.BLOCKED,
            }:
                self.connection.execute("COMMIT")
                return current
            stamp = _now()
            updated = self.connection.execute(
                """
                UPDATE owner_dispatches
                SET status=?, finished_at=?, failure_code=?, failure_reason=?
                WHERE request_id=? AND status=?
                """,
                (
                    OwnerDispatchStatus.FINISHED.value,
                    stamp,
                    failure_code,
                    failure_reason,
                    request_id,
                    OwnerDispatchStatus.STARTED.value,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("owner dispatch finish lost concurrency race")
            self._audit(
                current.task_id,
                "OWNER_DISPATCH_FINISHED",
                {
                    "dispatch_id": current.dispatch_id,
                    "request_id": request_id,
                    "execution_target_id": current.execution_target_id,
                    "failure_code": failure_code,
                    "failure_reason": failure_reason,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_owner_dispatch_by_request_id(request_id)

    def mark_owner_dispatch_cancelled(self, request_id: str) -> OwnerDispatchRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_owner_dispatch_by_request_id(request_id)
            if current.status in {
                OwnerDispatchStatus.FINISHED,
                OwnerDispatchStatus.CANCELLED,
                OwnerDispatchStatus.BLOCKED,
            }:
                self.connection.execute("COMMIT")
                return current
            stamp = _now()
            updated = self.connection.execute(
                """
                UPDATE owner_dispatches
                SET status=?, finished_at=?, failure_code=?, failure_reason=?
                WHERE request_id=? AND status IN (?,?)
                """,
                (
                    OwnerDispatchStatus.CANCELLED.value,
                    stamp,
                    "OWNER_CANCELLED",
                    "owner cancelled the dispatched worker execution",
                    request_id,
                    OwnerDispatchStatus.RESERVED.value,
                    OwnerDispatchStatus.STARTED.value,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("owner dispatch cancellation lost concurrency race")
            self._audit(
                current.task_id,
                "OWNER_DISPATCH_CANCELLED",
                {
                    "dispatch_id": current.dispatch_id,
                    "request_id": request_id,
                    "execution_target_id": current.execution_target_id,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_owner_dispatch_by_request_id(request_id)

    def start_dispatched_worker(
        self,
        *,
        dispatch_id: str,
        task_id: str,
        expected_task_version: int,
        run_id: str,
        worker_id: str,
        writer_token: str,
        pid: int | None = None,
        expected_state: TaskState | None = None,
        attempt_id: str | None = None,
        executor_id: str | None = None,
    ) -> TaskRecord:
        """Atomically pair the run row, the state transition and dispatch START.

        Round 6 §8 — the legal source state is a property of the DURABLE
        dispatch row, not of the caller: ``expected_state`` is derived
        inside this transaction from ``dispatch.authority`` via
        :func:`expected_source_state_for_dispatch_authority`
        (OWNER_INITIATED_EXECUTION → READY, SUPERVISED_AUTO →
        AUTO_GRACE). A caller-supplied ``expected_state`` is accepted
        only as a consistency assertion — a value that disagrees with
        the authority-derived state raises before any mutation, so no
        caller can weaken the rule (e.g. pass ``READY`` for a
        SUPERVISED_AUTO reservation). Unknown authority fails closed
        with :class:`ValueError`. The transition is validated against
        the exact expected state both in Python and in the SQL
        ``WHERE state=?`` guard, so a concurrent veto / mode-change
        abort that moved the task out of the expected state fails
        closed instead of racing the worker start.
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            dispatch = self.connection.execute(
                "SELECT * FROM owner_dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if dispatch is None:
                raise KeyError(dispatch_id)
            if dispatch["task_id"] != task_id:
                raise ValueError("dispatch_id does not belong to task_id")
            if dispatch["status"] != OwnerDispatchStatus.RESERVED.value:
                raise RuntimeError("owner dispatch is not reserved")
            # Round 6 §8: the durable authority owns the source state —
            # re-derived here inside BEGIN IMMEDIATE, never trusted
            # from the caller alone.
            authority_state = expected_source_state_for_dispatch_authority(dispatch["authority"])
            if expected_state is not None and expected_state is not authority_state:
                raise ValueError(
                    "expected_state conflicts with the dispatch authority's"
                    f" legal source state {authority_state.value}"
                )
            expected_state = authority_state
            task = self.get_task(task_id)
            self._check_manual_target_binding(task, dispatch["execution_target_id"])
            if task.state is not expected_state:
                raise ValueError(f"dispatched worker can only start from {expected_state.value}")
            if task.state_version != expected_task_version:
                raise RuntimeError("stale task state_version")
            claimed = self.connection.execute(
                "SELECT attempt_id FROM worker_attempts WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if claimed is not None:
                if claimed["attempt_id"] != attempt_id or executor_id is None:
                    raise RuntimeError("dispatched worker requires exact attempt and executor")
                attempt = self._check_attempt_executor(attempt_id, executor_id)
                self._check_attempt_launch_authority(attempt)
                if attempt.spawn_state != "SPAWN_OBSERVED":
                    raise RuntimeError("dispatched worker requires an observed spawn ticket")
                if attempt.pid != pid or attempt.run_id not in (None, run_id):
                    raise RuntimeError("dispatched worker run does not match spawned attempt")
                if attempt.run_id is None:
                    self.connection.execute(
                        "UPDATE worker_attempts SET run_id=?,updated_at=? WHERE attempt_id=?",
                        (run_id, _now(), attempt_id),
                    )
            else:
                if attempt_id is not None or executor_id is not None:
                    raise RuntimeError("worker attempt is not claimed")
                self.assert_cleanup_clear(task_id)
            workspace = self.get_workspace(task_id)
            if workspace.writer_token != writer_token:
                raise RuntimeError("dispatched worker requires the active writer lock")
            active = self.connection.execute(
                "SELECT run_id FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
                (task_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("task already has an active worker run")

            stamp = _now()
            next_version = task.state_version + 1
            self.connection.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, task_id, worker_id, pid, "RUNNING", stamp, None, None),
            )
            updated_task = self.connection.execute(
                """
                UPDATE tasks SET state=?, state_version=?, updated_at=?
                WHERE task_id=? AND state_version=? AND state=?
                """,
                (
                    TaskState.RUNNING.value,
                    next_version,
                    stamp,
                    task_id,
                    task.state_version,
                    expected_state.value,
                ),
            )
            if updated_task.rowcount != 1:
                raise RuntimeError("dispatched worker lost task-state concurrency race")
            updated_dispatch = self.connection.execute(
                """
                UPDATE owner_dispatches SET status=?, started_at=?
                WHERE dispatch_id=? AND status=?
                """,
                (
                    OwnerDispatchStatus.STARTED.value,
                    stamp,
                    dispatch_id,
                    OwnerDispatchStatus.RESERVED.value,
                ),
            )
            if updated_dispatch.rowcount != 1:
                raise RuntimeError("dispatched worker lost dispatch-state concurrency race")
            self._audit(
                task_id,
                "RUN_STARTED",
                {"run_id": run_id, "worker_id": worker_id, "pid": pid},
            )
            self._audit(
                task_id,
                "TASK_STATE_CHANGED",
                {
                    "from": expected_state.value,
                    "to": TaskState.RUNNING.value,
                    "state_version": next_version,
                    "reason": "host-supervised owner dispatch worker started",
                },
            )
            self._audit(
                task_id,
                "OWNER_DISPATCH_STARTED",
                {"dispatch_id": dispatch_id, "request_id": dispatch["request_id"]},
            )
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self.connection.execute("ROLLBACK")
            raise RuntimeError("worker run violates durable run ownership constraints") from error
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def assert_running_invariant(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task.state is not TaskState.RUNNING:
            return
        active_runs = self.connection.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE task_id=? AND status='RUNNING'",
            (task_id,),
        ).fetchone()["n"]
        if active_runs != 1:
            raise RuntimeError("RUNNING task must have exactly one active run")
        workspace = self.get_workspace(task_id)
        if workspace.writer_token is None:
            raise RuntimeError("RUNNING task must retain the writer lock")

    @staticmethod
    def _owner_dispatch_from_row(row: sqlite3.Row) -> OwnerDispatchRecord:
        return OwnerDispatchRecord(
            dispatch_id=row["dispatch_id"],
            request_id=row["request_id"],
            task_id=row["task_id"],
            task_state_version=row["task_state_version"],
            execution_target_id=row["execution_target_id"],
            authority=row["authority"],
            status=OwnerDispatchStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                None if row["started_at"] is None else datetime.fromisoformat(row["started_at"])
            ),
            finished_at=(
                None if row["finished_at"] is None else datetime.fromisoformat(row["finished_at"])
            ),
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
        )

    def finish_run(self, run_id: str, *, status: str, result: Any = None) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT task_id,status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] != "RUNNING":
                raise RuntimeError("run is not active")
            updated = self.connection.execute(
                """
                UPDATE runs SET status=?, finished_at=?, result_json=?
                WHERE run_id=? AND status='RUNNING'
                """,
                (status, _now(), _json(result), run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("run finish lost concurrency race")
            self._audit(row["task_id"], "RUN_FINISHED", {"run_id": run_id, "status": status})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def record_routing_decision(
        self,
        *,
        decision_id: str,
        request_id: str,
        task_id: str | None,
        payload: Any,
    ) -> None:
        rendered = _json(payload)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT * FROM routing_decisions WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                if existing["decision_id"] != decision_id or existing["payload_json"] != rendered:
                    raise ValueError("request_id already has a conflicting routing decision")
                self.connection.execute("COMMIT")
                return
            self.connection.execute(
                "INSERT INTO routing_decisions VALUES(?,?,?,?,?)",
                (decision_id, request_id, task_id, rendered, _now()),
            )
            self._audit(task_id, "ROUTING_DECISION_RECORDED", {"decision_id": decision_id})
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reconcile_startup(self) -> tuple[str, ...]:
        """Atomically block uncertain in-flight task state and interrupt active runs."""

        blocked: list[str] = []
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._fence_previous_worker_attempts_tx()
            rows = self.connection.execute(
                "SELECT task_id,state,state_version FROM tasks WHERE state IN (?,?,?) ORDER BY task_id",
                (
                    TaskState.RUNNING.value,
                    TaskState.WORKER_FINISHED.value,
                    TaskState.VERIFYING.value,
                ),
            ).fetchall()
            for row in rows:
                next_version = row["state_version"] + 1
                updated = self.connection.execute(
                    """
                    UPDATE tasks SET state=?, state_version=?, updated_at=?
                    WHERE task_id=? AND state_version=?
                    """,
                    (
                        TaskState.BLOCKED.value,
                        next_version,
                        _now(),
                        row["task_id"],
                        row["state_version"],
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("startup reconciliation lost task state race")
                self._audit(
                    row["task_id"],
                    "TASK_STATE_CHANGED",
                    {
                        "from": row["state"],
                        "to": TaskState.BLOCKED.value,
                        "state_version": next_version,
                        "reason": "startup reconciliation cannot prove prior execution state",
                    },
                )
                blocked.append(row["task_id"])
            interrupted_at = _now()
            active_runs = self.connection.execute(
                "SELECT run_id,task_id FROM runs WHERE status='RUNNING' ORDER BY run_id"
            ).fetchall()
            for run in active_runs:
                updated = self.connection.execute(
                    """
                    UPDATE runs SET status='INTERRUPTED', finished_at=?
                    WHERE run_id=? AND status='RUNNING'
                    """,
                    (interrupted_at, run["run_id"]),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("startup reconciliation lost run state race")
                self._audit(
                    run["task_id"],
                    "RUN_FINISHED",
                    {"run_id": run["run_id"], "status": "INTERRUPTED"},
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return tuple(blocked)

    def audit_events(self, task_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT event_type,payload_json,created_at FROM audit_events WHERE task_id=? ORDER BY audit_id",
            (task_id,),
        ).fetchall()
        return tuple(
            {
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )


__all__ = [
    "DelegatedTaskLineage",
    "DelegatedTaskLineageError",
    "DelegatedTaskLineageRule",
    "OwnerDispatchRecord",
    "OwnerDispatchStatus",
    "ProjectAvailability",
    "ProjectRecord",
    "SafetyKernelStore",
    "TaskRecord",
    "TaskState",
    "WorkspaceRecord",
    "WorkerAttemptRecord",
]
