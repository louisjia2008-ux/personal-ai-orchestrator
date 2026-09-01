"""P4.0 typed local control-plane API over a Unix Domain Socket.

The control plane is a read-mostly, host-owned surface. Clients may submit tasks, inspect
task/run/verification/routing truth, request bounded cancellation of non-running tasks, and read
sanitized provider/quota health plus Production ACTIVE status. Clients can never mark results
VERIFIED, forge evidence, mutate Safety Kernel state directly, resolve approvals, or enable
Production ACTIVE.

The daemon remains authoritative: every write goes through the same Safety Kernel transactions
the execution path uses, and every response is rendered from explicitly whitelisted models so
credential material (for example account ``credential_ref`` values) can never transit this API.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.approval import ApprovalAuthority
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.provider_registry_manager import (
    ProviderRegistryManager,
)
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult

CONTROL_API_VERSION = "v1"
MAX_REQUEST_BYTES = 64 * 1024
MAX_DRAIN_BYTES = 1024 * 1024
MAX_LIST_LIMIT = 200
DEFAULT_LIST_LIMIT = 50
MAX_INTENT_LENGTH = 8192
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ControlPlaneError(Exception):
    """Sanitized control-plane failure mapped to an HTTP status and error code."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskSubmitRequest(_ViewModel):
    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    intent: str = Field(min_length=1, max_length=MAX_INTENT_LENGTH)


class CancelRequest(_ViewModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class TaskView(_ViewModel):
    task_id: str
    request_id: str
    intent: str
    state: str
    state_version: int
    created_at: str
    updated_at: str


class CancelView(_ViewModel):
    task: TaskView
    cancelled_now: bool


class RunView(_ViewModel):
    run_id: str
    task_id: str
    worker_id: str
    pid: int | None
    status: str
    started_at: str
    finished_at: str | None
    result: Any = None


class TaskListView(_ViewModel):
    tasks: tuple[TaskView, ...]
    total: int


class RunListView(_ViewModel):
    runs: tuple[RunView, ...]


class VerificationReportView(_ViewModel):
    task_id: str
    task_state: str
    status: str
    evidence_id: str | None = None
    failure_reason: str | None = None
    result: VerificationResult | None = None


class RoutingDecisionView(_ViewModel):
    task_id: str | None
    decision_id: str
    request_id: str
    created_at: str
    decision: dict[str, Any]


class DashboardCountsView(_ViewModel):
    running: int
    ready: int
    blocked: int
    verified: int
    completed: int
    total: int


class ActivityEventView(_ViewModel):
    event_type: str
    task_id: str | None
    created_at: str
    summary: str


class DashboardSummaryView(_ViewModel):
    connection: HealthView
    counts: DashboardCountsView
    recent_tasks: tuple[TaskView, ...]
    providers: ProviderHealthListView
    active_status: ActiveStatusView
    important_blockers: tuple[str, ...]
    recent_events: tuple[ActivityEventView, ...]


class WorkspaceView(_ViewModel):
    task_id: str
    repo_path: str
    worktree_path: str
    branch: str
    base_sha: str
    writer_locked: bool


class TaskDetailView(_ViewModel):
    task: TaskView
    runs: tuple[RunView, ...]
    routing: RoutingDecisionView | None = None
    verification: VerificationReportView
    approvals: ApprovalListView
    workspace: WorkspaceView | None = None
    events: tuple[ActivityEventView, ...]


class SanitizedEvidenceSourceView(_ViewModel):
    source_type: str
    observed_at: str | None = None


class QuotaWindowHealthView(_ViewModel):
    window_id: str
    window_kind: str
    state: str
    confidence: str
    remaining_fraction: float | None = None
    reset_at: str | None = None


class QuotaPoolHealthView(_ViewModel):
    quota_pool_id: str
    name: str
    plan_id: str
    state: str
    confidence: str
    measurement_source_type: str
    observed_at: str | None = None
    windows: tuple[QuotaWindowHealthView, ...] = ()


class ObservedAvailabilityView(_ViewModel):
    state: str
    observed_at: str
    measurement_source: str
    confidence: str
    sanitized_reason_code: str | None = None


class ExecutionTargetHealthView(_ViewModel):
    execution_target_id: str
    model_sku_id: str
    runtime_id: str
    enabled: bool
    runtime_available: bool | None = None
    observed_availability: ObservedAvailabilityView | None = None


class ProviderHealthView(_ViewModel):
    provider_id: str
    display_name: str
    account_count: int
    quota_pools: tuple[QuotaPoolHealthView, ...] = ()
    execution_targets: tuple[ExecutionTargetHealthView, ...] = ()
    evidence_source: str | None = None
    auth_status: str | None = None
    execution_status: str | None = None
    last_checked: str | None = None


class ProviderHealthListView(_ViewModel):
    providers: tuple[ProviderHealthView, ...]


class ProviderDiscoveryStatusView(_ViewModel):
    discovery_state: str
    last_discovered_at: str | None = None
    provider_count: int = 0
    execution_target_count: int = 0
    last_error_code: str | None = None
    catalog_snapshot_id: str | None = None
    source_method: str | None = None


class ActiveStatusView(_ViewModel):
    production_active: str
    authorized: bool
    blocking_reasons: tuple[str, ...]
    gate: dict[str, bool]


class ApprovalView(_ViewModel):
    approval_id: str
    task_id: str
    kind: str
    status: str
    created_at: str
    resolved_at: str | None = None


class ApprovalListView(_ViewModel):
    approvals: tuple[ApprovalView, ...]


class HealthView(_ViewModel):
    status: str
    api_version: str


def _task_view(record) -> TaskView:
    return TaskView(
        task_id=record.task_id,
        request_id=record.request_id,
        intent=record.intent,
        state=record.state.value,
        state_version=record.state_version,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


@dataclass
class ControlPlaneService:
    """Host-owned facade rendering sanitized orchestrator truth for local clients."""

    registry: ModelRegistry
    store: SafetyKernelStore
    activation_gate: ActiveRoutingGate = field(default_factory=ActiveRoutingGate)
    runtime_availability: dict[str, bool] = field(default_factory=dict)
    verification_journal: VerificationEvidenceJournal | None = None
    quota_availability_journal: QuotaAvailabilityJournal | None = None
    provider_registry_manager: ProviderRegistryManager | None = None

    def open_request(self) -> ControlPlaneService:
        """Return a request-local service bound to a fresh SQLite connection."""

        if self.store.path == ":memory:":
            raise RuntimeError("threaded control plane requires a file-backed SafetyKernelStore")
        return ControlPlaneService(
            registry=self.registry,
            store=SafetyKernelStore(self.store.path),
            activation_gate=self.activation_gate,
            runtime_availability=self.runtime_availability,
            verification_journal=self.verification_journal,
            quota_availability_journal=self.quota_availability_journal,
            provider_registry_manager=self.provider_registry_manager,
        )

    @staticmethod
    def _validate_identifier(kind: str, value: str) -> None:
        if not _IDENTIFIER.match(value):
            raise ControlPlaneError(400, f"invalid_{kind}")

    def submit_task(self, payload: dict[str, Any]) -> TaskView:
        request = TaskSubmitRequest.model_validate(payload)
        for kind, value in (
            ("task_id", request.task_id),
            ("request_id", request.request_id),
        ):
            self._validate_identifier(kind, value)
        try:
            record = self.store.submit_task(
                task_id=request.task_id,
                request_id=request.request_id,
                intent=request.intent,
            )
        except ValueError:
            raise ControlPlaneError(400, "conflicting_request_id") from None
        return _task_view(record)

    def list_tasks(self, *, limit: int = DEFAULT_LIST_LIMIT) -> TaskListView:
        bounded = max(1, min(limit, MAX_LIST_LIMIT))
        rows = self.store.connection.execute(
            "SELECT task_id FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (bounded,),
        ).fetchall()
        total = self.store.connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        tasks = tuple(_task_view(self.store.get_task(row["task_id"])) for row in rows)
        return TaskListView(tasks=tasks, total=total)

    def get_task(self, task_id: str) -> TaskView:
        self._validate_identifier("task_id", task_id)
        try:
            return _task_view(self.store.get_task(task_id))
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None

    def task_runs(self, task_id: str) -> RunListView:
        self.get_task(task_id)
        rows = self.store.connection.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY rowid", (task_id,)
        ).fetchall()
        return RunListView(runs=tuple(self._run_view(row) for row in rows))

    def get_run(self, run_id: str) -> RunView:
        self._validate_identifier("run_id", run_id)
        row = self.store.connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ControlPlaneError(404, "run_not_found")
        return self._run_view(row)

    @staticmethod
    def _run_view(row) -> RunView:
        result: Any = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError:
                result = None
        rendered = json.dumps(result, default=str) if result is not None else ""
        if len(rendered) > MAX_REQUEST_BYTES:
            result = {"truncated": True}
        return RunView(
            run_id=row["run_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            pid=row["pid"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result=result,
        )

    def cancel_task(self, task_id: str, payload: dict[str, Any] | None) -> CancelView:
        self._validate_identifier("task_id", task_id)
        request = CancelRequest.model_validate(payload or {})
        if request.request_id is not None:
            self._validate_identifier("request_id", request.request_id)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        if task.state is TaskState.CANCELLED:
            return CancelView(task=_task_view(task), cancelled_now=False)
        if task.state is TaskState.RUNNING:
            raise ControlPlaneError(
                409,
                "running_task_cancellation_requires_execution_supervisor",
            )
        if task.state in {TaskState.FAILED, TaskState.COMPLETED}:
            raise ControlPlaneError(409, "task_state_is_terminal")
        try:
            updated = self.store.transition_task(
                task_id,
                TaskState.CANCELLED,
                expected_version=task.state_version,
                reason="control-plane client-requested cancellation",
            )
        except (ValueError, RuntimeError) as error:
            raise ControlPlaneError(409, "task_state_cannot_be_cancelled") from error
        return CancelView(task=_task_view(updated), cancelled_now=True)

    def verification_report(self, task_id: str) -> VerificationReportView:
        try:
            record = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        evidence_id: str | None = None
        failure_reason: str | None = None
        prefix = "deterministic verification passed: "
        for event in self.store.audit_events(task_id):
            if event["event_type"] != "TASK_STATE_CHANGED":
                continue
            payload = event["payload"]
            reason = payload.get("reason")
            if payload.get("to") == TaskState.VERIFIED.value and isinstance(reason, str):
                if reason.startswith(prefix):
                    evidence_id = reason[len(prefix) :]
            if payload.get("from") == TaskState.VERIFYING.value and payload.get(
                "to"
            ) == TaskState.BLOCKED.value:
                failure_reason = reason if isinstance(reason, str) else None
        result: VerificationResult | None = None
        if evidence_id is not None and self.verification_journal is not None:
            result = self.verification_journal.load(evidence_id)
        if record.state is TaskState.VERIFIED or evidence_id is not None:
            if evidence_id is None:
                status = "VERIFIED_EVIDENCE_MISSING"
            elif result is None and self.verification_journal is not None:
                status = "VERIFIED_EVIDENCE_UNAVAILABLE"
            else:
                status = "VERIFIED"
        elif failure_reason is not None:
            status = "FAILED_VERIFICATION"
        elif record.state is TaskState.VERIFYING:
            status = "IN_PROGRESS"
        else:
            status = "NOT_VERIFIED"
        return VerificationReportView(
            task_id=task_id,
            task_state=record.state.value,
            status=status,
            evidence_id=evidence_id,
            failure_reason=failure_reason,
            result=result,
        )

    def routing_decision(self, task_id: str) -> RoutingDecisionView:
        self.get_task(task_id)
        row = self.store.connection.execute(
            "SELECT * FROM routing_decisions WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneError(404, "routing_decision_not_found")
        return RoutingDecisionView(
            task_id=row["task_id"],
            decision_id=row["decision_id"],
            request_id=row["request_id"],
            created_at=row["created_at"],
            decision=json.loads(row["payload_json"]),
        )

    @staticmethod
    def _event_summary(event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "TASK_SUBMITTED":
            return "task submitted"
        if event_type == "TASK_STATE_CHANGED":
            before = payload.get("from", "UNKNOWN")
            after = payload.get("to", "UNKNOWN")
            reason = payload.get("reason")
            suffix = f": {reason}" if isinstance(reason, str) and reason else ""
            return f"{before} -> {after}{suffix}"
        if event_type == "ROUTING_DECISION_RECORDED":
            return f"routing decision {payload.get('decision_id', 'UNKNOWN')} recorded"
        if event_type == "RUN_STARTED":
            return f"worker {payload.get('worker_id', 'UNKNOWN')} started"
        if event_type == "RUN_FINISHED":
            run_id = payload.get("run_id", "UNKNOWN")
            status = payload.get("status", "UNKNOWN")
            return f"run {run_id} finished as {status}"
        if event_type == "WORKSPACE_REGISTERED":
            return "workspace registered"
        if event_type == "WRITER_ACQUIRED":
            return "writer lock acquired"
        if event_type == "WRITER_RELEASED":
            return "writer lock released"
        return event_type.lower().replace("_", " ")

    def _activity_events(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ActivityEventView, ...]:
        bounded = max(1, min(limit, MAX_LIST_LIMIT))
        if task_id is None:
            rows = self.store.connection.execute(
                """
                SELECT task_id,event_type,payload_json,created_at
                FROM audit_events
                ORDER BY audit_id DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        else:
            rows = self.store.connection.execute(
                """
                SELECT task_id,event_type,payload_json,created_at
                FROM audit_events
                WHERE task_id=?
                ORDER BY audit_id
                LIMIT ?
                """,
                (task_id, bounded),
            ).fetchall()
        events: list[ActivityEventView] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            events.append(
                ActivityEventView(
                    event_type=row["event_type"],
                    task_id=row["task_id"],
                    created_at=row["created_at"],
                    summary=self._event_summary(row["event_type"], payload),
                )
            )
        return tuple(events)

    def dashboard_summary(self) -> DashboardSummaryView:
        task_rows = self.store.connection.execute(
            "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"
        ).fetchall()
        by_state = {row["state"]: row["n"] for row in task_rows}
        blockers = list(self.active_status().blocking_reasons)
        blocked_count = int(by_state.get(TaskState.BLOCKED.value, 0))
        if blocked_count:
            blockers.insert(0, f"{blocked_count} task(s) blocked")
        providers = self.providers()
        for provider in providers.providers:
            if not provider.quota_pools and not provider.execution_targets:
                continue
            unknown_pools = [
                pool.name for pool in provider.quota_pools if pool.confidence == "UNKNOWN"
            ]
            if unknown_pools:
                blockers.append(f"{provider.display_name} quota UNKNOWN")
        return DashboardSummaryView(
            connection=self.health(),
            counts=DashboardCountsView(
                running=int(by_state.get(TaskState.RUNNING.value, 0)),
                ready=int(
                    by_state.get(TaskState.READY.value, 0)
                    + by_state.get(TaskState.SUBMITTED.value, 0)
                ),
                blocked=blocked_count,
                verified=int(by_state.get(TaskState.VERIFIED.value, 0)),
                completed=int(by_state.get(TaskState.COMPLETED.value, 0)),
                total=sum(int(value) for value in by_state.values()),
            ),
            recent_tasks=self.list_tasks(limit=10).tasks,
            providers=providers,
            active_status=self.active_status(),
            important_blockers=tuple(blockers),
            recent_events=self._activity_events(limit=20),
        )

    def _workspace_view(self, task_id: str) -> WorkspaceView | None:
        row = self.store.connection.execute(
            "SELECT * FROM workspaces WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkspaceView(
            task_id=row["task_id"],
            repo_path=row["repo_path"],
            worktree_path=row["worktree_path"],
            branch=row["branch"],
            base_sha=row["base_sha"],
            writer_locked=row["writer_token"] is not None,
        )

    def task_detail(self, task_id: str) -> TaskDetailView:
        task = self.get_task(task_id)
        try:
            routing = self.routing_decision(task_id)
        except ControlPlaneError as error:
            if error.status != 404:
                raise
            routing = None
        return TaskDetailView(
            task=task,
            runs=self.task_runs(task_id).runs,
            routing=routing,
            verification=self.verification_report(task_id),
            approvals=self.approvals_for_task(task_id),
            workspace=self._workspace_view(task_id),
            events=self._activity_events(task_id=task_id, limit=100),
        )

    def _quota_pool_view(self, pool) -> QuotaPoolHealthView:
        snapshot = pool.snapshot
        windows = tuple(
            QuotaWindowHealthView(
                window_id=window.window_id,
                window_kind=window.window_kind.value,
                state=window.state.value,
                confidence=window.confidence.value,
                remaining_fraction=window.remaining_fraction,
                reset_at=window.reset_at.isoformat() if window.reset_at else None,
            )
            for window in snapshot.windows
        )
        observed = snapshot.observed_at or snapshot.source.observed_at
        return QuotaPoolHealthView(
            quota_pool_id=pool.id,
            name=pool.name,
            plan_id=pool.plan_id,
            state=snapshot.state.value,
            confidence=snapshot.confidence.value,
            measurement_source_type=snapshot.source.source_type.value,
            observed_at=observed.isoformat() if observed else None,
            windows=windows,
        )

    def _execution_target_view(self, target) -> ExecutionTargetHealthView:
        observed: ObservedAvailabilityView | None = None
        if self.quota_availability_journal is not None:
            evidence = self.quota_availability_journal.load(target.id)
            if evidence is not None:
                observed = ObservedAvailabilityView(
                    state=evidence.state.value,
                    observed_at=evidence.observed_at.isoformat(),
                    measurement_source=evidence.measurement_source.value,
                    confidence=evidence.confidence.value,
                    sanitized_reason_code=evidence.sanitized_reason_code,
                )
        return ExecutionTargetHealthView(
            execution_target_id=target.id,
            model_sku_id=target.model_sku_id,
            runtime_id=target.runtime_id,
            enabled=target.enabled,
            runtime_available=self.runtime_availability.get(target.id),
            observed_availability=observed,
        )

    def providers(self) -> ProviderHealthListView:
        views: list[ProviderHealthView] = []
        plans_by_account: dict[str, list[str]] = {}
        # P4.2.4-A: prefer the dynamic registry owned by the
        # ProviderRegistryManager when one is configured. This is how
        # the bundled product daemon surfaces real GLM / MiniMax CN
        # discovery results even though the static runtime.json still
        # carries the legacy empty-bootstrap snapshot.
        effective_registry = (
            self.provider_registry_manager.registry()
            if self.provider_registry_manager is not None
            else self.registry
        )
        for plan in effective_registry.plans.values():
            plans_by_account.setdefault(plan.account_id, []).append(plan.id)
        evidence_by_provider = self._evidence_by_provider()
        for provider_id, provider in sorted(effective_registry.providers.items()):
            account_ids = [
                account.id
                for account in self.registry.accounts.values()
                if account.provider_id == provider_id
            ]
            plan_ids = {
                plan_id
                for account_id in account_ids
                for plan_id in plans_by_account.get(account_id, [])
            }
            pools = tuple(
                self._quota_pool_view(pool)
                for pool_id, pool in sorted(effective_registry.quota_pools.items())
                if pool.plan_id in plan_ids
            )
            targets = tuple(
                self._execution_target_view(target)
                for target_id, target in sorted(effective_registry.execution_targets.items())
                if effective_registry.models[target.model_sku_id].provider_id == provider_id
            )
            evidence = evidence_by_provider.get(provider_id)
            views.append(
                ProviderHealthView(
                    provider_id=provider_id,
                    display_name=provider.display_name,
                    account_count=len(account_ids),
                    quota_pools=pools,
                    execution_targets=targets,
                    evidence_source=evidence.evidence_source if evidence else None,
                    auth_status=evidence.auth_status if evidence else None,
                    execution_status=evidence.execution_status if evidence else None,
                    last_checked=(
                        evidence.observed_at.isoformat()
                        if evidence is not None and evidence.observed_at is not None
                        else None
                    ),
                )
            )
        return ProviderHealthListView(providers=tuple(views))

    def _evidence_by_provider(self) -> dict[str, Any]:
        """Map ``provider_id`` to the latest sanitized discovery record.

        Returns an empty mapping when no manager is attached. This is
        intentionally permissive: the Control API must continue to
        work even before P4.2.4-A discovery completes for the very
        first time.
        """

        if self.provider_registry_manager is None:
            return {}
        result = self.provider_registry_manager.last_discovery_result()
        if result is None:
            return {}
        return {
            record.provider_id: record
            for record in result.providers
        }

    def provider_discovery_status(self) -> ProviderDiscoveryStatusView:
        if self.provider_registry_manager is None:
            return ProviderDiscoveryStatusView(
                discovery_state="PENDING",
                provider_count=0,
                execution_target_count=0,
            )
        status = self.provider_registry_manager.status()
        return ProviderDiscoveryStatusView(
            discovery_state=status.discovery_state,
            last_discovered_at=status.last_discovered_at,
            provider_count=status.provider_count,
            execution_target_count=status.execution_target_count,
            last_error_code=status.last_error_code,
            catalog_snapshot_id=status.catalog_snapshot_id,
            source_method=status.source_method,
        )

    def refresh_providers(self) -> ProviderDiscoveryStatusView:
        """Run a fresh discovery cycle and return the new status.

        Concurrency: the manager coalesces overlapping calls so the
        OpenCode CLI is invoked at most once per refresh.
        """

        if self.provider_registry_manager is None:
            return ProviderDiscoveryStatusView(
                discovery_state="UNAVAILABLE",
                provider_count=0,
                execution_target_count=0,
                last_error_code="PROVIDER_REGISTRY_MANAGER_NOT_CONFIGURED",
            )
        status = self.provider_registry_manager.refresh()
        if status is None:
            status = self.provider_registry_manager.status()
        return ProviderDiscoveryStatusView(
            discovery_state=status.discovery_state,
            last_discovered_at=status.last_discovered_at,
            provider_count=status.provider_count,
            execution_target_count=status.execution_target_count,
            last_error_code=status.last_error_code,
            catalog_snapshot_id=status.catalog_snapshot_id,
            source_method=status.source_method,
        )

    def quota(self) -> ProviderHealthListView:
        return self.providers()

    def active_status(self) -> ActiveStatusView:
        gate = self.activation_gate
        return ActiveStatusView(
            production_active="ACTIVE" if gate.authorized else "DISABLED_BY_DESIGN",
            authorized=gate.authorized,
            blocking_reasons=gate.blocking_reasons(),
            gate={
                "p0_safety_kernel_authoritative": gate.p0_safety_kernel_authoritative,
                "p1_verifier_authoritative": gate.p1_verifier_authoritative,
                "adapter_fail_closed_validated": gate.adapter_fail_closed_validated,
                "shadow_evidence_accepted": gate.shadow_evidence_accepted,
                "safe_bypass_validated": gate.safe_bypass_validated,
                "owner_approved": gate.owner_approved,
            },
        )

    def approvals_for_task(self, task_id: str) -> ApprovalListView:
        self.get_task(task_id)
        rows = self.store.connection.execute(
            "SELECT approval_id FROM approvals WHERE task_id=? ORDER BY rowid", (task_id,)
        ).fetchall()
        authority = ApprovalAuthority(self.store)
        views = tuple(self._approval_view(authority.get(row["approval_id"])) for row in rows)
        return ApprovalListView(approvals=views)

    def get_approval(self, approval_id: str) -> ApprovalView:
        self._validate_identifier("approval_id", approval_id)
        authority = ApprovalAuthority(self.store)
        try:
            return self._approval_view(authority.get(approval_id))
        except KeyError:
            raise ControlPlaneError(404, "approval_not_found") from None

    @staticmethod
    def _approval_view(record) -> ApprovalView:
        return ApprovalView(
            approval_id=record.approval_id,
            task_id=record.task_id,
            kind=record.kind.value,
            status=record.status.value,
            created_at=record.created_at.isoformat(),
            resolved_at=record.resolved_at.isoformat() if record.resolved_at else None,
        )

    def health(self) -> HealthView:
        return HealthView(status="ok", api_version=CONTROL_API_VERSION)


def _host_allowed(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(f"http://{value}")
    except ValueError:
        return False
    return parsed.hostname in _ALLOWED_HOSTS


def _origin_allowed(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in _ALLOWED_HOSTS


def handler_for_control(service: ControlPlaneService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalAIOrchestratorControl/0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            rendered = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(rendered)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(rendered)

        def _view(self, status: int, view: BaseModel) -> None:
            self._json(status, json.loads(view.model_dump_json()))

        def _segments(self) -> tuple[tuple[str, ...], dict[str, list[str]]]:
            split = urlsplit(self.path)
            segments = tuple(part for part in split.path.split("/") if part)
            return segments, parse_qs(split.query)

        def _guard_headers(self) -> bool:
            if not _host_allowed(self.headers.get("host")):
                self._json(403, {"error": "invalid_host"})
                return False
            if not _origin_allowed(self.headers.get("origin")):
                self._json(403, {"error": "invalid_origin"})
                return False
            return True

        def _drain(self, length: int) -> None:
            """Discard a bounded rejected body so local clients can finish sending."""

            remaining = min(length, MAX_DRAIN_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _read_json(self) -> dict[str, Any] | None:
            content_type = self.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(415, {"error": "json_required"})
                return None
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid_content_length"})
                return None
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._drain(max(length, 0))
                self._json(413, {"error": "request_too_large_or_empty"})
                return None
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_json"})
                return None
            if not isinstance(payload, dict):
                self._json(400, {"error": "invalid_json_object"})
                return None
            return payload

        def _dispatch(self, method: str) -> None:
            if not self._guard_headers():
                return
            segments, query = self._segments()
            request_service = None
            try:
                if not segments or segments[0] != CONTROL_API_VERSION:
                    self._json(404, {"error": "not_found"})
                    return
                rest = segments[1:]
                if method not in ("GET", "POST"):
                    self._json(405, {"error": "method_not_allowed"})
                    return
                request_service = service.open_request()
                self._route(method, rest, query, request_service)
            except ControlPlaneError as error:
                self._json(error.status, {"error": error.code})
            except ValidationError:
                self._json(400, {"error": "invalid_json_schema"})
            except Exception:
                self._json(503, {"error": "control_plane_unavailable"})

        def _route(
            self,
            method: str,
            rest: tuple[str, ...],
            query: dict[str, list[str]],
            request_service: ControlPlaneService,
        ) -> None:
            count = len(rest)

            if rest == ("health",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.health())
                return

            if rest == ("tasks",):
                if method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(201, request_service.submit_task(payload))
                    return
                if method == "GET":
                    limit = DEFAULT_LIST_LIMIT
                    if "limit" in query:
                        try:
                            limit = int(query["limit"][0])
                        except (ValueError, IndexError):
                            self._json(400, {"error": "invalid_limit"})
                            return
                    self._view(200, request_service.list_tasks(limit=limit))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if count >= 2 and rest[0] == "tasks":
                task_id = rest[1]
                sub = rest[2] if count == 3 else None
                if count == 2:
                    if method != "GET":
                        self._json(405, {"error": "method_not_allowed"})
                        return
                    self._view(200, request_service.get_task(task_id))
                    return
                if count == 3 and sub == "runs" and method == "GET":
                    self._view(200, request_service.task_runs(task_id))
                    return
                if count == 3 and sub == "verification" and method == "GET":
                    self._view(200, request_service.verification_report(task_id))
                    return
                if count == 3 and sub == "routing" and method == "GET":
                    self._view(200, request_service.routing_decision(task_id))
                    return
                if count == 3 and sub == "cancel" and method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.cancel_task(task_id, payload))
                    return
                if count == 3 and sub == "approvals" and method == "GET":
                    self._view(200, request_service.approvals_for_task(task_id))
                    return
                if count == 3 and sub == "detail" and method == "GET":
                    self._view(200, request_service.task_detail(task_id))
                    return
                self._json(404, {"error": "not_found"})
                return

            if count == 2 and rest[0] == "runs" and method == "GET":
                self._view(200, request_service.get_run(rest[1]))
                return

            if count == 2 and rest[0] == "approvals" and method == "GET":
                self._view(200, request_service.get_approval(rest[1]))
                return

            if rest == ("providers",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.providers())
                return

            if rest == ("providers", "status"):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.provider_discovery_status())
                return

            if rest == ("providers", "refresh"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                # Refresh is intentionally idempotent: a missing/empty
                # body is treated the same as an explicit empty JSON
                # object, since the operation has no parameters.
                length = int(self.headers.get("content-length", "0") or 0)
                if length > 0:
                    payload = self._read_json()
                    if payload is None:
                        return
                self._view(200, request_service.refresh_providers())
                return

            if rest == ("quota",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.quota())
                return

            if rest == ("active-status",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.active_status())
                return

            if rest == ("dashboard",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.dashboard_summary())
                return

            self._json(404, {"error": "not_found"})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

    return Handler


class UnixThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server bound to a permission-restricted Unix Domain Socket."""

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str | Path,
        handler: type[BaseHTTPRequestHandler],
        *,
        socket_mode: int = 0o600,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._socket_mode = socket_mode
        super().__init__(str(self._socket_path), handler)

    def server_bind(self) -> None:
        path = self._socket_path
        if path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.5)
                probe.connect(str(path))
            except OSError:
                path.unlink(missing_ok=True)
            else:
                raise RuntimeError("control socket is already served by another process")
            finally:
                probe.close()
        super().server_bind()
        os.chmod(path, self._socket_mode)

    def server_close(self) -> None:
        super().server_close()
        try:
            self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass


class ControlPlaneServer:
    """Runnable UDS control-plane server bound to one host-owned service."""

    def __init__(
        self,
        service: ControlPlaneService,
        socket_path: str | Path,
        *,
        socket_mode: int = 0o600,
    ) -> None:
        if service.store.path == ":memory:":
            raise ValueError("threaded control plane requires a durable file-backed state store")
        self.service = service
        self.httpd = UnixThreadingHTTPServer(
            socket_path, handler_for_control(service), socket_mode=socket_mode
        )
        self._thread: threading.Thread | None = None

    @property
    def socket_path(self) -> Path:
        return self.httpd._socket_path

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.httpd.server_close()


__all__ = [
    "CONTROL_API_VERSION",
    "ControlPlaneError",
    "ControlPlaneServer",
    "ControlPlaneService",
    "MAX_REQUEST_BYTES",
    "UnixThreadingHTTPServer",
    "handler_for_control",
]
