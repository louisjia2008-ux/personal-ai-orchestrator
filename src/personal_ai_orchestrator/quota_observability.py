"""Temporal-scarcity traces over the canonical model-registry quota snapshots.

P3 does not define a second quota domain. Provider collectors, the model registry, cache,
explanations, and the scheduler all share ``model_registry.QuotaSnapshot`` and
``model_registry.QuotaWindowSnapshot``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import EvidenceSource as QuotaEvidenceSource
from personal_ai_orchestrator.model_registry import (
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
    quota_snapshot_id: str
    observed_at: datetime
    windows: tuple[QuotaWindowPaceTrace, ...]
    known_min_pace: float | None
    effective_pace: float | None
    all_binding_windows_known: bool
    scarcity_class: ScarcityClass


def build_pace_trace(
    snapshot: QuotaSnapshot,
    *,
    at: datetime | None = None,
    thresholds: ScarcityThresholds = DEFAULT_SCARCITY_THRESHOLDS,
    required_window_kinds: tuple[QuotaWindowKind, ...] = (),
) -> QuotaPaceTrace:
    reference = at or snapshot.observation_time()
    windows: list[QuotaWindowPaceTrace] = []
    for window in snapshot.windows:
        remaining_time_fraction = window.remaining_time_fraction(at=reference)
        pace = window.pace(at=reference)
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

    known_min = snapshot.known_min_pace(
        at=reference,
        required_window_kinds=required_window_kinds,
    )
    effective = snapshot.effective_pace(
        at=reference,
        required_window_kinds=required_window_kinds,
    )
    active = snapshot.active_windows(
        at=reference,
        required_window_kinds=required_window_kinds,
    )
    required = set(required_window_kinds)
    present = {window.window_kind for window in active}
    required_complete = not required or required.issubset(present)
    all_known = (
        required_complete
        and bool(active)
        and all(window.pace(at=reference) is not None for window in active)
    )
    if not required_complete:
        effective = None
    return QuotaPaceTrace(
        quota_pool_id=snapshot.quota_pool_id or "UNKNOWN",
        quota_snapshot_id=snapshot.id,
        observed_at=reference,
        windows=tuple(windows),
        known_min_pace=known_min,
        effective_pace=effective,
        all_binding_windows_known=all_known,
        scarcity_class=thresholds.classify(effective),
    )


def render_quota_explanation(
    provider_name: str,
    pool_name: str,
    snapshot: QuotaSnapshot,
    *,
    at: datetime | None = None,
    thresholds: ScarcityThresholds = DEFAULT_SCARCITY_THRESHOLDS,
    required_window_kinds: tuple[QuotaWindowKind, ...] = (),
) -> str:
    """Render a human explanation directly from the computed pace trace."""

    trace = build_pace_trace(
        snapshot,
        at=at,
        thresholds=thresholds,
        required_window_kinds=required_window_kinds,
    )
    lines = [
        f"Provider: {provider_name}",
        f"Pool: {pool_name}",
        f"Quota snapshot: {snapshot.id}",
        f"Observation: {snapshot.confidence.value}",
    ]
    for window in trace.windows:
        remaining = (
            "UNKNOWN" if window.remaining_fraction is None else f"{window.remaining_fraction:.1%}"
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
    known_min = "UNKNOWN" if trace.known_min_pace is None else f"{trace.known_min_pace:.3f}"
    effective = "UNKNOWN" if trace.effective_pace is None else f"{trace.effective_pace:.3f}"
    source = snapshot.source.reference or snapshot.source.source_type.value
    lines.extend(
        [
            f"known minimum pace: {known_min}",
            # Preserve the established explanation key for existing consumers while
            # additionally making its stricter routing semantics explicit below.
            f"effective pace: {effective}",
            f"effective routing pace: {effective}",
            f"all binding windows known: {str(trace.all_binding_windows_known).lower()}",
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
