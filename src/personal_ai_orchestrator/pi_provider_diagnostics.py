"""Bounded provider error observations, without retaining provider-controlled text.

Only a validated Pi error channel may call this normalizer. The fingerprint is
of the bounded error sample, not the complete worker stream or HTTP response.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256

from personal_ai_orchestrator.worker_outcome_classifier import (
    AUTH_MARKERS,
    CONTEXT_MARKERS,
    OVERLOAD_MARKERS,
    QUOTA_EXHAUSTED_MARKERS,
    RATE_LIMIT_MARKERS,
    WorkerFailureClass,
    classify_worker_failure,
)

SAMPLE_LIMIT = 2048
PROVIDER_REASONS = frozenset(
    {
        "AUTH",
        "QUOTA_EXHAUSTED",
        "RATE_LIMITED",
        "SERVER_OVERLOADED",
        "CONTEXT_RESTRICTED",
        "REQUEST_TIMEOUT",
        "CONNECTION_FAILED",
        "STREAM_INCOMPLETE",
        "HTTP_ERROR",
        "CONFLICTING_ERROR",
        "UNCLASSIFIED",
        "NO_ERROR_DETAIL",
    }
)
PROVIDER_ERROR_CODES = frozenset(
    {
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
        "usage_limit_reached",
        "rate_limit_exceeded",
        "rate_limit_error",
        "too_many_requests",
        "overloaded_error",
        "server_overloaded",
        "service_unavailable",
        "context_length_exceeded",
        "context_window_exceeded",
        "prompt_too_long",
        "authentication_error",
        "authorization_error",
        "invalid_api_key",
        "unauthorized",
    }
)
PROVIDER_FIELDS = frozenset(
    {
        "pi_provider_error_reason",
        "pi_provider_http_status",
        "pi_provider_error_code",
        "pi_provider_error_sample_sha256",
        "pi_provider_error_sample_chars",
        "pi_provider_error_sample_at_limit",
    }
)
# These leading formats are emitted by Pi's installed provider-error formatter
# and the OpenAI-compatible SDK. Bare numbers elsewhere have no HTTP authority.
_STATUS = re.compile(
    r"^(?:(?:HTTP(?:/\d(?:\.\d)?)?|status(?: code)?)\s*[:=]?\s*)?"
    r"([45]\d\d)(?=\s*[:\-]|\s+status code\b|\s*$)",
    re.IGNORECASE,
)
_CONNECTION = re.compile(
    r"^(?:(?:TypeError|Error):\s*)?(?:fetch failed|network error|connection error|"
    r"connection refused|connection lost|socket hang up|other side closed|"
    r"read ECONNRESET|ECONNRESET|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|terminated)"
    r"(?=$|[\s:.;(])",
    re.IGNORECASE,
)
_TIMEOUT = re.compile(
    r"^(?:(?:TimeoutError|Error):\s*)?(?:request timed out|request timeout|"
    r"connection timed out|ETIMEDOUT|timeout)(?=$|[\s:.;(])",
    re.IGNORECASE,
)
_STREAM = re.compile(
    r"^(?:stream ended before (?:message_stop|a terminal response event)|"
    r"stream ended without|HTTP2 request did not get a response)(?=$|[\s:.;(])",
    re.IGNORECASE,
)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("ambiguous error record")
        record[key] = value
    return record


def _constant(_: str) -> object:
    raise ValueError("non-JSON error record")


def provider_error_diagnostics(message: str | None) -> dict[str, object]:
    """Return static reason/codes, bounded numeric observations and a sample hash."""
    sample = message[:SAMPLE_LIMIT] if isinstance(message, str) else ""
    result: dict[str, object] = {
        "pi_provider_error_reason": "NO_ERROR_DETAIL" if not sample else "UNCLASSIFIED",
        "pi_provider_error_sample_chars": len(sample),
        "pi_provider_error_sample_at_limit": len(sample) == SAMPLE_LIMIT,
    }
    if not sample:
        return result
    result["pi_provider_error_sample_sha256"] = sha256(
        sample.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    text = sample.strip()
    if not text:
        return result
    match = _STATUS.match(text)
    status = int(match[1]) if match else None
    if status is not None:
        result["pi_provider_http_status"] = status
    body = text[match.end() :].lstrip(" :\t-") if match else text
    record = None
    if body.startswith("{"):
        try:
            record = json.loads(body, object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, RecursionError):
            result["pi_provider_error_reason"] = "CONFLICTING_ERROR"
            return result
    if isinstance(record, dict):
        nested = record.get("error")
        nested = nested if isinstance(nested, dict) else record
        statuses = {status} if status is not None else set()
        for source in (record, nested):
            for key in ("status", "status_code", "statusCode"):
                value = source.get(key)
                if type(value) is int and 400 <= value <= 599:
                    statuses.add(value)
        if len(statuses) > 1:
            result["pi_provider_error_reason"] = "CONFLICTING_ERROR"
            result.pop("pi_provider_http_status", None)
            return result
        status = next(iter(statuses), None)
        codes = {
            value.lower()
            for key in ("code", "type")
            if isinstance(value := nested.get(key), str) and value.lower() in PROVIDER_ERROR_CODES
        }
        transport = dict(record)
        if status is not None:
            transport["status_code"] = status
        verdict = classify_worker_failure(exit_code=1, stderr_tail="", transport_error=transport)
        if verdict not in (WorkerFailureClass.NONE, WorkerFailureClass.UNCLASSIFIED):
            result["pi_provider_error_reason"] = verdict.value
        elif codes:
            result["pi_provider_error_reason"] = "CONFLICTING_ERROR"
            return result
        if len(codes) == 1:
            result["pi_provider_error_code"] = next(iter(codes))
    elif status is not None:
        verdict = classify_worker_failure(exit_code=1, stderr_tail=f"HTTP {status}")
        if verdict not in (WorkerFailureClass.NONE, WorkerFailureClass.UNCLASSIFIED):
            result["pi_provider_error_reason"] = verdict.value
    if status is not None:
        result["pi_provider_http_status"] = status
        if result["pi_provider_error_reason"] == "UNCLASSIFIED":
            result["pi_provider_error_reason"] = (
                "REQUEST_TIMEOUT" if status in (408, 504) else "HTTP_ERROR"
            )
    elif not isinstance(record, dict):
        if _TIMEOUT.match(text):
            result["pi_provider_error_reason"] = "REQUEST_TIMEOUT"
        elif _CONNECTION.match(text):
            result["pi_provider_error_reason"] = "CONNECTION_FAILED"
        elif _STREAM.match(text):
            result["pi_provider_error_reason"] = "STREAM_INCOMPLETE"
        else:
            leading = re.sub(r"^(?:Error|APIError):\s*", "", text.splitlines()[0])
            markers = (
                AUTH_MARKERS
                + CONTEXT_MARKERS
                + OVERLOAD_MARKERS
                + QUOTA_EXHAUSTED_MARKERS
                + RATE_LIMIT_MARKERS
            )
            if any(
                re.match(re.escape(marker) + r"(?!\w)", leading, re.IGNORECASE)
                for marker in markers
            ):
                verdict = classify_worker_failure(exit_code=1, stderr_tail=leading)
                if verdict not in (WorkerFailureClass.NONE, WorkerFailureClass.UNCLASSIFIED):
                    result["pi_provider_error_reason"] = verdict.value
    return result


def safe_provider_error_metadata(result: dict | object) -> dict[str, object]:
    """Revalidate the complete tuple at persistence; partial/corrupt details disappear."""
    if not isinstance(result, dict):
        return {}
    if (
        result.get("pi_diagnostic_code") != "PI_PROVIDER_ERROR"
        or result.get("pi_stop_reason") != "error"
        or result.get("output_truncated") is not False
        or result.get("timed_out") is not False
        or any(
            result.get(key) is not True
            for key in (
                "pi_stream_valid",
                "pi_session_seen",
                "pi_agent_start_seen",
                "pi_agent_end_seen",
            )
        )
    ):
        return {}
    reason = result.get("pi_provider_error_reason")
    count = result.get("pi_provider_error_sample_chars")
    at_limit = result.get("pi_provider_error_sample_at_limit")
    if (
        not isinstance(reason, str)
        or reason not in PROVIDER_REASONS
        or type(count) is not int
        or not 0 <= count <= SAMPLE_LIMIT
        or type(at_limit) is not bool
        or at_limit != (count == SAMPLE_LIMIT)
    ):
        return {}
    safe = {
        key: result[key]
        for key in (
            "pi_provider_error_reason",
            "pi_provider_error_sample_chars",
            "pi_provider_error_sample_at_limit",
        )
    }
    digest = result.get("pi_provider_error_sample_sha256")
    if count:
        if (
            reason == "NO_ERROR_DETAIL"
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            return {}
        safe["pi_provider_error_sample_sha256"] = digest
    elif reason != "NO_ERROR_DETAIL" or digest is not None:
        return {}
    status = result.get("pi_provider_http_status")
    if status is not None:
        if type(status) is not int or not 400 <= status <= 599 or not count:
            return {}
        safe["pi_provider_http_status"] = status
    code = result.get("pi_provider_error_code")
    if code is not None:
        if not isinstance(code, str) or code not in PROVIDER_ERROR_CODES or not count:
            return {}
        safe["pi_provider_error_code"] = code
    return safe
