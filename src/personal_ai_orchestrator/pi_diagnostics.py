"""Transcript-free Pi diagnostics. These fields never authorize execution."""

from __future__ import annotations

import re
from collections.abc import Mapping

from personal_ai_orchestrator.pi_provider_diagnostics import safe_provider_error_metadata
from personal_ai_orchestrator.pi_runtime import PiJsonRunSummary
from personal_ai_orchestrator.pi_stream_capture import PI_STREAM_LIMIT_REASONS

PI_PARSE_ERRORS = frozenset(
    {
        "invalid_utf8",
        "invalid_jsonl",
        "invalid_event_shape",
        "missing_session_header",
        "invalid_tool_lifecycle",
        "incomplete_tool_lifecycle",
        "invalid_agent_lifecycle",
        "stream_limit_exceeded",
    }
)
PI_DIAGNOSTIC_CODES = frozenset(
    {
        *(f"PI_{value.upper()}" for value in PI_PARSE_ERRORS),
        "PI_COMPLETE",
        "PI_TIMEOUT",
        "PI_OUTPUT_TRUNCATED",
        "PI_PROCESS_EXIT",
        "PI_MISSING_SESSION",
        "PI_MISSING_AGENT_START",
        "PI_MISSING_AGENT_END",
        "PI_EXTENSION_ERROR",
        "PI_MISSING_ASSISTANT_RESULT",
        "PI_PROVIDER_ERROR",
        "PI_ABORTED",
        "PI_UNEXPECTED_STOP_REASON",
        "PI_MODEL_MISMATCH",
        "PI_EXPECTED_MODEL_UNAVAILABLE",
        "PI_DIAGNOSTICS_UNAVAILABLE",
    }
)
PI_STOP_REASONS = frozenset({"stop", "error", "aborted", "length", "toolUse"})


def pi_diagnostic_code(
    summary: PiJsonRunSummary,
    *,
    expected_model_ref: str | None,
    process_exit_code: int | None,
    truncated: bool,
    timeout: bool,
) -> str:
    if timeout:
        return "PI_TIMEOUT"
    if truncated:
        return "PI_OUTPUT_TRUNCATED"
    if process_exit_code not in (None, 0):
        return "PI_PROCESS_EXIT"
    if summary.parse_error in PI_PARSE_ERRORS:
        return f"PI_{summary.parse_error.upper()}"
    if not summary.session_seen:
        return "PI_MISSING_SESSION"
    if not summary.agent_start_seen:
        return "PI_MISSING_AGENT_START"
    if not summary.agent_end_seen:
        return "PI_MISSING_AGENT_END"
    if summary.extension_error_count:
        return "PI_EXTENSION_ERROR"
    if summary.final_stop_reason == "error":
        return "PI_PROVIDER_ERROR"
    if summary.final_stop_reason == "aborted":
        return "PI_ABORTED"
    if summary.final_provider is None or summary.final_model is None:
        return "PI_MISSING_ASSISTANT_RESULT"
    if summary.final_stop_reason != "stop":
        return "PI_UNEXPECTED_STOP_REASON"
    if expected_model_ref is None:
        return "PI_EXPECTED_MODEL_UNAVAILABLE"
    if not summary.matches_model_ref(expected_model_ref):
        return "PI_MODEL_MISMATCH"
    return "PI_COMPLETE"


def safe_pi_failure_metadata(result: Mapping[str, object]) -> dict[str, object]:
    """Revalidate the persistence boundary; never copy provider-controlled text."""
    if result.get("runtime") != "pi-json":
        return {}
    safe: dict[str, object] = {"runtime": "pi-json"}
    code = result.get("pi_diagnostic_code")
    if isinstance(code, str) and code in PI_DIAGNOSTIC_CODES:
        safe["pi_diagnostic_code"] = code
    parse_error = result.get("pi_parse_error")
    if isinstance(parse_error, str) and parse_error in PI_PARSE_ERRORS:
        safe["pi_parse_error"] = parse_error
    stop = result.get("pi_stop_reason")
    if isinstance(stop, str) and stop in PI_STOP_REASONS:
        safe["pi_stop_reason"] = stop
    for name in (
        "pi_protocol_valid",
        "pi_stream_valid",
        "pi_session_seen",
        "pi_agent_start_seen",
        "pi_agent_end_seen",
        "pi_model_matches",
        "pi_stream_complete",
        "pi_stdout_capture_truncated",
        "pi_stderr_capture_truncated",
    ):
        value = result.get(name)
        if type(value) is bool:
            safe[name] = value
    for name in (
        "pi_event_count",
        "pi_tool_start_count",
        "pi_tool_end_count",
        "pi_extension_error_count",
        "pi_stream_observed_bytes",
    ):
        value = result.get(name)
        if type(value) is int and 0 <= value <= 2**31 - 1:
            safe[name] = value
    digest = result.get("pi_stream_observed_sha256")
    if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
        safe["pi_stream_observed_sha256"] = digest
    reason = result.get("pi_stream_limit_reason")
    if isinstance(reason, str) and reason in PI_STREAM_LIMIT_REASONS:
        safe["pi_stream_limit_reason"] = reason
    process_code = result.get("worker_process_exit_code")
    if type(process_code) is int and -255 <= process_code <= 255:
        safe["worker_process_exit_code"] = process_code
    safe.update(safe_provider_error_metadata(dict(result)))
    return safe
