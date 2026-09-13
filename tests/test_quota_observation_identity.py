"""PI 5B2.2: cold-process quota identity regression. No provider/worker calls."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personal_ai_orchestrator.daemon import default_quota_collectors
from personal_ai_orchestrator.dispatch_executor import OwnerDispatchExecutor
from personal_ai_orchestrator.dispatch_recommendation_service import DispatchRecommendationService
from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry, QuotaState
from personal_ai_orchestrator.pi_provider_discovery import (
    PiAuthStatus,
    PiDiscoveryResult,
    PiDiscoveryState,
    PiProviderDiscovery,
    build_pi_registry,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    observe_exhaustion,
)
from personal_ai_orchestrator.quota_cache import QuotaSnapshotCache
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.quota_collectors.minimax import MiniMaxQuotaCollector
from personal_ai_orchestrator.quota_refresh import QUOTA_SOURCE_BY_PROVIDER, QuotaRefreshService
from personal_ai_orchestrator.runtime_quota_routing import quota_pool_id_for_target
from personal_ai_orchestrator.scheduler import RoutingObjective
from tests.quota_identity_fixtures import bind
from tests.test_quota_collectors import FakeTransport, minimax_payload

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)
PROVIDER = "minimax-cn-coding-plan"
TARGET = "pi-minimax-cn-coding-plan-MiniMax-M3"
POOL = "minimax-token-plan-cn"


def _deny_network(*_args, **_kwargs):
    raise AssertionError("network forbidden in quota identity regression")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)


def _registry():
    return build_pi_registry(PiDiscoveryResult(
        discovered_at=NOW, pi_path="fixture-only-never-executed", pi_version="fixture",
        state=PiDiscoveryState.DISCOVERED,
        providers=(PiProviderDiscovery(
            provider_id=PROVIDER, runtime_provider_id="minimax-cn", display_name="MiniMax CN",
            auth_status=PiAuthStatus.READY, model_skus=("MiniMax-M3", "MiniMax-M2.7"),
            observed_at=NOW,
        ),),
    ))


def _refresh(root):
    return QuotaRefreshService(
        runtime_state_root=root, connected_provider_ids=lambda: (PROVIDER, "minimax-cn"),
        environ={}, auth_store_paths=(), now=lambda: NOW,
    )


def _persist(root):
    collector = MiniMaxQuotaCollector(
        bearer_token="offline-fixture", transport=FakeTransport(minimax_payload()), now=lambda: NOW,
    )
    refresh = QuotaRefreshService(
        runtime_state_root=root, connected_provider_ids=lambda: (PROVIDER,),
        collectors={PROVIDER: collector}, environ={}, auth_store_paths=(), now=lambda: NOW,
    )
    observation = refresh.refresh(PROVIDER)[0]
    assert observation.snapshot.quota_pool_id == POOL
    return observation.snapshot


def _recommendation(root, registry=None, now=NOW):
    refresh = _refresh(root)
    assert not refresh._observed_pool_id
    registry = registry or _registry()
    service = DispatchRecommendationService(
        SimpleNamespace(record_system_event=lambda *_a, **_k: None),
        registry_provider=lambda: registry, quota_refresh_service=refresh,
        execution_evidence_journal=SimpleNamespace(
            latest_verified_for_target=lambda _: (SimpleNamespace(observed_at=now), None)),
        quota_availability_journal=QuotaAvailabilityJournal(root), tier_table=None,
        runtime_availability=None, runtime_availability_fallback=lambda _: True,
    )
    result, candidates, _ = service.recommend_for_task(
        SimpleNamespace(min_tier="T1"), policy=RoutingObjective.BALANCED, now=now,
    )
    return result, candidates


def _parent_admit(root, quota):
    # Exercise the actual startup admission method, without execute/spawn/worktrees.
    executor = OwnerDispatchExecutor.__new__(OwnerDispatchExecutor)
    executor._registry_provider = _registry
    executor._quota_availability_journal = QuotaAvailabilityJournal(root)
    executor.config = SimpleNamespace(require_quota_certainty=True)
    executor._quota_collectors = {PROVIDER: SimpleNamespace(collect=lambda: QuotaCollectionResult(
        status=QuotaCollectionStatus.SUCCESS, snapshot=quota))}
    with patch("personal_ai_orchestrator.dispatch_executor.datetime") as clock:
        clock.now.return_value = NOW
        return executor._admit_quota(
            SimpleNamespace(_audit=lambda *_a: None),
            SimpleNamespace(execution_target_id=TARGET, task_id="fixture", dispatch_id="fixture"),
        )


def _cold_report(root):
    refresh = _refresh(root)
    assert not refresh._observed_pool_id
    registry = _registry()
    pool = quota_pool_id_for_target(registry, execution_target_id=TARGET, now=NOW)
    quota = refresh.snapshot_for_pool(pool)
    assert quota is not None
    # Prove recommendation works before startup admission and cannot refresh.
    with (
        patch.object(QuotaRefreshService, "refresh", side_effect=AssertionError("no refresh")),
        patch.object(MiniMaxQuotaCollector, "collect", side_effect=AssertionError("no collect")),
    ):
        recommendation, candidates = _recommendation(root, registry)
    assert recommendation.top_pick is not None
    parent = _parent_admit(root, quota)
    assert parent.admitted
    assert any(c.execution_target_id == TARGET and c.remaining_fractions for c in candidates)
    assert all(c.remaining_fractions == (0.8, 0.2) for c in candidates)
    assert len(list((root / "quota").glob("quota-*.json"))) == 1
    return {
        "pool": pool, "snapshot_id": quota.id, "parent_pool": parent.evidence.quota_pool_id,
        "parent_state": parent.evidence.state.value,
        "eligible": [e.execution_target_id for e in recommendation.evaluations if e.admitted],
        "ephemeral_mapping": refresh._observed_pool_id, "model_calls": 0,
    }


def test_cross_process_minimax_readiness(tmp_path):
    env = {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    prefix = """
import socket
from pathlib import Path
from tests.test_quota_observation_identity import _persist, _cold_report, _deny_network
socket.socket.connect = _deny_network
socket.create_connection = _deny_network
"""
    subprocess.run([sys.executable, "-c", prefix + "\nimport sys; _persist(Path(sys.argv[1]))",
                    str(tmp_path)], check=True, env=env, capture_output=True, text=True, timeout=20)
    result = subprocess.run(
        [sys.executable, "-c", prefix +
         "\nimport json, sys; print(json.dumps(_cold_report(Path(sys.argv[1]))))", str(tmp_path)],
        check=True, env=env, capture_output=True, text=True, timeout=20,
    )
    report = json.loads(result.stdout)
    assert report["pool"] == report["parent_pool"] == POOL
    assert TARGET in report["eligible"]
    assert report["parent_state"] == "AVAILABLE_OBSERVED"
    assert report["ephemeral_mapping"] == {}
    assert report["model_calls"] == 0


def test_parent_and_child_use_same_persisted_pool(tmp_path):
    quota = _persist(tmp_path)
    report = _cold_report(tmp_path)
    assert report["snapshot_id"] == quota.id


def test_shared_observation_and_scarcity_across_targets(tmp_path):
    _persist(tmp_path)
    result, candidates = _recommendation(tmp_path)
    assert len([e for e in result.evaluations if e.admitted]) == 2
    assert all(len(c.remaining_fractions) == 2 for c in candidates)
    sibling = next(c.execution_target_id for c in candidates if c.execution_target_id != TARGET)
    QuotaAvailabilityJournal(tmp_path).save(observe_exhaustion(
        None, execution_target_id=sibling, provider_id="minimax-cn", quota_pool_id=POOL,
        observed_at=NOW, sanitized_reason_code="FIXTURE_EXHAUSTED",
    ))
    result, candidates = _recommendation(tmp_path)
    assert result.top_pick is None
    assert all(c.availability_state is QuotaAvailabilityState.COOLDOWN for c in candidates)
    assert len(list((tmp_path / "quota").glob("quota-*.json"))) == 1


def test_missing_observation_fails_closed(tmp_path):
    result, candidates = _recommendation(tmp_path)
    assert result.top_pick is None
    assert all(not c.remaining_fractions for c in candidates)
    assert all("no quota observation" in " ".join(e.reasons) for e in result.evaluations)


@pytest.mark.parametrize("case", ["stale", "future", "unknown", "exhausted", "expired"])
def test_invalid_observation_does_not_become_available(tmp_path, case):
    quota = _persist(tmp_path)
    now = NOW
    if case == "stale":
        now += timedelta(seconds=601)
    elif case == "future":
        now -= timedelta(seconds=1)
    elif case == "unknown":
        quota = quota.model_copy(update={"confidence": EvidenceConfidence.UNKNOWN})
    elif case == "exhausted":
        quota = quota.model_copy(update={"state": QuotaState.EXHAUSTED})
    elif case == "expired":
        now += timedelta(days=8)
    QuotaSnapshotCache(tmp_path / "quota").write(quota)
    result, _ = _recommendation(tmp_path, now=now)
    assert result.top_pick is None
    assert _refresh(tmp_path).snapshot_for_pool(POOL).id == quota.id


def test_unknown_provider_does_not_guess_by_target_name(tmp_path):
    registry = _registry()
    data = registry.model_dump(mode="json")
    raw = json.dumps(data).replace(PROVIDER, "unknown-coding-plan-cn")
    registry = ModelRegistry.model_validate_json(raw)
    target = next(iter(registry.execution_targets))
    _persist(tmp_path)
    assert quota_pool_id_for_target(registry, execution_target_id=target, now=NOW) is None
    result, _ = _recommendation(tmp_path, registry)
    assert result.top_pick is None


def test_explicit_binding_overrides_static_metadata(tmp_path):
    registry = _registry()
    model = registry.execution_targets[TARGET].model_sku_id
    registry = bind(registry, model, "account-specific-pool", NOW)
    assert quota_pool_id_for_target(registry, execution_target_id=TARGET, now=NOW) == (
        "account-specific-pool")
    quota = registry.quota_pools["account-specific-pool"].snapshot
    QuotaSnapshotCache(tmp_path / "quota").write(quota)
    result, _ = _recommendation(tmp_path, registry)
    assert result.top_pick.execution_target_id == TARGET
    # An expired/ambiguous fact cannot fall back to the static MiniMax pool.
    fact = registry.quota_bindings[0]
    for facts in (
        (fact.model_copy(update={"effective_until": NOW + timedelta(seconds=1)}),),
        (fact, fact.model_copy(update={"id": "ambiguous"})),
        (fact.model_copy(update={"confidence": EvidenceConfidence.UNKNOWN}),),
    ):
        modified = registry.model_copy(update={"quota_bindings": facts})
        assert quota_pool_id_for_target(
            modified, execution_target_id=TARGET, now=NOW + timedelta(seconds=2),
        ) is None


def test_default_collectors_use_source_identity_and_region(monkeypatch):
    monkeypatch.setattr("personal_ai_orchestrator.daemon.os.environ", {
        "MINIMAX_API_KEY": "offline-fixture", "ZAI_API_KEY": "offline-fixture",
    })
    collectors = default_quota_collectors()
    assert set(collectors) == {
        "zai-coding-plan", "minimax-coding-plan", PROVIDER, "opencode",
    }
    for provider in set(collectors) - {"opencode"}:
        expected_pool = QUOTA_SOURCE_BY_PROVIDER[provider].quota_pool_id
        assert collectors[provider]._quota_pool_id == expected_pool
    assert "minimaxi.com" in collectors[PROVIDER]._endpoint
    assert "minimax.io" in collectors["minimax-coding-plan"]._endpoint


def test_parent_rejects_unrelated_pool(tmp_path):
    quota = _persist(tmp_path).model_copy(update={"quota_pool_id": "unrelated-pool"})
    admission = _parent_admit(tmp_path, quota)
    assert not admission.admitted
    assert admission.evidence.quota_pool_id == POOL
    assert admission.evidence.state is QuotaAvailabilityState.UNKNOWN


def test_disconnected_target_cannot_use_cached_balance(tmp_path, monkeypatch):
    _persist(tmp_path)
    monkeypatch.setattr(QuotaRefreshService, "observations", lambda _: ())
    result, _ = _recommendation(tmp_path)
    assert result.top_pick is None


def test_misplaced_or_corrupt_cache_fails_closed(tmp_path):
    quota = _persist(tmp_path)
    cache = QuotaSnapshotCache(tmp_path / "quota")
    path = cache.path_for(POOL)
    misplaced = quota.model_copy(update={"quota_pool_id": "other-account-pool"})
    other = cache.write(misplaced)
    path.write_bytes(other.read_bytes())
    assert _refresh(tmp_path).snapshot_for_pool(POOL) is None
    path.write_text("invalid json")
    assert _refresh(tmp_path).snapshot_for_pool(POOL) is None


def test_legacy_provider_cooldown_is_read_without_rewriting(tmp_path):
    from personal_ai_orchestrator.runtime_quota_routing import active_shared_pool_blocker
    journal = QuotaAvailabilityJournal(tmp_path)
    journal.save(observe_exhaustion(
        None, execution_target_id=TARGET, provider_id=PROVIDER, quota_pool_id=PROVIDER,
        observed_at=NOW, sanitized_reason_code="FIXTURE_LEGACY",
    ))
    path = journal.path_for(TARGET)
    before = path.read_bytes()
    assert active_shared_pool_blocker(
        journal, provider_id=PROVIDER, quota_pool_id=POOL, now=NOW,
    ) is not None
    assert path.read_bytes() == before
