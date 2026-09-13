from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
    observe_exhaustion,
    observe_success,
    unknown_availability,
)
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.runtime_quota_routing import active_shared_pool_blocker
from personal_ai_orchestrator.safety_kernel import OwnerDispatchStatus


def _registry() -> ModelRegistry:
    provider = Provider(id="minimax-cn-coding-plan", display_name="MiniMax CN")
    account = Account(id=provider.id, provider_id=provider.id, label=provider.display_name)
    model = ModelSKU(
        id="minimax-cn-coding-plan/MiniMax-M3",
        provider_id=provider.id,
        display_name="MiniMax-M3",
    )
    opencode = ExecutionTarget(
        id="minimax-cn-coding-plan-MiniMax-M3",
        model_sku_id=model.id,
        account_id=account.id,
        runtime_id="opencode",
        runtime_provider_id="opencode",
        execution_verified=True,
    )
    pi = ExecutionTarget(
        id="pi-minimax-cn-coding-plan-MiniMax-M3",
        model_sku_id=model.id,
        account_id=account.id,
        runtime_id="pi",
        runtime_provider_id="minimax-cn",
        execution_verified=True,
    )
    return ModelRegistry(
        providers={provider.id: provider},
        accounts={account.id: account},
        models={model.id: model},
        execution_targets={opencode.id: opencode, pi.id: pi},
    )


def test_active_pool_blocker_survives_newer_unknown(tmp_path: Path) -> None:
    journal = QuotaAvailabilityJournal(tmp_path)
    now = datetime.now(UTC)
    blocker = observe_exhaustion(
        None,
        execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
        provider_id="minimax-cn-coding-plan",
        quota_pool_id="minimax-cn-coding-plan",
        observed_at=now - timedelta(minutes=2),
        sanitized_reason_code="USAGE_LIMIT",
    )
    journal.save(blocker)
    journal.save(
        unknown_availability(
            execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=1),
        )
    )

    effective = active_shared_pool_blocker(
        journal,
        provider_id="minimax-cn-coding-plan",
        quota_pool_id="minimax-cn-coding-plan",
        now=now,
    )

    assert effective is not None
    assert effective.execution_target_id == "minimax-cn-coding-plan-MiniMax-M3"
    assert effective.state_at(now=now) is QuotaAvailabilityState.COOLDOWN


def test_newer_success_releases_older_shared_pool_blocker(tmp_path: Path) -> None:
    journal = QuotaAvailabilityJournal(tmp_path)
    now = datetime.now(UTC)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=2),
            sanitized_reason_code="USAGE_LIMIT",
        )
    )
    journal.save(
        observe_success(
            None,
            execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=1),
        )
    )

    assert (
        active_shared_pool_blocker(
            journal,
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            now=now,
        )
        is None
    )


def test_expired_shared_cooldown_does_not_block_runtime_switch(tmp_path: Path) -> None:
    journal = QuotaAvailabilityJournal(tmp_path)
    observed = datetime.now(UTC) - timedelta(minutes=10)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=observed,
            sanitized_reason_code="USAGE_LIMIT",
            minimum_cooldown_seconds=60,
        )
    )

    assert (
        active_shared_pool_blocker(
            journal,
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            now=datetime.now(UTC),
        )
        is None
    )


def test_runtime_router_blocks_pi_when_opencode_sibling_pool_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.runtime_dispatch_executor as module

    registry = _registry()
    journal = QuotaAvailabilityJournal(tmp_path)
    now = datetime.now(UTC)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now,
            sanitized_reason_code="USAGE_LIMIT",
        )
    )

    class FakeStore:
        instances: list[FakeStore] = []

        def __init__(self, _state_db):
            self.blocked: tuple[str, str | None] | None = None
            self.instances.append(self)

        def get_owner_dispatch_by_request_id(self, _request_id):
            return SimpleNamespace(
                status=OwnerDispatchStatus.RESERVED,
                execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            )

        def mark_owner_dispatch_blocked(
            self,
            _request_id,
            *,
            failure_code,
            failure_reason=None,
        ):
            self.blocked = (failure_code, failure_reason)

        def close(self):
            return None

    class RecordingExecutor:
        def __init__(self):
            self.calls: list[str] = []
            self._quota_availability_journal = journal
            self.execution_supervisor = SimpleNamespace(get=lambda _task_id: None)

        def execute(self, request_id: str) -> None:
            self.calls.append(request_id)

    executor = RecordingExecutor()
    monkeypatch.setattr(module, "SafetyKernelStore", FakeStore)

    RuntimeDispatchExecutor(
        state_db="ignored",
        registry_provider=lambda: registry,
        executors={"pi": executor},
    ).execute("request-shared-pool")

    assert executor.calls == []
    assert FakeStore.instances[-1].blocked is not None
    assert FakeStore.instances[-1].blocked[0] == "SHARED_QUOTA_POOL_BLOCKED"


def test_runtime_router_allows_pi_after_newer_pool_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.runtime_dispatch_executor as module

    registry = _registry()
    journal = QuotaAvailabilityJournal(tmp_path)
    now = datetime.now(UTC)
    journal.save(
        observe_exhaustion(
            None,
            execution_target_id="minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=2),
            sanitized_reason_code="USAGE_LIMIT",
        )
    )
    journal.save(
        observe_success(
            None,
            execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            provider_id="minimax-cn-coding-plan",
            quota_pool_id="minimax-cn-coding-plan",
            observed_at=now - timedelta(minutes=1),
        )
    )

    class FakeStore:
        def __init__(self, _state_db):
            pass

        def get_owner_dispatch_by_request_id(self, _request_id):
            return SimpleNamespace(
                status=OwnerDispatchStatus.RESERVED,
                execution_target_id="pi-minimax-cn-coding-plan-MiniMax-M3",
            )

        def mark_owner_dispatch_blocked(self, *_args, **_kwargs):
            raise AssertionError("recovered shared pool must not block dispatch")

        def close(self):
            return None

    class RecordingExecutor:
        def __init__(self):
            self.calls: list[str] = []
            self._quota_availability_journal = journal
            self.execution_supervisor = SimpleNamespace(get=lambda _task_id: None)

        def execute(self, request_id: str) -> None:
            self.calls.append(request_id)

    executor = RecordingExecutor()
    monkeypatch.setattr(module, "SafetyKernelStore", FakeStore)

    RuntimeDispatchExecutor(
        state_db="ignored",
        registry_provider=lambda: registry,
        executors={"pi": executor},
    ).execute("request-recovered-pool")

    assert executor.calls == ["request-recovered-pool"]
