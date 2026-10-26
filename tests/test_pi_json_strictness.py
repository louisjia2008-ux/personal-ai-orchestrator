"""Regression tests for strict JSON parsing and Pi's explicit retry protocol."""

import json

import pytest

from personal_ai_orchestrator.pi_runtime import summarize_pi_json_stream


def _valid_stream() -> bytes:
    lines = [
        '{"type":"session"}',
        '{"type":"agent_start"}',
        '{"type":"message_end","message":{"role":"assistant","provider":"openai","model":"gpt","stopReason":"stop"}}',
        '{"type":"tool_execution_start","toolCallId":"t1"}',
        '{"type":"tool_execution_end","toolCallId":"t1"}',
        '{"type":"agent_end"}',
    ]
    return ("\n".join(lines) + "\n").encode()


def _parse_error(data: bytes) -> str:
    summary = summarize_pi_json_stream(data)
    assert summary.protocol_valid is False
    return summary.parse_error or ""


def test_valid_stream_passes() -> None:
    summary = summarize_pi_json_stream(_valid_stream())
    assert summary.protocol_valid is True
    assert summary.parse_error is None
    assert summary.event_count == 6
    assert summary.final_provider == "openai"
    assert summary.final_model == "gpt"
    assert summary.final_stop_reason == "stop"
    assert summary.tool_start_count == 1
    assert summary.tool_end_count == 1


def test_duplicate_top_level_field_rejected() -> None:
    stream = b'{"type":"session","type":"session"}\n'
    assert _parse_error(stream) == "invalid_jsonl"


def test_duplicate_nested_field_rejected() -> None:
    stream = (
        b'{"type":"session"}\n'
        b'{"type":"message_end","message":{"role":"assistant","role":"assistant"}}\n'
    )
    assert _parse_error(stream) == "invalid_jsonl"


def test_duplicate_escaped_field_rejected() -> None:
    # "\\u0074ype" decodes to "type", colliding with the plain key.
    stream = b'{"type":"session","\\u0074ype":"session"}\n'
    assert _parse_error(stream) == "invalid_jsonl"


def test_nan_rejected() -> None:
    stream = (
        b'{"type":"session"}\n{"type":"message_end","message":{"role":"assistant","score":NaN}}\n'
    )
    assert _parse_error(stream) == "invalid_jsonl"


def test_infinity_rejected() -> None:
    stream = (
        b'{"type":"session"}\n'
        b'{"type":"message_end","message":{"role":"assistant","score":Infinity}}\n'
    )
    assert _parse_error(stream) == "invalid_jsonl"


def test_negative_infinity_rejected() -> None:
    stream = (
        b'{"type":"session"}\n'
        b'{"type":"message_end","message":{"role":"assistant","score":-Infinity}}\n'
    )
    assert _parse_error(stream) == "invalid_jsonl"


def test_duplicate_in_array_object_rejected() -> None:
    stream = b'{"type":"session"}\n{"type":"message_end","items":[{"a":1,"a":2}]}\n'
    assert _parse_error(stream) == "invalid_jsonl"


def test_legal_numbers_and_unicode_pass() -> None:
    stream = (
        (
            '{"type":"session"}\\n'
            '{"type":"agent_start"}\\n'
            '{"type":"message_end","message":{"role":"assistant","provider":"p",'
            '"model":"m","stopReason":"stop","score":1.5,"count":-3,"note":"ok ok"}}\\n'
            '{"type":"agent_end"}\\n'
        )
        .replace("\\n", "\n")
        .encode()
    )
    summary = summarize_pi_json_stream(stream)
    assert summary.protocol_valid is True
    assert summary.parse_error is None


def test_same_word_in_text_is_not_duplicate_field() -> None:
    stream = (
        b'{"type":"session"}\n'
        b'{"type":"agent_start"}\n'
        b'{"type":"message_end","message":{"role":"assistant","text":"stop stop stop"}}\n'
        b'{"type":"agent_end"}\n'
    )
    summary = summarize_pi_json_stream(stream)
    assert summary.protocol_valid is True


def test_multi_turn_messages_pass() -> None:
    stream = (
        b'{"type":"session"}\n'
        b'{"type":"agent_start"}\n'
        b'{"type":"message_end","message":{"role":"assistant","provider":"a","model":"m1","stopReason":"stop"}}\n'
        b'{"type":"tool_execution_start","toolCallId":"t1"}\n'
        b'{"type":"tool_execution_end","toolCallId":"t1"}\n'
        b'{"type":"message_end","message":{"role":"assistant","provider":"a","model":"m1","stopReason":"tool_use"}}\n'
        b'{"type":"message_end","message":{"role":"assistant","provider":"a","model":"m1","stopReason":"stop"}}\n'
        b'{"type":"agent_end"}\n'
    )
    summary = summarize_pi_json_stream(stream)
    assert summary.protocol_valid is True
    assert summary.tool_start_count == 1
    assert summary.tool_end_count == 1


def _retry_message(stop: str = "stop") -> dict:
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "zai",
            "model": "glm-5.3",
            "stopReason": stop,
            "errorMessage": "private-retry-canary" if stop == "error" else None,
        },
    }


def _retry_marker(attempt: object = 1) -> dict:
    return {"type": "auto_retry_start", "attempt": attempt, "maxAttempts": 3, "delayMs": 0}


def _retry_stream(*events: dict):
    return summarize_pi_json_stream(b"\n".join(json.dumps(e).encode() for e in events))


def _retry_prefix() -> list[dict]:
    return [
        {"type": "session"},
        {"type": "agent_start"},
        _retry_message("error"),
        {"type": "agent_end", "willRetry": True},
        _retry_marker(),
        {"type": "agent_start"},
    ]


def test_marked_retry_can_finish_with_fresh_identity_and_no_prior_error() -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": 1},
        {"type": "agent_end", "willRetry": False},
    )
    assert value.matches_model_ref("zai/glm-5.3")
    assert value.final_error_message is None


@pytest.mark.parametrize("attempt", [True, 0, 4, "1", 1.0])
def test_invalid_retry_marker_cannot_authorize_restart(attempt: object) -> None:
    events = _retry_prefix()
    events[4] = _retry_marker(attempt)
    value = _retry_stream(*events, _retry_message(), {"type": "agent_end"})
    assert value.parse_error == "invalid_agent_lifecycle"


def test_retry_missing_final_end_cannot_reuse_prior_end() -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": 1},
    )
    assert value.protocol_valid
    assert not value.agent_end_seen
    assert not value.completed


def test_retry_start_without_initial_interval_is_invalid() -> None:
    value = _retry_stream(
        {"type": "session"},
        _retry_marker(),
        {"type": "agent_start"},
        _retry_message(),
        {"type": "agent_end"},
    )
    assert value.parse_error == "invalid_agent_lifecycle"


def test_spurious_retry_success_is_invalid() -> None:
    value = _retry_stream(
        {"type": "session"},
        {"type": "agent_start"},
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": 1},
        {"type": "agent_end"},
    )
    assert value.parse_error == "invalid_agent_lifecycle"


@pytest.mark.parametrize("will_retry", [None, False, True, "true"])
def test_successful_interval_cannot_authorize_retry(will_retry: object) -> None:
    end = {"type": "agent_end"}
    if will_retry is not None:
        end["willRetry"] = will_retry
    value = _retry_stream(
        {"type": "session"},
        {"type": "agent_start"},
        _retry_message(),
        end,
        _retry_marker(),
        {"type": "agent_start"},
    )
    assert value.parse_error == "invalid_agent_lifecycle"


def test_missing_retry_success_marker_is_invalid() -> None:
    value = _retry_stream(*_retry_prefix(), _retry_message(), {"type": "agent_end"})
    assert value.parse_error == "invalid_agent_lifecycle"


def test_retry_can_recover_with_tool_use_and_restart_retry_budget() -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message("toolUse"),
        {"type": "auto_retry_end", "success": True, "attempt": 1},
        {"type": "tool_execution_start", "toolCallId": "read1"},
        {"type": "tool_execution_end", "toolCallId": "read1"},
        _retry_message("error"),
        {"type": "agent_end", "willRetry": True},
        _retry_marker(),
        {"type": "agent_start"},
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": 1},
        {"type": "agent_end"},
    )
    assert value.completed
    assert value.tool_start_count == value.tool_end_count == 1


def test_consecutive_failed_attempts_then_success() -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message("error"),
        {"type": "agent_end", "willRetry": True},
        _retry_marker(2),
        {"type": "agent_start"},
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": 2},
        {"type": "agent_end"},
    )
    assert value.completed


def test_exhausted_retry_keeps_final_error() -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message("error"),
        {"type": "agent_end", "willRetry": False},
        {"type": "auto_retry_end", "success": False, "attempt": 1},
    )
    assert value.protocol_valid
    assert not value.completed
    assert value.final_error_message == "private-retry-canary"


def test_cancel_during_retry_wait_does_not_complete() -> None:
    value = _retry_stream(
        *_retry_prefix()[:-1], {"type": "auto_retry_end", "success": False, "attempt": 1}
    )
    assert value.protocol_valid
    assert not value.completed


@pytest.mark.parametrize("attempt", [True, 2])
def test_mismatched_retry_success_is_invalid(attempt: object) -> None:
    value = _retry_stream(
        *_retry_prefix(),
        _retry_message(),
        {"type": "auto_retry_end", "success": True, "attempt": attempt},
        {"type": "agent_end"},
    )
    assert value.parse_error == "invalid_agent_lifecycle"
