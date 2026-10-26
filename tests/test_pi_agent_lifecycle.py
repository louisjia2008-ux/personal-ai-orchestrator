"""Regression tests for agent lifecycle ordering in summarize_pi_json_stream."""

from __future__ import annotations

import json

from personal_ai_orchestrator.pi_runtime import summarize_pi_json_stream

SESSION = {"type": "session", "version": 3, "id": "s1", "cwd": "/tmp/w"}
AGENT_START = {"type": "agent_start"}
AGENT_END = {"type": "agent_end", "messages": []}


def _assistant_message_end(**overrides: object) -> dict:
    message = {
        "role": "assistant",
        "provider": "zai",
        "model": "glm-5.3",
        "stopReason": "stop",
    }
    message.update(overrides)
    return {"type": "message_end", "message": message}


def _stream(*events: object) -> bytes:
    return "\n".join(json.dumps(event) for event in events).encode("utf-8")


def test_agent_end_before_agent_start_is_invalid() -> None:
    summary = summarize_pi_json_stream(_stream(SESSION, AGENT_END, AGENT_START, AGENT_END))
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_agent_lifecycle"
    assert summary.session_seen is True
    assert summary.agent_end_seen is False
    assert summary.event_count == 2


def test_duplicate_agent_start_is_invalid() -> None:
    summary = summarize_pi_json_stream(_stream(SESSION, AGENT_START, AGENT_START))
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_agent_lifecycle"
    assert summary.agent_start_seen is True
    assert summary.agent_end_seen is False


def test_duplicate_agent_end_is_invalid() -> None:
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            _assistant_message_end(),
            AGENT_END,
            AGENT_END,
        )
    )
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_agent_lifecycle"
    assert summary.agent_start_seen is True
    assert summary.agent_end_seen is True


def test_second_agent_start_after_agent_end_is_invalid() -> None:
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            _assistant_message_end(),
            AGENT_END,
            AGENT_START,
            _assistant_message_end(),
            AGENT_END,
        )
    )
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_agent_lifecycle"
    assert summary.agent_start_seen is True
    assert summary.agent_end_seen is True


def test_assistant_message_end_outside_agent_interval_is_invalid() -> None:
    summary = summarize_pi_json_stream(_stream(SESSION, _assistant_message_end()))
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_agent_lifecycle"

    after = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            _assistant_message_end(),
            AGENT_END,
            _assistant_message_end(),
        )
    )
    assert after.protocol_valid is False
    assert after.parse_error == "invalid_agent_lifecycle"


def test_tool_events_outside_agent_interval_are_invalid() -> None:
    tool_start = {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "read"}
    tool_end = {"type": "tool_execution_end", "toolCallId": "t1", "toolName": "read"}

    before = summarize_pi_json_stream(
        _stream(SESSION, tool_start, tool_end, AGENT_START, AGENT_END)
    )
    assert before.protocol_valid is False
    assert before.parse_error == "invalid_agent_lifecycle"

    straddling = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            tool_start,
            AGENT_END,
            tool_end,
        )
    )
    assert straddling.protocol_valid is False
    assert straddling.parse_error == "invalid_agent_lifecycle"


def test_missing_agent_end_keeps_prior_semantics() -> None:
    summary = summarize_pi_json_stream(_stream(SESSION, AGENT_START, _assistant_message_end()))
    assert summary.protocol_valid is True
    assert summary.completed is False
    assert summary.agent_end_seen is False


def test_valid_multi_turn_stream_with_tools_is_accepted() -> None:
    turn_one_tool_start = {
        "type": "tool_execution_start",
        "toolCallId": "t1",
        "toolName": "read",
    }
    turn_one_tool_end = {
        "type": "tool_execution_end",
        "toolCallId": "t1",
        "toolName": "read",
    }
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            _assistant_message_end(),
            turn_one_tool_start,
            turn_one_tool_end,
            _assistant_message_end(),
            AGENT_END,
        )
    )
    assert summary.protocol_valid is True
    assert summary.completed is True
    assert summary.tool_start_count == 1
    assert summary.tool_end_count == 1
    assert summary.final_stop_reason == "stop"


def test_valid_stream_with_error_stop_and_extension_error_is_accepted() -> None:
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            {"type": "extension_error", "error": "guard hiccup"},
            _assistant_message_end(stopReason="error", errorMessage="429: limit"),
            AGENT_END,
        )
    )
    assert summary.protocol_valid is True
    assert summary.completed is False
    assert summary.extension_error_count == 1
    assert summary.final_stop_reason == "error"
    assert summary.final_error_message == "429: limit"


def test_unpaired_tool_ids_still_report_tool_lifecycle_error() -> None:
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "read"},
            {"type": "tool_execution_end", "toolCallId": "t2", "toolName": "read"},
        )
    )
    assert summary.protocol_valid is False
    assert summary.parse_error == "invalid_tool_lifecycle"


def test_incomplete_tool_lifecycle_still_detected() -> None:
    summary = summarize_pi_json_stream(
        _stream(
            SESSION,
            AGENT_START,
            {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "read"},
            AGENT_END,
        )
    )
    assert summary.protocol_valid is False
    assert summary.parse_error == "incomplete_tool_lifecycle"
