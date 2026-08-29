import json
from datetime import UTC, datetime

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    QuotaSnapshot,
    QuotaState,
)
from personal_ai_orchestrator.quota_cache import QuotaSnapshotCache

NOW = datetime(2026, 8, 29, 8, tzinfo=UTC)


def test_v1_cache_is_ignored_after_replay_identity_upgrade(tmp_path) -> None:
    snapshot = QuotaSnapshot(
        quota_pool_id="pool",
        observed_at=NOW,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=EvidenceSource(
            source_type=EvidenceSourceType.LOCAL_OBSERVATION,
            observed_at=NOW,
            confidence=EvidenceConfidence.UNKNOWN,
        ),
    )
    cache = QuotaSnapshotCache(tmp_path)
    old_snapshot = snapshot.model_dump(mode="json")
    old_snapshot.pop("id")
    cache.path_for("pool").write_text(
        json.dumps({"schema_version": 1, "snapshot": old_snapshot}),
        encoding="utf-8",
    )

    assert cache.load("pool") is None
