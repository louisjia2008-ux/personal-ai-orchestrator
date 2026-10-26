"""Provider diagnostics retain useful observations without retaining private error text."""

import hashlib
import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.execution_controller import _failure_result_payload
from personal_ai_orchestrator.pi_dispatch_executor import PiOwnerDispatchExecutor
from personal_ai_orchestrator.pi_provider_diagnostics import (
    PROVIDER_FIELDS,
    SAMPLE_LIMIT,
    provider_error_diagnostics,
    safe_provider_error_metadata,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_pi_dispatch_executor import _executor, _reserve, _run_row


def envelope(message, *, complete=True, stop="error", truncated=False, model="glm-5.3"):
    events = [
        {"type": "session", "version": 3, "id": "s", "cwd": "fixture"},
        {"type": "agent_start"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "zai",
                "model": model,
                "stopReason": stop,
                "errorMessage": message,
                "content": [{"type": "text", "text": "private-assistant-canary"}],
            },
        },
    ]
    if complete:
        events.append({"type": "agent_end", "messages": []})
    raw = ("\n".join(json.dumps(x) for x in events) + "\n").encode()
    return PiOwnerDispatchExecutor._host_result_envelope(
        70 if stop == "error" else 0,
        raw,
        b"",
        truncated=truncated,
        expected_model_ref="zai/glm-5.3",
        process_exit_code=0,
    )


@pytest.mark.parametrize(
    "message,reason,status",
    [
        (
            '503: {"error":{"type":"overloaded_error","message":"private-canary"}}',
            "SERVER_OVERLOADED",
            503,
        ),
        ("HTTP 429: private-canary", "RATE_LIMITED", 429),
        ("403 status code (no body)", "AUTH", 403),
        ("HTTP 401: private-canary", "AUTH", 401),
        (
            '{"error":{"code":"insufficient_quota","status":429,"message":"private-canary"}}',
            "QUOTA_EXHAUSTED",
            429,
        ),
        (
            '{"error":{"code":"context_length_exceeded","message":"private-canary"}}',
            "CONTEXT_RESTRICTED",
            None,
        ),
        ("Request timed out: private-canary", "REQUEST_TIMEOUT", None),
        ("TypeError: fetch failed private-canary", "CONNECTION_FAILED", None),
        ("read ECONNRESET private-canary", "CONNECTION_FAILED", None),
        ("stream ended before a terminal response event private-canary", "STREAM_INCOMPLETE", None),
        ("HTTP 504: private-canary", "REQUEST_TIMEOUT", 504),
        ("HTTP 400: private-canary", "HTTP_ERROR", 400),
        ("private-canary /private/path/model-503", "UNCLASSIFIED", None),
        (None, "NO_ERROR_DETAIL", None),
        ("", "NO_ERROR_DETAIL", None),
        ("  ", "UNCLASSIFIED", None),
    ],
)
def test_static_reason_status_and_fingerprint(message, reason, status):
    result = envelope(message)
    persisted = _failure_result_payload(exit_code=70, signal=None, worker_result=result)
    assert persisted["pi_diagnostic_code"] == "PI_PROVIDER_ERROR"
    assert persisted["pi_provider_error_reason"] == reason
    assert persisted.get("pi_provider_http_status") == status
    assert persisted["worker_process_exit_code"] == 0 and persisted["exit_code"] == 70
    rendered = json.dumps(persisted)
    assert "private-canary" not in rendered and "private-assistant-canary" not in rendered
    assert "stdout_tail" not in persisted and "stderr_tail" not in persisted
    if message:
        sample = message[:SAMPLE_LIMIT]
        assert (
            persisted["pi_provider_error_sample_sha256"]
            == hashlib.sha256(sample.encode("utf-8", errors="surrogatepass")).hexdigest()
        )


@pytest.mark.parametrize(
    "message",
    [
        '{"error":{"code":"insufficient_quota","code":"authentication_error"}}',
        '{"error":{"status":429},"status":503}',
        '{"error":{"code":"insufficient_quota","status":503}}',
        '{"error":{"status":NaN}}',
        '503: {"error":{"status":503,"status":429}}',
    ],
)
def test_ambiguous_error_detail_cannot_establish_specific_cause(message):
    result = provider_error_diagnostics(message)
    assert result["pi_provider_error_reason"] == "CONFLICTING_ERROR"
    assert "pi_provider_error_code" not in result


@pytest.mark.parametrize(
    "message", ["\ud800", "\udfff", "连接 private-canary 😀", "x" * (SAMPLE_LIMIT + 200)]
)
def test_unicode_and_bounded_sample_never_crash_or_echo(message):
    result = provider_error_diagnostics(message)
    assert result["pi_provider_error_sample_chars"] == min(len(message), SAMPLE_LIMIT)
    assert result["pi_provider_error_sample_at_limit"] == (len(message) >= SAMPLE_LIMIT)
    assert len(result["pi_provider_error_sample_sha256"]) == 64
    assert "private-canary" not in json.dumps(result)


@pytest.mark.parametrize("kwargs", [{"complete": False}, {"truncated": True}, {"stop": "stop"}])
def test_non_error_or_untrusted_protocol_cannot_supply_provider_details(kwargs):
    result = envelope("HTTP 503: private-canary", **kwargs)
    assert not PROVIDER_FIELDS.intersection(result)
    assert not PROVIDER_FIELDS.intersection(
        _failure_result_payload(exit_code=70, signal=None, worker_result=result)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("pi_provider_error_reason", "private-canary"),
        ("pi_provider_http_status", True),
        ("pi_provider_http_status", 999),
        ("pi_provider_error_code", "private-canary"),
        ("pi_provider_error_sample_sha256", "private-canary"),
        ("pi_provider_error_sample_chars", True),
        ("pi_provider_error_sample_chars", 2**63),
        ("pi_provider_error_sample_at_limit", "true"),
        ("pi_agent_end_seen", False),
        ("pi_stop_reason", "stop"),
    ],
)
def test_persistence_revalidates_entire_detail_tuple(field, value):
    result = envelope("HTTP 503: private-canary")
    result[field] = value
    assert safe_provider_error_metadata(result) == {}


def test_real_executor_persists_safe_details_and_releases_writer(tmp_path: Path):
    executor, repo, state_db = _executor(
        tmp_path,
        complete=True,
        stop_reason="error",
        error_message='503: {"error":{"type":"overloaded_error","message":"private-canary"}}',
    )
    _reserve(repo, state_db)
    executor.execute("dispatch-pi")
    store = SafetyKernelStore(state_db)
    try:
        run = _run_row(store)
        result = json.loads(run["result_json"])
        assert run["status"] == "FAILED" and store.get_task("task-pi").state is TaskState.BLOCKED
        assert result["pi_provider_error_reason"] == "SERVER_OVERLOADED"
        assert (
            result["pi_provider_http_status"] == 503
            and result["pi_provider_error_code"] == "overloaded_error"
        )
        assert "private-canary" not in json.dumps(result)
        assert store.get_workspace("task-pi").writer_token is None
    finally:
        store.close()


def test_conflicting_sdk_body_cannot_promote_quota_or_auth():
    result = envelope('503: {"error":{"code":"insufficient_quota"}}')
    assert result["worker_failure_class"] == "UNCLASSIFIED"
    assert result["pi_provider_error_reason"] == "CONFLICTING_ERROR"


@pytest.mark.parametrize(
    "message",
    [
        "POST /private/path request body said quota exceeded",
        "private-canary request input included unauthorized",
        "request trace 503 and rate limit in an echoed prompt",
    ],
)
def test_echoed_request_text_is_not_a_provider_reason(message):
    assert provider_error_diagnostics(message)["pi_provider_error_reason"] == "UNCLASSIFIED"
