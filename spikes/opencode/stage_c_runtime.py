"""Stage C provider-native runtime driver (real OpenCode + real provider).

Unlike Stage B (which asserts OpenCode state semantics against a deliberately
fake ``spike-provider/target-model``), Stage C exercises the full path against a
provider that is already authenticated inside OpenCode:

    fake routing daemon (credential-free orchestrator)
      -> RoutingRequest / RoutingDecision contract
      -> resolve_adapter_outcome()  (the committed thin-adapter decision logic)
      -> session-scoped model switch via OpenCode's documented HTTP API
      -> one minimal, harmless real completion
      -> second session provably untouched

The orchestrator/daemon and this driver never read, copy, or transmit provider
credentials.  OpenCode owns provider authentication; this driver only calls
OpenCode's supported session HTTP surface (``/api/session/...``) and reports
sanitized contract metadata plus non-secret token/usage counters.

Runtime note (recorded drift): the pinned Stage A/B pair
``@opencode-ai/cli@0.0.0-beta-18387`` publishes no ``darwin-arm64`` binary and
cannot be installed on this Mac, and its plugin command/switch API differs from
the standalone runtime that actually holds the local provider credentials.  This
driver therefore realizes the thin adapter as a host-side client over the same
underlying OpenCode operation (``POST /api/session/{id}/model`` emitting the
``session.next.model.switched`` durable event) that the beta plugin wrapped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

from personal_ai_orchestrator.opencode_contract import (
    AdapterAction,
    ModelRef,
    RoutingDecision,
    RoutingMode,
    RoutingRequest,
    resolve_adapter_outcome,
)

ZERO_TOKENS = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}


class StageCError(RuntimeError):
    pass


class OpenCode:
    """Minimal client for OpenCode's supported session HTTP surface."""

    def __init__(self, base_url: str, directory: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.directory = directory

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"x-opencode-directory": self.directory}
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - network contract
            raise StageCError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode(errors="replace")
        return parsed.get("data", parsed) if isinstance(parsed, dict) else parsed

    def create_session(self) -> dict:
        return self._request("POST", "/api/session", {"location": {"directory": self.directory}})

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/api/session/{session_id}")

    def switch_model(self, session_id: str, model: ModelRef) -> None:
        payload: dict[str, Any] = {"providerID": model.provider_id, "id": model.model_id}
        if model.variant:
            payload["variant"] = model.variant
        self._request("POST", f"/api/session/{session_id}/model", {"model": payload})

    def prompt(self, session_id: str, text: str) -> dict:
        return self._request(
            "POST", f"/api/session/{session_id}/prompt", {"prompt": {"text": text}}
        )

    def messages(self, session_id: str) -> list[dict]:
        data = self._request("GET", f"/api/session/{session_id}/message")
        return data if isinstance(data, list) else []

    def interrupt(self, session_id: str) -> None:
        self._request("POST", f"/api/session/{session_id}/interrupt", {})

    def delete_session(self, session_id: str) -> None:
        try:
            self._request("DELETE", f"/api/session/{session_id}")
        except StageCError:
            pass

    def healthy(self) -> bool:
        try:
            return bool(self._request("GET", "/api/health").get("healthy"))
        except Exception:
            return False


def ask_daemon(daemon_url: str, request: RoutingRequest) -> RoutingDecision:
    body = request.model_dump_json().encode()
    req = urllib.request.Request(
        f"{daemon_url.rstrip('/')}/v1/opencode/route",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return RoutingDecision.model_validate_json(resp.read())


def assistant_from(messages: list[dict]) -> dict | None:
    """Return the newest completed assistant message, normalizing message shape."""
    best = None
    for msg in messages:
        info = msg.get("info", msg)
        if info.get("type") != "assistant":
            continue
        parts = msg.get("parts") or info.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        completed = bool((info.get("time") or {}).get("completed"))
        best = {
            "text": text,
            "tokens": info.get("tokens"),
            "cost": info.get("cost"),
            "finish": info.get("finish"),
            "model": info.get("model"),
            "error": info.get("error"),
            "completed": completed,
        }
    return best


def wait_for_completion(
    oc: OpenCode,
    session_id: str,
    timeout_s: int = 120,
    error_probe=None,
) -> dict | None:
    deadline = time.time() + timeout_s
    latest = None
    while time.time() < deadline:
        time.sleep(2)
        latest = assistant_from(oc.messages(session_id))
        if latest and (latest["completed"] or latest.get("finish")):
            return latest
        # Stop early if the runtime has already logged a fatal provider error.
        if error_probe is not None and error_probe():
            return latest
    return latest


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StageCError(message)


def provider_error_from_log(log_path: str | None, provider: str, model: str) -> str | None:
    """Best-effort, sanitized read of a provider/model runtime error from the
    OpenCode log. Returns a short category string, never raw credentials."""
    if not log_path:
        return None
    try:
        with open(log_path, errors="replace") as handle:
            tail = handle.readlines()[-500:]
    except OSError:
        return None
    needle = f"{provider}/{model}"
    for line in reversed(tail):
        if "ModelUnavailableError" in line and needle in line:
            return f"MODEL_UNAVAILABLE (SessionRunnerModel.ModelUnavailableError: {needle})"
        if needle in line and ("Failed to drain Session" in line or "level=ERROR" in line):
            for token in ("401", "403", "429", "quota", "rate", "unauthorized", "forbidden"):
                if token.lower() in line.lower():
                    return f"PROVIDER_ERROR ({token}) for {needle}"
    return None


def run(args: argparse.Namespace) -> dict:
    oc = OpenCode(args.server_url, args.directory)
    real_ref = ModelRef(provider_id=args.provider, model_id=args.model)
    evidence: dict[str, Any] = {
        "provider_id": args.provider,
        "model_id": args.model,
        "server": args.server_url,
    }
    created: list[str] = []

    try:
        require(oc.healthy(), "OpenCode server is not healthy")

        # 1. Two independent disposable sessions, both model-less and usage-free.
        session_a = oc.create_session()["id"]
        created.append(session_a)
        session_b = oc.create_session()["id"]
        created.append(session_b)
        for label, sid in (("A", session_a), ("B", session_b)):
            snap = oc.get_session(sid)
            require(snap.get("model") is None, f"session {label} unexpectedly has a model")
            require(snap.get("cost") == 0, f"session {label} cost != 0 before use")
            require(snap.get("tokens") == ZERO_TOKENS, f"session {label} tokens != 0 before use")

        # 2. SHADOW: daemon recommends the real model but the adapter must not switch.
        shadow_req = RoutingRequest(
            request_id=str(uuid4()),
            session_id=session_a,
            location=args.directory,
            mode=RoutingMode.SHADOW,
        )
        shadow_decision = ask_daemon(args.daemon_url, shadow_req)
        shadow_outcome = resolve_adapter_outcome(shadow_req, shadow_decision)
        require(
            shadow_outcome.action is AdapterAction.RECORD_ONLY,
            f"SHADOW outcome was {shadow_outcome.action}, expected RECORD_ONLY",
        )
        require(
            shadow_outcome.target_model == real_ref,
            f"SHADOW recommendation {shadow_outcome.target_model} != real {real_ref}",
        )
        require(
            oc.get_session(session_a).get("model") is None,
            "SHADOW must not switch the session model",
        )
        evidence["shadow"] = {
            "action": shadow_outcome.action.value,
            "recommended_model": f"{real_ref.provider_id}/{real_ref.model_id}",
            "session_model_after": None,
            "result": "PASS",
        }

        # 3. ACTIVE: adapter applies a session-scoped switch to the real model on A only.
        active_req = RoutingRequest(
            request_id=str(uuid4()),
            session_id=session_a,
            location=args.directory,
            mode=RoutingMode.ACTIVE,
        )
        active_decision = ask_daemon(args.daemon_url, active_req)
        active_outcome = resolve_adapter_outcome(active_req, active_decision)
        require(
            active_outcome.action is AdapterAction.SWITCH_MODEL,
            f"ACTIVE outcome was {active_outcome.action}, expected SWITCH_MODEL",
        )
        require(active_outcome.target_model == real_ref, "ACTIVE target model mismatch")
        oc.switch_model(session_a, active_outcome.target_model)

        after_a = oc.get_session(session_a).get("model") or {}
        require(after_a.get("providerID") == args.provider, f"A provider mismatch: {after_a}")
        require(after_a.get("id") == args.model, f"A model mismatch: {after_a}")
        require(after_a.get("variant") in (None, "default"), f"A variant unexpected: {after_a}")

        b_model = oc.get_session(session_b).get("model")
        require(b_model is None, f"session B was disturbed: {b_model}")
        evidence["active"] = {
            "action": active_outcome.action.value,
            "session_a_model": f"{after_a.get('providerID')}/{after_a.get('id')}",
            "session_a_variant": after_a.get("variant"),
            "session_b_model": b_model,
            "result": "PASS",
        }

        # 4. One minimal, harmless real completion through the routed session.
        expected = "# OpenCode Stage C Disposable Fixture"
        oc.prompt(
            session_a,
            "Respond with exactly this line and nothing else, no preamble: " + expected,
        )
        completion = wait_for_completion(
            oc,
            session_a,
            timeout_s=args.completion_timeout,
            error_probe=lambda: provider_error_from_log(
                args.opencode_log, args.provider, args.model
            ),
        )
        provider_error = provider_error_from_log(args.opencode_log, args.provider, args.model)

        completion_ok = (
            completion is not None
            and completion.get("error") in (None, {})
            and bool(completion["text"].strip())
            and (completion.get("model") or {}).get("providerID") == args.provider
            and (completion.get("model") or {}).get("id") == args.model
        )

        if completion_ok:
            comp_model = completion.get("model") or {}
            evidence["completion"] = {
                "result": "PASS",
                "finish": completion.get("finish"),
                "text": completion["text"].strip(),
                "text_matches_fixture_h1": completion["text"].strip() == expected,
                "tokens": completion.get("tokens"),
                "cost": completion.get("cost"),
                "model": f"{comp_model.get('providerID')}/{comp_model.get('id')}",
                "variant": comp_model.get("variant"),
            }
        else:
            # Auth was present and the session-scoped switch applied, but the
            # provider did not (or could not) execute a turn. Record the exact
            # blocker instead of a fabricated pass.
            evidence["completion"] = {
                "result": "BLOCKED",
                "reason": "no successful assistant turn from the authenticated provider",
                "provider_error": provider_error,
                "assistant_error": (completion or {}).get("error"),
                "finish": (completion or {}).get("finish"),
            }
            evidence["result"] = "PARTIAL"

        # 5. Post-completion isolation: B stays pristine either way.
        b_after = oc.get_session(session_b)
        require(b_after.get("model") is None, "session B gained a model after A's turn")
        require(b_after.get("cost") == 0, "session B cost changed")
        require(b_after.get("tokens") == ZERO_TOKENS, "session B tokens changed")
        evidence["session_isolation"] = {
            "session_b_model": None,
            "session_b_cost": 0,
            "session_b_tokens": ZERO_TOKENS,
            "result": "PASS",
        }

        # 6. Bounded cancellation smoke -- only meaningful if the provider runs.
        if completion_ok:
            evidence["cancellation"] = cancellation_smoke(oc, real_ref, args, created)
        else:
            evidence["cancellation"] = {
                "result": "SKIPPED",
                "reason": "provider model does not execute turns; nothing to cancel",
            }

        require(oc.healthy(), "OpenCode server unhealthy after runtime checks")
        evidence.setdefault("result", "PASS")
    except StageCError as exc:
        evidence.setdefault("result", "FAIL")
        evidence["error"] = str(exc)
        raise
    finally:
        for sid in created:
            oc.delete_session(sid)
        evidence["sessions_deleted"] = created

    return evidence


def cancellation_smoke(
    oc: OpenCode, real_ref: ModelRef, args: argparse.Namespace, created: list[str]
) -> dict:
    """Interrupt one in-flight turn on a dedicated disposable session, precisely.

    Records the honest outcome. If the tiny turn settles before the interrupt is
    observable, reports CANCELLATION_NOT_PROVEN with the exact reason instead of a
    fabricated PASS.
    """
    session_c = oc.create_session()["id"]
    created.append(session_c)
    oc.switch_model(session_c, real_ref)
    # A longer task so the turn is genuinely in-flight when interrupted.
    oc.prompt(
        session_c,
        "Write a detailed multi-paragraph essay about the history of computing.",
    )
    # Interrupt only once the assistant turn is actually generating, so the abort
    # lands mid-flight and produces a clear marker. Bounded to keep quota minimal.
    for _ in range(15):
        time.sleep(0.4)
        current = assistant_from(oc.messages(session_c))
        if current is not None:
            break
    time.sleep(0.4)
    oc.interrupt(session_c)
    settled = wait_for_completion(oc, session_c, timeout_s=30)
    finish = (settled or {}).get("finish")
    error = (settled or {}).get("error") or {}
    error_message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    healthy = oc.healthy()
    interrupted = finish == "error" and "interrupt" in error_message.lower()
    if interrupted:
        return {
            "result": "PASS",
            "finish": finish,
            "interrupt_marker": error_message,
            "server_healthy_after": healthy,
        }
    if finish == "stop":
        return {
            "result": "CANCELLATION_NOT_PROVEN",
            "reason": "turn completed before the interrupt could abort it",
            "finish": finish,
            "server_healthy_after": healthy,
        }
    return {
        "result": "CANCELLATION_NOT_PROVEN",
        "reason": f"no clear interrupt marker (finish={finish!r}, error={error_message!r})",
        "finish": finish,
        "server_healthy_after": healthy,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCode Stage C provider-native runtime driver")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--daemon-url", required=True)
    parser.add_argument("--directory", required=True, help="disposable fixture directory")
    parser.add_argument("--provider", required=True, help="real OpenCode provider id")
    parser.add_argument("--model", required=True, help="real OpenCode catalog model id")
    parser.add_argument("--completion-timeout", type=int, default=120)
    parser.add_argument(
        "--opencode-log",
        default=None,
        help="OpenCode log path, scanned (read-only) for sanitized provider errors",
    )
    parser.add_argument("--evidence-out", default=None)
    args = parser.parse_args()

    try:
        evidence = run(args)
        status = 0
    except StageCError as exc:
        evidence = {"result": "FAIL", "error": str(exc), "provider_id": args.provider}
        status = 1

    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.evidence_out:
        with open(args.evidence_out, "w") as handle:
            handle.write(rendered + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
