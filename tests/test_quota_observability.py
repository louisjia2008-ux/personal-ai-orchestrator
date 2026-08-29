from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSourceType,
    QuotaSnapshot as RegistryQuotaSnapshot,
    QuotaState,
    QuotaWindowSnapshot as RegistryQuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_observability import (
    DEFAULT_SCARCITY_THRESHOLDS,
    QuotaEvidenceSource,
    QuotaSnapshot,
    QuotaWindowKind,
    QuotaWindowSnapshot,
    ScarcityClass,
    build_pace_trace,
    render_quota_explanation,
)

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def source(
    *,
    observed_at: datetime = NOW,
    confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
) -> QuotaEvidenceSource:
    return QuotaEvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        reference="https://provider.example/quota",
        observed_at=observed_at,
        confidence=confidence,
    )


def window(
    window_id: str,
    *,
    remaining_fraction: float | None,
    started: datetime | None,
    reset: datetime | None,
    confidence: EvidenceConfidence = EvidenceConfidence.EXACT,
    observed_at: datetime = NOW,
    kind: QuotaWindowKind = QuotaWindowKind.CUSTOM,
    duration_seconds: float | None = None,
) -> QuotaWindowSnapshot:
    return QuotaWindowSnapshot(
        window_id=window_id,
        window_kind=kind,
        duration_seconds=duration_seconds,
        window_started_at=started,
        reset_at=reset,
        remaining_fraction=remaining_fraction,
        state=QuotaState.AVAILABLE if remaining_fraction else QuotaState.EXHAUSTED,
        confidence=confidence,
        source=source(observed_at=observed_at, confidence=confidence),
    )


def snapshot(*windows: QuotaWindowSnapshot) -> QuotaSnapshot:
    return QuotaSnapshot(
        quota_pool_id="minimax-coding-plan",
        provider_id="minimax",
        plan_id="coding-plan",
        observed_at=NOW,
        windows=windows,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source(),
    )


def test_p3_reuses_model_registry_quota_domain() -> None:
    assert QuotaSnapshot is RegistryQuotaSnapshot
    assert QuotaWindowSnapshot is RegistryQuotaWindowSnapshot


def test_snapshot_serialization_round_trip() -> None:
    value = snapshot(
        window(
            "5h",
            remaining_fraction=0.8,
            started=NOW - timedelta(hours=3),
            reset=NOW + timedelta(hours=2),
            kind=QuotaWindowKind.FIVE_HOUR,
        )
    )

    restored = QuotaSnapshot.model_validate_json(value.model_dump_json())

    assert restored == value
    assert restored.source.reference == "https://provider.example/quota"


def test_exact_precise_value_is_valid() -> None:
    observed = window(
        "5h",
        remaining_fraction=0.42,
        started=NOW - timedelta(hours=1),
        reset=NOW + timedelta(hours=4),
    )

    assert observed.remaining_fraction == pytest.approx(0.42)
    assert observed.confidence is EvidenceConfidence.EXACT


def test_unknown_confidence_rejects_precise_fraction() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN confidence"):
        window(
            "week",
            remaining_fraction=0.5,
            started=NOW - timedelta(days=2),
            reset=NOW + timedelta(days=5),
            confidence=EvidenceConfidence.UNKNOWN,
        )


def test_multiple_windows_effective_pace_uses_minimum() -> None:
    five_hour = window(
        "5h",
        remaining_fraction=0.8,
        started=NOW - timedelta(hours=3),
        reset=NOW + timedelta(hours=2),
        kind=QuotaWindowKind.FIVE_HOUR,
    )
    weekly = window(
        "weekly",
        remaining_fraction=0.2,
        started=NOW - timedelta(days=3, hours=12),
        reset=NOW + timedelta(days=3, hours=12),
        kind=QuotaWindowKind.WEEKLY,
    )
    value = snapshot(five_hour, weekly)

    assert five_hour.pace() == pytest.approx(2.0)
    assert weekly.pace() == pytest.approx(0.4)
    assert value.effective_pace() == pytest.approx(0.4)
    trace = build_pace_trace(value)
    assert trace.effective_pace == pytest.approx(0.4)
    assert trace.scarcity_class is ScarcityClass.CRITICAL


def test_zero_remaining_quota_is_critical() -> None:
    value = snapshot(
        window(
            "5h",
            remaining_fraction=0.0,
            started=NOW - timedelta(hours=2),
            reset=NOW + timedelta(hours=3),
        )
    )

    assert value.effective_pace() == 0.0
    assert build_pace_trace(value).scarcity_class is ScarcityClass.CRITICAL


def test_window_near_reset_can_be_harvest_without_guessing() -> None:
    value = snapshot(
        window(
            "5h",
            remaining_fraction=0.2,
            started=NOW - timedelta(hours=4, minutes=55),
            reset=NOW + timedelta(minutes=5),
        )
    )

    assert value.effective_pace() == pytest.approx(12.0)
    assert build_pace_trace(value).scarcity_class is ScarcityClass.HARVEST


def test_expired_window_has_unknown_pace() -> None:
    expired = window(
        "5h",
        remaining_fraction=0.5,
        started=NOW - timedelta(hours=6),
        reset=NOW - timedelta(hours=1),
        observed_at=NOW,
    )

    assert expired.remaining_time_fraction() is None
    assert expired.pace() is None


def test_missing_reset_at_has_unknown_pace() -> None:
    no_reset = window(
        "weekly",
        remaining_fraction=0.5,
        started=None,
        reset=None,
        duration_seconds=7 * 24 * 3600,
    )

    assert no_reset.pace() is None


def test_missing_remaining_fraction_has_unknown_pace() -> None:
    no_remaining = window(
        "weekly",
        remaining_fraction=None,
        started=NOW - timedelta(days=2),
        reset=NOW + timedelta(days=5),
    )

    assert no_remaining.pace() is None


def test_stale_snapshot() -> None:
    value = snapshot()

    assert value.is_stale(as_of=NOW + timedelta(minutes=10), max_age_seconds=300)
    assert not value.is_stale(as_of=NOW + timedelta(minutes=4), max_age_seconds=300)


@pytest.mark.parametrize(
    ("pace", "expected"),
    [
        (0.4999, ScarcityClass.CRITICAL),
        (0.5, ScarcityClass.CONSERVE),
        (0.7999, ScarcityClass.CONSERVE),
        (0.8, ScarcityClass.ON_PACE),
        (1.2, ScarcityClass.ON_PACE),
        (1.2001, ScarcityClass.SURPLUS),
        (1.5, ScarcityClass.SURPLUS),
        (1.5001, ScarcityClass.HARVEST),
        (None, ScarcityClass.UNKNOWN),
    ],
)
def test_scarcity_classification_boundaries(
    pace: float | None,
    expected: ScarcityClass,
) -> None:
    assert DEFAULT_SCARCITY_THRESHOLDS.classify(pace) is expected


def test_explanation_is_rendered_from_actual_trace() -> None:
    value = snapshot(
        window(
            "5h",
            remaining_fraction=0.8,
            started=NOW - timedelta(hours=3),
            reset=NOW + timedelta(hours=2),
        )
    )

    rendered = render_quota_explanation("MiniMax", "Coding Plan", value)

    assert "Provider: MiniMax" in rendered
    assert "Observation: EXACT" in rendered
    assert "pace: 2.000" in rendered
    assert "effective pace: 2.000" in rendered
    assert "scarcity class: HARVEST" in rendered
    assert "https://provider.example/quota" in rendered
