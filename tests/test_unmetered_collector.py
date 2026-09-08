"""M1 WP4 — UnmeteredQuotaCollector contract.

The collector never opens a network socket. Its output mirrors the
windowed collectors' shape: ``QuotaSnapshot`` carrying one UNMETERED
``QuotaWindowSnapshot`` (used_fraction=None so ``quota_burn.assess``
short-circuits to ``BurnPressure.UNMETERED``) and a
``PlanQuotaProjection`` for the dispatch panel's plan-first view.
"""

from __future__ import annotations

from datetime import UTC, datetime

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityState
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.quota_collectors.unmetered import (
    UnmeteredQuotaCollector,
)

FIXED = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_collector_emits_single_unmetered_window() -> None:
    collector = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=("big-pickle",),
        observed_at=FIXED,
    )
    result = collector.collect()
    assert result.status is QuotaCollectionStatus.SUCCESS
    assert result.snapshot is not None
    windows = result.snapshot.windows
    assert len(windows) == 1
    window = windows[0]
    from personal_ai_orchestrator.model_registry import QuotaWindowKind

    assert window.window_kind is QuotaWindowKind.UNMETERED
    assert window.used_fraction is None
    assert window.remaining_fraction is None
    assert window.duration_seconds is None
    assert result.snapshot.observed_at == FIXED


def test_collector_windows_passes_assess_unmetered_short_circuit() -> None:
    from personal_ai_orchestrator.model_registry import QuotaWindowKind
    from personal_ai_orchestrator.quota_burn import BurnPressure, assess

    collector = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=(),
        observed_at=FIXED,
    )
    result = collector.collect()
    assert result.snapshot is not None
    window = result.snapshot.windows[0]
    assert window.window_kind is QuotaWindowKind.UNMETERED
    assessment = assess(
        window_started_at=window.window_started_at,
        reset_at=FIXED,
        used_fraction=window.used_fraction,
        now=FIXED,
    )
    assert assessment.pressure is BurnPressure.UNMETERED


def test_collector_projection_marks_covered_models() -> None:
    collector = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=("big-pickle", "mimo-v2.5-free"),
        observed_at=FIXED,
    )
    result = collector.collect()
    assert result.projection is not None
    assert result.projection.pool.covered_model_ids == ("big-pickle", "mimo-v2.5-free")
    assert result.projection.pool.shared_across_models is True


def test_collector_evidence_method_emits_unmetered_baseline() -> None:
    collector = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=("big-pickle",),
        observed_at=FIXED,
    )
    evidence = collector.evidence(
        execution_target_id="opencode-big-pickle",
        quota_pool_id="opencode",
    )
    assert evidence.state is QuotaAvailabilityState.AVAILABLE_UNMETERED
    assert (
        evidence.previous_state_baseline
        is QuotaAvailabilityState.AVAILABLE_UNMETERED
    )
    assert evidence.consecutive_failures == 0
    # ``observe_success`` defaults confidence to ESTIMATED; the
    # unmetered path keeps that default because the source is local.
    assert evidence.confidence is EvidenceConfidence.ESTIMATED


def test_collector_is_deterministic_for_tests() -> None:
    collector = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=("big-pickle",),
        observed_at=FIXED,
    )
    first = collector.collect()
    second = collector.collect()
    assert first.model_dump_json() == second.model_dump_json()
