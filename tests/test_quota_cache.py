from datetime import UTC, datetime, timedelta

from personal_ai_orchestrator.quota_cache import (
    QuotaSnapshotCache,
    refresh_with_last_known_good,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.quota_collectors.minimax import normalize_minimax_quota

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def snapshot():
    return normalize_minimax_quota(
        {
            "model_remains": [
                {
                    "model_name": "MiniMax-M3",
                    "start_time": ms(NOW - timedelta(hours=3)),
                    "end_time": ms(NOW + timedelta(hours=2)),
                    "current_interval_remaining_percent": 80,
                    "weekly_start_time": ms(NOW - timedelta(days=3, hours=12)),
                    "weekly_end_time": ms(NOW + timedelta(days=3, hours=12)),
                    "current_weekly_remaining_percent": 20,
                }
            ]
        },
        observed_at=NOW,
    )


class StaticCollector:
    def __init__(self, result: QuotaCollectionResult) -> None:
        self.result = result

    def collect(self) -> QuotaCollectionResult:
        return self.result


def test_cache_round_trip_and_atomic_temp_cleanup(tmp_path) -> None:
    value = snapshot()
    cache = QuotaSnapshotCache(tmp_path)

    path = cache.write(value)
    restored = cache.load(value.quota_pool_id)

    assert restored == value
    assert path.exists()
    assert list(tmp_path.glob(f".{path.name}.*")) == []


def test_persisted_snapshot_contains_no_credential_fields_or_values(tmp_path) -> None:
    value = snapshot()
    cache = QuotaSnapshotCache(tmp_path)

    path = cache.write(value)
    rendered = path.read_text(encoding="utf-8").lower()

    assert "authorization" not in rendered
    assert "api_key" not in rendered
    assert "bearer " not in rendered
    assert "fixture-only-value" not in rendered


def test_last_known_good_survives_refresh_failure(tmp_path) -> None:
    value = snapshot()
    cache = QuotaSnapshotCache(tmp_path)
    cache.write(value)
    collector = StaticCollector(
        QuotaCollectionResult(
            status=QuotaCollectionStatus.PROVIDER_ERROR,
            error_category="PROVIDER_UNAVAILABLE",
        )
    )

    result = refresh_with_last_known_good(
        collector,
        cache,
        quota_pool_id=value.quota_pool_id,
    )

    assert result.status is QuotaCollectionStatus.STALE
    assert result.last_known_good == value
    assert result.snapshot is None
    assert result.error_category == "PROVIDER_UNAVAILABLE"


def test_refresh_failure_without_cache_remains_provider_error(tmp_path) -> None:
    cache = QuotaSnapshotCache(tmp_path)
    collector = StaticCollector(
        QuotaCollectionResult(
            status=QuotaCollectionStatus.PROVIDER_ERROR,
            error_category="TIMEOUT",
        )
    )

    result = refresh_with_last_known_good(
        collector,
        cache,
        quota_pool_id="minimax-token-plan",
    )

    assert result.status is QuotaCollectionStatus.PROVIDER_ERROR
    assert result.snapshot is None
    assert result.last_known_good is None


def test_successful_refresh_replaces_last_known_good(tmp_path) -> None:
    value = snapshot()
    cache = QuotaSnapshotCache(tmp_path)
    collector = StaticCollector(
        QuotaCollectionResult(
            status=QuotaCollectionStatus.SUCCESS,
            snapshot=value,
        )
    )

    result = refresh_with_last_known_good(
        collector,
        cache,
        quota_pool_id=value.quota_pool_id,
    )

    assert result.status is QuotaCollectionStatus.SUCCESS
    assert cache.load(value.quota_pool_id) == value
