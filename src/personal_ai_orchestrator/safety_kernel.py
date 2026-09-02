"""Host-owned durable task state and writer-lock authority.

This module intentionally owns safety state outside coding agents. Worker prose never advances a
Task directly to VERIFIED/COMPLETED; deterministic verification and explicit host transitions do.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

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


_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.SUBMITTED: frozenset({TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.BLOCKED, TaskState.CANCELLED}),
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
}


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


class SafetyKernelStore:
    """SQLite/WAL source of truth for P0 task, run, workspace, audit and routing state."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
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
            """
        )
        self._ensure_column("tasks", "project_id", "TEXT")
        self._ensure_column("tasks", "base_sha", "TEXT")
        self._ensure_column("tasks", "working_subpath", "TEXT")
        self._ensure_column("tasks", "scheduling_policy", "TEXT")
        self._ensure_column("tasks", "manual_execution_target_id", "TEXT")
        self._ensure_column("projects", "scheduling_policy", "TEXT")
        self._ensure_column("projects", "manual_execution_target_id", "TEXT")
        self._ensure_column("workspaces", "project_id", "TEXT")
        self._ensure_column("workspaces", "working_subpath", "TEXT")

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
    ) -> TaskRecord:
        if project_id is not None:
            self.get_project(project_id)
            if base_sha is None:
                raise ValueError("base_sha is required for registered project tasks")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
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
                    scheduling_policy,manual_execution_target_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        row = self.connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_from_row(row)

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
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
        )

    def transition_task(
        self,
        task_id: str,
        new_state: TaskState,
        *,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> TaskRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if expected_version is not None and current.state_version != expected_version:
                raise RuntimeError("stale task state_version")
            if new_state not in _ALLOWED_TRANSITIONS[current.state]:
                raise ValueError(f"invalid task transition {current.state} -> {new_state}")
            stamp = _now()
            next_version = current.state_version + 1
            updated = self.connection.execute(
                """
                UPDATE tasks SET state=?, state_version=?, updated_at=?
                WHERE task_id=? AND state_version=?
                """,
                (new_state.value, next_version, stamp, task_id, current.state_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("task transition lost optimistic concurrency race")
            self._audit(
                task_id,
                "TASK_STATE_CHANGED",
                {
                    "from": current.state.value,
                    "to": new_state.value,
                    "state_version": next_version,
                    "reason": reason,
                },
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return self.get_task(task_id)

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
                same = (
                    record.dispatch_id == dispatch_id
                    and record.task_id == task_id
                    and record.task_state_version == task_state_version
                    and record.execution_target_id == execution_target_id
                    and record.authority == authority
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
    ) -> TaskRecord:
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
            task = self.get_task(task_id)
            if task.state is not TaskState.READY:
                raise ValueError("dispatched worker can only start from READY")
            if task.state_version != expected_task_version:
                raise RuntimeError("stale task state_version")
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
                    TaskState.READY.value,
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
                    "from": TaskState.READY.value,
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
    "OwnerDispatchRecord",
    "OwnerDispatchStatus",
    "ProjectAvailability",
    "ProjectRecord",
    "SafetyKernelStore",
    "TaskRecord",
    "TaskState",
    "WorkspaceRecord",
]
