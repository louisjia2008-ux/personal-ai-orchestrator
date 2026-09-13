"""Host-owned bounded SHADOW calibration campaign state for PI-5B3F.

The campaign only decides whether an already-occurring delegation observation
may collect PI-5B3E quota evidence. It cannot create a task, choose a target,
admit quota, launch/retry/cancel a worker, or change verification authority.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from personal_ai_orchestrator.model_registry import RegistryModel
from personal_ai_orchestrator.provider_acceptance import assert_sanitized

SCHEMA = "delegation-calibration-campaign-v1"
MAX_CAMPAIGN_OBSERVATIONS = 1000
MAX_CAMPAIGN_PROJECTS = 64


class DelegationCampaignState(StrEnum):
    OFF = "OFF"
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    STOPPED = "STOPPED"


class DelegationCampaignSnapshot(RegistryModel):
    schema: str = SCHEMA
    campaign_id: str | None = None
    state: DelegationCampaignState = DelegationCampaignState.OFF
    max_observations: int | None = Field(default=None, ge=1, le=MAX_CAMPAIGN_OBSERVATIONS)
    project_ids: tuple[str, ...] = ()
    admitted_observations: dict[str, str] = Field(default_factory=dict)
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    updated_at: datetime | None = None
    reason_code: str = "CAMPAIGN_OFF"

    @property
    def consumed_observations(self) -> int:
        return len(self.admitted_observations)

    @property
    def remaining_observations(self) -> int | None:
        if self.max_observations is None:
            return None
        return max(0, self.max_observations - self.consumed_observations)


class DelegationCalibrationCampaignStore:
    """Thread-safe atomic campaign store; missing/corrupt state fails closed OFF."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self._lock = threading.RLock()
        self._snapshot = self._load()

    @staticmethod
    def _off(reason_code: str = "CAMPAIGN_OFF") -> DelegationCampaignSnapshot:
        return DelegationCampaignSnapshot(reason_code=reason_code)

    def _load(self) -> DelegationCampaignSnapshot:
        if self.path is None or not self.path.exists():
            return self._off()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            snapshot = DelegationCampaignSnapshot.model_validate(payload)
            assert_sanitized(snapshot.model_dump(mode="json"))
        except (OSError, ValueError, json.JSONDecodeError):
            return self._off("CAMPAIGN_STATE_INVALID")
        if snapshot.schema != SCHEMA:
            return self._off("CAMPAIGN_SCHEMA_UNSUPPORTED")
        if snapshot.state is DelegationCampaignState.ACTIVE:
            if (
                snapshot.max_observations is None
                or snapshot.consumed_observations >= snapshot.max_observations
            ):
                return snapshot.model_copy(
                    update={
                        "state": DelegationCampaignState.EXHAUSTED,
                        "reason_code": "BUDGET_EXHAUSTED",
                    }
                )
        return snapshot

    def _persist(self, snapshot: DelegationCampaignSnapshot) -> None:
        assert_sanitized(snapshot.model_dump(mode="json"))
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = snapshot.model_dump_json(indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def snapshot(self) -> DelegationCampaignSnapshot:
        with self._lock:
            return self._snapshot.model_copy(deep=True)

    @staticmethod
    def _validate_scope(max_observations: int, project_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= max_observations <= MAX_CAMPAIGN_OBSERVATIONS:
            raise ValueError("max_observations_out_of_range")
        normalized = tuple(sorted(set(project_ids)))
        if len(normalized) > MAX_CAMPAIGN_PROJECTS:
            raise ValueError("too_many_project_ids")
        if any(not item.strip() for item in normalized):
            raise ValueError("project_ids_must_not_be_blank")
        return normalized

    def start(
        self,
        *,
        max_observations: int,
        project_ids: tuple[str, ...] = (),
    ) -> DelegationCampaignSnapshot:
        projects = self._validate_scope(max_observations, project_ids)
        with self._lock:
            if self._snapshot.state is DelegationCampaignState.ACTIVE:
                raise ValueError("campaign_already_active")
            now = datetime.now(UTC)
            snapshot = DelegationCampaignSnapshot(
                campaign_id=f"delegation-campaign-{uuid4().hex}",
                state=DelegationCampaignState.ACTIVE,
                max_observations=max_observations,
                project_ids=projects,
                admitted_observations={},
                started_at=now,
                stopped_at=None,
                updated_at=now,
                reason_code="OWNER_STARTED",
            )
            self._persist(snapshot)
            self._snapshot = snapshot
            return snapshot.model_copy(deep=True)

    def stop(self) -> DelegationCampaignSnapshot:
        with self._lock:
            current = self._snapshot
            if current.state in {DelegationCampaignState.OFF, DelegationCampaignState.STOPPED}:
                return current.model_copy(deep=True)
            now = datetime.now(UTC)
            snapshot = current.model_copy(
                update={
                    "state": DelegationCampaignState.STOPPED,
                    "stopped_at": now,
                    "updated_at": now,
                    "reason_code": "OWNER_STOPPED",
                }
            )
            self._persist(snapshot)
            self._snapshot = snapshot
            return snapshot.model_copy(deep=True)

    def claim(self, *, observation_id: str, project_id: str) -> bool:
        """Reserve one campaign slot for an immutable observation, idempotently."""
        if not observation_id or not project_id:
            return False
        with self._lock:
            current = self._snapshot
            existing_project = current.admitted_observations.get(observation_id)
            if existing_project is not None:
                return existing_project == project_id
            if current.state is not DelegationCampaignState.ACTIVE:
                return False
            if current.project_ids and project_id not in current.project_ids:
                return False
            if current.max_observations is None:
                return False
            if current.consumed_observations >= current.max_observations:
                exhausted = current.model_copy(
                    update={
                        "state": DelegationCampaignState.EXHAUSTED,
                        "updated_at": datetime.now(UTC),
                        "reason_code": "BUDGET_EXHAUSTED",
                    }
                )
                self._persist(exhausted)
                self._snapshot = exhausted
                return False

            admitted = dict(current.admitted_observations)
            admitted[observation_id] = project_id
            state = (
                DelegationCampaignState.EXHAUSTED
                if len(admitted) >= current.max_observations
                else DelegationCampaignState.ACTIVE
            )
            snapshot = current.model_copy(
                update={
                    "admitted_observations": admitted,
                    "state": state,
                    "updated_at": datetime.now(UTC),
                    "reason_code": (
                        "BUDGET_EXHAUSTED"
                        if state is DelegationCampaignState.EXHAUSTED
                        else "OBSERVATION_ADMITTED"
                    ),
                }
            )
            self._persist(snapshot)
            self._snapshot = snapshot
            return True

    def is_admitted(self, *, observation_id: str, project_id: str) -> bool:
        """Already-reserved observations may finish after EXHAUSTED/STOPPED."""
        with self._lock:
            return self._snapshot.admitted_observations.get(observation_id) == project_id


__all__ = [
    "DelegationCalibrationCampaignStore",
    "DelegationCampaignSnapshot",
    "DelegationCampaignState",
    "MAX_CAMPAIGN_OBSERVATIONS",
    "MAX_CAMPAIGN_PROJECTS",
    "SCHEMA",
]
