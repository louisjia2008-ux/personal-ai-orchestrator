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

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneService
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationOutcome,
    build_execution_evidence,
)
from personal_ai_orchestrator.model_registry import (
    Account,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
)
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
    tmp_state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    assert provider_view.connection_state is None


def _static_execution_registry(*, static_verified: bool = False) -> ModelRegistry:
    return ModelRegistry(
        providers={"p": Provider(id="p", display_name="Provider")},
        accounts={"a": Account(id="a", provider_id="p", label="account")},
        models={"m": ModelSKU(id="m", provider_id="p", display_name="Model")},
        execution_targets={
            "target": ExecutionTarget(
                id="target",
                model_sku_id="m",
                account_id="a",
                runtime_id="opencode",
                enabled=True,
                execution_verified=static_verified,
            )
        },
    )


def test_target_health_separates_history_from_current_launch_authority(
    tmp_path: Path,
) -> None:
    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    journal = ExecutionEvidenceJournal(runtime_state_root)
    old = datetime.now(UTC) - timedelta(days=31)
    journal.append(
        build_execution_evidence(
            provider_id="p",
            execution_target_id="target",
            model_sku_id="m",
            observed_at=old,
            result=ExecutionVerificationOutcome.VERIFIED,
            reason_code="TEST_EXPIRED",
        )
    )
    service = ControlPlaneService(
        registry=_static_execution_registry(),
        store=SafetyKernelStore(tmp_path / "state.db"),
        activation_gate=ActiveRoutingGate(),
        runtime_availability={"target": True},
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
        execution_evidence_journal=journal,
    )
    target = service.providers().providers[0].execution_targets[0]
    assert target.execution_verified is True
    assert target.execution_verified_stale is True
    assert target.execution_launch_authorized is False
    assert target.execution_verification_observed_at == old.isoformat()


def test_target_health_static_verification_matches_launch_gate(tmp_path: Path) -> None:
    runtime_state_root = tmp_path / "runtime-state"
    runtime_state_root.mkdir()
    service = ControlPlaneService(
        registry=_static_execution_registry(static_verified=True),
        store=SafetyKernelStore(tmp_path / "state.db"),
        activation_gate=ActiveRoutingGate(),
        runtime_availability={"target": True},
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
    )
    target = service.providers().providers[0].execution_targets[0]
    assert target.execution_verified is True
    assert target.execution_launch_authorized is True
    assert target.execution_verification_observed_at is None


def test_provider_connections_separate_connected_from_available_to_add(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    projection = service.provider_connections()
    assert projection.connected == ()
    assert len(projection.available_to_add) == 1
    assert projection.available_to_add[0].provider_id == "zai-coding-plan"
    assert projection.available_to_add[0].connection_state == "DISCOVERED"

    connected = service.connect_provider({"provider_id": "zai-coding-plan"})
    assert connected.connection_state == "CONNECTED"
    assert connected.auth_state == "AUTH_UNKNOWN"
    assert connected.execution_verified is False
    assert connected.credential_reference_type == "OPENCODE_AUTH"

    projection = service.provider_connections()
    assert len(projection.connected) == 1
    assert projection.available_to_add == ()


def test_provider_connection_survives_restart_and_disconnect_is_local_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    service.connect_provider({"provider_id": "zai-coding-plan"})

    restarted = ProviderRegistryManager(runtime_state_root=runtime_state_root)
    restarted_service = _build_service(
        runtime_state_root=runtime_state_root,
        state_db=state_db,
        manager=restarted,
        static_registry=_empty_static_registry(),
    )
    assert len(restarted_service.provider_connections().connected) == 1

    disconnected = restarted_service.disconnect_provider(
        "zai-coding-plan",
        {"confirm": True},
    )
    assert disconnected.connection_state == "DISCONNECTED"
    projection = restarted_service.provider_connections()
    assert projection.connected == ()
    assert len(projection.available_to_add) == 1
    # Discovery evidence is still present; disconnect only disables the
    # orchestrator connection and never deletes OpenCode/provider auth.
    assert restarted.registry().providers["zai-coding-plan"].display_name == "GLM / Z.AI"


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
