"""Append-only ExecutionEvidenceJournal: latest_for_target and demote-fallback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)


def _evidence(
    tmp_path: Path,
    *,
    target_id: str,
    result: ExecutionVerificationOutcome,
    reason_code: str,
    observed_at: datetime,
) -> None:
    journal = ExecutionEvidenceJournal(tmp_path)
    record = build_execution_evidence(
        provider_id="provider-x",
        execution_target_id=target_id,
        model_sku_id="provider-x/model-a",
        observed_at=observed_at,
        result=result,
        reason_code=reason_code,
    )
    journal.append(record)


def test_latest_verified_for_target_returns_none_when_no_history(tmp_path: Path) -> None:
    journal = ExecutionEvidenceJournal(tmp_path)
    evidence, stale_since = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence is None
    assert stale_since is None


def test_latest_verified_for_target_returns_latest_when_verified(tmp_path: Path) -> None:
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
        observed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
        observed_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
    )
    journal = ExecutionEvidenceJournal(tmp_path)
    evidence, stale_since = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence is not None
    assert evidence.result is ExecutionVerificationOutcome.VERIFIED
    assert evidence.observed_at == datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    assert stale_since is None


def test_latest_verified_for_target_falls_back_when_latest_is_unknown(tmp_path: Path) -> None:
    # The whole point of the demote-fallback: a transient UNKNOWN must not
    # destroy the verified history. The historical VERIFIED row is returned
    # with a non-None stale_since pointing at when the UNKNOWN arrived.
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
        observed_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
        reason_code="QUOTA_BLOCKED",
        observed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    journal = ExecutionEvidenceJournal(tmp_path)
    evidence, stale_since = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence is not None
    assert evidence.result is ExecutionVerificationOutcome.VERIFIED
    assert evidence.observed_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert stale_since == datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_latest_verified_for_target_returns_none_when_never_verified(tmp_path: Path) -> None:
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.AUTH_FAILED,
        reason_code="AUTH_FAILED",
        observed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.RUNTIME_UNAVAILABLE,
        reason_code="RUNTIME_UNAVAILABLE",
        observed_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
    )
    journal = ExecutionEvidenceJournal(tmp_path)
    evidence, stale_since = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence is None
    assert stale_since == datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_latest_verified_for_target_isolates_per_target(tmp_path: Path) -> None:
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
        observed_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    _evidence(
        tmp_path,
        target_id="provider-x/model-a",
        result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
        reason_code="QUOTA_BLOCKED",
        observed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    _evidence(
        tmp_path,
        target_id="provider-x/model-b",
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
        observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )
    journal = ExecutionEvidenceJournal(tmp_path)

    evidence_a, stale_a = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence_a is not None
    assert evidence_a.observed_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert stale_a == datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    evidence_b, stale_b = journal.latest_verified_for_target("provider-x/model-b")
    assert evidence_b is not None
    assert evidence_b.observed_at == datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert stale_b is None


def test_latest_verified_for_target_uses_mtime_tiebreaker(tmp_path: Path) -> None:
    """When two records share an observed_at, the newer file wins.

    This mirrors ``latest_for_target``'s tiebreaker so a rebuild of the same
    evidence row (rare in practice but not impossible) does not regress the
    fallback semantic.
    """

    base = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    journal = ExecutionEvidenceJournal(tmp_path)
    first = build_execution_evidence(
        provider_id="provider-x",
        execution_target_id="provider-x/model-a",
        model_sku_id="provider-x/model-a",
        observed_at=base,
        result=ExecutionVerificationOutcome.VERIFIED,
        reason_code="PROBE_PASS",
    )
    second = build_execution_evidence(
        provider_id="provider-x",
        execution_target_id="provider-x/model-a",
        model_sku_id="provider-x/model-a",
        observed_at=base + timedelta(seconds=1),
        result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
        reason_code="QUOTA_BLOCKED",
    )
    journal.append(first)
    journal.append(second)

    evidence, stale_since = journal.latest_verified_for_target("provider-x/model-a")
    assert evidence is not None
    assert evidence.observed_at == base
    assert stale_since == base + timedelta(seconds=1)


# -----------------------------------------------------------------------------
# M1 WP4 — QUOTA_BLOCKED evidence falls back to the most recent VERIFIED row.
# -----------------------------------------------------------------------------


def test_quota_blocked_evidence_falls_back_to_most_recent_verified(tmp_path) -> None:
    """``QUOTA_BLOCKED`` is a transient outcome; demote-fallback to ``stale=True``.

    The ``ExecutionEvidenceJournal.latest_verified_for_target``
    helper already falls back from any non-VERIFIED outcome to the
    most recent VERIFIED row. M1 WP4 makes this contract explicit
    for ``QUOTA_BLOCKED`` — a 429 / usage-limit hit on a previously
    verified target must NOT flip the target's chip to "unverified".
    """

    from datetime import UTC, datetime, timedelta

    from personal_ai_orchestrator.execution_evidence import (
        ExecutionEvidenceJournal,
        ExecutionVerificationOutcome,
        build_execution_evidence,
    )

    journal = ExecutionEvidenceJournal(tmp_path)
    target_id = "opencode-big-pickle"
    earlier = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    later = earlier + timedelta(minutes=10)

    journal.append(
        build_execution_evidence(
            provider_id="opencode",
            execution_target_id=target_id,
            model_sku_id="opencode-big-pickle",
            observed_at=earlier,
            result=ExecutionVerificationOutcome.VERIFIED,
            reason_code="REAL_WORKER_DISPATCH_SUCCEEDED",
        )
    )
    journal.append(
        build_execution_evidence(
            provider_id="opencode",
            execution_target_id=target_id,
            model_sku_id="opencode-big-pickle",
            observed_at=later,
            result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
            reason_code="WORKER_QUOTA_BLOCKED",
        )
    )

    evidence, stale_since = journal.latest_verified_for_target(target_id)
    assert evidence is not None
    assert evidence.result is ExecutionVerificationOutcome.VERIFIED
    # ``stale_since`` is non-None exactly when the latest is non-VERIFIED.
    assert stale_since is not None
    assert stale_since == later


def test_quota_blocked_with_no_history_returns_none_verified_and_stale(tmp_path) -> None:
    """A target with only a QUOTA_BLOCKED row and no history returns ``(None, observed_at)``.

    The ``verified`` half of the tuple is ``None`` because there is
    no historical VERIFIED row. ``stale_since`` is the latest's
    observed_at because the latest is non-VERIFIED — the owner sees
    ``execution_verified=False`` and ``execution_verified_stale=True``
    (the journal knows the row exists; the gate just has no verified
    history to fall back to).
    """

    from datetime import UTC, datetime

    from personal_ai_orchestrator.execution_evidence import (
        ExecutionEvidenceJournal,
        ExecutionVerificationOutcome,
        build_execution_evidence,
    )

    journal = ExecutionEvidenceJournal(tmp_path)
    target_id = "opencode-big-pickle"
    when = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    journal.append(
        build_execution_evidence(
            provider_id="opencode",
            execution_target_id=target_id,
            model_sku_id="opencode-big-pickle",
            observed_at=when,
            result=ExecutionVerificationOutcome.QUOTA_BLOCKED,
            reason_code="WORKER_QUOTA_BLOCKED",
        )
    )
    evidence, stale_since = journal.latest_verified_for_target(target_id)
    assert evidence is None
    # ``stale_since`` is non-None because the latest is non-VERIFIED
    # (the existing behaviour, unchanged by WP4).
    assert stale_since == when
