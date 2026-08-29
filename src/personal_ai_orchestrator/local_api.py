"""Bounded loopback HTTP adapter for the local routing service.

This is not a public REST API. It binds only to loopback, accepts JSON only, bounds body size,
and returns sanitized errors. Provider credentials never transit this endpoint.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import type

from pydantic import ValidationError

from personal_ai_orchestrator.opencode_contract import RoutingRequest
from personal_ai_orchestrator.routing_service import RoutingService

MAX_REQUEST_BYTES = 64 * 1024
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _host_allowed(value: str | None) -> bool:
    if value is None:
        return False
    host = value.rsplit(":", 1)[0] if not value.startswith("[") else value.split("]", 1)[0] + "]"
    return host in _ALLOWED_HOSTS


def handler_for(service: RoutingService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalAIOrchestrator/0"

        def log_message(self, format: str, *args: object) -> None:
            # Deliberately avoid request-body/provider data in the default HTTP access log.
            return

        def _json(self, status: int, payload: object) -> None:
            rendered = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(rendered)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(rendered)

        def do_POST(self) -> None:
            if self.path != "/v1/opencode/route":
                self._json(404, {"error": "not_found"})
                return
            if not _host_allowed(self.headers.get("host")):
                self._json(403, {"error": "invalid_host"})
                return
            origin = self.headers.get("origin")
            if origin is not None and not any(
                origin.startswith(f"http://{host}") for host in ("127.0.0.1", "localhost", "[::1]")
            ):
                self._json(403, {"error": "invalid_origin"})
                return
            content_type = self.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(415, {"error": "json_required"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "request_too_large_or_empty"})
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw)
                request = RoutingRequest.model_validate(payload)
                decision = service.route(request)
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
                self._json(400, {"error": "invalid_routing_request"})
                return
            except Exception:
                self._json(503, {"error": "routing_unavailable"})
                return
            self._json(200, decision.model_dump(mode="json"))

    return Handler


def serve(service: RoutingService, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local routing API must bind to loopback")
    server = ThreadingHTTPServer((host, port), handler_for(service))
    server.serve_forever()


__all__ = ["MAX_REQUEST_BYTES", "handler_for", "serve"]
