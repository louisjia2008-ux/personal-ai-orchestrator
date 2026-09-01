"""Tests for the ControlPlaneService provider projection contract.

P4.2.4-A.1 §26 — effective-registry consistency.

The ControlPlaneService exposes provider / quota / execution-target
projections through ``GET /v1/providers``. When a
:class:`ProviderRegistryManager` is attached (the bundled macOS daemon
case), the ``providers()`` handler must read **only** from the
dynamic registry returned by ``manager.registry()`` — it must never
silently fall back to the static ``self.registry`` for accounts,
plans, pools, or models.

A regression where ``self.registry.accounts.values()`` is read while
``effective_registry`` is dynamic would surface as: the dynamic
discovery populates a non-empty ``effective_registry`` but the static
``registry`` is empty (the default product config), so the provider
projection reports ``account_count=0`` even though the dynamic
registry carries one synthetic account per provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneService
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
    build_registry,
)
from personal_ai_orchestrator.provider_registry_manager import (
    ProviderRegistryManager,
)
from personal_ai_orchestrator.provider_registry_store import save
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.verification_evidence import (
    VerificationEvidenceJournal,
)


def _empty_static_registry() -> ModelRegistry:
    return ModelRegistry()


def _dynamic_manager_with_provider(
    tmp_state_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> ProviderRegistryManager:
    """Build a manager whose ``registry()`` returns a synthetic dynamic
    registry with one provider, one synthetic account, one model, and
    one execution target.
    """

    result = DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_scope_verified=True,
                execution_verified=False,
            ),
        ),
        state=DiscoveryState.DISCOVERED,
    )
    save(result, runtime_state_root=tmp_state_root)

    def _no_op_discover(**_kwargs: object) -> DiscoveryCycleOutcome:
        return DiscoveryCycleOutcome(result=result, error_code=None, error_message=None)

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _no_op_discover,
    )
    return ProviderRegistryManager(runtime_state_root=tmp_state_root)


def _build_service(
    *,
    runtime_state_root: Path,
    state_db: Path,
    manager: ProviderRegistryManager | None,
    static_registry: ModelRegistry,
) -> ControlPlaneService:
    return ControlPlaneService(
        registry=static_registry,
        store=SafetyKernelStore(state_db),
        activation_gate=ActiveRoutingGate(),
        runtime_availability={},
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
        provider_registry_manager=manager,
    )


def test_providers_uses_effective_registry_accounts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``manager.registry()`` returns a dynamic registry with one
    synthetic account, the Control API must report ``account_count=1``
    — never ``account_count=0`` because of a stale ``self.registry``
    read.
    """

    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    state_db = tmp_path / "state.db"
    manager = _dynamic_manager_with_provider(runtime_state_root, monkeypatch)
    service = _build_service(
        runtime_state_root=runtime_state_root,
        state_db=state_db,
        manager=manager,
        static_registry=_empty_static_registry(),
    )
    views = service.providers()
    assert len(views.providers) == 1
    provider_view = views.providers[0]
    assert provider_view.provider_id == "zai-coding-plan"
    # Regression coverage: must NOT be zero. Before A.1 the handler read
    # ``self.registry.accounts.values()`` which is always empty in the
    # bundled daemon.
    assert provider_view.account_count == 1
    # The execution target must come from the dynamic registry too.
    assert len(provider_view.execution_targets) == 1
    target = provider_view.execution_targets[0]
    assert target.model_sku_id == "zai-coding-plan/glm-5.3"
    # Discovery evidence is wired through.
    assert provider_view.evidence_source == "DISCOVERED_FROM_CATALOG"
    assert provider_view.auth_status == "AUTH_FROM_ENV_PRESENCE"


def test_providers_does_not_fall_back_to_static_registry_when_dynamic_empty(
    tmp_path: Path,
) -> None:
    """When the manager exists but its dynamic registry is empty (cold
    start, no snapshot, no refresh yet), the Control API must report zero
    providers — it must NOT silently fall back to ``self.registry``
    which is also empty in the bundled daemon case.
    """

    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    state_db = tmp_path / "state.db"
    # Manager is constructed on an empty state root; constructor finds
    # no persisted snapshot and leaves the dynamic registry empty.
    manager = ProviderRegistryManager(runtime_state_root=runtime_state_root)
    assert manager.registry().providers == {}

    service = _build_service(
        runtime_state_root=runtime_state_root,
        state_db=state_db,
        manager=manager,
        static_registry=_empty_static_registry(),
    )
    assert service.providers().providers == ()


def test_providers_falls_back_to_static_registry_when_no_manager(
    tmp_path: Path,
) -> None:
    """When no manager is attached (the loopback / non-bundled daemon
    case), the Control API must project from the static
    ``self.registry`` so existing installations see their static truth.
    """

    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    state_db = tmp_path / "state.db"
    static_registry = build_registry(
        DiscoveryResult(
            discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            opencode_path="/usr/bin/opencode",
            opencode_version="1.18.25",
            source_method="opencode_cli_inspection",
            providers=(
                ProviderDiscovery(
                    provider_id="zai-coding-plan",
                    display_name="GLM / Z.AI",
                    auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                    execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                    evidence_source="DISCOVERED_FROM_CATALOG",
                    model_skus=("glm-5.3",),
                    env_variables_present=("ZAI_API_KEY",),
                    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
            state=DiscoveryState.DISCOVERED,
        )
    )

    service = _build_service(
        runtime_state_root=runtime_state_root,
        state_db=state_db,
        manager=None,
        static_registry=static_registry,
    )
    views = service.providers()
    assert len(views.providers) == 1
    assert views.providers[0].provider_id == "zai-coding-plan"
    assert views.providers[0].account_count == 1
    assert len(views.providers[0].execution_targets) == 1