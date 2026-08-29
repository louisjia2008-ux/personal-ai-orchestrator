"""Temporal-scarcity traces over the canonical model-registry quota snapshots.

P3 does not define a second quota domain. Provider collectors, the model registry, cache,
explanations, and the future scheduler all share ``model_registry.QuotaSnapshot`` and
``model_registry.QuotaWindowSnapshot``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import (
    EvidenceSource as QuotaEvidenceSource,
    QuotaSnapshot,
    QuotaWindowKind,
    QuotaWindowSnapshot,
    RegistryModel,
)


class ScarcityClass(StrEnum):
    CRITICAL = "CRITICAL"
    CONSERVE = "CONSERVE"
    ON_PACE = "ON_PACE"
    SURPLUS = "SURPLUS"
    HARVEST = "HARVEST"
    UNKNOWN = "UNKNOWN"


class ScarcityThresholds(RegistryModel):
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


class QuotaWindowPaceTrace(RegistryModel):
    window_id: str = Field(min_length=1)
    window_kind: QuotaWindowKind
    remaining_fraction: float | None
    remaining_time_fraction: float | None
    reset_at: datetime | None
    pace: float | None
    scarcity_class: ScarcityClass


class QuotaPaceTrace(RegistryModel):
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
    reference = at or snapshot.observation_time()
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
        quota_pool_id=snapshot.quota_pool_id or "UNKNOWN",
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
    source = snapshot.source.reference or snapshot.source.source_type.value
    lines.extend(
        [
            f"effective pace: {effective}",
            f"scarcity class: {trace.scarcity_class.value}",
            f"Source: {source}",
            f"As of: {snapshot.observation_time().isoformat()}",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SCARCITY_THRESHOLDS",
    "QuotaEvidenceSource",
    "QuotaPaceTrace",
    "QuotaSnapshot",
    "QuotaWindowKind",
    "QuotaWindowPaceTrace",
    "QuotaWindowSnapshot",
    "ScarcityClass",
    "ScarcityThresholds",
    "build_pace_trace",
    "render_quota_explanation",
]
