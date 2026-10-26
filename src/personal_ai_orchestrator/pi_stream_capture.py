"""Bounded streaming evidence for Pi, independent of retained transcript bytes.

Every input line is decoded strictly. Only the fields consumed by the existing
protocol validator survive the projection; assistant content, tool arguments
and results never enter it. The raw pipe is hashed while the supervisor keeps
its existing bounded prefix. Neither projection nor capture grants task authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from personal_ai_orchestrator.pi_runtime import (
    PiJsonRunSummary,
    _bounded_error_message,
    summarize_pi_json_stream,
)

PI_MAX_STREAM_BYTES = 64 * 1024 * 1024
PI_MAX_EVENT_BYTES = 4 * 1024 * 1024
PI_MAX_STREAM_EVENTS = 250_000
PI_MAX_PROJECTION_BYTES = 4 * 1024 * 1024
PI_MAX_IDENTITY_CHARS = 512
PI_STREAM_LIMIT_REASONS = frozenset(
    {
        "total_bytes",
        "line_bytes",
        "event_count",
        "projection_bytes",
        "identity_chars",
    }
)


def _reject_constant(_value: str):
    raise ValueError("non-standard JSON constant")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class PiStreamCapture:
    """One observer for one exact child; feed before the diagnostic prefix cap."""

    def __init__(self) -> None:
        self.observed_bytes = 0
        self.event_count = 0
        self.limit_reason: str | None = None
        self.summary: PiJsonRunSummary | None = None
        self.complete = False
        self._hash = hashlib.sha256()
        self._pending = bytearray()
        self._projection = bytearray()
        self._parse_error: str | None = None

    def _invalid(self, reason: str) -> None:
        if self._parse_error is None:
            self._parse_error = reason
        self._pending.clear()

    def _limit(self, reason: str) -> None:
        self.limit_reason = reason
        self._invalid("stream_limit_exceeded")

    def feed(self, chunk: bytes) -> bool:
        if self.summary is not None or self.limit_reason is not None:
            return False
        self.observed_bytes += len(chunk)
        self._hash.update(chunk)
        if self.observed_bytes > PI_MAX_STREAM_BYTES:
            self._limit("total_bytes")
            return False
        if self._parse_error is not None:
            # Keep the full observed hash/count and hard budget after a bad line,
            # but never accept a later event as a repair of that line.
            return True
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            if len(self._pending) + len(part) > PI_MAX_EVENT_BYTES:
                self._limit("line_bytes")
                return False
            self._pending.extend(part)
            if index < len(parts) - 1:
                line = bytes(self._pending)
                self._pending.clear()
                self._line(line)
                if self.limit_reason is not None:
                    return False
                if self._parse_error is not None:
                    return True
        return True

    def _identity(self, value):
        if not isinstance(value, str):
            return None
        if len(value) > PI_MAX_IDENTITY_CHARS:
            self._limit("identity_chars")
            return None
        return value

    def _line(self, raw: bytes) -> None:
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw:
            return
        try:
            event = json.loads(
                raw.decode("utf-8", errors="strict"),
                parse_constant=_reject_constant,
                object_pairs_hook=_strict_object,
            )
        except UnicodeDecodeError:
            self._invalid("invalid_utf8")
            return
        except (ValueError, RecursionError):
            self._invalid("invalid_jsonl")
            return
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self._invalid("invalid_event_shape")
            return
        self.event_count += 1
        if self.event_count > PI_MAX_STREAM_EVENTS:
            self._limit("event_count")
            return
        kind = event["type"]
        if self.event_count == 1 and kind != "session":
            self._invalid("missing_session_header")
            return
        projected: dict = {"type": kind}
        if kind == "agent_end":
            value = event.get("willRetry", False)
            projected["willRetry"] = value if type(value) is bool else None
        elif kind in {"auto_retry_start", "auto_retry_end"}:
            for key in ("attempt", "maxAttempts"):
                value = event.get(key)
                projected[key] = value if type(value) is int else None
            value = event.get("success")
            projected["success"] = value if type(value) is bool else None
        elif kind == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                return
            projected["message"] = {
                "role": "assistant",
                **{
                    key: self._identity(message.get(key))
                    for key in ("provider", "model", "stopReason")
                },
                "errorMessage": _bounded_error_message(message.get("errorMessage")),
            }
        elif kind in {"tool_execution_start", "tool_execution_end"}:
            projected["toolCallId"] = self._identity(event.get("toolCallId"))
        elif kind not in {"session", "agent_start", "extension_error"}:
            return
        if self.limit_reason is not None:
            return
        encoded = json.dumps(projected, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        if len(self._projection) + len(encoded) > PI_MAX_PROJECTION_BYTES:
            self._limit("projection_bytes")
            return
        self._projection.extend(encoded)

    def finish(self, *, eof: bool) -> PiJsonRunSummary:
        if self.summary is None:
            if self._parse_error is None and self._pending:
                self._line(bytes(self._pending))
            self._pending.clear()
            summary = summarize_pi_json_stream(bytes(self._projection))
            self._projection.clear()
            if summary.protocol_valid and self._parse_error is not None:
                summary = replace(summary, protocol_valid=False, parse_error=self._parse_error)
            if summary.protocol_valid and not eof:
                summary = replace(summary, protocol_valid=False, parse_error="invalid_jsonl")
            self.summary = replace(summary, event_count=self.event_count)
            self.complete = eof and self.limit_reason is None
        return self.summary

    def metadata(self) -> dict[str, object]:
        result: dict[str, object] = {
            "pi_stream_complete": self.complete,
            "pi_stream_observed_bytes": self.observed_bytes,
            "pi_stream_observed_sha256": self._hash.hexdigest(),
        }
        if self.limit_reason is not None:
            result["pi_stream_limit_reason"] = self.limit_reason
        return result
