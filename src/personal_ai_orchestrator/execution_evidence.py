"""Durable execution-verification evidence for launch surfaces.

No execution target may claim ``execution_verified=True`` without a durable
evidence record produced by a real worker invocation observed by the host.
A failed call must never establish VERIFIED.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ExecutionVerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    AUTH_FAILED = "AUTH_FAILED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ExecutionVerificationMethod(StrEnum):
    REAL_WORKER_INVOCATION = "REAL_WORKER_INVOCATION"


class ExecutionVerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    execution_target_id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    observed_at: datetime
    verification_method: ExecutionVerificationMethod
    result: ExecutionVerificationOutcome
    reason_code: str = Field(min_length=1)

    @property
    def establishes_verified(self) -> bool:
        return self.result is ExecutionVerificationOutcome.VERIFIED


def build_execution_evidence(
    *,
    provider_id: str,
    execution_target_id: str,
    model_sku_id: str,
    observed_at: datetime | None = None,
    result: ExecutionVerificationOutcome,
    reason_code: str,
    verification_method: ExecutionVerificationMethod = (
        ExecutionVerificationMethod.REAL_WORKER_INVOCATION
    ),
) -> ExecutionVerificationEvidence:
    stamp = observed_at or datetime.now(UTC)
    payload = {
        "provider_id": provider_id,
        "execution_target_id": execution_target_id,
        "model_sku_id": model_sku_id,
        "observed_at": stamp.isoformat(),
        "verification_method": verification_method.value,
        "result": result.value,
        "reason_code": reason_code,
    }
    digest = sha256(
        "".join(f"{key}={payload[key]}" for key in sorted(payload)).encode("utf-8")
    ).hexdigest()
    return ExecutionVerificationEvidence(
        evidence_id=f"exec-verify-{digest[:24]}",
        provider_id=provider_id,
        execution_target_id=execution_target_id,
        model_sku_id=model_sku_id,
        observed_at=stamp,
        verification_method=verification_method,
        result=result,
        reason_code=reason_code,
    )


class ExecutionEvidenceJournal:
    """Append-only store of execution-verification evidence records."""

    def __init__(self, root: Path) -> None:
        self.directory = root / "execution-evidence"

    def path_for(self, evidence_id: str) -> Path:
        if not evidence_id or any(part in evidence_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe evidence_id")
        return self.directory / f"{evidence_id}.json"

    def append(self, evidence: ExecutionVerificationEvidence) -> Path:
        target = self.path_for(evidence.evidence_id)
        rendered = evidence.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("evidence_id already has different content")
            return target
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(self, evidence_id: str) -> ExecutionVerificationEvidence | None:
        path = self.path_for(evidence_id)
        if not path.exists():
            return None
        return ExecutionVerificationEvidence.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def latest_for_target(
        self,
        execution_target_id: str,
    ) -> ExecutionVerificationEvidence | None:
        if not self.directory.exists():
            return None
        latest: ExecutionVerificationEvidence | None = None
        latest_key: tuple[datetime, int] | None = None
        for path in sorted(self.directory.glob("exec-verify-*.json")):
            try:
                evidence = ExecutionVerificationEvidence.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if evidence.execution_target_id != execution_target_id:
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                mtime_ns = 0
            key = (evidence.observed_at, mtime_ns)
            if latest_key is None or key > latest_key:
                latest = evidence
                latest_key = key
        return latest

    def latest_verified_for_provider(
        self,
        provider_id: str,
    ) -> ExecutionVerificationEvidence | None:
        """Return the newest VERIFIED evidence recorded for exactly this provider surface.

        The match is on the literal ``provider_id``, which already encodes region and
        plan surface (``minimax-cn-coding-plan`` is not ``minimax-coding-plan``). No
        prefix or family matching is performed, so evidence never promotes a sibling
        surface it did not actually exercise.
        """

        if not self.directory.exists():
            return None
        latest: ExecutionVerificationEvidence | None = None
        latest_key: tuple[datetime, int] | None = None
        for path in sorted(self.directory.glob("exec-verify-*.json")):
            try:
                evidence = ExecutionVerificationEvidence.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if evidence.provider_id != provider_id or not evidence.establishes_verified:
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                mtime_ns = 0
            key = (evidence.observed_at, mtime_ns)
            if latest_key is None or key > latest_key:
                latest = evidence
                latest_key = key
        return latest

    def target_has_verified_evidence(
        self,
        execution_target_id: str,
        *,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Return whether the latest exact-target evidence authorizes launch.

        This deliberately does not use the historical demote-fallback helper:
        launch authority follows the latest target observation and, when a max
        age is supplied, its freshness. ``now`` is injectable so every caller
        can project the same rule deterministically in tests and UI views.
        """

        latest = self.latest_for_target(execution_target_id)
        if latest is None or not latest.establishes_verified:
            return False
        if max_age_seconds is not None:
            reference = now or datetime.now(UTC)
            if reference.tzinfo is None or reference.utcoffset() is None:
                raise ValueError("now must be timezone-aware")
            age = (reference - latest.observed_at).total_seconds()
            if age < 0 or age > max_age_seconds:
                return False
        return True

    def latest_verified_for_target(
        self,
        execution_target_id: str,
    ) -> tuple[ExecutionVerificationEvidence | None, datetime | None]:
        """Return ``(evidence, stale_since)`` with the demote-fallback semantic.

        ``evidence`` is the newest VERIFIED record for ``execution_target_id``,
        falling back from a non-VERIFIED latest (UNKNOWN, AUTH_FAILED, …) so
        transient failures do not destroy a real verified history. ``stale_since``
        is non-``None`` exactly when this fallback fired — i.e. the latest
        evidence for the target is non-VERIFIED while an older VERIFIED exists.
        Callers surface that as ``execution_verified_stale=True`` so the owner
        can see "we have history, but the latest run did not actually succeed".

        Returns ``(None, None)`` when the target has no recorded history.
        Returns ``(evidence, None)`` when the latest record is VERIFIED.
        """

        latest_any = self.latest_for_target(execution_target_id)
        if latest_any is None:
            return (None, None)
        if latest_any.establishes_verified:
            return (latest_any, None)
        # The latest is non-VERIFIED. Walk again for the most recent VERIFIED row.
        verified, _ = self._latest_verified_row(execution_target_id)
        # ``stale_since`` is set whenever the latest evidence is non-VERIFIED,
        # whether or not a historical VERIFIED exists — the UI uses it to show
        # "the most recent run did not actually succeed".
        return (verified, latest_any.observed_at)

    def _latest_verified_row(
        self, execution_target_id: str
    ) -> tuple[ExecutionVerificationEvidence | None, tuple[datetime, int] | None]:
        if not self.directory.exists():
            return (None, None)
        latest: ExecutionVerificationEvidence | None = None
        latest_key: tuple[datetime, int] | None = None
        for path in sorted(self.directory.glob("exec-verify-*.json")):
            try:
                evidence = ExecutionVerificationEvidence.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if evidence.execution_target_id != execution_target_id:
                continue
            if not evidence.establishes_verified:
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                mtime_ns = 0
            key = (evidence.observed_at, mtime_ns)
            if latest_key is None or key > latest_key:
                latest = evidence
                latest_key = key
        return (latest, latest_key)


__all__ = [
    "ExecutionEvidenceJournal",
    "ExecutionVerificationEvidence",
    "ExecutionVerificationMethod",
    "ExecutionVerificationOutcome",
    "build_execution_evidence",
]
