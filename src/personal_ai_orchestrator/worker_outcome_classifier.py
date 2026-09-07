"""M1 WP4 — worker-failure classifier.

The owner-dispatch path (``dispatch_executor``) runs each
``opencode run`` invocation through a worker. Today the only
quota-related signal on that path is the text of the worker's
``stderr`` — the legacy ``shadow_campaign_runner`` does the same
text-match dance, but only for shadow campaigns.

``classify_worker_failure`` is the single hook that replaces both
the shadow list and the lack-of-classification on the owner
path. It runs **only on non-zero worker exits** and reads **only
``stderr_tail``** — never ``stdout`` — so a single hit is
deterministic and the false-positive surface is bounded.

Marker lists are deliberately conservative. ``"quota"`` as a
substring matches this project's own source code; matching
``"quota"`` would lock out every dispatch the moment any other
worker happened to log the word. The list below is the union of
what OpenCode Zen's worker actually emits, what ``429`` HTTP
transports commonly log, and what rate-limit-aware providers
expose as a server message. ``"usage limit"`` and
``"rate limit"`` are the two phrases the upstream docs call out
explicitly; ``"429````,``too many requests````,````insufficient quota``/``exceeded`` round out the canonical set.

A future M2 migration to typed errors from ``opencode serve``
will replace this module's text match with a structured field
on the worker result; the dispatch executor's call site stays
the same because the call shape does not depend on how the
verdict was produced.
"""

from __future__ import annotations

from enum import StrEnum


class WorkerFailureClass(StrEnum):
    """Worker-side failure verdict.

    ``NONE`` means the worker exited 0 and no further action is
    needed. ``QUOTA_OR_RATE_LIMIT`` triggers the dispatch
    executor's worker-classified throttle hook (commit 3 wires
    this into ``record_run_outcome``). ``AUTH`` triggers the
    auth-failure path. ``UNCLASSIFIED`` is the default for any
    non-zero exit whose stderr does not match a marker — the
    owner sees a generic failure on the run view.
    """

    NONE = "NONE"
    QUOTA_OR_RATE_LIMIT = "QUOTA_OR_RATE_LIMIT"
    AUTH = "AUTH"
    UNCLASSIFIED = "UNCLASSIFIED"


#: Markers that identify a quota or rate-limit verdict.
#: Conservative on purpose: ``"quota"`` alone would match this
#: project's own source files and lock out every dispatch. Only
#: phrased versions of the canonical server messages are
#: recognised. Match is case-insensitive (lowercased at the
#: call site).
QUOTA_OR_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit",
    "rate limit",
    "too many requests",
    "quota exceeded",
    "insufficient quota",
    "429",
)


#: Markers that identify an authentication verdict. ``"auth"``
#: alone is too broad (worker binaries log the word for
#: unrelated reasons); we use ``"unauthorized"``, the HTTP
#: codes, and ``"not logged in"``.
AUTH_MARKERS: tuple[str, ...] = (
    "unauthorized",
    "401",
    "403",
    "not logged in",
)


def classify_worker_failure(
    *,
    exit_code: int | None,
    stderr_tail: str,
    stdout_tail: str = "",
) -> WorkerFailureClass:
    """Classify one worker invocation's failure mode.

    A zero ``exit_code`` short-circuits to ``NONE`` — there is no
    failure to classify. ``stderr_tail`` may be empty; the empty
    string matches nothing. The match is case-insensitive.

    The function is pure: no globals, no I/O, no clock. A test
    asserting the same ``(exit_code, stderr_tail)`` returns the
    same class is the contract — callers can build test fixtures
    with literal stderr strings and pin the verdict.

    ``stdout_tail`` is accepted for API symmetry but never
    inspected: many models emit ``"quota exceeded"`` as
    plain-prose recovery instructions on the success path, and
    matching those would lock out legitimate completions. M1
    WP4 specifically excludes stdout from the verdict.
    """

    if exit_code == 0:
        return WorkerFailureClass.NONE
    haystack = stderr_tail.lower() if stderr_tail else ""
    if not haystack:
        return WorkerFailureClass.UNCLASSIFIED
    if any(marker in haystack for marker in QUOTA_OR_RATE_LIMIT_MARKERS):
        return WorkerFailureClass.QUOTA_OR_RATE_LIMIT
    if any(marker in haystack for marker in AUTH_MARKERS):
        return WorkerFailureClass.AUTH
    return WorkerFailureClass.UNCLASSIFIED


__all__ = [
    "AUTH_MARKERS",
    "QUOTA_OR_RATE_LIMIT_MARKERS",
    "WorkerFailureClass",
    "classify_worker_failure",
]