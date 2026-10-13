"""Offline, synthetic failure taxonomy fixtures: no provider calls or private logs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.worker_outcome_classifier import (
    AUTH_MARKERS,
    CONTEXT_MARKERS,
    OVERLOAD_MARKERS,
    QUOTA_EXHAUSTED_MARKERS,
    RATE_LIMIT_MARKERS,
    WorkerFailureClass,
    classify_worker_failure,
    worker_failure_diagnostics,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/worker_failures/classifications.json").read_text()
)


@pytest.mark.parametrize("fixture", FIXTURES, ids=[item["id"] for item in FIXTURES])
def test_failure_fixtures(fixture: dict[str, str]) -> None:
    args = {"exit_code": 1, "stderr_tail": fixture["stderr"]}
    first = classify_worker_failure(**args)
    assert first.value == fixture["expected"]
    assert classify_worker_failure(**args) is first
    assert (
        classify_worker_failure(exit_code=0, stderr_tail=fixture["stderr"])
        is WorkerFailureClass.NONE
    )
    assert (
        classify_worker_failure(exit_code=None, stderr_tail=fixture["stderr"])
        is WorkerFailureClass.UNCLASSIFIED
    )


@pytest.mark.parametrize(
    ("markers", "expected"),
    [
        (QUOTA_EXHAUSTED_MARKERS, WorkerFailureClass.QUOTA_EXHAUSTED),
        (RATE_LIMIT_MARKERS, WorkerFailureClass.RATE_LIMITED),
        (OVERLOAD_MARKERS, WorkerFailureClass.SERVER_OVERLOADED),
        (CONTEXT_MARKERS, WorkerFailureClass.CONTEXT_RESTRICTED),
        (AUTH_MARKERS, WorkerFailureClass.AUTH),
    ],
)
def test_conservative_marker_groups(markers: tuple[str, ...], expected: WorkerFailureClass) -> None:
    assert isinstance(markers, tuple)
    assert "quota" not in markers
    for marker in markers:
        assert marker and marker.lower() == marker
        assert classify_worker_failure(exit_code=1, stderr_tail=marker) is expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": "insufficient_quota", "status_code": 429}, WorkerFailureClass.QUOTA_EXHAUSTED),
        ({"status_code": 429}, WorkerFailureClass.RATE_LIMITED),
        ({"status_code": 503}, WorkerFailureClass.SERVER_OVERLOADED),
        ({"code": "context_length_exceeded"}, WorkerFailureClass.CONTEXT_RESTRICTED),
        ({"type": "authentication_error"}, WorkerFailureClass.AUTH),
    ],
)
def test_structured_transport_precedes_stderr(
    error: dict[str, object], expected: WorkerFailureClass
):
    assert (
        classify_worker_failure(exit_code=1, stderr_tail="quota exceeded", transport_error=error)
        is expected
    )


def test_stdout_has_no_failure_authority() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail="worker exited with status 1",
            stdout_tail='{"error":{"code":"insufficient_quota"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_conflicting_structured_envelopes_stay_unknown() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota"}}\n'
            '{"error":{"type":"authentication_error"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


@pytest.mark.parametrize("payload", ["[" * 10000, "1" * 5000, "null", "true", '"429"'])
def test_malformed_or_unsupported_structured_fields_stay_unknown(payload: str) -> None:
    assert (
        classify_worker_failure(exit_code=1, stderr_tail='{"error":{"status":' + payload + "}}")
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_diagnostics_only_expose_allowlisted_class_and_message() -> None:
    verdict = classify_worker_failure(
        exit_code=1,
        stderr_tail="HTTP 503: private request /private/example body synthetic-secret",
    )
    result = worker_failure_diagnostics(verdict)
    assert result == {
        "worker_failure_class": "SERVER_OVERLOADED",
        "worker_failure_message": "Provider service is temporarily overloaded.",
    }
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "error",
    [
        {"code": "insufficient_quota", "status": 503},
        {"code": "context_length_exceeded", "type": "authentication_error"},
        {"error": {"status": 401}, "status": 503},
        {"code": "rate_limit_error", "status": 503},
        {"error": {"status": 429}, "status": 503},
        {"code": "authentication_error", "type": "rate_limit_error"},
    ],
)
def test_conflicting_structured_fields_cannot_establish_exhaustion(
    error: dict[str, object],
) -> None:
    assert (
        classify_worker_failure(exit_code=1, stderr_tail="quota exceeded", transport_error=error)
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_arbitrary_json_output_object_is_not_a_transport_record() -> None:
    assert (
        classify_worker_failure(
            exit_code=1, stderr_tail='{"content":{"error":{"code":"insufficient_quota"}}}'
        )
        is WorkerFailureClass.UNCLASSIFIED
    )
