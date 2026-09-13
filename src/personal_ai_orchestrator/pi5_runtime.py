"""PI-5 bounded request transport for Pi workers.

The parser accepts only completed custom-tool lifecycles whose start arguments
match the structured tool result. It never starts another process or grants a
worker authority over provider/model/runtime selection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from personal_ai_orchestrator.pi5_contract import (
    PI5_MAX_REQUESTS_PER_PARENT_RUN,
    PI5_SCHEMA_VERSION,
    DelegationRequest,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized

PI5_TOOL_NAME = "pao_delegate"


@dataclass(frozen=True)
class DelegationExtraction:
    requests: tuple[DelegationRequest, ...]
    rejected_count: int = 0
    parse_error: str | None = None

    @property
    def valid(self) -> bool:
        return self.parse_error is None


def extract_requests(data: bytes) -> DelegationExtraction:
    """Extract accepted PI-5 requests from a Pi JSON event stream."""

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return DelegationExtraction((), parse_error="invalid_utf8")

    pending: dict[str, dict[str, object]] = {}
    requests: list[DelegationRequest] = []
    rejected_count = 0

    def invalid(code: str) -> DelegationExtraction:
        return DelegationExtraction(
            tuple(requests), rejected_count=rejected_count, parse_error=code
        )

    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return invalid("invalid_jsonl")
        if not isinstance(event, dict):
            return invalid("invalid_event_shape")

        event_type = event.get("type")
        tool_name = event.get("toolName")
        if event_type == "tool_execution_start" and tool_name == PI5_TOOL_NAME:
            tool_call_id = event.get("toolCallId")
            args = event.get("args")
            if (
                not isinstance(tool_call_id, str)
                or not isinstance(args, dict)
                or tool_call_id in pending
            ):
                return invalid("invalid_request_start")
            pending[tool_call_id] = args
            continue

        if event_type != "tool_execution_end" or tool_name != PI5_TOOL_NAME:
            continue

        tool_call_id = event.get("toolCallId")
        if not isinstance(tool_call_id, str) or tool_call_id not in pending:
            return invalid("invalid_request_lifecycle")
        start_args = pending.pop(tool_call_id)
        if event.get("isError") is True:
            rejected_count += 1
            continue

        result = event.get("result")
        details = result.get("details") if isinstance(result, dict) else None
        marker = details.get("paoDelegation") if isinstance(details, dict) else None
        if not isinstance(marker, dict):
            rejected_count += 1
            continue
        if marker.get("schemaVersion") != PI5_SCHEMA_VERSION:
            return invalid("request_schema_mismatch")
        if marker.get("accepted") is not True:
            rejected_count += 1
            continue
        if len(requests) >= PI5_MAX_REQUESTS_PER_PARENT_RUN:
            return invalid("request_budget_exceeded")

        payload = {
            "schema_version": marker.get("schemaVersion"),
            "tool_call_id": tool_call_id,
            "ordinal": marker.get("ordinal"),
            "intent": marker.get("intent"),
            "reason": marker.get("reason"),
        }
        if (
            start_args.get("intent") != payload["intent"]
            or start_args.get("reason") != payload["reason"]
        ):
            return invalid("request_payload_mismatch")
        try:
            request = DelegationRequest.model_validate(payload)
            assert_sanitized(request.model_dump(mode="json"))
        except (ValidationError, ValueError):
            return invalid("invalid_request")
        if request.ordinal != len(requests) + 1:
            return invalid("request_ordinal_mismatch")
        requests.append(request)

    if pending:
        return invalid("incomplete_request_lifecycle")
    return DelegationExtraction(tuple(requests), rejected_count=rejected_count)


__all__ = [
    "PI5_TOOL_NAME",
    "DelegationExtraction",
    "extract_requests",
]
