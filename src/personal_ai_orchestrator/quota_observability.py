"""Normalized provider quota observations and temporal-scarcity traces.

P3 deliberately models only quota truth. Provider-specific collectors normalize into
``QuotaSnapshot``; scheduling decisions remain out of scope until P3.5.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSourceType,
    QuotaState,
)


class QuotaWindowKind(StrEnum):
    FIVE_HOUR = "FIVE_HOUR"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    DAILY = "DAILY"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class ScarcityClass(StrEnum):
    CRITICAL = "CRITICAL"
    CONSERVE = "CONSERVE"
    ON_PACE = "ON_PACE"
    SURPLUS = "SURPLUS"
    HARVEST = "HARVEST"
    UNKNOWN = "UNKNOWN"


class QuotaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class QuotaEvidenceSource(QuotaModel):
    source_type: EvidenceSourceType
    source_uri: str | None = None
    observed_at: datetime
    confidence: EvidenceConfidence
    note: str | None = None

    @model_validator(mode="after")
    def validate_observed_at(self) -> QuotaEvidenceSource:
        _require_aware(self.observed_at, "observed_at")
        return self


class QuotaWindowSnapshot(QuotaModel):
    window_id: str = Field(min_length=1)
    window_kind: QuotaWindowKind = QuotaWindowKind.UNKNOWN
    duration_seconds: float | None = Field(default=None, gt=0)
    window_started_at: datetime | None = None
    reset_at: datetime | None = None
    remaining_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    used_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    remaining_units: float | None = Field(default=None, ge=0.0)
    used_units: float | None = Field(default=None, ge=0.0)
    unit: str | None = None
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: QuotaEvidenceSource

    @model_validator(mode="after")
    def validate_window(self) -> QuotaWindowSnapshot:
        if self.window_started_at is not None:
            _require_aware(self.window_started_at, "window_started_at")
        if self.reset_at is not None:
            _require_aware(self.reset_at, "reset_at")
        if (
            self.window_started_at is not None
            and self.reset_at is not None
            and self.reset_at <= self.window_started_at
        ):
            raise ValueError("reset_at must be after window_started_at")

        precise_values = (
            self.remaining_fraction,
            self.used_fraction,
            self.remaining_units,
            self.used_units,
        )
        if self.confidence is EvidenceConfidence.UNKNOWN and any(
            value is not None for value in precise_values
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise quota values")

        if self.remaining_fraction is not None and self.used_fraction is not None:
            if abs((self.remaining_fraction + self.used_fraction) - 1.0) > 1e-6:
                raise ValueError("remaining_fraction + used_fraction must equal 1")
        return self

    def remaining_time_fraction(self, *, at: datetime | None = None) -> float | None:
        """Return the fraction of the reset window remaining, when observable."""

        reference = at or self.source.observed_at
        _require_aware(reference, "at")
        if self.reset_at is None or reference >= self.reset_at:
            return None
        if self.window_started_at is not None and reference < self.window_started_at:
            return None

        duration = self.duration_seconds
        if duration is None and self.window_started_at is not None:
            duration = (self.reset_at - self.window_started_at).total_seconds()
        if duration is None or duration <= 0:
            return None

        remaining = (self.reset_at - reference).total_seconds()
        fraction = remaining / duration
        if not isfinite(fraction) or fraction <= 0:
            return None
        return min(1.0, fraction)

    def pace(self, *, at: datetime | None = None) -> float | None:
        """remaining quota fraction / remaining time fraction."""

        if self.remaining_fraction is None or self.confidence is EvidenceConfidence.UNKNOWN:
            return None
        time_fraction = self.remaining_time_fraction(at=at)
        if time_fraction is None or time_fraction <= 0:
            return None
        value = self.remaining_fraction / time_fraction
        return value if isfinite(value) else None


class QuotaSnapshot(QuotaModel):
    schema_version: int = Field(default=1, ge=1)
    quota_pool_id: str = Field(min_length=1)
    provider_id: str | None = None
    account_id: str | None = None
    plan_id: str | None = None
    observed_at: datetime
    windows: tuple[QuotaWindowSnapshot, ...] = ()
    remaining_units: float | None = Field(default=None, ge=0.0)
    used_units: float | None = Field(default=None, ge=0.0)
    unit: str | None = None
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: QuotaEvidenceSource

    @model_validator(mode="after")
    def validate_snapshot(self) -> QuotaSnapshot:
        _require_aware(self.observed_at, "observed_at")
        if self.confidence is EvidenceConfidence.UNKNOWN and (
            self.remaining_units is not None or self.used_units is not None
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise quota units")
        ids = [window.window_id for window in self.windows]
        if len(ids) != len(set(ids)):
            raise ValueError("quota snapshot contains duplicate window_id values")
        return self

    def effective_pace(self, *, at: datetime | None = None) -> float | None:
        paces = [pace for window in self.windows if (pace := window.pace(at=at)) is not None]
        return min(paces) if paces else None

    def is_stale(self, *, as_of: datetime, max_age_seconds: float) -> bool:
        _require_aware(as_of, "as_of")
        return (as_of - self.observed_at).total_seconds() > max_age_seconds


class ScarcityThresholds(QuotaModel):
    critical_below: float = 0.5
    conserve_below: float = 0.8
    on_pace_upper: float = 1.2
    surplus_upper: float = 1.5

    @model_validator(mode="after")
    def validate_order(self) -> ScarcityThresholds:
        values = (
            self.critical_below,
            self.conserve_below,
            self.on_pace_upper,
            self.surplus_upper,
        )
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("scarcity thresholds must be strictly increasing")
        return self

    def classify(self, pace: float | None) -> ScarcityClass:
        if pace is None or not isfinite(pace):
            return ScarcityClass.UNKNOWN
        if pace < self.critical_below:
            return ScarcityClass.CRITICAL
        if pace < self.conserve_below:
            return ScarcityClass.CONSERVE
        if pace <= self.on_pace_upper:
            return ScarcityClass.ON_PACE
        if pace <= self.surplus_upper:
            return ScarcityClass.SURPLUS
        return ScarcityClass.HARVEST


DEFAULT_SCARCITY_THRESHOLDS = ScarcityThresholds()


class QuotaWindowPaceTrace(QuotaModel):
    window_id: str
    window_kind: QuotaWindowKind
    remaining_fraction: float | None
    remaining_time_fraction: float | None
    reset_at: datetime | None
    pace: float | None
    scarcity_class: ScarcityClass


class QuotaPaceTrace(QuotaModel):
    quota_pool_id: str
    observed_at: datetime
    windows: tuple[QuotaWindowPaceTrace, ...]
    effective_pace: float | None
    scarcity_class: ScarcityClass


def build_pace_trace(
    snapshot: QuotaSnapshot,
    *,
    at: datetime | None = None,
    thresholds: ScarcityThresholds = DEFAULT_SCARCITY_THRESHOLDS,
) -> QuotaPaceTrace:
    reference = at or snapshot.observed_at
    windows: list[QuotaWindowPaceTrace] = []
    valid_paces: list[float] = []
    for window in snapshot.windows:
        remaining_time_fraction = window.remaining_time_fraction(at=reference)
        pace = window.pace(at=reference)
        if pace is not None:
            valid_paces.append(pace)
        windows.append(
            QuotaWindowPaceTrace(
                window_id=window.window_id,
                window_kind=window.window_kind,
                remaining_fraction=window.remaining_fraction,
                remaining_time_fraction=remaining_time_fraction,
                reset_at=window.reset_at,
                pace=pace,
                scarcity_class=thresholds.classify(pace),
            )
        )
    effective = min(valid_paces) if valid_paces else None
    return QuotaPaceTrace(
        quota_pool_id=snapshot.quota_pool_id,
        observed_at=reference,
        windows=tuple(windows),
        effective_pace=effective,
        scarcity_class=thresholds.classify(effective),
    )


def render_quota_explanation(
    provider_name: str,
    pool_name: str,
    snapshot: QuotaSnapshot,
    *,
    at: datetime | None = None,
    thresholds: ScarcityThresholds = DEFAULT_SCARCITY_THRESHOLDS,
) -> str:
    """Render a human explanation directly from the computed pace trace."""

    trace = build_pace_trace(snapshot, at=at, thresholds=thresholds)
    lines = [
        f"Provider: {provider_name}",
        f"Pool: {pool_name}",
        f"Observation: {snapshot.confidence.value}",
    ]
    for window in trace.windows:
        remaining = (
            "UNKNOWN"
            if window.remaining_fraction is None
            else f"{window.remaining_fraction:.1%}"
        )
        reset = window.reset_at.isoformat() if window.reset_at is not None else "UNKNOWN"
        pace = "UNKNOWN" if window.pace is None else f"{window.pace:.3f}"
        lines.extend(
            [
                f"{window.window_id}:",
                f"  remaining: {remaining}",
                f"  reset: {reset}",
                f"  pace: {pace}",
            ]
        )
    effective = "UNKNOWN" if trace.effective_pace is None else f"{trace.effective_pace:.3f}"
    source = snapshot.source.source_uri or snapshot.source.source_type.value
    lines.extend(
        [
            f"effective pace: {effective}",
            f"scarcity class: {trace.scarcity_class.value}",
            f"Source: {source}",
            f"As of: {snapshot.observed_at.isoformat()}",
        ]
    )
    return "\n".join(lines)
