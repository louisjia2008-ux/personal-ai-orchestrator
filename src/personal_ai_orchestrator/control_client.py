"""Typed local client for the P4.0 control plane over a Unix Domain Socket.

The client is never authoritative: it only issues structured JSON requests and renders sanitized
responses into typed view models. Connection failures surface as ``ControlPlaneUnavailable`` so
callers (including the CLI) can fail closed with a clean message instead of raw tracebacks.
"""

from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from personal_ai_orchestrator.control_api import (
    ActiveStatusView,
    ApprovalListView,
    ApprovalView,
    CancelView,
    ControlPlaneError,
    DashboardSummaryView,
    DispatchTaskView,
    HealthView,
    ImportConnectionsView,
    OwnerExecutionSettingsView,
    ProjectListView,
    ProjectRemoveView,
    ProjectView,
    ProviderHealthListView,
    QuotaOverviewView,
    QuotaRefreshResultView,
    RoutingDecisionView,
    RunListView,
    RunView,
    SchedulingSettingsView,
    TaskDetailView,
    TaskListView,
    TaskSubmitRequest,
    TaskView,
    VerificationReportView,
)

DEFAULT_SOCKET_MODE = 0o600


class ControlPlaneUnavailable(RuntimeError):
    """The control-plane daemon could not be reached on its socket."""


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str | Path, *, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class ControlPlaneClient:
    """Thin typed client; one request per connection to avoid shared socket state."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 10.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"cache-control": "no-store"}
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            connection = UnixSocketHTTPConnection(self.socket_path, timeout=self.timeout)
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                raw = response.read()
            finally:
                connection.close()
        except OSError as error:
            raise ControlPlaneUnavailable(
                f"control plane unavailable at {self.socket_path}"
            ) from error
        try:
            rendered = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ControlPlaneError(502, "invalid_daemon_response") from None
        if not isinstance(rendered, dict):
            raise ControlPlaneError(502, "invalid_daemon_response")
        if response.status >= 400:
            code = rendered.get("error")
            raise ControlPlaneError(response.status, code if isinstance(code, str) else "error")
        return rendered

    def _get(self, path: str, view: type[BaseModel]) -> BaseModel:
        return view.model_validate(self._request("GET", path))

    def health(self) -> HealthView:
        return self._get("/v1/health", HealthView)  # type: ignore[return-value]

    def resolve_project(self, *, path: str) -> ProjectView:
        rendered = self._request("POST", "/v1/projects/resolve", payload={"path": path})
        return ProjectView.model_validate(rendered)

    def register_project(
        self,
        *,
        path: str,
        display_name: str | None = None,
        security_bookmark_b64: str | None = None,
    ) -> ProjectView:
        payload: dict[str, Any] = {"path": path}
        if display_name is not None:
            payload["display_name"] = display_name
        if security_bookmark_b64 is not None:
            payload["security_bookmark_b64"] = security_bookmark_b64
        rendered = self._request("POST", "/v1/projects", payload=payload)
        return ProjectView.model_validate(rendered)

    def list_projects(self) -> ProjectListView:
        return self._get("/v1/projects", ProjectListView)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> ProjectView:
        return self._get(f"/v1/projects/{project_id}", ProjectView)  # type: ignore[return-value]

    def mark_project_opened(self, project_id: str) -> ProjectView:
        rendered = self._request("POST", f"/v1/projects/{project_id}/opened", payload={})
        return ProjectView.model_validate(rendered)

    def remove_project(self, project_id: str) -> ProjectRemoveView:
        rendered = self._request("POST", f"/v1/projects/{project_id}/remove", payload={})
        return ProjectRemoveView.model_validate(rendered)

    def submit(
        self,
        *,
        task_id: str,
        request_id: str,
        project_id: str,
        intent: str,
        scheduling_policy: str | None = None,
        manual_execution_target_id: str | None = None,
    ) -> TaskView:
        payload = TaskSubmitRequest(
            task_id=task_id,
            request_id=request_id,
            project_id=project_id,
            intent=intent,
            scheduling_policy=scheduling_policy,
            manual_execution_target_id=manual_execution_target_id,
        )
        rendered = self._request(
            "POST",
            "/v1/tasks",
            payload=json.loads(payload.model_dump_json()),
        )
        return TaskView.model_validate(rendered)

    def get_task(self, task_id: str) -> TaskView:
        return self._get(f"/v1/tasks/{task_id}", TaskView)  # type: ignore[return-value]

    def list_tasks(self, *, limit: int | None = None) -> TaskListView:
        path = "/v1/tasks" if limit is None else f"/v1/tasks?limit={int(limit)}"
        return self._get(path, TaskListView)  # type: ignore[return-value]

    def dashboard(self) -> DashboardSummaryView:
        return self._get("/v1/dashboard", DashboardSummaryView)  # type: ignore[return-value]

    def task_detail(self, task_id: str) -> TaskDetailView:
        return self._get(f"/v1/tasks/{task_id}/detail", TaskDetailView)  # type: ignore[return-value]

    def task_runs(self, task_id: str) -> RunListView:
        return self._get(f"/v1/tasks/{task_id}/runs", RunListView)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> RunView:
        return self._get(f"/v1/runs/{run_id}", RunView)  # type: ignore[return-value]

    def cancel(self, task_id: str, *, request_id: str | None = None) -> CancelView:
        payload = {"request_id": request_id} if request_id is not None else {}
        rendered = self._request("POST", f"/v1/tasks/{task_id}/cancel", payload=payload)
        return CancelView.model_validate(rendered)

    def dispatch(
        self,
        task_id: str,
        *,
        request_id: str,
        task_state_version: int,
        execution_target_id: str,
    ) -> DispatchTaskView:
        rendered = self._request(
            "POST",
            f"/v1/tasks/{task_id}/dispatch",
            payload={
                "request_id": request_id,
                "task_state_version": task_state_version,
                "execution_target_id": execution_target_id,
            },
        )
        return DispatchTaskView.model_validate(rendered)

    def owner_execution_settings(self) -> OwnerExecutionSettingsView:
        return self._get(  # type: ignore[return-value]
            "/v1/settings/owner-execution",
            OwnerExecutionSettingsView,
        )

    def set_owner_execution_enabled(self, enabled: bool) -> OwnerExecutionSettingsView:
        rendered = self._request(
            "PUT",
            "/v1/settings/owner-execution",
            payload={"owner_initiated_execution_enabled": bool(enabled)},
        )
        return OwnerExecutionSettingsView.model_validate(rendered)

    def scheduling_settings(self) -> SchedulingSettingsView:
        return self._get(  # type: ignore[return-value]
            "/v1/settings/scheduling",
            SchedulingSettingsView,
        )

    def set_default_scheduling_policy(self, policy: str) -> SchedulingSettingsView:
        rendered = self._request(
            "PUT",
            "/v1/settings/scheduling",
            payload={"default_scheduling_policy": policy},
        )
        return SchedulingSettingsView.model_validate(rendered)

    def set_project_scheduling_policy(
        self,
        project_id: str,
        *,
        scheduling_policy: str | None,
        manual_execution_target_id: str | None = None,
    ) -> ProjectView:
        rendered = self._request(
            "PUT",
            f"/v1/projects/{project_id}/scheduling",
            payload={
                "scheduling_policy": scheduling_policy,
                "manual_execution_target_id": manual_execution_target_id,
            },
        )
        return ProjectView.model_validate(rendered)

    def import_provider_connections(
        self,
        provider_ids: Sequence[str],
    ) -> ImportConnectionsView:
        rendered = self._request(
            "POST",
            "/v1/provider-connections/import",
            payload={"provider_ids": list(provider_ids)},
        )
        return ImportConnectionsView.model_validate(rendered)

    def get_dispatch(self, request_id: str) -> DispatchTaskView:
        return self._get(f"/v1/dispatches/{request_id}", DispatchTaskView)  # type: ignore[return-value]

    def verification_report(self, task_id: str) -> VerificationReportView:
        return self._get(  # type: ignore[return-value]
            f"/v1/tasks/{task_id}/verification",
            VerificationReportView,
        )

    def routing_decision(self, task_id: str) -> RoutingDecisionView:
        return self._get(  # type: ignore[return-value]
            f"/v1/tasks/{task_id}/routing",
            RoutingDecisionView,
        )

    def providers(self) -> ProviderHealthListView:
        return self._get("/v1/providers", ProviderHealthListView)  # type: ignore[return-value]

    def quota(self) -> QuotaOverviewView:
        """Connection-based quota projection (connected providers, not pools)."""

        return self._get("/v1/quota", QuotaOverviewView)  # type: ignore[return-value]

    def refresh_quota(self, provider_id: str | None = None) -> QuotaRefreshResultView:
        """Explicit read-only quota collection, distinct from provider discovery."""

        path = (
            f"/v1/providers/{provider_id}/quota/refresh"
            if provider_id is not None
            else "/v1/quota/refresh"
        )
        return QuotaRefreshResultView.model_validate(self._request("POST", path, payload={}))

    def active_status(self) -> ActiveStatusView:
        return self._get("/v1/active-status", ActiveStatusView)  # type: ignore[return-value]

    def approvals_for_task(self, task_id: str) -> ApprovalListView:
        return self._get(  # type: ignore[return-value]
            f"/v1/tasks/{task_id}/approvals",
            ApprovalListView,
        )

    def get_approval(self, approval_id: str) -> ApprovalView:
        return self._get(f"/v1/approvals/{approval_id}", ApprovalView)  # type: ignore[return-value]


__all__ = [
    "ControlPlaneClient",
    "ControlPlaneError",
    "ControlPlaneUnavailable",
    "UnixSocketHTTPConnection",
]
