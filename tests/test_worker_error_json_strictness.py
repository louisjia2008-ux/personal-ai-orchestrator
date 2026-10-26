"""Strict JSON decoding for structured stderr: ambiguous envelopes stay unknown."""

from __future__ import annotations

from personal_ai_orchestrator.worker_outcome_classifier import (
    WorkerFailureClass,
    classify_worker_failure,
)


def test_duplicate_top_level_key_is_unclassified() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"authentication_error"},'
            '"error":{"code":"insufficient_quota"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_escape_encoded_duplicate_key_is_unclassified() -> None:
    # "\\u0065rror" decodes to "error", duplicating the plain key above.
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota"},'
            '"\\u0065rror":{"code":"authentication_error"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_nested_duplicate_key_is_unclassified() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota","code":"authentication_error"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_deeply_nested_duplicate_key_is_unclassified() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"details":{"inner":{"code":"insufficient_quota",'
            '"code":"authentication_error"}}}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_finite_json_numbers_are_accepted() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota","status_code":429,'
            '"request_id":8675309,"retry_after":0.5}}',
        )
        is WorkerFailureClass.QUOTA_EXHAUSTED
    )


def test_nan_and_infinity_constants_are_unclassified() -> None:
    for constant in ("NaN", "Infinity", "-Infinity"):
        assert (
            classify_worker_failure(
                exit_code=1,
                stderr_tail='{"error":{"code":"insufficient_quota","status":' + constant + "}}",
            )
            is WorkerFailureClass.UNCLASSIFIED
        ), constant


def test_ambiguous_line_is_not_masked_by_later_valid_envelope() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"authentication_error"},'
            '"error":{"code":"insufficient_quota"}}\n'
            '{"error":{"code":"insufficient_quota"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )


def test_valid_envelope_still_classifies() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota","status_code":429}}',
        )
        is WorkerFailureClass.QUOTA_EXHAUSTED
    )


def test_nan_inside_string_value_is_fine() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota","message":"got NaN and Infinity"}}',
        )
        is WorkerFailureClass.QUOTA_EXHAUSTED
    )


def test_plain_labelled_stderr_still_uses_markers() -> None:
    assert (
        classify_worker_failure(exit_code=1, stderr_tail="api key rejected: quota exceeded")
        is WorkerFailureClass.QUOTA_EXHAUSTED
    )


def test_misleading_stdout_is_ignored() -> None:
    assert (
        classify_worker_failure(
            exit_code=1,
            stderr_tail='{"error":{"code":"insufficient_quota"}}',
            stdout_tail='{"error":{"code":"authentication_error"}}',
        )
        is WorkerFailureClass.QUOTA_EXHAUSTED
    )


def test_compatible_semantics_preserved() -> None:
    # stdout ignored, exit code 0/NONE, None/UNKNOWN unchanged.
    assert (
        classify_worker_failure(
            exit_code=0,
            stderr_tail='{"error":{"code":"authentication_error"},'
            '"error":{"code":"insufficient_quota"}}',
        )
        is WorkerFailureClass.NONE
    )
    assert (
        classify_worker_failure(
            exit_code=None,
            stderr_tail='{"error":{"code":"authentication_error"},'
            '"error":{"code":"insufficient_quota"}}',
        )
        is WorkerFailureClass.UNCLASSIFIED
    )
    assert classify_worker_failure(exit_code=1, stderr_tail="") is WorkerFailureClass.UNCLASSIFIED
