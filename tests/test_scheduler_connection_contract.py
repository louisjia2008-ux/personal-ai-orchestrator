"""Only owner-connected providers may be scheduled, and explanations come from evidence.

P4.2.6.1 §19/§13/§15. Discovery, import candidacy, and connection are three different
things; only the last one grants scheduling eligibility. Safety decides who may
compete, policy only decides which eligible candidate wins — so a high-scoring model
that fails a gate must never be selected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.model_registry import (
    Account,
    CapabilityProfile,
    EvidenceConfidence,
    EvidenceSource,
    EvidenceSourceType,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Plan,
    PlanKind,
    PoolKind,
    PoolMembership,
    Provider,
    QuotaBinding,
    QuotaPool,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
)
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.provider_registry_store import save
from personal_ai_orchestrator.routing_bridge import build_routing_decision
from personal_ai_orchestrator.scheduler import TaskProfile, route_task

NOW = datetime(2026, 8, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# Registry manager: candidacy is not connection
# --------------------------------------------------------------------------


def _discovery_result(*, scope_verified: bool) -> DiscoveryResult:
    return DiscoveryResult(
        discovered_at=NOW,
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=(
                    AuthStatus.AUTH_FROM_ENV_PRESENCE
                    if scope_verified
                    else AuthStatus.AUTH_REQUIRED
                ),
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=(),
                observed_at=NOW,
                catalog_discovered=True,
                credential_evidence_present=scope_verified,
                credential_region_verified=scope_verified,
                credential_plan_surface_verified=scope_verified,
                credential_scope_verified=scope_verified,
                execution_verified=False,
            ),
        ),
        state=DiscoveryState.DISCOVERED,
    )


def _manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope_verified: bool,
) -> ProviderRegistryManager:
    result = _discovery_result(scope_verified=scope_verified)
    save(result, runtime_state_root=tmp_path)
    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        lambda **_kwargs: DiscoveryCycleOutcome(
            result=result, error_code=None, error_message=None
        ),
    )
    return ProviderRegistryManager(runtime_state_root=tmp_path)


def test_catalog_only_provider_is_not_scheduler_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch, scope_verified=False)

    assert manager.connected_provider_ids() == frozenset()


def test_import_candidate_is_not_scheduler_eligible_until_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Being *offered* for import grants nothing. Only the owner's import does."""

    manager = _manager(tmp_path, monkeypatch, scope_verified=True)

    assert [c.provider_id for c in manager.import_candidates()] == ["zai-coding-plan"]
    assert manager.connected_provider_ids() == frozenset()

    manager.import_connections(("zai-coding-plan",))

    assert manager.connected_provider_ids() == frozenset({"zai-coding-plan"})


def test_imported_connection_survives_manager_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch, scope_verified=True)
    manager.import_connections(("zai-coding-plan",))

    restarted = ProviderRegistryManager(runtime_state_root=tmp_path)

    assert restarted.connected_provider_ids() == frozenset({"zai-coding-plan"})


def test_disconnect_removes_eligibility_without_touching_provider_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch, scope_verified=True)
    manager.import_connections(("zai-coding-plan",))

    manager.disconnect_provider("zai-coding-plan")

    assert manager.connected_provider_ids() == frozenset()
    # Disconnect is an orchestrator-local decision: discovery still sees the
    # provider's credential evidence, because nothing was revoked upstream.
    discovered = manager.last_discovery_result()
    assert discovered is not None
    assert discovered.providers[0].credential_scope_verified is True


# --------------------------------------------------------------------------
# Safety precedes scoring
# --------------------------------------------------------------------------


def _registry_with_two_targets() -> ModelRegistry:
    """A high-capability target plus a modest one, on two separate providers."""

    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        reference="provider://quota",
        confidence=EvidenceConfidence.EXACT,
    )

    def _pool(pool_id: str, plan_id: str) -> QuotaPool:
        window = QuotaWindowSnapshot(
            window_id=f"{pool_id}-5h",
            window_kind=QuotaWindowKind.FIVE_HOUR,
            duration_seconds=18000,
            remaining_fraction=0.8,
            window_started_at=NOW - timedelta(hours=3),
            reset_at=NOW + timedelta(hours=2),
            state=QuotaState.AVAILABLE,
            confidence=EvidenceConfidence.EXACT,
            source=source,
        )
        return QuotaPool(
            id=pool_id,
            plan_id=plan_id,
            name="shared",
            snapshot=QuotaSnapshot(
                id=f"quota-{pool_id}",
                quota_pool_id=pool_id,
                observed_at=NOW,
                state=QuotaState.AVAILABLE,
                confidence=EvidenceConfidence.EXACT,
                source=source,
                windows=(window,),
            ),
            required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
        )

    def _binding(name: str, sku: str, target: str, pool: str) -> QuotaBinding:
        return QuotaBinding(
            id=name,
            model_sku_id=sku,
            execution_target_id=target,
            quota_pool_id=pool,
            effective_from=NOW - timedelta(days=1),
            recorded_at=NOW - timedelta(days=1),
            confidence=EvidenceConfidence.EXACT,
            source=source,
        )

    return ModelRegistry(
        providers={
            "p-strong": Provider(id="p-strong", display_name="Strong Provider"),
            "p-modest": Provider(id="p-modest", display_name="Modest Provider"),
        },
        accounts={
            "acct-strong": Account(
                id="acct-strong", provider_id="p-strong", label="subscription"
            ),
            "acct-modest": Account(
                id="acct-modest", provider_id="p-modest", label="subscription"
            ),
        },
        plans={
            "plan-strong": Plan(
                id="plan-strong",
                account_id="acct-strong",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            ),
            "plan-modest": Plan(
                id="plan-modest",
                account_id="acct-modest",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            ),
        },
        quota_pools={
            "pool-strong": _pool("pool-strong", "plan-strong"),
            "pool-modest": _pool("pool-modest", "plan-modest"),
        },
        models={
            "sku-strong": ModelSKU(
                id="sku-strong",
                provider_id="p-strong",
                display_name="Strong model",
                capabilities=CapabilityProfile(
                    scores={"debugging": 0.99, "reasoning": 0.99}
                ),
            ),
            "sku-modest": ModelSKU(
                id="sku-modest",
                provider_id="p-modest",
                display_name="Modest model",
                capabilities=CapabilityProfile(
                    scores={"debugging": 0.82, "reasoning": 0.8}
                ),
            ),
        },
        execution_targets={
            "t-strong": ExecutionTarget(
                id="t-strong",
                model_sku_id="sku-strong",
                account_id="acct-strong",
                runtime_id="opencode",
                execution_verified=True,
            ),
            "t-modest": ExecutionTarget(
                id="t-modest",
                model_sku_id="sku-modest",
                account_id="acct-modest",
                runtime_id="opencode",
                execution_verified=True,
            ),
        },
        quota_bindings=(
            _binding("b-strong", "sku-strong", "t-strong", "pool-strong"),
            _binding("b-modest", "sku-modest", "t-modest", "pool-modest"),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="sku-strong",
                execution_target_id="t-strong",
            ),
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="sku-modest",
                execution_target_id="t-modest",
            ),
        ),
    )


def _worker_task() -> TaskProfile:
    return TaskProfile(
        task_id="task-1",
        required_capabilities={"debugging": 0.8},
        predicted_quota_fraction_p90=0.05,
    )


def test_ineligible_high_capability_target_never_wins() -> None:
    """The best model on paper loses to a gate, not to a score."""

    decision = route_task(
        _registry_with_two_targets(),
        task=_worker_task(),
        now=NOW,
        known_at=NOW,
        # The strong target's runtime is down; only the modest one may run.
        runtime_availability={"t-strong": False, "t-modest": True},
        connected_provider_ids=frozenset({"p-strong", "p-modest"}),
    )

    strong = next(e for e in decision.evaluations if e.execution_target_id == "t-strong")
    assert strong.eligible is False
    assert strong.score is None
    assert decision.selected_execution_target_id == "t-modest"


def test_disconnected_provider_excluded_even_when_runtime_is_available() -> None:
    decision = route_task(
        _registry_with_two_targets(),
        task=_worker_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"t-strong": True, "t-modest": True},
        # The owner connected only the modest provider.
        connected_provider_ids=frozenset({"p-modest"}),
    )

    strong = next(e for e in decision.evaluations if e.execution_target_id == "t-strong")
    assert strong.reasons == ("provider not connected by owner",)
    assert decision.selected_execution_target_id == "t-modest"


# --------------------------------------------------------------------------
# Explanations are structured, and carry no secrets
# --------------------------------------------------------------------------


def _explained_decision():
    registry = _registry_with_two_targets()
    scheduler = route_task(
        registry,
        task=_worker_task(),
        now=NOW,
        known_at=NOW,
        runtime_availability={"t-strong": False, "t-modest": True},
        connected_provider_ids=frozenset({"p-strong", "p-modest"}),
    )
    return build_routing_decision(
        RoutingRequest(
            request_id="rr-1",
            session_id="session-1",
            task_id="task-1",
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        scheduler,
        registry,
        catalog_snapshot_id="catalog-1",
        policy_snapshot_id="policy-1",
        decided_at=NOW,
        policy_resolution={
            "requested_task_policy": None,
            "manual_execution_target_id": None,
            "project_policy": None,
            "global_default_policy": "BALANCED",
            "resolved_policy": "BALANCED",
            "resolution_source": "GLOBAL_DEFAULT",
        },
    )


def test_explanation_reports_why_selected_and_why_not_selected() -> None:
    decision = _explained_decision()
    explanation = decision.explanation
    assert explanation is not None

    candidates = {item["execution_target_id"]: item for item in explanation["candidates"]}
    strong = candidates["t-strong"]

    assert strong["selected"] is False
    # The reason must be the scheduler's own structured evidence, not prose.
    assert strong["why_not_selected"]
    assert "runtime" in strong["why_not_selected"].lower()
    assert candidates["t-modest"]["selected"] is True


def test_explanation_records_policy_resolution_source() -> None:
    explanation = _explained_decision().explanation
    assert explanation is not None

    assert explanation["policy_resolution"]["resolution_source"] == "GLOBAL_DEFAULT"
    assert explanation["policy_resolution"]["resolved_policy"] == "BALANCED"


def test_routing_record_contains_no_credential_material() -> None:
    rendered = _explained_decision().model_dump_json()

    for forbidden in ("api_key", "apiKey", "Bearer", "auth.json", "secret", "token"):
        assert forbidden not in rendered
