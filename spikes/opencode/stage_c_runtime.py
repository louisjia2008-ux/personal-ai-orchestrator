"""Stage C provider-native runtime driver (real OpenCode + real provider).

Stage C exercises the provider-native OpenCode path without moving credentials into the
orchestrator.  The driver uses only supported OpenCode session HTTP surfaces and emits
sanitized evidence.  Completion acceptance is deliberately strict: provider/model identity,
finish reason, assistant-error state, and exact repository-derived output must all match.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
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

ZERO_TOKENS = {
    "input": 0,
    "output": 0,
    "reasoning": 0,
    "cache": {"read": 0, "write": 0},
}
REPO_READ_PROMPT = """Read README.md in this repository.

Return only the Markdown H1 heading exactly as written.
Do not edit any file.
Do not create files.
Do not explain."""


class StageCError(RuntimeError):
    pass


class OpenCodeHTTPError(StageCError):
    def __init__(self, method: str, path: str, status: int) -> None:
        self.status = status
        self.category = http_error_category(status)
        super().__init__(f"{method} {path} -> {self.category}")


def http_error_category(status: int) -> str:
    if status == 401:
        return "HTTP_401"
    if status == 403:
        return "HTTP_403"
    if status == 429:
        return "HTTP_429"
    return "UNKNOWN_PROVIDER_ERROR"


def sanitize_error_category(value: object) -> str | None:
    """Map unknown provider/runtime text to a bounded non-sensitive category."""

    if value in (None, {}, ""):
        return None
    try:
        text = json.dumps(value, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        text = str(type(value)).lower()

    if "modelunavailable" in text or "model unavailable" in text:
        return "MODEL_UNAVAILABLE"
    if "401" in text:
        return "HTTP_401"
    if "403" in text:
        return "HTTP_403"
    if "429" in text or "rate limit" in text or "rate_limit" in text:
        return "HTTP_429"
    if "unauthorized" in text or "authorization" in text or "forbidden" in text:
        return "AUTHORIZATION_ERROR"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "unavailable" in text or "connection" in text:
        return "PROVIDER_UNAVAILABLE"
    return "UNKNOWN_PROVIDER_ERROR"


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
            # Never read or persist the raw provider/OpenCode response body here.
            raise OpenCodeHTTPError(method, path, exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:  # pragma: no cover - network contract
            raise StageCError(f"{method} {path} -> TIMEOUT") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network contract
            category = "TIMEOUT" if isinstance(exc.reason, TimeoutError) else "PROVIDER_UNAVAILABLE"
            raise StageCError(f"{method} {path} -> {category}") from exc

        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Non-JSON success payloads are not committed to evidence by this driver.
            return None
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
            "POST",
            f"/api/session/{session_id}/prompt",
            {"prompt": {"text": text}},
        )

    def messages(self, session_id: str) -> list[dict]:
        data = self._request("GET", f"/api/session/{session_id}/message")
        return data if isinstance(data, list) else []

    def interrupt(self, session_id: str) -> None:
        self._request("POST", f"/api/session/{session_id}/interrupt", {})

    def delete_session(self, session_id: str) -> dict[str, object]:
        """Attempt deletion, then verify the session is no longer retrievable."""

        result: dict[str, object] = {
            "session_id": session_id,
            "attempted": True,
            "verified": False,
        }
        try:
            self._request("DELETE", f"/api/session/{session_id}")
        except StageCError as exc:
            result["error_category"] = sanitize_error_category(str(exc))
            return result

        try:
            remaining = self.get_session(session_id)
        except OpenCodeHTTPError as exc:
            if exc.status == 404:
                result["verified"] = True
            else:
                result["error_category"] = exc.category
            return result
        except StageCError as exc:
            result["error_category"] = sanitize_error_category(str(exc))
            return result

        if remaining in (None, {}, False):
            result["verified"] = True
        return result

    def healthy(self) -> bool:
        try:
            payload = self._request("GET", "/api/health")
            return bool(isinstance(payload, dict) and payload.get("healthy"))
        except StageCError:
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
        if error_probe is not None and error_probe():
            return latest
    return latest


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StageCError(message)


def expected_repository_h1(directory: str) -> str:
    """Host-side oracle: parse the first Markdown H1 from the disposable README."""

    readme = Path(directory) / "README.md"
    with readme.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if line.startswith("# "):
                return line
    raise StageCError("disposable fixture README.md does not contain a Markdown H1")


def completion_oracle(
    completion: dict | None,
    *,
    expected_text: str,
    provider: str,
    model: str,
) -> tuple[bool, str]:
    """Strict deterministic completion gate used by Stage C and unit tests."""

    if completion is None:
        return False, "MISSING_COMPLETION"
    error_category = sanitize_error_category(completion.get("error"))
    if error_category is not None:
        return False, f"ASSISTANT_ERROR:{error_category}"
    observed_model = completion.get("model") or {}
    if observed_model.get("providerID") != provider:
        return False, "PROVIDER_MISMATCH"
    if observed_model.get("id") != model:
        return False, "MODEL_MISMATCH"
    if completion.get("finish") != "stop":
        return False, "FINISH_NOT_STOP"
    if completion.get("text", "").strip() != expected_text:
        return False, "OUTPUT_MISMATCH"
    return True, "PASS"


def provider_error_from_log(log_path: str | None, provider: str, model: str) -> str | None:
    """Return only a bounded error category from the OpenCode runtime log."""

    if not log_path:
        return None
    try:
        with open(log_path, errors="replace") as handle:
            tail = handle.readlines()[-500:]
    except OSError:
        return None
    needle = f"{provider}/{model}"
    for line in reversed(tail):
        if needle not in line:
            continue
        if "ModelUnavailableError" in line or "Model unavailable" in line:
            return "MODEL_UNAVAILABLE"
        lowered = line.lower()
        if "401" in lowered:
            return "HTTP_401"
        if "403" in lowered or "forbidden" in lowered or "unauthorized" in lowered:
            return "HTTP_403"
        if "429" in lowered or "rate limit" in lowered:
            return "HTTP_429"
        if "timeout" in lowered:
            return "TIMEOUT"
        if "unavailable" in lowered or "failed to drain session" in lowered:
            return "PROVIDER_UNAVAILABLE"
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

        session_a = oc.create_session()["id"]
        created.append(session_a)
        session_b = oc.create_session()["id"]
        created.append(session_b)
        for label, sid in (("A", session_a), ("B", session_b)):
            snap = oc.get_session(sid)
            require(snap.get("model") is None, f"session {label} unexpectedly has a model")
            require(snap.get("cost") == 0, f"session {label} cost != 0 before use")
            require(snap.get("tokens") == ZERO_TOKENS, f"session {label} tokens != 0 before use")

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
            f"SHADOW recommendation {shadow_outcome.target_model} != real model",
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
        require(after_a.get("providerID") == args.provider, "session A provider mismatch")
        require(after_a.get("id") == args.model, "session A model mismatch")
        require(after_a.get("variant") in (None, "default"), "session A variant unexpected")
        b_model = oc.get_session(session_b).get("model")
        require(b_model is None, "session B was disturbed by session A ACTIVE switch")
        evidence["active"] = {
            "action": active_outcome.action.value,
            "session_a_model": f"{after_a.get('providerID')}/{after_a.get('id')}",
            "session_a_variant": after_a.get("variant"),
            "session_b_model": b_model,
            "result": "PASS",
        }

        expected = expected_repository_h1(args.directory)
        oc.prompt(session_a, REPO_READ_PROMPT)
        completion = wait_for_completion(
            oc,
            session_a,
            timeout_s=args.completion_timeout,
            error_probe=lambda: provider_error_from_log(
                args.opencode_log,
                args.provider,
                args.model,
            ),
        )
        provider_error = provider_error_from_log(args.opencode_log, args.provider, args.model)
        completion_ok, oracle_result = completion_oracle(
            completion,
            expected_text=expected,
            provider=args.provider,
            model=args.model,
        )

        if completion_ok:
            comp_model = completion.get("model") or {}
            evidence["completion"] = {
                "result": "PASS",
                "oracle": oracle_result,
                "finish": completion.get("finish"),
                "text": completion.get("text", "").strip(),
                "text_matches_repository_h1": True,
                "tokens": completion.get("tokens"),
                "cost": completion.get("cost"),
                "model": f"{comp_model.get('providerID')}/{comp_model.get('id')}",
                "variant": comp_model.get("variant"),
            }
        else:
            evidence["completion"] = {
                "result": "BLOCKED",
                "reason": oracle_result,
                "provider_error": provider_error,
                "assistant_error_category": sanitize_error_category(
                    (completion or {}).get("error")
                ),
                "finish": (completion or {}).get("finish"),
            }
            evidence["result"] = "PARTIAL"

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

        if completion_ok:
            evidence["cancellation"] = cancellation_smoke(oc, real_ref, args, created)
        else:
            evidence["cancellation"] = {
                "result": "SKIPPED",
                "reason": "provider completion did not clear the deterministic oracle",
            }

        require(oc.healthy(), "OpenCode server unhealthy after runtime checks")
        evidence.setdefault("result", "PASS")
    except StageCError as exc:
        evidence.setdefault("result", "FAIL")
        evidence["error_category"] = sanitize_error_category(str(exc))
        raise
    finally:
        deletion_results = [oc.delete_session(sid) for sid in created]
        evidence["sessions_deletion_attempted"] = [
            item["session_id"] for item in deletion_results if item["attempted"]
        ]
        evidence["sessions_deletion_verified"] = [
            item["session_id"] for item in deletion_results if item["verified"]
        ]
        evidence["session_cleanup"] = (
            "PASS"
            if deletion_results and all(bool(item["verified"]) for item in deletion_results)
            else "NOT_PROVEN"
        )

    return evidence


def cancellation_smoke(
    oc: OpenCode,
    real_ref: ModelRef,
    args: argparse.Namespace,
    created: list[str],
) -> dict:
    """Interrupt one in-flight turn and emit only sanitized cancellation evidence."""

    session_c = oc.create_session()["id"]
    created.append(session_c)
    oc.switch_model(session_c, real_ref)
    oc.prompt(session_c, "Write a detailed multi-paragraph essay about the history of computing.")
    for _ in range(15):
        time.sleep(0.4)
        current = assistant_from(oc.messages(session_c))
        if current is not None:
            break
    time.sleep(0.4)
    oc.interrupt(session_c)
    settled = wait_for_completion(oc, session_c, timeout_s=30)
    finish = (settled or {}).get("finish")
    error_category = sanitize_error_category((settled or {}).get("error"))
    error_value = (settled or {}).get("error")
    interrupted = finish == "error" and error_value not in (None, {}, "")
    healthy = oc.healthy()
    if interrupted:
        return {
            "result": "PASS",
            "finish": finish,
            "error_category": error_category,
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
        "reason": "no completed interrupt marker was observed",
        "finish": finish,
        "error_category": error_category,
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
        help="OpenCode log path, scanned read-only for sanitized provider categories",
    )
    parser.add_argument("--evidence-out", default=None)
    args = parser.parse_args()

    try:
        evidence = run(args)
        status = 0 if evidence.get("result") == "PASS" else 2
    except StageCError as exc:
        evidence = {
            "result": "FAIL",
            "error_category": sanitize_error_category(str(exc)),
            "provider_id": args.provider,
        }
        status = 1

    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.evidence_out:
        with open(args.evidence_out, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
