from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_ai_orchestrator.model_registry import (
    Account,
    ExecutionTarget,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.pi_provider_discovery import (
    PiAuthStatus,
    PiDiscoveryCycleOutcome,
    PiDiscoveryResult,
    PiDiscoveryState,
    PiProviderDiscovery,
    build_pi_registry,
)
from personal_ai_orchestrator.pi_provider_registry_manager import (
    PiProviderRegistryManager,
)
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.runtime_registry import merge_runtime_registries
from personal_ai_orchestrator.safety_kernel import OwnerDispatchStatus


NOW = datetime(2026, 9, 12, 22, 0, tzinfo=UTC)


def _pi_result() -> PiDiscoveryResult:
    return PiDiscoveryResult(
        discovered_at=NOW,
        pi_path="/usr/local/bin/pi",
        pi_version="0.85.1",
        providers=(
            PiProviderDiscovery(
                provider_id="zai-coding-plan",
                runtime_provider_id="zai",
                display_name="GLM / Z.AI",
                auth_status=PiAuthStatus.READY,
                model_skus=("glm-5.3",),
                observed_at=NOW,
            ),
        ),
        state=PiDiscoveryState.DISCOVERED,
        configured_family_count=1,
    )


def _base_registry() -> ModelRegistry:
    provider = Provider(id="zai-coding-plan", display_name="GLM / Z.AI")
    account = Account(
        id="zai-coding-plan",
        provider_id=provider.id,
        label=provider.display_name,
    )
    model = ModelSKU(
        id="zai-coding-plan/glm-5.3",
        provider_id=provider.id,
        display_name="glm-5.3",
    )
    target = ExecutionTarget(
        id="zai-coding-plan-glm-5.3",
        model_sku_id=model.id,
        account_id=account.id,
        runtime_id="opencode",
        runtime_provider_id="opencode",
        execution_verified=True,
    )
    return ModelRegistry(
        providers={provider.id: provider},
        accounts={account.id: account},
        models={model.id: model},
        execution_targets={target.id: target},
    )


def test_merge_runtime_registries_keeps_model_identity_and_adds_pi_target() -> None:
    merged = merge_runtime_registries(
        _base_registry(),
        build_pi_registry(_pi_result()),
    )

    assert set(merged.models) == {"zai-coding-plan/glm-5.3"}
    assert "zai-coding-plan-glm-5.3" in merged.execution_targets
    assert "pi-zai-coding-plan-glm-5.3" in merged.execution_targets
    assert merged.execution_targets["zai-coding-plan-glm-5.3"].runtime_id == "opencode"
    pi_target = merged.execution_targets["pi-zai-coding-plan-glm-5.3"]
    assert pi_target.runtime_id == "pi"
    assert pi_target.runtime_provider_id == "zai"
    assert pi_target.execution_verified is False


def test_pi_manager_rehydrates_without_running_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_registry_manager as module

    outcome = PiDiscoveryCycleOutcome(
        result=_pi_result(),
        error_code=None,
        error_message=None,
    )
    calls = 0

    def fake_discover(*, pi_path=None):
        nonlocal calls
        calls += 1
        return outcome

    monkeypatch.setattr(module, "discover_pi", fake_discover)
    first = PiProviderRegistryManager(runtime_state_root=tmp_path)
    assert first.refresh().discovery_state == "DISCOVERED"
    assert calls == 1

    def fail_discover(*, pi_path=None):
        raise AssertionError("rehydration must not invoke Pi discovery")

    monkeypatch.setattr(module, "discover_pi", fail_discover)
    second = PiProviderRegistryManager(runtime_state_root=tmp_path)
    assert second.status().discovery_state == "DISCOVERED"
    assert "pi-zai-coding-plan-glm-5.3" in second.registry().execution_targets


def test_provider_manager_exposes_pi_ready_provider_to_routing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_registry_manager as module

    monkeypatch.setattr(
        module,
        "discover_pi",
        lambda **_: PiDiscoveryCycleOutcome(
            result=_pi_result(),
            error_code=None,
            error_message=None,
        ),
    )
    pi_manager = PiProviderRegistryManager(runtime_state_root=tmp_path)
    pi_manager.refresh()

    manager = ProviderRegistryManager(
        runtime_state_root=tmp_path / "opencode",
        pi_runtime_manager=pi_manager,
    )
    assert "zai-coding-plan" in manager.routing_connected_provider_ids()
    assert manager.connected_provider_ids() == frozenset()


def test_runtime_dispatch_executor_routes_by_target_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import personal_ai_orchestrator.runtime_dispatch_executor as module

    class FakeStore:
        def __init__(self, _state_db):
            self.blocked = None

        def get_owner_dispatch_by_request_id(self, _request_id):
            return SimpleNamespace(
                status=OwnerDispatchStatus.RESERVED,
                execution_target_id="pi-zai-coding-plan-glm-5.3",
            )

        def mark_owner_dispatch_blocked(self, _request_id, *, failure_code, failure_reason=None):
            self.blocked = (failure_code, failure_reason)

        def close(self):
            return None

    class RecordingExecutor:
        def __init__(self):
            self.calls: list[str] = []
            self.execution_supervisor = SimpleNamespace(get=lambda _task_id: None)

        def execute(self, request_id: str) -> None:
            self.calls.append(request_id)

    pi_executor = RecordingExecutor()
    monkeypatch.setattr(module, "SafetyKernelStore", FakeStore)

    router = RuntimeDispatchExecutor(
        state_db="ignored",
        registry_provider=lambda: build_pi_registry(_pi_result()),
        executors={"pi": pi_executor},
    )
    router.execute("request-1")

    assert pi_executor.calls == ["request-1"]


def test_runtime_dispatch_executor_fails_closed_on_unsupported_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import personal_ai_orchestrator.runtime_dispatch_executor as module

    class FakeStore:
        instances: list[FakeStore] = []

        def __init__(self, _state_db):
            self.blocked: tuple[str, str | None] | None = None
            self.instances.append(self)

        def get_owner_dispatch_by_request_id(self, _request_id):
            return SimpleNamespace(
                status=OwnerDispatchStatus.RESERVED,
                execution_target_id="unknown-target",
            )

        def mark_owner_dispatch_blocked(self, _request_id, *, failure_code, failure_reason=None):
            self.blocked = (failure_code, failure_reason)

        def close(self):
            return None

    monkeypatch.setattr(module, "SafetyKernelStore", FakeStore)
    provider = Provider(id="zai-coding-plan", display_name="GLM / Z.AI")
    account = Account(id=provider.id, provider_id=provider.id, label=provider.display_name)
    model = ModelSKU(
        id="zai-coding-plan/glm-5.3",
        provider_id=provider.id,
        display_name="glm-5.3",
    )
    registry = ModelRegistry(
        providers={provider.id: provider},
        accounts={account.id: account},
        models={model.id: model},
        execution_targets={
            "unknown-target": ExecutionTarget(
                id="unknown-target",
                model_sku_id=model.id,
                account_id=account.id,
                runtime_id="future-runtime",
                runtime_provider_id="future-runtime",
            )
        },
    )

    RuntimeDispatchExecutor(
        state_db="ignored",
        registry_provider=lambda: registry,
        executors={},
    ).execute("request-unsupported")

    assert FakeStore.instances[-1].blocked is not None
    assert FakeStore.instances[-1].blocked[0] == "UNSUPPORTED_RUNTIME"
