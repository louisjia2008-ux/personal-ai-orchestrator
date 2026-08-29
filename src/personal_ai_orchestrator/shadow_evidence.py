"""Append-only Shadow Mode evidence for falsifiable routing validation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class ShadowEvidenceJournal:
    """Credential-free append-only filesystem journal keyed by observation identity."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "shadow-evidence"

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


__all__ = ["ShadowEvidenceJournal", "ShadowEvidenceSummary", "ShadowObservation"]
