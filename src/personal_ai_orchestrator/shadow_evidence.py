"""Append-only Shadow Mode evidence for falsifiable routing validation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShadowCampaignStatus(StrEnum):
    BOOTSTRAPPED = "BOOTSTRAPPED"
    COLLECTING = "COLLECTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ResetCycleSource(StrEnum):
    SYNTHETIC_TEST = "SYNTHETIC_TEST"
    PROVIDER_EXACT = "PROVIDER_EXACT"
    PROVIDER_ESTIMATED = "PROVIDER_ESTIMATED"
    LOCALLY_INFERRED = "LOCALLY_INFERRED"
    UNKNOWN = "UNKNOWN"


class ShadowFailureClass(StrEnum):
    NONE = "NONE"
    MODEL_TASK_FAILURE = "MODEL_TASK_FAILURE"
    VERIFIER_FAILURE = "VERIFIER_FAILURE"
    WORKER_INVOCATION_FAILURE = "WORKER_INVOCATION_FAILURE"
    WORKER_PROCESS_FAILURE = "WORKER_PROCESS_FAILURE"
    AUTH_FAILURE = "AUTH_FAILURE"
    TIMEOUT = "TIMEOUT"
    INFRA_FAILURE = "INFRA_FAILURE"
    POLICY_BLOCK = "POLICY_BLOCK"
    CANCELLED = "CANCELLED"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class ShadowFailureStage(StrEnum):
    NONE = "NONE"
    ROUTING = "ROUTING"
    AUTH = "AUTH"
    INVOCATION = "INVOCATION"
    EXECUTION = "EXECUTION"
    VERIFICATION = "VERIFICATION"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class ShadowQualityOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    MODEL_QUALITY_FAILED = "MODEL_QUALITY_FAILED"
    OPERATIONAL_FAILED = "OPERATIONAL_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ResetCycleReference(FrozenModel):
    reset_cycle_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    quota_snapshot_id: str | None = None
    reset_at: datetime | None = None
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: ResetCycleSource = ResetCycleSource.UNKNOWN
    source_method: str = Field(min_length=1)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_reset_at(self) -> ResetCycleReference:
        if self.reset_at is not None and (
            self.reset_at.tzinfo is None or self.reset_at.utcoffset() is None
        ):
            raise ValueError("reset_at must be timezone-aware")
        if self.observed_at is not None and (
            self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        if self.source is ResetCycleSource.PROVIDER_EXACT:
            if self.confidence is not EvidenceConfidence.EXACT:
                raise ValueError("PROVIDER_EXACT reset cycles require EXACT confidence")
            if self.reset_at is None:
                raise ValueError("PROVIDER_EXACT reset cycles require reset_at")
        return self

    @property
    def counts_for_real_acceptance(self) -> bool:
        return (
            self.source is ResetCycleSource.PROVIDER_EXACT
            and self.confidence is EvidenceConfidence.EXACT
            and self.reset_at is not None
        )


class ShadowAcceptancePolicy(FrozenModel):
    minimum_observations: int = Field(default=20, ge=1)
    minimum_real_reset_cycles: int = Field(default=2, ge=2)
    require_zero_regressions: bool = True


class ShadowCampaignState(FrozenModel):
    campaign_id: str = Field(min_length=1)
    status: ShadowCampaignStatus = ShadowCampaignStatus.BOOTSTRAPPED
    started_at: datetime
    head: str = Field(min_length=1)
    catalog_snapshot_id: str = Field(min_length=1)
    policy_snapshot_id: str = Field(min_length=1)
    providers_enabled: tuple[str, ...] = ()
    providers_unknown: tuple[str, ...] = ()
    reset_cycles_required: int = Field(default=2, ge=2)
    acceptance_policy: ShadowAcceptancePolicy = Field(default_factory=ShadowAcceptancePolicy)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_acceptance_policy(cls, value: object) -> object:
        if isinstance(value, dict) and "acceptance_policy" not in value:
            migrated = dict(value)
            migrated["acceptance_policy"] = {
                "minimum_observations": 20,
                "minimum_real_reset_cycles": migrated.get("reset_cycles_required", 2),
                "require_zero_regressions": True,
            }
            return migrated
        return value

    @field_validator("status", mode="before")
    @classmethod
    def migrate_legacy_active(cls, value: object) -> object:
        if value == "ACTIVE":
            return ShadowCampaignStatus.BOOTSTRAPPED
        return value

    @model_validator(mode="after")
    def validate_started_at(self) -> ShadowCampaignState:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if self.reset_cycles_required != self.acceptance_policy.minimum_real_reset_cycles:
            raise ValueError("reset_cycles_required must match acceptance policy")
        return self


class PendingShadowObservation(FrozenModel):
    pending_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    manual_execution_target_id: str = Field(min_length=1)
    scheduler_execution_target_id: str | None = None
    catalog_snapshot_id: str = Field(min_length=1)
    policy_snapshot_id: str = Field(min_length=1)
    quota_snapshot_ids: tuple[str, ...] = ()
    provider_id: str | None = None
    quota_pool_id: str | None = None
    task_family: str = "unknown"
    quota_confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    collector_status: QuotaCollectionStatus | None = None
    predicted_burn_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    started_at: datetime

    @model_validator(mode="after")
    def validate_started_at(self) -> PendingShadowObservation:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        return self


class ShadowObservation(FrozenModel):
    observation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    reset_cycle_ids: tuple[str, ...] = ()
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
    execution_success: bool = True
    verification_success: bool = False
    quality_outcome: ShadowQualityOutcome = ShadowQualityOutcome.UNKNOWN
    failure_class: ShadowFailureClass = ShadowFailureClass.UNKNOWN_FAILURE
    failure_stage: ShadowFailureStage = ShadowFailureStage.NONE
    verified: bool
    regression_detected: bool = False
    attempts_to_green: int | None = Field(default=None, ge=1)
    time_to_green_seconds: float | None = Field(default=None, ge=0.0)
    handoff_count: int = Field(default=0, ge=0)
    recommendation_followed: bool
    observed_at: datetime

    @model_validator(mode="before")
    @classmethod
    def migrate_failure_taxonomy(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        verified = bool(migrated.get("verified", False))
        migrated.setdefault("execution_success", True)
        migrated.setdefault("verification_success", verified)
        migrated.setdefault(
            "quality_outcome",
            ShadowQualityOutcome.VERIFIED.value if verified else ShadowQualityOutcome.UNKNOWN.value,
        )
        migrated.setdefault(
            "failure_class",
            ShadowFailureClass.NONE.value
            if verified
            else ShadowFailureClass.UNKNOWN_FAILURE.value,
        )
        migrated.setdefault(
            "failure_stage",
            ShadowFailureStage.NONE.value
            if verified
            else ShadowFailureStage.INFRASTRUCTURE.value,
        )
        return migrated

    @model_validator(mode="after")
    def validate_recommendation_followed(self) -> ShadowObservation:
        if self.scheduler_execution_target_id is None and self.recommendation_followed:
            raise ValueError("cannot follow a missing scheduler recommendation")
        expected = self.scheduler_execution_target_id == self.manual_execution_target_id
        if self.scheduler_execution_target_id is not None and self.recommendation_followed != expected:
            raise ValueError("recommendation_followed conflicts with target identities")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.verified and self.quality_outcome is not ShadowQualityOutcome.VERIFIED:
            raise ValueError("verified observations must carry VERIFIED quality_outcome")
        if self.verified and self.failure_class is not ShadowFailureClass.NONE:
            raise ValueError("verified observations must not carry a failure_class")
        if self.failure_class is ShadowFailureClass.NONE and self.failure_stage is not ShadowFailureStage.NONE:
            raise ValueError("NONE failure class must use NONE failure stage")
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
        execution_success: bool = True,
        verification_success: bool | None = None,
        quality_outcome: ShadowQualityOutcome | None = None,
        failure_class: ShadowFailureClass | None = None,
        failure_stage: ShadowFailureStage | None = None,
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
        verification_success = verified if verification_success is None else verification_success
        if quality_outcome is None:
            quality_outcome = (
                ShadowQualityOutcome.VERIFIED if verified else ShadowQualityOutcome.UNKNOWN
            )
        if failure_class is None:
            failure_class = ShadowFailureClass.NONE if verified else ShadowFailureClass.UNKNOWN_FAILURE
        if failure_stage is None:
            failure_stage = ShadowFailureStage.NONE if verified else ShadowFailureStage.INFRASTRUCTURE
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
            execution_success=execution_success,
            verification_success=verification_success,
            quality_outcome=quality_outcome,
            failure_class=failure_class,
            failure_stage=failure_stage,
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
    real_reset_cycles_observed: int
    synthetic_reset_cycles: int
    unknown_reset_observations: int
    collector_failure_observations: int
    quality_eligible_observations: int
    verified_observations: int
    model_task_failures: int
    verifier_failures: int
    operational_failures: int
    policy_blocks: int
    regressions: int
    recommendation_matches: int
    review_eligible: bool
    blocking_reasons: tuple[str, ...]


class ShadowGroupSummary(FrozenModel):
    provider_id: str
    quota_pool_id: str
    task_family: str
    execution_target_id: str
    observations: int
    verified_observations: int
    quality_eligible_observations: int
    model_task_failures: int
    verifier_failures: int
    operational_failures: int
    policy_blocks: int
    reset_cycles: int
    real_reset_cycles_observed: int
    review_eligible: bool
    blocking_reasons: tuple[str, ...]
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
    real_reset_cycles_observed: int
    quality_observations: int
    synthetic_reset_cycles: int
    unknown_reset_observations: int
    collector_failure_observations: int
    quality_eligible_observations: int
    review_eligible: bool
    model_task_failures: int
    verifier_failures: int
    operational_failures: int
    policy_blocks: int
    active_eligible_cohorts: tuple[str, ...] = ()
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


def _percentile(
    values: list[float],
    percentile: float,
    *,
    minimum_samples: int = 10,
) -> float | None:
    if len(values) < minimum_samples:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _total_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values), 10)


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


class ShadowEvidenceJournal:
    """Credential-free append-only filesystem journal keyed by observation identity."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "shadow-evidence"
        self.reset_cycle_directory = root / "reset-cycles"
        self.pending_directory = root / "pending-shadow"
        self.campaign_path = root / "shadow-campaign.json"

    def path_for(self, observation_id: str) -> Path:
        if not observation_id or any(part in observation_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe observation_id")
        return self.directory / f"{observation_id}.json"

    def reset_cycle_path_for(self, reset_cycle_id: str) -> Path:
        if not reset_cycle_id or any(part in reset_cycle_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe reset_cycle_id")
        return self.reset_cycle_directory / f"{reset_cycle_id}.json"

    def pending_path_for(self, pending_id: str) -> Path:
        if not pending_id or any(part in pending_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe pending_id")
        return self.pending_directory / f"{pending_id}.json"

    def append_reset_cycle(self, reference: ResetCycleReference) -> Path:
        target = self.reset_cycle_path_for(reference.reset_cycle_id)
        rendered = reference.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("reset_cycle_id already has different content")
            return target
        return _atomic_write_json(target, rendered)

    def load_reset_cycle(self, reset_cycle_id: str) -> ResetCycleReference:
        return ResetCycleReference.model_validate_json(
            self.reset_cycle_path_for(reset_cycle_id).read_text(encoding="utf-8")
        )

    def load_reset_cycles(self) -> tuple[ResetCycleReference, ...]:
        if not self.reset_cycle_directory.exists():
            return ()
        return tuple(
            ResetCycleReference.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.reset_cycle_directory.glob("*.json"))
        )

    def append_pending(self, pending: PendingShadowObservation) -> Path:
        target = self.pending_path_for(pending.pending_id)
        rendered = pending.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("pending_id already has different content")
            return target
        return _atomic_write_json(target, rendered)

    def load_pending(self, pending_id: str) -> PendingShadowObservation:
        return PendingShadowObservation.model_validate_json(
            self.pending_path_for(pending_id).read_text(encoding="utf-8")
        )

    def load_pending_all(self) -> tuple[PendingShadowObservation, ...]:
        if not self.pending_directory.exists():
            return ()
        return tuple(
            PendingShadowObservation.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.pending_directory.glob("*.json"))
        )

    def _reset_references_for(self, observation: ShadowObservation) -> tuple[ResetCycleReference, ...]:
        references: list[ResetCycleReference] = []
        for reset_cycle_id in observation.reset_cycle_ids:
            try:
                reference = self.load_reset_cycle(reset_cycle_id)
            except FileNotFoundError as error:
                raise ValueError(f"missing reset cycle reference: {reset_cycle_id}") from error
            if observation.provider_id is not None and reference.provider_id != observation.provider_id:
                raise ValueError("reset cycle provider_id does not match observation")
            if observation.quota_pool_id is not None and reference.quota_pool_id != observation.quota_pool_id:
                raise ValueError("reset cycle quota_pool_id does not match observation")
            references.append(reference)
        return tuple(references)

    def append(self, observation: ShadowObservation) -> Path:
        self._reset_references_for(observation)
        target = self.path_for(observation.observation_id)
        rendered = observation.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("observation_id already has different content")
            return target
        return _atomic_write_json(target, rendered)

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
        return _atomic_write_json(target, rendered)

    def load_campaign_state(self) -> ShadowCampaignState | None:
        try:
            return ShadowCampaignState.model_validate_json(
                self.campaign_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None

    def finalize_pending(
        self,
        pending_id: str,
        *,
        reset_cycle_ids: tuple[str, ...] = (),
        quota_after_snapshot_ids: tuple[str, ...] = (),
        observed_burn_fraction: float | None = None,
        execution_success: bool = True,
        verification_success: bool | None = None,
        quality_outcome: ShadowQualityOutcome | None = None,
        failure_class: ShadowFailureClass | None = None,
        failure_stage: ShadowFailureStage | None = None,
        verified: bool,
        regression_detected: bool = False,
        attempts_to_green: int | None = None,
        time_to_green_seconds: float | None = None,
        handoff_count: int = 0,
        observed_at: datetime,
    ) -> Path:
        pending = self.load_pending(pending_id)
        observation = ShadowObservation.build(
            task_id=pending.task_id,
            request_id=pending.request_id,
            decision_id=pending.decision_id,
            reset_cycle_ids=reset_cycle_ids,
            manual_execution_target_id=pending.manual_execution_target_id,
            scheduler_execution_target_id=pending.scheduler_execution_target_id,
            catalog_snapshot_id=pending.catalog_snapshot_id,
            policy_snapshot_id=pending.policy_snapshot_id,
            quota_snapshot_ids=pending.quota_snapshot_ids,
            quota_after_snapshot_ids=quota_after_snapshot_ids,
            provider_id=pending.provider_id,
            quota_pool_id=pending.quota_pool_id,
            task_family=pending.task_family,
            quota_confidence=pending.quota_confidence,
            collector_status=pending.collector_status,
            predicted_burn_fraction=pending.predicted_burn_fraction,
            observed_burn_fraction=observed_burn_fraction,
            execution_success=execution_success,
            verification_success=verification_success,
            quality_outcome=quality_outcome,
            failure_class=failure_class,
            failure_stage=failure_stage,
            verified=verified,
            regression_detected=regression_detected,
            attempts_to_green=attempts_to_green,
            time_to_green_seconds=time_to_green_seconds,
            handoff_count=handoff_count,
            observed_at=observed_at,
        )
        return self.append(observation)

    @staticmethod
    def _cohort_id(
        *,
        provider_id: str,
        quota_pool_id: str,
        execution_target_id: str,
        task_family: str,
    ) -> str:
        return "|".join((provider_id, quota_pool_id, execution_target_id, task_family))

    def _real_reset_ids_for_items(
        self,
        items: list[ShadowObservation],
    ) -> set[str]:
        ids: set[str] = set()
        for item in items:
            for reference in self._reset_references_for(item):
                if reference.counts_for_real_acceptance:
                    ids.add(reference.reset_cycle_id)
        return ids

    def _synthetic_reset_ids_for_items(
        self,
        items: list[ShadowObservation],
    ) -> set[str]:
        ids: set[str] = set()
        for item in items:
            for reference in self._reset_references_for(item):
                if reference.source is ResetCycleSource.SYNTHETIC_TEST:
                    ids.add(reference.reset_cycle_id)
        return ids

    @staticmethod
    def _collector_failure_observations(items: list[ShadowObservation]) -> int:
        return sum(
            item.collector_status
            in {
                QuotaCollectionStatus.AUTH_REQUIRED,
                QuotaCollectionStatus.RATE_LIMITED,
                QuotaCollectionStatus.PROVIDER_ERROR,
                QuotaCollectionStatus.UNKNOWN,
            }
            for item in items
        )

    @staticmethod
    def _model_task_failures(items: list[ShadowObservation]) -> int:
        return sum(item.failure_class is ShadowFailureClass.MODEL_TASK_FAILURE for item in items)

    @staticmethod
    def _verifier_failures(items: list[ShadowObservation]) -> int:
        return sum(item.failure_class is ShadowFailureClass.VERIFIER_FAILURE for item in items)

    @staticmethod
    def _operational_failures(items: list[ShadowObservation]) -> int:
        operational = {
            ShadowFailureClass.WORKER_INVOCATION_FAILURE,
            ShadowFailureClass.WORKER_PROCESS_FAILURE,
            ShadowFailureClass.AUTH_FAILURE,
            ShadowFailureClass.TIMEOUT,
            ShadowFailureClass.INFRA_FAILURE,
            ShadowFailureClass.CANCELLED,
            ShadowFailureClass.UNKNOWN_FAILURE,
        }
        return sum(item.failure_class in operational for item in items)

    @staticmethod
    def _policy_blocks(items: list[ShadowObservation]) -> int:
        return sum(item.failure_class is ShadowFailureClass.POLICY_BLOCK for item in items)

    @staticmethod
    def _quality_eligible_observations(items: list[ShadowObservation]) -> int:
        return sum(
            item.failure_class
            in {
                ShadowFailureClass.NONE,
                ShadowFailureClass.MODEL_TASK_FAILURE,
                ShadowFailureClass.VERIFIER_FAILURE,
            }
            for item in items
        )

    def _eligibility_blockers(
        self,
        items: list[ShadowObservation],
        *,
        minimum_observations: int,
        minimum_real_reset_cycles: int,
        require_zero_regressions: bool,
    ) -> list[str]:
        verified = sum(item.verified for item in items)
        regressions = sum(item.regression_detected for item in items)
        real_reset_cycles = len(self._real_reset_ids_for_items(items))
        blockers: list[str] = []
        if len(items) < minimum_observations:
            blockers.append(f"need at least {minimum_observations} observations; have {len(items)}")
        if real_reset_cycles < minimum_real_reset_cycles:
            blockers.append(
                f"need at least {minimum_real_reset_cycles} real reset cycles; have {real_reset_cycles}"
            )
        if verified != len(items):
            blockers.append("not every Shadow observation has a verified outcome")
        if require_zero_regressions and regressions:
            blockers.append(f"{regressions} verified regression(s) observed")
        return blockers

    def summarize(
        self,
        *,
        minimum_observations: int = 20,
        minimum_reset_cycles: int = 2,
        require_zero_regressions: bool = True,
    ) -> ShadowEvidenceSummary:
        observations = list(self.load_all())
        reset_cycles = {cycle for item in observations for cycle in item.reset_cycle_ids}
        real_reset_cycles = self._real_reset_ids_for_items(observations)
        synthetic_reset_cycles = self._synthetic_reset_ids_for_items(observations)
        unknown_quota = sum(item.quota_confidence is EvidenceConfidence.UNKNOWN for item in observations)
        verified = sum(item.verified for item in observations)
        regressions = sum(item.regression_detected for item in observations)
        matches = sum(item.recommendation_followed for item in observations)
        blockers = self._eligibility_blockers(
            observations,
            minimum_observations=minimum_observations,
            minimum_real_reset_cycles=minimum_reset_cycles,
            require_zero_regressions=require_zero_regressions,
        )
        return ShadowEvidenceSummary(
            observations=len(observations),
            reset_cycles=len(reset_cycles),
            real_reset_cycles_observed=len(real_reset_cycles),
            synthetic_reset_cycles=len(synthetic_reset_cycles),
            unknown_reset_observations=unknown_quota,
            collector_failure_observations=self._collector_failure_observations(observations),
            quality_eligible_observations=self._quality_eligible_observations(observations),
            verified_observations=verified,
            model_task_failures=self._model_task_failures(observations),
            verifier_failures=self._verifier_failures(observations),
            operational_failures=self._operational_failures(observations),
            policy_blocks=self._policy_blocks(observations),
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
        observations = list(self.load_all())
        policy = (
            state.acceptance_policy
            if state is not None
            else ShadowAcceptancePolicy(
                minimum_observations=minimum_observations,
                minimum_real_reset_cycles=minimum_reset_cycles,
                require_zero_regressions=require_zero_regressions,
            )
        )
        base = self.summarize(
            minimum_observations=policy.minimum_observations,
            minimum_reset_cycles=policy.minimum_real_reset_cycles,
            require_zero_regressions=policy.require_zero_regressions,
        )
        groups: list[ShadowGroupSummary] = []
        keys = sorted(
            {
                (
                    item.provider_id or "unknown",
                    item.quota_pool_id or "unknown",
                    item.manual_execution_target_id,
                    item.task_family,
                )
                for item in observations
            }
        )
        active_eligible_cohorts: list[str] = []
        for provider_id, quota_pool_id, execution_target_id, task_family in keys:
            items = [
                item
                for item in observations
                if (item.provider_id or "unknown") == provider_id
                and (item.quota_pool_id or "unknown") == quota_pool_id
                and item.manual_execution_target_id == execution_target_id
                and item.task_family == task_family
            ]
            reset_cycles = {cycle for item in items for cycle in item.reset_cycle_ids}
            real_reset_cycles = self._real_reset_ids_for_items(items)
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
            collector_failures = self._collector_failure_observations(items)
            group_blockers = self._eligibility_blockers(
                items,
                minimum_observations=policy.minimum_observations,
                minimum_real_reset_cycles=policy.minimum_real_reset_cycles,
                require_zero_regressions=policy.require_zero_regressions,
            )
            cohort_id = self._cohort_id(
                provider_id=provider_id,
                quota_pool_id=quota_pool_id,
                execution_target_id=execution_target_id,
                task_family=task_family,
            )
            if not group_blockers:
                active_eligible_cohorts.append(cohort_id)
            groups.append(
                ShadowGroupSummary(
                    provider_id=provider_id,
                    quota_pool_id=quota_pool_id,
                    task_family=task_family,
                    execution_target_id=execution_target_id,
                    observations=len(items),
                    verified_observations=verified,
                    quality_eligible_observations=self._quality_eligible_observations(items),
                    model_task_failures=self._model_task_failures(items),
                    verifier_failures=self._verifier_failures(items),
                    operational_failures=self._operational_failures(items),
                    policy_blocks=self._policy_blocks(items),
                    reset_cycles=len(reset_cycles),
                    real_reset_cycles_observed=len(real_reset_cycles),
                    review_eligible=not group_blockers,
                    blocking_reasons=tuple(group_blockers),
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
        required = policy.minimum_real_reset_cycles
        no_observation_blocker = ("no evidence cohorts have observations",) if not groups else ()
        return ShadowCampaignSummary(
            campaign_id=None if state is None else state.campaign_id,
            status=None if state is None else state.status,
            started_at=None if state is None else state.started_at,
            head=None if state is None else state.head,
            catalog_snapshot_id=None if state is None else state.catalog_snapshot_id,
            policy_snapshot_id=None if state is None else state.policy_snapshot_id,
            reset_cycles_required=required,
            reset_cycles_observed=base.reset_cycles,
            real_reset_cycles_observed=base.real_reset_cycles_observed,
            quality_observations=base.observations,
            synthetic_reset_cycles=base.synthetic_reset_cycles,
            unknown_reset_observations=base.unknown_reset_observations,
            collector_failure_observations=base.collector_failure_observations,
            quality_eligible_observations=base.quality_eligible_observations,
            review_eligible=bool(groups) and all(group.review_eligible for group in groups),
            model_task_failures=base.model_task_failures,
            verifier_failures=base.verifier_failures,
            operational_failures=base.operational_failures,
            policy_blocks=base.policy_blocks,
            active_eligible_cohorts=tuple(active_eligible_cohorts),
            production_active_authorized=False,
            groups=tuple(groups),
            blocking_reasons=base.blocking_reasons + no_observation_blocker,
        )


__all__ = [
    "PendingShadowObservation",
    "ResetCycleSource",
    "ResetCycleReference",
    "ShadowAcceptancePolicy",
    "ShadowCampaignState",
    "ShadowCampaignStatus",
    "ShadowCampaignSummary",
    "ShadowEvidenceJournal",
    "ShadowEvidenceSummary",
    "ShadowFailureClass",
    "ShadowFailureStage",
    "ShadowGroupSummary",
    "ShadowObservation",
    "ShadowQualityOutcome",
]
