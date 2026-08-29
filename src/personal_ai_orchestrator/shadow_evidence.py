"""Append-only Shadow Mode evidence for falsifiable routing validation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShadowCampaignStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ResetCycleReference(FrozenModel):
    reset_cycle_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    quota_snapshot_id: str | None = None
    reset_at: datetime | None = None
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source_method: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reset_at(self) -> ResetCycleReference:
        if self.reset_at is not None and (
            self.reset_at.tzinfo is None or self.reset_at.utcoffset() is None
        ):
            raise ValueError("reset_at must be timezone-aware")
        return self


class ShadowCampaignState(FrozenModel):
    campaign_id: str = Field(min_length=1)
    status: ShadowCampaignStatus = ShadowCampaignStatus.ACTIVE
    started_at: datetime
    head: str = Field(min_length=1)
    catalog_snapshot_id: str = Field(min_length=1)
    policy_snapshot_id: str = Field(min_length=1)
    providers_enabled: tuple[str, ...] = ()
    providers_unknown: tuple[str, ...] = ()
    reset_cycles_required: int = Field(default=2, ge=2)

    @model_validator(mode="after")
    def validate_started_at(self) -> ShadowCampaignState:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        return self


class ShadowObservation(FrozenModel):
    observation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    reset_cycle_ids: tuple[str, ...] = Field(min_length=1)
    manual_execution_target_id: str = Field(min_length=1)
    scheduler_execution_target_id: str | None = None
    catalog_snapshot_id: str = Field(min_length=1)
    policy_snapshot_id: str = Field(min_length=1)
    quota_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    quota_after_snapshot_ids: tuple[str, ...] = ()
    provider_id: str | None = None
    quota_pool_id: str | None = None
    task_family: str = "unknown"
    quota_confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    collector_status: QuotaCollectionStatus | None = None
    predicted_burn_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    observed_burn_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    verified: bool
    regression_detected: bool = False
    attempts_to_green: int | None = Field(default=None, ge=1)
    time_to_green_seconds: float | None = Field(default=None, ge=0.0)
    handoff_count: int = Field(default=0, ge=0)
    recommendation_followed: bool
    observed_at: datetime

    @model_validator(mode="after")
    def validate_recommendation_followed(self) -> ShadowObservation:
        if self.scheduler_execution_target_id is None and self.recommendation_followed:
            raise ValueError("cannot follow a missing scheduler recommendation")
        expected = self.scheduler_execution_target_id == self.manual_execution_target_id
        if self.scheduler_execution_target_id is not None and self.recommendation_followed != expected:
            raise ValueError("recommendation_followed conflicts with target identities")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self

    @classmethod
    def build(
        cls,
        *,
        task_id: str,
        request_id: str,
        decision_id: str,
        reset_cycle_ids: tuple[str, ...],
        manual_execution_target_id: str,
        scheduler_execution_target_id: str | None,
        catalog_snapshot_id: str,
        policy_snapshot_id: str,
        quota_snapshot_ids: tuple[str, ...],
        quota_after_snapshot_ids: tuple[str, ...] = (),
        provider_id: str | None = None,
        quota_pool_id: str | None = None,
        task_family: str = "unknown",
        quota_confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN,
        collector_status: QuotaCollectionStatus | None = None,
        predicted_burn_fraction: float | None = None,
        observed_burn_fraction: float | None = None,
        verified: bool,
        regression_detected: bool = False,
        attempts_to_green: int | None = None,
        time_to_green_seconds: float | None = None,
        handoff_count: int = 0,
        observed_at: datetime,
    ) -> ShadowObservation:
        recommendation_followed = (
            scheduler_execution_target_id is not None
            and scheduler_execution_target_id == manual_execution_target_id
        )
        identity_payload = {
            "task_id": task_id,
            "request_id": request_id,
            "decision_id": decision_id,
            "reset_cycle_ids": reset_cycle_ids,
            "catalog_snapshot_id": catalog_snapshot_id,
            "policy_snapshot_id": policy_snapshot_id,
            "quota_snapshot_ids": quota_snapshot_ids,
        }
        digest = sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            observation_id=f"shadow-{digest[:24]}",
            task_id=task_id,
            request_id=request_id,
            decision_id=decision_id,
            reset_cycle_ids=reset_cycle_ids,
            manual_execution_target_id=manual_execution_target_id,
            scheduler_execution_target_id=scheduler_execution_target_id,
            catalog_snapshot_id=catalog_snapshot_id,
            policy_snapshot_id=policy_snapshot_id,
            quota_snapshot_ids=quota_snapshot_ids,
            quota_after_snapshot_ids=quota_after_snapshot_ids,
            provider_id=provider_id,
            quota_pool_id=quota_pool_id,
            task_family=task_family,
            quota_confidence=quota_confidence,
            collector_status=collector_status,
            predicted_burn_fraction=predicted_burn_fraction,
            observed_burn_fraction=observed_burn_fraction,
            verified=verified,
            regression_detected=regression_detected,
            attempts_to_green=attempts_to_green,
            time_to_green_seconds=time_to_green_seconds,
            handoff_count=handoff_count,
            recommendation_followed=recommendation_followed,
            observed_at=observed_at,
        )


class ShadowEvidenceSummary(FrozenModel):
    observations: int
    reset_cycles: int
    verified_observations: int
    regressions: int
    recommendation_matches: int
    review_eligible: bool
    blocking_reasons: tuple[str, ...]


class ShadowGroupSummary(FrozenModel):
    provider_id: str
    quota_pool_id: str
    task_family: str
    observations: int
    verified_observations: int
    reset_cycles: int
    baseline_targets: tuple[str, ...]
    shadow_targets: tuple[str, ...]
    recommendation_matches: int
    recommendation_disagreements: int
    agreement_rate: float | None
    attempts_to_green_median: float | None
    time_to_green_p50_seconds: float | None
    time_to_green_p90_seconds: float | None
    predicted_burn_fraction_total: float | None
    observed_burn_fraction_total: float | None
    quota_to_green_fraction: float | None
    handoff_count: int
    verifier_failure_rate: float
    regressions: int
    regression_rate: float
    unknown_quota_observations: int
    collector_failure_observations: int


class ShadowCampaignSummary(FrozenModel):
    campaign_id: str | None = None
    status: ShadowCampaignStatus | None = None
    started_at: datetime | None = None
    head: str | None = None
    catalog_snapshot_id: str | None = None
    policy_snapshot_id: str | None = None
    reset_cycles_required: int
    reset_cycles_observed: int
    review_eligible: bool
    production_active_authorized: bool = False
    groups: tuple[ShadowGroupSummary, ...]
    blocking_reasons: tuple[str, ...]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _total_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values), 10)


class ShadowEvidenceJournal:
    """Credential-free append-only filesystem journal keyed by observation identity."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "shadow-evidence"
        self.campaign_path = root / "shadow-campaign.json"

    def path_for(self, observation_id: str) -> Path:
        if not observation_id or any(part in observation_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe observation_id")
        return self.directory / f"{observation_id}.json"

    def append(self, observation: ShadowObservation) -> Path:
        target = self.path_for(observation.observation_id)
        rendered = observation.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("observation_id already has different content")
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

    def load_all(self) -> tuple[ShadowObservation, ...]:
        if not self.directory.exists():
            return ()
        return tuple(
            ShadowObservation.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.directory.glob("shadow-*.json"))
        )

    def save_campaign_state(self, state: ShadowCampaignState) -> Path:
        rendered = state.model_dump_json(indent=2) + "\n"
        target = self.campaign_path
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

    def load_campaign_state(self) -> ShadowCampaignState | None:
        try:
            return ShadowCampaignState.model_validate_json(
                self.campaign_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None

    def summarize(
        self,
        *,
        minimum_observations: int = 20,
        minimum_reset_cycles: int = 2,
        require_zero_regressions: bool = True,
    ) -> ShadowEvidenceSummary:
        observations = self.load_all()
        reset_cycles = {cycle for item in observations for cycle in item.reset_cycle_ids}
        verified = sum(item.verified for item in observations)
        regressions = sum(item.regression_detected for item in observations)
        matches = sum(item.recommendation_followed for item in observations)
        blockers: list[str] = []
        if len(observations) < minimum_observations:
            blockers.append(
                f"need at least {minimum_observations} observations; have {len(observations)}"
            )
        if len(reset_cycles) < minimum_reset_cycles:
            blockers.append(
                f"need at least {minimum_reset_cycles} reset cycles; have {len(reset_cycles)}"
            )
        if verified != len(observations):
            blockers.append("not every Shadow observation has a verified outcome")
        if require_zero_regressions and regressions:
            blockers.append(f"{regressions} verified regression(s) observed")
        return ShadowEvidenceSummary(
            observations=len(observations),
            reset_cycles=len(reset_cycles),
            verified_observations=verified,
            regressions=regressions,
            recommendation_matches=matches,
            review_eligible=not blockers,
            blocking_reasons=tuple(blockers),
        )

    def summarize_campaign(
        self,
        *,
        minimum_observations: int = 20,
        minimum_reset_cycles: int = 2,
        require_zero_regressions: bool = True,
    ) -> ShadowCampaignSummary:
        state = self.load_campaign_state()
        observations = self.load_all()
        base = self.summarize(
            minimum_observations=minimum_observations,
            minimum_reset_cycles=minimum_reset_cycles,
            require_zero_regressions=require_zero_regressions,
        )
        groups: list[ShadowGroupSummary] = []
        keys = sorted(
            {
                (
                    item.provider_id or "unknown",
                    item.quota_pool_id or "unknown",
                    item.task_family,
                )
                for item in observations
            }
        )
        for provider_id, quota_pool_id, task_family in keys:
            items = [
                item
                for item in observations
                if (item.provider_id or "unknown") == provider_id
                and (item.quota_pool_id or "unknown") == quota_pool_id
                and item.task_family == task_family
            ]
            reset_cycles = {cycle for item in items for cycle in item.reset_cycle_ids}
            matches = sum(item.recommendation_followed for item in items)
            disagreements = sum(
                item.scheduler_execution_target_id is not None
                and not item.recommendation_followed
                for item in items
            )
            attempts = [
                float(item.attempts_to_green)
                for item in items
                if item.attempts_to_green is not None
            ]
            times = [
                item.time_to_green_seconds
                for item in items
                if item.time_to_green_seconds is not None
            ]
            predicted = [
                item.predicted_burn_fraction
                for item in items
                if item.predicted_burn_fraction is not None
            ]
            observed = [
                item.observed_burn_fraction
                for item in items
                if item.observed_burn_fraction is not None
            ]
            verified = sum(item.verified for item in items)
            regressions = sum(item.regression_detected for item in items)
            unknown_quota = sum(
                item.quota_confidence is EvidenceConfidence.UNKNOWN for item in items
            )
            collector_failures = sum(
                item.collector_status
                in {
                    QuotaCollectionStatus.AUTH_REQUIRED,
                    QuotaCollectionStatus.RATE_LIMITED,
                    QuotaCollectionStatus.PROVIDER_ERROR,
                    QuotaCollectionStatus.UNKNOWN,
                }
                for item in items
            )
            groups.append(
                ShadowGroupSummary(
                    provider_id=provider_id,
                    quota_pool_id=quota_pool_id,
                    task_family=task_family,
                    observations=len(items),
                    verified_observations=verified,
                    reset_cycles=len(reset_cycles),
                    baseline_targets=tuple(
                        sorted({item.manual_execution_target_id for item in items})
                    ),
                    shadow_targets=tuple(
                        sorted(
                            {
                                item.scheduler_execution_target_id
                                for item in items
                                if item.scheduler_execution_target_id is not None
                            }
                        )
                    ),
                    recommendation_matches=matches,
                    recommendation_disagreements=disagreements,
                    agreement_rate=(matches / len(items) if items else None),
                    attempts_to_green_median=_median(attempts),
                    time_to_green_p50_seconds=_median(times),
                    time_to_green_p90_seconds=_percentile(times, 0.9),
                    predicted_burn_fraction_total=_total_or_none(predicted),
                    observed_burn_fraction_total=_total_or_none(observed),
                    quota_to_green_fraction=_total_or_none(observed),
                    handoff_count=sum(item.handoff_count for item in items),
                    verifier_failure_rate=(1.0 - (verified / len(items)) if items else 0.0),
                    regressions=regressions,
                    regression_rate=(regressions / len(items) if items else 0.0),
                    unknown_quota_observations=unknown_quota,
                    collector_failure_observations=collector_failures,
                )
            )
        required = state.reset_cycles_required if state is not None else minimum_reset_cycles
        return ShadowCampaignSummary(
            campaign_id=None if state is None else state.campaign_id,
            status=None if state is None else state.status,
            started_at=None if state is None else state.started_at,
            head=None if state is None else state.head,
            catalog_snapshot_id=None if state is None else state.catalog_snapshot_id,
            policy_snapshot_id=None if state is None else state.policy_snapshot_id,
            reset_cycles_required=required,
            reset_cycles_observed=base.reset_cycles,
            review_eligible=base.review_eligible,
            production_active_authorized=False,
            groups=tuple(groups),
            blocking_reasons=base.blocking_reasons,
        )


__all__ = [
    "ResetCycleReference",
    "ShadowCampaignState",
    "ShadowCampaignStatus",
    "ShadowCampaignSummary",
    "ShadowEvidenceJournal",
    "ShadowEvidenceSummary",
    "ShadowGroupSummary",
    "ShadowObservation",
]
