"""Damaged display projections must not hide connected providers or real snapshots."""

import json
from pathlib import Path

import pytest

from personal_ai_orchestrator.quota_cache import PlanProjectionCache
from personal_ai_orchestrator.quota_collectors.zai import ZAIQuotaCollector
from personal_ai_orchestrator.quota_refresh import QuotaObservationState, QuotaRefreshService
from tests.test_quota_refresh import _RecordingTransport


def _service(root: Path) -> QuotaRefreshService:
    return QuotaRefreshService(
        runtime_state_root=root,
        connected_provider_ids=lambda: ("zai-coding-plan",),
        collectors={
            "zai-coding-plan": ZAIQuotaCollector(
                authorization_token="fixture-only",
                transport=_RecordingTransport(
                    {
                        "data": {
                            "limits": [
                                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 30.0}
                            ]
                        }
                    }
                ),
            )
        },
        environ={},
        auth_store_paths=(),
    )


@pytest.mark.parametrize("damage", ["invalid_utf8", "directory"])
@pytest.mark.parametrize("has_snapshot", [False, True])
def test_unreadable_projection_preserves_provider_and_snapshot(tmp_path, damage, has_snapshot):
    service = _service(tmp_path)
    before = service.refresh()[0] if has_snapshot else service.observations()[0]
    path = PlanProjectionCache(tmp_path / "quota").path_for("zai-coding-plan")
    path.parent.mkdir(exist_ok=True)
    path.unlink(missing_ok=True)
    if damage == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff\xfe")

    after = service.observations()[0]
    assert after.provider_id == "zai-coding-plan"
    assert after.snapshot == before.snapshot
    assert after.projection is None
    assert after.observation_state == (
        QuotaObservationState.OBSERVED if has_snapshot else QuotaObservationState.UNKNOWN
    )


def test_misplaced_projection_is_not_reported_as_another_pools_balance(tmp_path):
    service = _service(tmp_path)
    before = service.refresh()[0]
    assert before.projection is not None
    cache = PlanProjectionCache(tmp_path / "quota")
    path = cache.path_for("zai-coding-plan")
    payload = json.loads(path.read_text())
    payload["projection"]["pool"]["pool_id"] = "unrelated-pool"
    path.write_text(json.dumps(payload))

    after = service.observations()[0]
    assert after.snapshot == before.snapshot
    assert after.projection is None
