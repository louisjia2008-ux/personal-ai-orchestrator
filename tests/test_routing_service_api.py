import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer

from personal_ai_orchestrator.local_api import handler_for
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _service(tmp_path) -> RoutingService:
    return RoutingService(
        registry=ModelRegistry(),
        store=SafetyKernelStore(tmp_path / "state.sqlite3"),
        catalog_snapshot_id="catalog-empty",
    )


def _start_server(service: RoutingService):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _route_payload(request_id: str) -> bytes:
    return json.dumps(
        {
            "request_id": request_id,
            "session_id": "session-http",
            "task_id": "missing-task",
            "mode": "SHADOW",
            "requested_at": NOW.isoformat(),
        }
    ).encode("utf-8")


def test_service_unknown_task_returns_no_switch_and_is_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    request = RoutingRequest(
        request_id="req-1",
        session_id="session-1",
        task_id="missing-task",
        mode=RoutingMode.SHADOW,
        requested_at=NOW,
    )
    first = service.route(request, now=NOW)
    second = service.route(request, now=NOW)
    assert first == second
    assert first.switch_requested is False
    assert first.selected_model is None
    assert "no authoritative TaskProfile" in (first.fallback_reason or "")


def test_loopback_http_route_endpoint_returns_valid_decision(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=_route_payload("req-http"),
            headers={"content-type": "application/json", "origin": "http://localhost:3000"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read())
        assert body["request_id"] == "req-http"
        assert body["mode"] == "SHADOW"
        assert body["switch_requested"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_loopback_http_rejects_non_json(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=b"hello",
            headers={"content-type": "text/plain"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
            raise AssertionError("non-JSON request unexpectedly succeeded")
        except urllib.error.HTTPError as error:
            assert error.code == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_loopback_http_rejects_origin_prefix_spoof(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=_route_payload("req-origin-spoof"),
            headers={
                "content-type": "application/json",
                "origin": "http://localhost.evil.invalid",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
            raise AssertionError("spoofed Origin unexpectedly succeeded")
        except urllib.error.HTTPError as error:
            assert error.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
