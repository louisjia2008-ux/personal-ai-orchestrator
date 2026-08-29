from datetime import UTC, datetime, timedelta
import json

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_capabilities import quota_observability
from personal_ai_orchestrator.quota_cli import load_snapshot
from personal_ai_orchestrator.quota_collectors.minimax import normalize_minimax_quota

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def test_quota_observability_answers_provider_plan_identity() -> None:
    minimax = quota_observability("minimax", "token-plan")
    zai = quota_observability("zai", "coding-plan")

    assert minimax.official_api is True
    assert minimax.confidence is EvidenceConfidence.EXACT
    assert minimax.endpoint == "https://www.minimax.io/v1/token_plan/remains"
    assert minimax.runtime_status == "AUTHENTICATION_INTEGRATION_BLOCKED"
    assert zai.official_api is True
    assert zai.confidence is EvidenceConfidence.ESTIMATED
    assert zai.runtime_status == "DEFERRED_PENDING_QUOTA_RESET_OR_AUTH"


def test_unknown_provider_plan_has_no_fabricated_capability() -> None:
    with pytest.raises(LookupError, match="no quota observability audit"):
        quota_observability("unknown", "plan")


def test_cli_loader_accepts_atomic_cache_wrapper(tmp_path) -> None:
    snapshot = normalize_minimax_quota(
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
    path = tmp_path / "quota.json"
    path.write_text(
        json.dumps({"schema_version": 1, "snapshot": snapshot.model_dump(mode="json")}),
        encoding="utf-8",
    )

    restored = load_snapshot(path)

    assert restored == snapshot
