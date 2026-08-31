"""Append-only verification evidence and conservative retry classification."""

from __future__ import annotations

import os
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.verifier import VerificationResult


class VerificationFailureClass(StrEnum):
    NONE = "NONE"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    HYGIENE_FAILURE = "HYGIENE_FAILURE"
    COMMAND_FAILURE = "COMMAND_FAILURE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    KNOWN_FLAKY_INFRA = "KNOWN_FLAKY_INFRA"
    UNKNOWN = "UNKNOWN"


def classify_failure(result: VerificationResult) -> VerificationFailureClass:
    if result.passed and result.evidence_id is not None:
        return VerificationFailureClass.NONE
    if result.passed and result.evidence_id is None:
        return VerificationFailureClass.MISSING_EVIDENCE
    reason = result.failure_reason or ""
    if "scope violation" in reason:
        return VerificationFailureClass.SCOPE_VIOLATION
    if "diff --check" in reason:
        return VerificationFailureClass.HYGIENE_FAILURE
    if "verifier command failed" in reason:
        return VerificationFailureClass.COMMAND_FAILURE
    if "no host evidence" in reason:
        return VerificationFailureClass.MISSING_EVIDENCE
    return VerificationFailureClass.UNKNOWN


class RetryPolicy(BaseModel):
    """Only explicitly attested flaky infrastructure may be retried automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_known_flaky_infra_retries: int = Field(default=1, ge=0, le=3)

    def should_retry(
        self,
        *,
        failure_class: VerificationFailureClass,
        previous_retries: int,
        host_attested_known_flaky_infra: bool = False,
    ) -> bool:
        if previous_retries < 0:
            raise ValueError("previous_retries cannot be negative")
        return (
            failure_class is VerificationFailureClass.KNOWN_FLAKY_INFRA
            and host_attested_known_flaky_infra
            and previous_retries < self.max_known_flaky_infra_retries
        )


class VerificationEvidenceJournal:
    """Persist exact structured verifier output by its content-addressed evidence ID."""

    def __init__(self, root: Path) -> None:
        self.directory = root / "verification-evidence"

    def path_for(self, evidence_id: str) -> Path:
        if not evidence_id or any(part in evidence_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe evidence_id")
        return self.directory / f"{evidence_id}.json"

    def append(self, result: VerificationResult) -> Path:
        if result.evidence_id is None:
            raise ValueError("verification result has no immutable evidence_id")
        target = self.path_for(result.evidence_id)
        rendered = result.model_dump_json(indent=2) + "\n"
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

    def load(self, evidence_id: str) -> VerificationResult | None:
        path = self.path_for(evidence_id)
        if not path.exists():
            return None
        return VerificationResult.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "RetryPolicy",
    "VerificationEvidenceJournal",
    "VerificationFailureClass",
    "classify_failure",
]
