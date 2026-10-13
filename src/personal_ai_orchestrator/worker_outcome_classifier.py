"""Bounded worker-failure diagnostics, without execution or retry authority.

Recognized transport error fields take precedence over conservative stderr
phrases; conflicting specific transport fields remain unclassified. A 429 is
a transient throttle unless explicit quota evidence exists; 503/529 indicate server
overload, not account exhaustion.
Ordinary model stdout is never inspected. Unknown evidence stays UNCLASSIFIED.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum


class WorkerFailureClass(StrEnum):
    NONE = "NONE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_OVERLOADED = "SERVER_OVERLOADED"
    CONTEXT_RESTRICTED = "CONTEXT_RESTRICTED"
    AUTH = "AUTH"
    UNCLASSIFIED = "UNCLASSIFIED"


QUOTA_EXHAUSTED_MARKERS: tuple[str, ...] = (
    "quota exceeded",
    "insufficient quota",
    "quota exhausted",
    "usage limit reached",
    "usage limit exceeded",
    "hit your usage limit",
)
RATE_LIMIT_MARKERS: tuple[str, ...] = ("rate limit", "rate-limit", "too many requests")
OVERLOAD_MARKERS: tuple[str, ...] = (
    "server overloaded",
    "server is overloaded",
    "service overloaded",
    "overloaded_error",
    "service unavailable",
)
CONTEXT_MARKERS: tuple[str, ...] = (
    "context length exceeded",
    "context window exceeded",
    "maximum context length",
    "prompt is too long",
    "input is too long",
    "context_length_exceeded",
)
AUTH_MARKERS: tuple[str, ...] = ("unauthorized", "not logged in", "invalid api key")

_CODE_CLASSES = {
    "insufficient_quota": WorkerFailureClass.QUOTA_EXHAUSTED,
    "quota_exceeded": WorkerFailureClass.QUOTA_EXHAUSTED,
    "quota_exhausted": WorkerFailureClass.QUOTA_EXHAUSTED,
    "usage_limit_reached": WorkerFailureClass.QUOTA_EXHAUSTED,
    "rate_limit_exceeded": WorkerFailureClass.RATE_LIMITED,
    "rate_limit_error": WorkerFailureClass.RATE_LIMITED,
    "too_many_requests": WorkerFailureClass.RATE_LIMITED,
    "overloaded_error": WorkerFailureClass.SERVER_OVERLOADED,
    "server_overloaded": WorkerFailureClass.SERVER_OVERLOADED,
    "service_unavailable": WorkerFailureClass.SERVER_OVERLOADED,
    "context_length_exceeded": WorkerFailureClass.CONTEXT_RESTRICTED,
    "context_window_exceeded": WorkerFailureClass.CONTEXT_RESTRICTED,
    "prompt_too_long": WorkerFailureClass.CONTEXT_RESTRICTED,
    "authentication_error": WorkerFailureClass.AUTH,
    "authorization_error": WorkerFailureClass.AUTH,
    "invalid_api_key": WorkerFailureClass.AUTH,
    "unauthorized": WorkerFailureClass.AUTH,
}
_STATUS_CLASSES = {
    401: WorkerFailureClass.AUTH,
    403: WorkerFailureClass.AUTH,
    429: WorkerFailureClass.RATE_LIMITED,
    503: WorkerFailureClass.SERVER_OVERLOADED,
    529: WorkerFailureClass.SERVER_OVERLOADED,
}
# Bare digits embedded in a path, timestamp, request ID, or source line do not
# establish an HTTP response. Require a transport label or a standalone code.
_HTTP_STATUS_RE = re.compile(
    r"\b(?:http(?:/\d(?:\.\d)?)?\s+|status(?:\s+code)?\s*[:=]?\s*)"
    r"(401|403|429|503|529)\b|^\s*(401|403|429|503|529)(?:\s*[:\-]|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_MAX_DIAGNOSTIC_CHARS = 8192
_MESSAGES = {
    WorkerFailureClass.NONE: "Worker exited successfully; verification is separate.",
    WorkerFailureClass.QUOTA_EXHAUSTED: "Provider reported quota exhaustion.",
    WorkerFailureClass.RATE_LIMITED: "Provider temporarily rate limited the request.",
    WorkerFailureClass.SERVER_OVERLOADED: "Provider service is temporarily overloaded.",
    WorkerFailureClass.CONTEXT_RESTRICTED: "Request exceeds the model context limit.",
    WorkerFailureClass.AUTH: "Provider authentication or access failed.",
    WorkerFailureClass.UNCLASSIFIED: "Worker failed; the cause is not established.",
}


def worker_failure_diagnostics(verdict: WorkerFailureClass) -> dict[str, str]:
    """Allowlisted UI fields only: never echo provider payloads, paths, or keys."""
    return {
        "worker_failure_class": verdict.value,
        "worker_failure_message": _MESSAGES[verdict],
    }


def _structured_class(error: Mapping[str, object]) -> WorkerFailureClass | None:
    # Adapters may pass a normalized transport record or an explicit provider
    # error envelope. Do not search arbitrary nested JSON or assistant content.
    body = error.get("error")
    body = body if isinstance(body, Mapping) else error
    specific: set[WorkerFailureClass] = set()
    for name in ("code", "type"):
        value = body.get(name)
        if isinstance(value, str) and value.lower() in _CODE_CLASSES:
            specific.add(_CODE_CLASSES[value.lower()])
    statuses: set[WorkerFailureClass] = set()
    for source in (body, error):
        for name in ("status_code", "statusCode", "status"):
            status = source.get(name)
            if type(status) is int and status in _STATUS_CLASSES:
                statuses.add(_STATUS_CLASSES[status])
    if len(statuses) > 1:
        return WorkerFailureClass.UNCLASSIFIED
    if len(specific) > 1 and WorkerFailureClass.AUTH not in specific:
        specific.discard(WorkerFailureClass.RATE_LIMITED)
    if len(specific) > 1:
        return WorkerFailureClass.UNCLASSIFIED
    detail = next(iter(specific), None)
    status = next(iter(statuses), None)
    if detail is not None and status is not None and detail is not status:
        if status is not WorkerFailureClass.RATE_LIMITED or detail is WorkerFailureClass.AUTH:
            return WorkerFailureClass.UNCLASSIFIED
    return detail or status


def _structured_stderr_verdict(text: str) -> WorkerFailureClass | None:
    verdicts: set[WorkerFailureClass] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("error"), dict):
            continue
        verdict = _structured_class(record)
        if verdict is not None:
            verdicts.add(verdict)
    if len(verdicts) > 1:
        return WorkerFailureClass.UNCLASSIFIED
    return next(iter(verdicts), None)


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", text) for marker in markers)


def classify_worker_failure(
    *,
    exit_code: int | None,
    stderr_tail: str,
    stdout_tail: str = "",
    transport_error: Mapping[str, object] | None = None,
) -> WorkerFailureClass:
    """Classify completed failures; stdout is intentionally ignored.

    ``transport_error`` must come from an adapter's error channel, never an
    assistant/tool output object. Exact known code/type fields are preferred;
    structured stderr must be a whole JSON error envelope. No raw input is
    returned. Missing exit evidence is not a completed provider failure.
    """
    if exit_code == 0:
        return WorkerFailureClass.NONE
    if exit_code is None:
        return WorkerFailureClass.UNCLASSIFIED
    if transport_error is not None:
        verdict = _structured_class(transport_error)
        if verdict is not None:
            return verdict
    text = stderr_tail[-_MAX_DIAGNOSTIC_CHARS:]
    verdict = _structured_stderr_verdict(text)
    if verdict is not None:
        return verdict
    text = text.lower()
    classes = {
        verdict
        for verdict, markers in (
            (WorkerFailureClass.QUOTA_EXHAUSTED, QUOTA_EXHAUSTED_MARKERS),
            (WorkerFailureClass.RATE_LIMITED, RATE_LIMIT_MARKERS),
            (WorkerFailureClass.SERVER_OVERLOADED, OVERLOAD_MARKERS),
            (WorkerFailureClass.CONTEXT_RESTRICTED, CONTEXT_MARKERS),
            (WorkerFailureClass.AUTH, AUTH_MARKERS),
        )
        if _has_marker(text, markers)
    }
    classes.update(
        _STATUS_CLASSES[int(match.group(1) or match.group(2))]
        for match in _HTTP_STATUS_RE.finditer(text)
    )
    # A generic throttle may accompany a more specific quota/overload/context
    # response. Conflicting specific failure kinds cannot establish a cause.
    if len(classes) > 1:
        classes.discard(WorkerFailureClass.RATE_LIMITED)
    if len(classes) == 1:
        return classes.pop()
    return WorkerFailureClass.UNCLASSIFIED


__all__ = [
    "AUTH_MARKERS",
    "CONTEXT_MARKERS",
    "OVERLOAD_MARKERS",
    "QUOTA_EXHAUSTED_MARKERS",
    "RATE_LIMIT_MARKERS",
    "WorkerFailureClass",
    "classify_worker_failure",
    "worker_failure_diagnostics",
]
