"""Resumable Shadow campaign queue state separate from execution evidence."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import RegistryModel
from personal_ai_orchestrator.shadow_evidence import ShadowFailureClass


class CampaignCaseState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    DEFERRED_QUOTA = "DEFERRED_QUOTA"
    CANCELLED = "CANCELLED"


class CampaignCaseLike(Protocol):
    case_id: str
    task_family: str


class CampaignCaseRecord(RegistryModel):
    case_id: str = Field(min_length=1)
    task_family: str = Field(min_length=1)
    state: CampaignCaseState = CampaignCaseState.PENDING
    execution_target_id: str | None = None
    run_count: int = Field(default=0, ge=0)
    last_observation_id: str | None = None
    last_failure_class: ShadowFailureClass | None = None
    deferred_reason_code: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_updated_at(self) -> CampaignCaseRecord:
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.state is CampaignCaseState.DEFERRED_QUOTA and self.deferred_reason_code is None:
            raise ValueError("DEFERRED_QUOTA requires deferred_reason_code")
        return self


class CampaignQueueSnapshot(RegistryModel):
    campaign_id: str = Field(min_length=1)
    execution_target_id: str = Field(min_length=1)
    cases: tuple[CampaignCaseRecord, ...]
    circuit_breaker_trips: int = Field(default=0, ge=0)
    quota_deferred_cases: int = Field(default=0, ge=0)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> CampaignQueueSnapshot:
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("campaign queue contains duplicate case_id values")
        return self

    def record_for(self, case_id: str) -> CampaignCaseRecord:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(case_id)

    @property
    def pending_case_ids(self) -> tuple[str, ...]:
        return tuple(
            item.case_id
            for item in self.cases
            if item.state is CampaignCaseState.PENDING
        )

    @property
    def deferred_case_ids(self) -> tuple[str, ...]:
        return tuple(
            item.case_id
            for item in self.cases
            if item.state is CampaignCaseState.DEFERRED_QUOTA
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


class CampaignQueueJournal:
    def __init__(self, root: Path) -> None:
        self.path = root / "shadow-campaign-queue.json"

    def load(self) -> CampaignQueueSnapshot | None:
        if not self.path.exists():
            return None
        return CampaignQueueSnapshot.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, snapshot: CampaignQueueSnapshot) -> Path:
        return _atomic_write_json(
            self.path,
            snapshot.model_dump_json(indent=2) + "\n",
        )

    def ensure(
        self,
        *,
        campaign_id: str,
        execution_target_id: str,
        cases: Sequence[CampaignCaseLike],
        now: datetime,
    ) -> CampaignQueueSnapshot:
        existing = self.load()
        case_records = {
            item.case_id: item
            for item in (existing.cases if existing is not None else ())
        }
        records: list[CampaignCaseRecord] = []
        for case in cases:
            records.append(
                case_records.get(case.case_id)
                or CampaignCaseRecord(
                    case_id=case.case_id,
                    task_family=case.task_family,
                    updated_at=now,
                )
            )
        snapshot = CampaignQueueSnapshot(
            campaign_id=campaign_id,
            execution_target_id=execution_target_id,
            cases=tuple(records),
            circuit_breaker_trips=0
            if existing is None
            else existing.circuit_breaker_trips,
            quota_deferred_cases=sum(
                item.state is CampaignCaseState.DEFERRED_QUOTA for item in records
            ),
            updated_at=now,
        )
        self.save(snapshot)
        return snapshot

    def mark_running(
        self,
        case_id: str,
        *,
        execution_target_id: str,
        now: datetime,
    ) -> CampaignQueueSnapshot:
        snapshot = self.load()
        if snapshot is None:
            raise RuntimeError("campaign queue must be initialized before mark_running")
        records = []
        for item in snapshot.cases:
            if item.case_id == case_id:
                records.append(
                    item.model_copy(
                        update={
                            "state": CampaignCaseState.RUNNING,
                            "execution_target_id": execution_target_id,
                            "run_count": item.run_count + 1,
                            "updated_at": now,
                        }
                    )
                )
            else:
                records.append(item)
        updated = snapshot.model_copy(update={"cases": tuple(records), "updated_at": now})
        self.save(updated)
        return updated

    def mark_result(
        self,
        case_id: str,
        *,
        verified: bool,
        failure_class: ShadowFailureClass,
        observation_id: str | None,
        now: datetime,
    ) -> CampaignQueueSnapshot:
        snapshot = self.load()
        if snapshot is None:
            raise RuntimeError("campaign queue must be initialized before mark_result")
        if failure_class is ShadowFailureClass.POLICY_BLOCK:
            state = CampaignCaseState.DEFERRED_QUOTA
            deferred_reason_code = "POLICY_BLOCK"
        elif verified:
            state = CampaignCaseState.VERIFIED
            deferred_reason_code = None
        else:
            state = CampaignCaseState.FAILED
            deferred_reason_code = None
        records = []
        for item in snapshot.cases:
            if item.case_id == case_id:
                records.append(
                    item.model_copy(
                        update={
                            "state": state,
                            "last_observation_id": observation_id,
                            "last_failure_class": failure_class,
                            "deferred_reason_code": deferred_reason_code,
                            "updated_at": now,
                        }
                    )
                )
            else:
                records.append(item)
        updated = snapshot.model_copy(
            update={
                "cases": tuple(records),
                "quota_deferred_cases": sum(
                    item.state is CampaignCaseState.DEFERRED_QUOTA for item in records
                ),
                "updated_at": now,
            }
        )
        self.save(updated)
        return updated

    def defer_unfinished_due_to_quota(
        self,
        *,
        reason_code: str,
        now: datetime,
    ) -> CampaignQueueSnapshot:
        snapshot = self.load()
        if snapshot is None:
            raise RuntimeError("campaign queue must be initialized before deferral")
        records: list[CampaignCaseRecord] = []
        for item in snapshot.cases:
            if item.state in {CampaignCaseState.PENDING, CampaignCaseState.RUNNING}:
                records.append(
                    item.model_copy(
                        update={
                            "state": CampaignCaseState.DEFERRED_QUOTA,
                            "deferred_reason_code": reason_code,
                            "updated_at": now,
                        }
                    )
                )
            else:
                records.append(item)
        updated = snapshot.model_copy(
            update={
                "cases": tuple(records),
                "circuit_breaker_trips": snapshot.circuit_breaker_trips + 1,
                "quota_deferred_cases": sum(
                    item.state is CampaignCaseState.DEFERRED_QUOTA for item in records
                ),
                "updated_at": now,
            }
        )
        self.save(updated)
        return updated

    def resume_deferred(
        self,
        *,
        now: datetime,
    ) -> CampaignQueueSnapshot:
        snapshot = self.load()
        if snapshot is None:
            raise RuntimeError("campaign queue must be initialized before resume")
        records = tuple(
            item.model_copy(
                update={
                    "state": CampaignCaseState.PENDING,
                    "deferred_reason_code": None,
                    "updated_at": now,
                }
            )
            if item.state is CampaignCaseState.DEFERRED_QUOTA
            else item
            for item in snapshot.cases
        )
        updated = snapshot.model_copy(
            update={
                "cases": records,
                "quota_deferred_cases": 0,
                "updated_at": now,
            }
        )
        self.save(updated)
        return updated


__all__ = [
    "CampaignCaseRecord",
    "CampaignCaseLike",
    "CampaignCaseState",
    "CampaignQueueJournal",
    "CampaignQueueSnapshot",
]
