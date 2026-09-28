from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.pi5_contract import (
    PI5_MAX_REQUESTS_PER_PARENT_RUN,
    DelegationRequest,
)
from personal_ai_orchestrator.pi5_runtime import extract_requests
from personal_ai_orchestrator.pi5_tool import PI5_TOOL_SOURCE, seed_pi5_tool


def _line(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _accepted_start(call_id: str, intent: str, reason: str) -> bytes:
    return _line(
        {
            "type": "tool_execution_start",
            "toolCallId": call_id,
            "toolName": "pao_delegate",
            "args": {"intent": intent, "reason": reason},
        }
    )


def _accepted_end(call_id: str, ordinal: int, intent: str, reason: str) -> bytes:
    return _line(
        {
            "type": "tool_execution_end",
            "toolCallId": call_id,
            "toolName": "pao_delegate",
            "isError": False,
            "result": {
                "content": [{"type": "text", "text": "recorded"}],
                "details": {
                    "paoDelegation": {
                        "schemaVersion": 1,
                        "accepted": True,
                        "ordinal": ordinal,
                        "intent": intent,
                        "reason": reason,
                    }
                },
            },
        }
    )


def test_request_contract_forbids_runtime_selection_fields() -> None:
    with pytest.raises(ValidationError):
        DelegationRequest.model_validate(
            {
                "schema_version": 1,
                "tool_call_id": "call-1",
                "ordinal": 1,
                "intent": "Inspect the parser behavior",
                "reason": "Independent review would help",
                "provider_id": "minimax-cn",
            }
        )


def test_request_contract_rejects_blank_intent() -> None:
    with pytest.raises(ValidationError):
        DelegationRequest(
            tool_call_id="call-1",
            ordinal=1,
            intent="   ",
            reason="Need independent review",
        )


def test_tool_source_is_non_executing_and_bounded(tmp_path: Path) -> None:
    lowered = PI5_TOOL_SOURCE.lower()
    assert "registertool" in lowered
    assert "node:child_process" not in lowered
    assert "spawn(" not in lowered
    assert "exec(" not in lowered
    assert str(PI5_MAX_REQUESTS_PER_PARENT_RUN) in PI5_TOOL_SOURCE

    target = seed_pi5_tool(tmp_path)
    first = target.read_text(encoding="utf-8")
    target.write_text("weakened", encoding="utf-8")
    seed_pi5_tool(tmp_path)
    assert target.read_text(encoding="utf-8") == first


def test_extracts_one_completed_request() -> None:
    data = b"\n".join(
        (
            _line({"type": "session", "version": 3}),
            _accepted_start("call-1", "Inspect module A", "Parallel analysis helps"),
            _accepted_end("call-1", 1, "Inspect module A", "Parallel analysis helps"),
        )
    )

    outcome = extract_requests(data)

    assert outcome.valid is True
    assert outcome.rejected_count == 0
    assert len(outcome.requests) == 1
    request = outcome.requests[0]
    assert request.tool_call_id == "call-1"
    assert request.ordinal == 1
    assert request.intent == "Inspect module A"


def test_rejected_tool_result_does_not_create_request() -> None:
    data = b"\n".join(
        (
            _accepted_start("call-1", "Inspect module A", "Need help"),
            _line(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "call-1",
                    "toolName": "pao_delegate",
                    "isError": False,
                    "result": {
                        "details": {
                            "paoDelegation": {
                                "schemaVersion": 1,
                                "accepted": False,
                                "reason": "budget_exhausted",
                            }
                        }
                    },
                }
            ),
        )
    )

    outcome = extract_requests(data)

    assert outcome.valid is True
    assert outcome.requests == ()
    assert outcome.rejected_count == 1


def test_start_and_end_payload_mismatch_fails_closed() -> None:
    data = b"\n".join(
        (
            _accepted_start("call-1", "Inspect module A", "Need help"),
            _accepted_end("call-1", 1, "Inspect module B", "Need help"),
        )
    )

    outcome = extract_requests(data)

    assert outcome.valid is False
    assert outcome.parse_error == "request_payload_mismatch"


def test_fourth_accepted_request_exceeds_host_budget() -> None:
    events: list[bytes] = []
    for index in range(1, PI5_MAX_REQUESTS_PER_PARENT_RUN + 2):
        call_id = f"call-{index}"
        intent = f"Inspect module {index}"
        events.extend(
            (
                _accepted_start(call_id, intent, "Need bounded review"),
                _accepted_end(call_id, index, intent, "Need bounded review"),
            )
        )

    outcome = extract_requests(b"\n".join(events))

    assert outcome.valid is False
    assert outcome.parse_error == "request_budget_exceeded"


def test_incomplete_request_lifecycle_fails_closed() -> None:
    outcome = extract_requests(_accepted_start("call-1", "Inspect module A", "Need help"))

    assert outcome.valid is False
    assert outcome.parse_error == "incomplete_request_lifecycle"
