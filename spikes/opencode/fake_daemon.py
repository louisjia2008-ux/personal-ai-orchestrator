"""Spike-only HTTP daemon for disposable OpenCode integration tests.

This is deliberately not the production daemon.  It exists to prove the thin
OpenCode adapter, safe bypass, Shadow Mode, and session-scoped switching before
quota collectors or scheduler policy are connected.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from pydantic import ValidationError

from personal_ai_orchestrator.opencode_contract import (
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
)


class FakeRoutingHandler(BaseHTTPRequestHandler):
    target_model = ModelRef(provider_id="minimax", model_id="m3")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/opencode/route":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(content_length))
            request = RoutingRequest.model_validate(payload)
        except (ValueError, json.JSONDecodeError, ValidationError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        selected_model = None if request.mode is RoutingMode.BYPASS else self.target_model
        decision = RoutingDecision(
            decision_id=f"fake-{uuid4()}",
            request_id=request.request_id,
            mode=request.mode,
            selected_model=selected_model,
            switch_requested=request.mode is RoutingMode.ACTIVE,
            explanation_ref="fake://fixed-model",
            catalog_snapshot_id="fake-catalog",
            policy_snapshot_id="fake-policy",
            quota_snapshot_ids=("fake-quota",),
            fallback_reason=(
                "bypass mode keeps the existing OpenCode model"
                if request.mode is RoutingMode.BYPASS
                else None
            ),
        )
        body = decision.model_dump_json().encode()

        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_model_ref(value: str) -> ModelRef:
    provider_id, separator, model_id = value.partition("/")
    if not separator or not provider_id or not model_id:
        raise argparse.ArgumentTypeError("model must use provider/model form")
    return ModelRef(provider_id=provider_id, model_id=model_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the spike-only fake routing daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", type=parse_model_ref, default=parse_model_ref("minimax/m3"))
    args = parser.parse_args()

    FakeRoutingHandler.target_model = args.model
    server = ThreadingHTTPServer((args.host, args.port), FakeRoutingHandler)
    print(f"fake orchestrator listening on http://{args.host}:{args.port}")
    print(f"fixed model: {args.model.provider_id}/{args.model.model_id}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
