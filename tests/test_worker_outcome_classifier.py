"""M1 WP4 — worker_outcome_classifier tests.

The classifier is the single hook that replaces shadow's old
``("usage limit", "quota", "rate limit")`` text match (the bare
``"quota"`` token was the source of the regression risk the user
flagged in commit 3 of M1 WP4). Tests pin the conservative
marker list: ``"quota"`` alone must NOT trip the classifier even
though it would have tripped shadow's old list. ``429`` and
``"too many requests"`` must trip.
"""

from __future__ import annotations

import pytest

from personal_ai_orchestrator.worker_outcome_classifier import (
    AUTH_MARKERS,
    QUOTA_OR_RATE_LIMIT_MARKERS,
    WorkerFailureClass,
    classify_worker_failure,
)


def test_exit_zero_is_always_none() -> None:
    assert classify_worker_failure(exit_code=0, stderr_tail="anything") is WorkerFailureClass.NONE
    assert classify_worker_failure(exit_code=0, stderr_tail="") is WorkerFailureClass.NONE


def test_bare_quota_word_does_not_match() -> None:
    """A line containing only the bare word ``quota`` stays UNCLASSIFIED.

    This is the regression the user called out: shadow's old
    list matched the bare ``quota`` substring, and the project's
    own source mentions ``quota`` 100+ times. The new list
    matches only phrased versions like ``"quota exceeded"`` or
    ``"insufficient quota"``.
    """

    verdict = classify_worker_failure(
        exit_code=1,
        stderr_tail=(
            "Traceback (most recent call last):\n"
            '  File "/Users/<user>/personal_ai_orchestrator/src/personal_ai_orchestrator/'
            'quota_availability.py", line 42, in <module>\n'
            '    raise ValueError("quota pool id is empty")\n'
        ),
    )
    assert verdict is WorkerFailureClass.UNCLASSIFIED


def test_quota_or_rate_limit_marker_list_is_conservative() -> None:
    """The marker list does not include the bare ``"quota"`` token."""

    assert "quota" not in QUOTA_OR_RATE_LIMIT_MARKERS
    # Each marker must be a non-empty lowercase string.
    for marker in QUOTA_OR_RATE_LIMIT_MARKERS:
        assert marker
        assert marker == marker.lower()


def test_classifier_trips_on_canonical_rate_limit_phrases() -> None:
    """Each canonical phrase returns ``QUOTA_OR_RATE_LIMIT``."""

    for marker in QUOTA_OR_RATE_LIMIT_MARKERS:
        verdict = classify_worker_failure(exit_code=1, stderr_tail=f"upstream replied: {marker}\n")
        assert verdict is WorkerFailureClass.QUOTA_OR_RATE_LIMIT, marker


def test_classifier_trips_on_429_alone() -> None:
    """The bare ``429`` HTTP code in stderr trips the classifier."""

    verdict = classify_worker_failure(
        exit_code=1,
        stderr_tail="opencode: HTTP 429 - too many requests",
    )
    assert verdict is WorkerFailureClass.QUOTA_OR_RATE_LIMIT


def test_classifier_is_case_insensitive() -> None:
    """Uppercase markers still trip (worker stderr may be unbuffered)."""

    verdict = classify_worker_failure(exit_code=1, stderr_tail="RATE LIMIT EXCEEDED")
    assert verdict is WorkerFailureClass.QUOTA_OR_RATE_LIMIT


def test_classifier_trips_on_auth_markers() -> None:
    """The 401 / 403 / unauthorized phrases return ``AUTH``."""

    for marker in AUTH_MARKERS:
        verdict = classify_worker_failure(exit_code=1, stderr_tail=f"upstream replied: {marker}\n")
        assert verdict is WorkerFailureClass.AUTH, marker


def test_classifier_quota_takes_precedence_over_auth() -> None:
    """When both kinds of markers are present, quota wins.

    Quota exhaustion is a more urgent signal than auth because
    the next observation window is shorter; the worker might
    re-auth on the next attempt, but the rate-limit cooldown
    is fixed.
    """

    verdict = classify_worker_failure(
        exit_code=1,
        stderr_tail="error 401: rate limit exceeded",
    )
    assert verdict is WorkerFailureClass.QUOTA_OR_RATE_LIMIT


def test_classifier_only_inspects_stderr_not_stdout() -> None:
    """``stdout`` matching a marker does NOT trip the classifier.

    Many models print ``"quota exceeded"`` in plain prose (the
    rate-limit recovery instructions OpenCode Zen's worker
    surfaces in the prompt). The classifier reads
    ``stderr_tail`` only.
    """

    verdict = classify_worker_failure(
        exit_code=1,
        stdout_tail="quota exceeded — please wait and retry",
        stderr_tail="worker exited with status 1",
    )
    assert verdict is WorkerFailureClass.UNCLASSIFIED


def test_classifier_empty_stderr_on_failure_is_unclassified() -> None:
    """A non-zero exit with no stderr is UNCLASSIFIED, not a false-positive quota."""

    verdict = classify_worker_failure(exit_code=1, stderr_tail="")
    assert verdict is WorkerFailureClass.UNCLASSIFIED


def test_classifier_pure_function() -> None:
    """The classifier has no side effects; calling it twice is a no-op.

    The classification result depends only on its arguments —
    no globals, no clock, no I/O. This test pins that contract
    so a future refactor cannot accidentally introduce hidden
    state.
    """

    args = {"exit_code": 1, "stderr_tail": "rate limit exceeded"}
    first = classify_worker_failure(**args)
    second = classify_worker_failure(**args)
    assert first is second
    assert first is WorkerFailureClass.QUOTA_OR_RATE_LIMIT


def test_classifier_marker_lists_are_frozen() -> None:
    """Module-level marker tuples are immutable so a runner cannot mutate them."""

    with pytest.raises((AttributeError, TypeError)):
        QUOTA_OR_RATE_LIMIT_MARKERS[0] = "quota"  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        AUTH_MARKERS[0] = "auth"  # type: ignore[index]
