"""Streaming projection preserves strict semantics across arbitrary pipe boundaries."""

import json

import pytest

from personal_ai_orchestrator.pi_diagnostics import safe_pi_failure_metadata
from personal_ai_orchestrator.pi_runtime import summarize_pi_json_stream
from personal_ai_orchestrator.pi_stream_capture import PiStreamCapture


def encoded(*events):
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events).encode()


SESSION = {"type": "session"}
START = {"type": "agent_start"}
END = {"type": "agent_end"}
MESSAGE = {
    "type": "message_end",
    "message": {
        "role": "assistant",
        "provider": "zai",
        "model": "glm-5.3",
        "stopReason": "stop",
    },
}


@pytest.mark.parametrize("width", [1, 2, 7, 8192])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_utf8_and_final_line_survive_chunk_boundaries(width, newline):
    data = encoded(
        SESSION, START, {"type": "message_update", "text": "中文😀"}, MESSAGE, END
    ).replace(b"\n", newline)
    capture = PiStreamCapture()
    for start in range(0, len(data), width):
        assert capture.feed(data[start : start + width])
    expected = summarize_pi_json_stream(data)
    assert capture.finish(eof=True) == expected
    assert capture.complete is True
    assert capture.finish(eof=False) == expected  # finalization is immutable


@pytest.mark.parametrize(
    "events",
    [
        (SESSION, START, END, END),
        (SESSION, END, START),
        (SESSION, START, {"type": "tool_execution_end", "toolCallId": "missing"}),
        (SESSION, START, {"type": "tool_execution_start", "toolCallId": "t"}, MESSAGE),
        (SESSION, START, {"type": "extension_error"}, MESSAGE, END),
        (SESSION, START, {"type": "agent_end", "willRetry": "private-canary"}),
        (SESSION, START, MESSAGE),
    ],
)
def test_projection_preserves_existing_negative_lifecycle_semantics(events):
    data = encoded(*events)
    expected = summarize_pi_json_stream(data)
    capture = PiStreamCapture()
    assert capture.feed(data)
    observed = capture.finish(eof=True)
    assert observed.protocol_valid == expected.protocol_valid
    assert observed.completed == expected.completed
    assert observed.parse_error == expected.parse_error


def test_explicit_provider_retry_preserves_only_the_final_interval():
    error = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "zai",
            "model": "glm-5.3",
            "stopReason": "error",
            "errorMessage": "HTTP 503 private-canary",
        },
    }
    data = encoded(
        SESSION,
        START,
        error,
        {"type": "agent_end", "willRetry": True},
        {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 2},
        START,
        MESSAGE,
        {"type": "auto_retry_end", "attempt": 1, "success": True},
        END,
    )
    capture = PiStreamCapture()
    assert capture.feed(data)
    assert capture.finish(eof=True) == summarize_pi_json_stream(data)
    assert capture.summary.matches_model_ref("zai/glm-5.3")
    assert capture.summary.final_error_message is None


@pytest.mark.parametrize(
    "constant,reason,body",
    [
        ("PI_MAX_STREAM_EVENTS", "event_count", encoded(SESSION, START)),
        ("PI_MAX_PROJECTION_BYTES", "projection_bytes", encoded(SESSION)),
        ("PI_MAX_IDENTITY_CHARS", "identity_chars", encoded(SESSION, START, MESSAGE)),
    ],
)
def test_additional_protocol_budgets_remain_fail_closed(monkeypatch, constant, reason, body):
    import personal_ai_orchestrator.pi_stream_capture as module

    monkeypatch.setattr(module, constant, 1)
    capture = PiStreamCapture()
    capture.feed(body + b"\n")
    summary = capture.finish(eof=True)
    assert capture.limit_reason == reason
    assert summary.protocol_valid is False
    assert capture.complete is False


def test_missing_pipe_eof_cannot_become_complete_even_with_agent_end():
    capture = PiStreamCapture()
    assert capture.feed(encoded(SESSION, START, MESSAGE, END))
    assert capture.finish(eof=False).completed is False
    assert capture.complete is False


def test_new_persisted_metadata_is_allowlisted_and_bounded():
    safe = safe_pi_failure_metadata(
        {
            "runtime": "pi-json",
            "pi_stream_complete": "private-canary",
            "pi_stdout_capture_truncated": True,
            "pi_stderr_capture_truncated": False,
            "pi_stream_observed_bytes": True,
            "pi_stream_observed_sha256": "private-canary",
            "pi_stream_limit_reason": "private-canary",
        }
    )
    assert safe == {
        "runtime": "pi-json",
        "pi_stdout_capture_truncated": True,
        "pi_stderr_capture_truncated": False,
    }
