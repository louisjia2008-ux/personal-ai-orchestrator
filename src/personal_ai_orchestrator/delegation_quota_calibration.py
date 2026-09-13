"""Opt-in, non-authoritative quota-pair capture for PI-5B3E SHADOW campaigns."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from personal_ai_orchestrator.model_registry import EvidenceConfidence, RegistryModel
from personal_ai_orchestrator.provider_acceptance import assert_sanitized


class QuotaPairComparisonReason(StrEnum):
    OK = "OK"
    BEFORE_MISSING = "BEFORE_MISSING"
    AFTER_MISSING = "AFTER_MISSING"
    POOL_MISMATCH = "POOL_MISMATCH"
    UNKNOWN_CONFIDENCE = "UNKNOWN_CONFIDENCE"
    WINDOWS_MISSING = "WINDOWS_MISSING"
    IMPRECISE_WINDOW = "IMPRECISE_WINDOW"
    WINDOW_SET_MISMATCH = "WINDOW_SET_MISMATCH"
    OBSERVATION_TIME_REGRESSED = "OBSERVATION_TIME_REGRESSED"
    SNAPSHOT_ID_REUSED = "SNAPSHOT_ID_REUSED"


class DelegationQuotaWindowBaseline(RegistryModel):
    window_id: str = Field(min_length=1)
    window_kind: str = Field(min_length=1)
    reset_at: datetime
    remaining_fraction: float = Field(ge=0.0, le=1.0)


class DelegationQuotaBaselineRecord(RegistryModel):
    schema_version: int = Field(default=1, ge=1)
    observation_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    observed_at: datetime
    windows: tuple[DelegationQuotaWindowBaseline, ...]


class DelegationQuotaPairResult(RegistryModel):
    comparable: bool
    reason: QuotaPairComparisonReason
    quota_before_snapshot_id: str | None = None
    quota_after_snapshot_id: str | None = None


class DelegationQuotaCalibrationJournal:
    """Append-only baseline records; outcome records keep the validated pair ids."""

    def __init__(self, root: str | Path) -> None:
        self.directory = Path(root) / "delegation-quota-calibration"

    def path_for(self, observation_id: str) -> Path:
        if not observation_id or any(
            part in observation_id for part in ("/", "\\", "..")
        ):
            raise ValueError("unsafe delegation quota observation id")
        return self.directory / f"{observation_id}.json"

    def append(self, record: DelegationQuotaBaselineRecord) -> Path:
        assert_sanitized(record.model_dump(mode="json"))
        target = self.path_for(record.observation_id)
        rendered = record.model_dump_json(indent=2) + "\n"
        if target.exists():
            existing = DelegationQuotaBaselineRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            if existing != record:
                raise ValueError(
                    "delegation quota baseline identity already has different content"
                )
            return target
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(self, observation_id: str) -> DelegationQuotaBaselineRecord | None:
        path = self.path_for(observation_id)
        if not path.exists():
            return None
        return DelegationQuotaBaselineRecord.model_validate_json(
            path.read_text(encoding="utf-8")
        )


def _snapshot_windows(snapshot: Any) -> tuple[DelegationQuotaWindowBaseline, ...] | None:
    try:
        reference = snapshot.observation_time()
        active = snapshot.active_windows(at=reference)
    except Exception:
        return None
    if not active:
        return None
    rows: list[DelegationQuotaWindowBaseline] = []
    for window in active:
        if (
            window.confidence is EvidenceConfidence.UNKNOWN
            or window.remaining_fraction is None
            or window.reset_at is None
        ):
            return None
        rows.append(
            DelegationQuotaWindowBaseline(
                window_id=window.window_id,
                window_kind=window.window_kind.value,
                reset_at=window.reset_at,
                remaining_fraction=window.remaining_fraction,
            )
        )
    rows.sort(
        key=lambda item: (
            item.window_id,
            item.window_kind,
            item.reset_at.isoformat(),
        )
    )
    return tuple(rows)


def build_quota_baseline(
    *,
    observation_id: str,
    provider_id: str,
    quota_pool_id: str,
    snapshot: Any | None,
) -> DelegationQuotaBaselineRecord | None:
    """Freeze one precise before snapshot already present in the host quota cache."""

    if snapshot is None or snapshot.quota_pool_id != quota_pool_id:
        return None
    if snapshot.confidence is EvidenceConfidence.UNKNOWN:
        return None
    windows = _snapshot_windows(snapshot)
    if windows is None:
        return None
    try:
        observed_at = snapshot.observation_time()
    except Exception:
        return None
    return DelegationQuotaBaselineRecord(
        observation_id=observation_id,
        provider_id=provider_id,
        quota_pool_id=quota_pool_id,
        snapshot_id=snapshot.id,
        observed_at=observed_at,
        windows=windows,
    )


def compare_quota_after(
    baseline: DelegationQuotaBaselineRecord | None,
    after_snapshot: Any | None,
) -> DelegationQuotaPairResult:
    """Validate comparability only; never attribute the delta to the child."""

    if baseline is None:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.BEFORE_MISSING,
        )
    if after_snapshot is None:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.AFTER_MISSING,
        )
    if after_snapshot.quota_pool_id != baseline.quota_pool_id:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.POOL_MISMATCH,
        )
    if after_snapshot.confidence is EvidenceConfidence.UNKNOWN:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.UNKNOWN_CONFIDENCE,
        )
    if after_snapshot.id == baseline.snapshot_id:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.SNAPSHOT_ID_REUSED,
        )
    try:
        observed_at = after_snapshot.observation_time()
    except Exception:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.AFTER_MISSING,
        )
    if observed_at < baseline.observed_at:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.OBSERVATION_TIME_REGRESSED,
        )
    after_windows = _snapshot_windows(after_snapshot)
    if after_windows is None:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.IMPRECISE_WINDOW,
        )
    before_keys = tuple(
        (window.window_id, window.window_kind, window.reset_at)
        for window in baseline.windows
    )
    after_keys = tuple(
        (window.window_id, window.window_kind, window.reset_at)
        for window in after_windows
    )
    if before_keys != after_keys:
        return DelegationQuotaPairResult(
            comparable=False,
            reason=QuotaPairComparisonReason.WINDOW_SET_MISMATCH,
        )
    return DelegationQuotaPairResult(
        comparable=True,
        reason=QuotaPairComparisonReason.OK,
        quota_before_snapshot_id=baseline.snapshot_id,
        quota_after_snapshot_id=after_snapshot.id,
    )


__all__ = [
    "DelegationQuotaBaselineRecord",
    "DelegationQuotaCalibrationJournal",
    "DelegationQuotaPairResult",
    "DelegationQuotaWindowBaseline",
    "QuotaPairComparisonReason",
    "build_quota_baseline",
    "compare_quota_after",
]
