"""Typed local client for the P4.0 control plane over a Unix Domain Socket.

The client is never authoritative: it only issues structured JSON requests and renders sanitized
responses into typed view models. Connection failures surface as ``ControlPlaneUnavailable`` so
callers (including the CLI) can fail closed with a clean message instead of raw tracebacks.
"""

from __future__ import annotations

import http.client
import json
import socket
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
    ProviderHealthListView,
    RoutingDecisionView,
    RunListView,
    RunView,
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

    def submit(
        self, *, task_id: str, request_id: str, intent: str
    ) -> TaskView:
        payload = TaskSubmitRequest(task_id=task_id, request_id=request_id, intent=intent)
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
                "authority": "OWNER_INITIATED_EXECUTION",
            },
        )
        return DispatchTaskView.model_validate(rendered)

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

    def quota(self) -> ProviderHealthListView:
        return self._get("/v1/quota", ProviderHealthListView)  # type: ignore[return-value]

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
