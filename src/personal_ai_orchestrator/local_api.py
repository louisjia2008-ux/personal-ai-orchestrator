"""Bounded loopback HTTP adapter for the local routing service.

This is not a public REST API. It binds only to loopback, accepts JSON only, bounds body size,
and returns sanitized errors. Provider credentials never transit this endpoint.
"""

from __future__ import annotations

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_ai_orchestrator.opencode_contract import RoutingRequest
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority

MAX_REQUEST_BYTES = 64 * 1024
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class _SwitchAuthorizePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_state_version: int = Field(ge=0)
    session_id: str = Field(min_length=1)


class _SwitchResolvePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    completed: bool


def _host_allowed(value: str | None) -> bool:
    if value is None:
        return False
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


def _request_store(service: RoutingService) -> SafetyKernelStore:
    if service.store.path == ":memory:":
        raise RuntimeError("threaded local API requires a file-backed SafetyKernelStore")
    return SafetyKernelStore(service.store.path)


def _route_thread_safe(service: RoutingService, request: RoutingRequest):
    """Use a request-local SQLite connection when the durable store is file backed."""

    request_store = _request_store(service)
    try:
        return replace(service, store=request_store).route(request)
    finally:
        request_store.close()


def _authorize_switch_thread_safe(service: RoutingService, payload: _SwitchAuthorizePayload):
    request_store = _request_store(service)
    try:
        return SwitchLeaseAuthority(request_store).authorize(**payload.model_dump())
    finally:
        request_store.close()


def _resolve_switch_thread_safe(service: RoutingService, payload: _SwitchResolvePayload):
    request_store = _request_store(service)
    try:
        return SwitchLeaseAuthority(request_store).resolve(**payload.model_dump())
    finally:
        request_store.close()


def handler_for(service: RoutingService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalAIOrchestrator/0"

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

        def _read_json(self) -> object | None:
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
                self._json(413, {"error": "request_too_large_or_empty"})
                return None
            try:
                return json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_json"})
                return None

        def do_POST(self) -> None:
            if self.path not in {
                "/v1/opencode/route",
                "/v1/opencode/authorize-switch",
                "/v1/opencode/resolve-switch",
            }:
                self._json(404, {"error": "not_found"})
                return
            if not _host_allowed(self.headers.get("host")):
                self._json(403, {"error": "invalid_host"})
                return
            if not _origin_allowed(self.headers.get("origin")):
                self._json(403, {"error": "invalid_origin"})
                return
            raw = self._read_json()
            if raw is None:
                return

            if self.path == "/v1/opencode/route":
                try:
                    request = RoutingRequest.model_validate(raw)
                    decision = _route_thread_safe(service, request)
                except (ValidationError, ValueError):
                    self._json(400, {"error": "invalid_routing_request"})
                    return
                except Exception:
                    self._json(503, {"error": "routing_unavailable"})
                    return
                self._json(200, decision.model_dump(mode="json"))
                return

            if self.path == "/v1/opencode/authorize-switch":
                try:
                    payload = _SwitchAuthorizePayload.model_validate(raw)
                    lease = _authorize_switch_thread_safe(service, payload)
                except (ValidationError, ValueError, KeyError):
                    self._json(400, {"error": "invalid_switch_authorization"})
                    return
                except RuntimeError:
                    self._json(409, {"error": "switch_not_authorized"})
                    return
                except Exception:
                    self._json(503, {"error": "switch_authorization_unavailable"})
                    return
                self._json(200, lease.model_dump(mode="json"))
                return

            try:
                payload = _SwitchResolvePayload.model_validate(raw)
                lease = _resolve_switch_thread_safe(service, payload)
            except (ValidationError, ValueError, KeyError):
                self._json(400, {"error": "invalid_switch_resolution"})
                return
            except RuntimeError:
                self._json(409, {"error": "switch_lease_not_active"})
                return
            except Exception:
                self._json(503, {"error": "switch_resolution_unavailable"})
                return
            self._json(200, lease.model_dump(mode="json"))

    return Handler


def serve(service: RoutingService, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local routing API must bind to loopback")
    if service.store.path == ":memory:":
        raise ValueError("threaded local routing API requires a durable file-backed state store")
    server = ThreadingHTTPServer((host, port), handler_for(service))
    server.serve_forever()


__all__ = ["MAX_REQUEST_BYTES", "handler_for", "serve"]
