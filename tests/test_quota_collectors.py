from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence, QuotaState
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionStatus,
    QuotaTransportError,
)
from personal_ai_orchestrator.quota_collectors.minimax import (
    MINIMAX_QUOTA_ENDPOINT,
    MiniMaxQuotaCollector,
    normalize_minimax_quota,
)
from personal_ai_orchestrator.quota_collectors.zai import (
    ZAI_QUOTA_ENDPOINT,
    ZAIQuotaCollector,
    normalize_zai_quota,
)
from personal_ai_orchestrator.quota_observability import QuotaWindowKind

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def minimax_payload() -> dict[str, Any]:
    return {
        "model_remains": [
            {
                "model_name": "MiniMax-M3",
                "start_time": ms(NOW - timedelta(hours=3)),
                "end_time": ms(NOW + timedelta(hours=2)),
                "current_interval_remaining_percent": 80,
                "current_interval_total_count": 1000,
                "current_interval_usage_count": 200,
                "weekly_start_time": ms(NOW - timedelta(days=3, hours=12)),
                "weekly_end_time": ms(NOW + timedelta(days=3, hours=12)),
                "current_weekly_remaining_percent": 20,
                "current_weekly_total_count": 5000,
                "current_weekly_usage_count": 4000,
            }
        ]
    }


class FakeTransport:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: QuotaTransportError | None = None,
    ) -> None:
        self.payload = payload or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        self.calls.append((url, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.payload


def test_minimax_fixture_normalizes_two_exact_windows() -> None:
    snapshot = normalize_minimax_quota(minimax_payload(), observed_at=NOW)

    five_hour, weekly = snapshot.windows
    assert snapshot.confidence is EvidenceConfidence.EXACT
    assert five_hour.window_kind is QuotaWindowKind.FIVE_HOUR
    assert weekly.window_kind is QuotaWindowKind.WEEKLY
    assert five_hour.remaining_fraction == pytest.approx(0.8)
    assert weekly.remaining_fraction == pytest.approx(0.2)
    assert five_hour.pace() == pytest.approx(2.0)
    assert weekly.pace() == pytest.approx(0.4)
    assert snapshot.effective_pace() == pytest.approx(0.4)
    assert snapshot.source.source_uri == MINIMAX_QUOTA_ENDPOINT


def test_minimax_missing_weekly_percentage_does_not_invent_precision() -> None:
    payload = minimax_payload()
    payload["model_remains"][0].pop("current_weekly_remaining_percent")

    snapshot = normalize_minimax_quota(payload, observed_at=NOW)

    assert snapshot.confidence is EvidenceConfidence.UNKNOWN
    assert snapshot.windows[0].confidence is EvidenceConfidence.EXACT
    assert snapshot.windows[1].confidence is EvidenceConfidence.UNKNOWN
    assert snapshot.windows[1].remaining_fraction is None


def test_minimax_collector_requires_explicit_supported_credential_input() -> None:
    result = MiniMaxQuotaCollector(bearer_token=None, transport=FakeTransport()).collect()

    assert result.status is QuotaCollectionStatus.AUTH_REQUIRED
    assert result.error_category == "AUTHENTICATION_INTEGRATION_BLOCKED"
    assert result.snapshot is None


def test_minimax_collector_is_read_only_and_normalized() -> None:
    transport = FakeTransport(minimax_payload())
    collector = MiniMaxQuotaCollector(
        bearer_token="fixture-only-value",
        transport=transport,
        timeout=3.0,
    )

    result = collector.collect()

    assert result.status is QuotaCollectionStatus.SUCCESS
    assert result.snapshot is not None
    assert result.snapshot.provider_id == "minimax"
    assert len(transport.calls) == 1
    url, headers, timeout = transport.calls[0]
    assert url == MINIMAX_QUOTA_ENDPOINT
    assert headers["Authorization"].startswith("Bearer ")
    assert timeout == 3.0


def test_provider_error_is_not_converted_to_exhausted_quota() -> None:
    transport = FakeTransport(
        error=QuotaTransportError(QuotaCollectionStatus.PROVIDER_ERROR, "PROVIDER_UNAVAILABLE")
    )

    result = MiniMaxQuotaCollector(
        bearer_token="fixture-only-value",
        transport=transport,
    ).collect()

    assert result.status is QuotaCollectionStatus.PROVIDER_ERROR
    assert result.snapshot is None
    assert result.error_category == "PROVIDER_UNAVAILABLE"


def test_zai_mocked_fixture_preserves_derived_confidence() -> None:
    payload = {
        "data": {
            "limits": [
                {
                    "type": "TOKENS_LIMIT",
                    "percentage": 60,
                }
            ]
        }
    }

    snapshot = normalize_zai_quota(payload, observed_at=NOW)

    window = snapshot.windows[0]
    assert snapshot.confidence is EvidenceConfidence.ESTIMATED
    assert window.remaining_fraction == pytest.approx(0.4)
    assert window.used_fraction == pytest.approx(0.6)
    assert window.reset_at is None
    assert window.pace() is None
    assert snapshot.source.source_uri == ZAI_QUOTA_ENDPOINT


def test_zai_missing_percentage_stays_unknown() -> None:
    snapshot = normalize_zai_quota(
        {"data": {"limits": [{"type": "TOKENS_LIMIT"}]}},
        observed_at=NOW,
    )

    assert snapshot.confidence is EvidenceConfidence.UNKNOWN
    assert snapshot.state is QuotaState.UNKNOWN
    assert snapshot.windows[0].remaining_fraction is None


def test_zai_runtime_auth_is_deferred_without_token() -> None:
    result = ZAIQuotaCollector(
        authorization_token=None,
        transport=FakeTransport(),
    ).collect()

    assert result.status is QuotaCollectionStatus.AUTH_REQUIRED
    assert result.error_category == "AUTHENTICATION_INTEGRATION_BLOCKED"
