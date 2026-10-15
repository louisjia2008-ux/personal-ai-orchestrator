"""Offline host-envelope, quota integration, and failure privacy regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.dispatch_executor import OwnerDispatchExecutor
from personal_ai_orchestrator.pi_dispatch_executor import PiOwnerDispatchExecutor
from personal_ai_orchestrator.pi_runtime import PI_PROTOCOL_ERROR_EXIT
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.worker_outcome_classifier import WorkerFailureClass
from tests.test_dispatch_executor import ExecutorHarness

TARGET_ID = "zai-coding-plan-glm-5.3"
CASES = [
    ("HTTP 429: quota exceeded", "QUOTA_EXHAUSTED", "USAGE_LIMIT"),
    ("HTTP 429: too many requests", "RATE_LIMITED", "WORKER_RATE_LIMIT"),
    ("HTTP 503: server overloaded", "SERVER_OVERLOADED", None),
    ("context length exceeded", "CONTEXT_RESTRICTED", None),
    ("HTTP 401: unauthorized", "AUTH", None),
    ("failed at line 429 in /private/example", "UNCLASSIFIED", None),
]


@pytest.mark.parametrize(("error", "expected", "reason"), CASES)
def test_windowed_dispatch_does_not_convert_transient_failures_to_exhaustion(
    tmp_path: Path, error: str, expected: str, reason: str | None
) -> None:
    harness = ExecutorHarness(tmp_path)
    request_id = harness.reserve()
    result = OwnerDispatchExecutor._host_result_envelope(
        1, b"private request body", error.encode(), truncated=False
    )
    assert result["worker_failure_class"] == expected
    store = SafetyKernelStore(harness.state_db)
    try:
        dispatch = store.get_owner_dispatch_by_request_id(request_id)
        harness.executor._classify_and_record_worker_outcome(
            store,
            dispatch=dispatch,
            execution_target_id=TARGET_ID,
            exit_code=1,
            worker_result=result,
        )
    finally:
        store.close()
    availability = harness.executor._quota_availability_journal.load(TARGET_ID)
    if reason is None:
        assert availability is None
    else:
        assert availability is not None
        assert availability.sanitized_reason_code == reason
        assert availability.consecutive_failures == 0
        assert (availability.exhaustion_observed_at is not None) == (expected == "QUOTA_EXHAUSTED")
        cooldown_seconds = (availability.cooldown_until - availability.observed_at).total_seconds()
        assert cooldown_seconds == (3600 if expected == "QUOTA_EXHAUSTED" else 900)
    evidence = harness.execution_evidence.latest_for_target(TARGET_ID)
    assert (evidence is not None) == (expected == "QUOTA_EXHAUSTED")
    if evidence is not None:
        assert evidence.result.value == "QUOTA_BLOCKED"
    assert not harness.executor.execution_supervisor.owned_task_ids()


def _pi_error_stream(message: str, *, stop_reason: str = "error") -> bytes:
    return "\n".join(
        json.dumps(event)
        for event in (
            {"type": "session"},
            {"type": "agent_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "provider": "fixture",
                    "model": "fixture-model",
                    "stopReason": stop_reason,
                    "errorMessage": message,
                    "content": [{"type": "text", "text": "quota exceeded"}],
                },
            },
            {"type": "agent_end"},
        )
    ).encode()


@pytest.mark.parametrize(("error", "expected", "reason"), CASES)
def test_pi_error_channel_classifies_without_exposing_raw_payloads(
    error: str, expected: str, reason: str | None
) -> None:
    private = "synthetic-secret /private/example user@example.invalid"
    result = PiOwnerDispatchExecutor._host_result_envelope(
        PI_PROTOCOL_ERROR_EXIT,
        _pi_error_stream(error + " " + private),
        b"",
        truncated=False,
    )
    assert result["worker_failure_class"] == expected
    assert result["pi_error_message"] == result["worker_failure_message"]
    rendered = json.dumps(result)
    for value in private.split():
        assert value not in rendered
    assert result["stdout_tail"] == ""
    assert result["stderr_tail"] == ""
    assert result["stdout_bytes"] > 0 and len(result["stdout_sha256"]) == 64


def test_nonerror_assistant_message_cannot_spoof_pi_quota() -> None:
    result = PiOwnerDispatchExecutor._host_result_envelope(
        PI_PROTOCOL_ERROR_EXIT,
        _pi_error_stream("quota exceeded", stop_reason="stop"),
        b"",
        truncated=False,
    )
    assert result["worker_failure_class"] == "UNCLASSIFIED"
    assert result["pi_error_message"] is None


def test_malformed_pi_stream_does_not_trust_partial_error() -> None:
    result = PiOwnerDispatchExecutor._host_result_envelope(
        PI_PROTOCOL_ERROR_EXIT,
        _pi_error_stream("quota exceeded") + b"\nnot-json",
        b"",
        truncated=False,
    )
    assert result["worker_failure_class"] == "UNCLASSIFIED"


def test_failed_plain_worker_transcript_is_not_exposed() -> None:
    result = OwnerDispatchExecutor._host_result_envelope(
        1, b"synthetic-secret", b"HTTP 503 /private/example Bearer synthetic-token", truncated=False
    )
    assert result["worker_failure_class"] == "SERVER_OVERLOADED"
    rendered = json.dumps(result)
    assert "synthetic-secret" not in rendered
    assert "synthetic-token" not in rendered
    assert "/private/example" not in rendered


def test_legacy_merged_failure_is_not_exhaustion(tmp_path: Path) -> None:
    harness = ExecutorHarness(tmp_path)
    assert (
        harness.executor._classify_worker_failure(
            exit_code=1,
            worker_result={
                "worker_failure_class": "QUOTA_OR_RATE_LIMIT",
                "stderr_tail": "quota exceeded",
            },
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


@pytest.mark.parametrize(("error", "expected", "reason"), CASES)
def test_failed_dispatch_persists_class_and_does_not_retry(
    tmp_path: Path, error: str, expected: str, reason: str | None
) -> None:
    harness = ExecutorHarness(tmp_path)
    harness.worker_bin.write_text("#!/bin/sh\nprintf '%s\\n' '" + error + "' >&2\nexit 1\n")
    request_id = harness.reserve()
    harness.run(request_id)
    harness.run(request_id)
    snapshot = harness.snapshot()
    try:
        assert snapshot["run_status"] == "FAILED"
        assert snapshot["task"].state.value == "BLOCKED"
        assert snapshot["writer_token"] is None
        rows = snapshot["store"].connection.execute("SELECT result_json FROM runs").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]["result_json"])["worker_failure_class"] == expected
        assert harness.main_unchanged()
    finally:
        snapshot["store"].close()


@pytest.mark.parametrize("truncated", [True, False])
def test_incomplete_pi_error_is_not_provider_evidence(truncated: bool) -> None:
    stream = _pi_error_stream("quota exceeded")
    if not truncated:
        stream = stream.rsplit(b"\n", 1)[0]
    result = PiOwnerDispatchExecutor._host_result_envelope(
        PI_PROTOCOL_ERROR_EXIT, stream, b"", truncated=truncated
    )
    assert result["worker_failure_class"] == "UNCLASSIFIED"


def test_failure_payload_reconstructs_messages_and_validates_metadata() -> None:
    from personal_ai_orchestrator.execution_controller import _failure_result_payload

    payload = _failure_result_payload(
        exit_code=1,
        signal=None,
        worker_result={
            "worker_failure_class": "SERVER_OVERLOADED",
            "worker_failure_message": "synthetic-secret /private/example",
            "stderr_tail": "synthetic-secret /private/example",
            "stdout_tail": "synthetic-secret /private/example",
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "/private/example",
            "stdout_bytes": True,
            "stderr_bytes": 2**64,
            "timed_out": "yes",
            "output_truncated": False,
        },
    )
    assert payload == {
        "exit_code": 1,
        "worker_failure_class": "SERVER_OVERLOADED",
        "worker_failure_message": "Provider service is temporarily overloaded.",
        "stdout_sha256": "a" * 64,
        "output_truncated": False,
    }


@pytest.mark.parametrize("recorded", ["NONE", "QUOTA_OR_RATE_LIMIT", "new-error", {"bad": "shape"}])
def test_failure_payload_does_not_trust_success_unknown_or_legacy_class(recorded: object) -> None:
    from personal_ai_orchestrator.execution_controller import _failure_result_payload

    payload = _failure_result_payload(
        exit_code=1,
        signal=None,
        worker_result={
            "worker_failure_class": recorded,
            "worker_failure_message": "quota exceeded",
        },
    )
    assert payload["worker_failure_class"] == "UNCLASSIFIED"
    assert "quota exceeded" not in json.dumps(payload)
