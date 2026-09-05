"""Host-owned observed quota availability without exact quota claims."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import EvidenceConfidence, RegistryModel
from personal_ai_orchestrator.quota_policy_analysis import MeasurementSource
from personal_ai_orchestrator.shadow_evidence import ResetCycleSource


class QuotaAvailabilityState(StrEnum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE_OBSERVED = "AVAILABLE_OBSERVED"
    EXHAUSTED_OBSERVED = "EXHAUSTED_OBSERVED"
    COOLDOWN = "COOLDOWN"
    RECOVERY_PROBE_DUE = "RECOVERY_PROBE_DUE"
    RECOVERED_OBSERVED = "RECOVERED_OBSERVED"
    #: The host has seen >= ``UNCERTAIN_LOCKED_THRESHOLD`` consecutive failed
    #: quota collections for this target without an intervening success.
    #: Admission MUST reject until a single success resets the streak. The
    #: state stays UNKNOWN underneath; ``state_at()`` projects the lock so
    #: callers can rely on one accessor for both signals.
    UNCERTAIN_LOCKED = "UNCERTAIN_LOCKED"


#: Consecutive collection failures after which ``state_at()`` projects
#: UNCERTAIN_LOCKED. Tuned so a single transient blip cannot lock a target,
#: but three failures in a row always can. See A2 in fix/m0-trust.
UNCERTAIN_LOCKED_THRESHOLD = 3


class QuotaAvailabilityEvidence(RegistryModel):
    execution_target_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    state: QuotaAvailabilityState = QuotaAvailabilityState.UNKNOWN
    observed_at: datetime
    measurement_source: MeasurementSource = MeasurementSource.LOCALLY_MEASURED
    confidence: EvidenceConfidence = EvidenceConfidence.ESTIMATED
    reset_cycle_source: ResetCycleSource = ResetCycleSource.LOCALLY_INFERRED
    sanitized_reason_code: str | None = None
    exhaustion_observed_at: datetime | None = None
    recovery_observed_at: datetime | None = None
    last_success_before_exhaustion: datetime | None = None
    first_success_after_exhaustion: datetime | None = None
    cooldown_until: datetime | None = None
    remaining_fraction: float | None = None
    reset_at: datetime | None = None
    #: Count of consecutive quota-collection attempts for this target that
    #: did NOT produce a definitive observation (no collector, collector
    #: returned UNKNOWN, etc.). Reset to 0 on every successful observation.
    #: ``state_at()`` projects UNCERTAIN_LOCKED when this crosses the
    #: threshold; the underlying ``state`` stays UNKNOWN so the lock can
    #: be cleared by the next ``observe_success``.
    consecutive_failures: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_local_availability_truth(self) -> QuotaAvailabilityEvidence:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        for field_name in (
            "exhaustion_observed_at",
            "recovery_observed_at",
            "last_success_before_exhaustion",
            "first_success_after_exhaustion",
            "cooldown_until",
        ):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.measurement_source is MeasurementSource.PROVIDER_EXACT:
            raise ValueError("observed availability must not claim PROVIDER_EXACT source")
        if self.confidence is EvidenceConfidence.EXACT:
            raise ValueError("observed availability must not carry EXACT confidence")
        if self.reset_cycle_source is ResetCycleSource.PROVIDER_EXACT:
            raise ValueError("observed availability must not create provider-exact reset cycles")
        if self.remaining_fraction is not None:
            raise ValueError("observed availability must not carry exact remaining_fraction")
        if self.reset_at is not None:
            raise ValueError("observed availability must not carry exact reset_at")
        return self

    @property
    def exhaustion_to_recovery_seconds(self) -> float | None:
        if self.exhaustion_observed_at is None or self.recovery_observed_at is None:
            return None
        return (self.recovery_observed_at - self.exhaustion_observed_at).total_seconds()

    def state_at(self, *, now: datetime) -> QuotaAvailabilityState:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.state is QuotaAvailabilityState.COOLDOWN and self.cooldown_until is not None:
            if now >= self.cooldown_until:
                return QuotaAvailabilityState.RECOVERY_PROBE_DUE
        # Project the lock on top of whatever underlying state we have. The
        # underlying state stays put so a single observe_success() can clear
        # both the lock AND the streak in one transaction.
        if (
            self.consecutive_failures >= UNCERTAIN_LOCKED_THRESHOLD
            and self.state is QuotaAvailabilityState.UNKNOWN
        ):
            return QuotaAvailabilityState.UNCERTAIN_LOCKED
        return self.state

    def blocks_quota_billable_launch(self, *, now: datetime) -> bool:
        return self.state_at(now=now) in {
            QuotaAvailabilityState.EXHAUSTED_OBSERVED,
            QuotaAvailabilityState.COOLDOWN,
        }


def unknown_availability(
    *,
    execution_target_id: str,
    provider_id: str,
    quota_pool_id: str,
    observed_at: datetime,
    previous: QuotaAvailabilityEvidence | None = None,
) -> QuotaAvailabilityEvidence:
    """Build an UNKNOWN observation, carrying forward the consecutive-failure streak.

    The first UNKNOWN observation for a target starts the streak at 1; every
    subsequent UNKNOWN bumps by 1. ``observe_success```` resets to 0. The
    streak is the authority for projecting ``UNCERTAIN_LOCKED`` via
    ``state_at``; the underlying ``state`` stays UNKNOWN so a single success
    can clear both in one transaction.
    """

    streak = 1
    if previous is not None and previous.state is QuotaAvailabilityState.UNKNOWN:
        streak = previous.consecutive_failures + 1
    return QuotaAvailabilityEvidence(
        execution_target_id=execution_target_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        state=QuotaAvailabilityState.UNKNOWN,
        observed_at=observed_at,
        confidence=EvidenceConfidence.UNKNOWN,
        reset_cycle_source=ResetCycleSource.UNKNOWN,
        consecutive_failures=streak,
    )


def observe_success(
    previous: QuotaAvailabilityEvidence | None,
    *,
    execution_target_id: str,
    provider_id: str,
    quota_pool_id: str,
    observed_at: datetime,
) -> QuotaAvailabilityEvidence:
    if previous is not None and previous.exhaustion_observed_at is not None:
        return QuotaAvailabilityEvidence(
            execution_target_id=execution_target_id,
            provider_id=provider_id,
            quota_pool_id=quota_pool_id,
            state=QuotaAvailabilityState.RECOVERED_OBSERVED,
            observed_at=observed_at,
            exhaustion_observed_at=previous.exhaustion_observed_at,
            recovery_observed_at=observed_at,
            last_success_before_exhaustion=previous.last_success_before_exhaustion,
            first_success_after_exhaustion=observed_at,
            sanitized_reason_code="RECOVERY_SUCCESS",
            # A successful observation clears the streak; the
            # UNCERTAIN_LOCKED projection is derived from it, so the lock
            # releases atomically with this write.
            consecutive_failures=0,
        )
    return QuotaAvailabilityEvidence(
        execution_target_id=execution_target_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        state=QuotaAvailabilityState.AVAILABLE_OBSERVED,
        observed_at=observed_at,
        sanitized_reason_code="SUCCESS_OBSERVED",
        consecutive_failures=0,
    )


def observe_exhaustion(
    previous: QuotaAvailabilityEvidence | None,
    *,
    execution_target_id: str,
    provider_id: str,
    quota_pool_id: str,
    observed_at: datetime,
    sanitized_reason_code: str,
    minimum_cooldown_seconds: float = 3600.0,
) -> QuotaAvailabilityEvidence:
    last_success = None
    if previous is not None:
        if previous.state in {
            QuotaAvailabilityState.AVAILABLE_OBSERVED,
            QuotaAvailabilityState.RECOVERED_OBSERVED,
        }:
            last_success = previous.observed_at
        else:
            last_success = previous.last_success_before_exhaustion
    return QuotaAvailabilityEvidence(
        execution_target_id=execution_target_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        state=QuotaAvailabilityState.COOLDOWN,
        observed_at=observed_at,
        exhaustion_observed_at=observed_at,
        last_success_before_exhaustion=last_success,
        cooldown_until=observed_at + timedelta(seconds=minimum_cooldown_seconds),
        sanitized_reason_code=sanitized_reason_code,
    )


def mark_recovery_probe_due(
    previous: QuotaAvailabilityEvidence,
    *,
    observed_at: datetime,
) -> QuotaAvailabilityEvidence:
    return previous.model_copy(
        update={
            "state": QuotaAvailabilityState.RECOVERY_PROBE_DUE,
            "observed_at": observed_at,
            "sanitized_reason_code": "RECOVERY_PROBE_DUE",
        }
    )


def _atomic_write_json(target: Path, rendered: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
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


class QuotaAvailabilityJournal:
    def __init__(self, root: Path) -> None:
        self.directory = root / "quota-availability"

    def path_for(self, execution_target_id: str) -> Path:
        if not execution_target_id or any(
            part in execution_target_id for part in ("/", "\\", "..")
        ):
            raise ValueError("unsafe execution_target_id")
        return self.directory / f"{execution_target_id}.json"

    def load(self, execution_target_id: str) -> QuotaAvailabilityEvidence | None:
        path = self.path_for(execution_target_id)
        if not path.exists():
            return None
        return QuotaAvailabilityEvidence.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, evidence: QuotaAvailabilityEvidence) -> Path:
        rendered = evidence.model_dump_json(indent=2) + "\n"
        return _atomic_write_json(self.path_for(evidence.execution_target_id), rendered)

    def snapshot(self) -> dict[str, object]:
        if not self.directory.exists():
            return {}
        result: dict[str, object] = {}
        for path in sorted(self.directory.glob("*.json")):
            evidence = QuotaAvailabilityEvidence.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            result[evidence.execution_target_id] = json.loads(evidence.model_dump_json())
        return result


__all__ = [
    "QuotaAvailabilityEvidence",
    "QuotaAvailabilityJournal",
    "QuotaAvailabilityState",
    "UNCERTAIN_LOCKED_THRESHOLD",
    "mark_recovery_probe_due",
    "observe_exhaustion",
    "observe_success",
    "unknown_availability",
]
