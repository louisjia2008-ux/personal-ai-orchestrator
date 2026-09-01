import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import pytest

from personal_ai_orchestrator.local_api import handler_for
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
from personal_ai_orchestrator.quota_collectors.base import QuotaCollectionStatus
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import TaskProfile
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _service(tmp_path) -> RoutingService:
    return RoutingService(
        registry=ModelRegistry(),
        store=SafetyKernelStore(tmp_path / "state.sqlite3"),
        catalog_snapshot_id="catalog-empty",
    )


def _registry() -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        observed_at=NOW,
        confidence=EvidenceConfidence.EXACT,
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=18000,
        remaining_fraction=0.8,
        window_started_at=NOW - timedelta(hours=3),
        reset_at=NOW + timedelta(hours=2),
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
    )
    snapshot = QuotaSnapshot(
        id="quota-1",
        quota_pool_id="pool",
        provider_id="minimax",
        observed_at=NOW,
        state=QuotaState.AVAILABLE,
        confidence=EvidenceConfidence.EXACT,
        source=source,
        windows=(window,),
    )
    return ModelRegistry(
        providers={"minimax": Provider(id="minimax", display_name="MiniMax")},
        accounts={"account": Account(id="account", provider_id="minimax", label="subscription")},
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Coding Plan",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="shared",
                snapshot=snapshot,
                required_window_kinds=(QuotaWindowKind.FIVE_HOUR,),
            )
        },
        models={
            "m3": ModelSKU(
                id="m3",
                provider_id="minimax",
                display_name="M3",
                capabilities=CapabilityProfile(scores={"debugging": 0.9}),
            )
        },
        execution_targets={
            "m3-sub": ExecutionTarget(
                id="m3-sub",
                model_sku_id="m3",
                account_id="account",
                runtime_id="opencode",
                execution_verified=True,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding",
                model_sku_id="m3",
                execution_target_id="m3-sub",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.EXACT,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="m3",
                execution_target_id="m3-sub",
            ),
        ),
    )


def _unknown_quota_registry() -> ModelRegistry:
    source = EvidenceSource(
        source_type=EvidenceSourceType.LOCAL_OBSERVATION,
        observed_at=NOW,
        confidence=EvidenceConfidence.UNKNOWN,
    )
    snapshot = QuotaSnapshot(
        id="quota-unknown",
        quota_pool_id="pool",
        provider_id="openai",
        observed_at=NOW,
        state=QuotaState.UNKNOWN,
        confidence=EvidenceConfidence.UNKNOWN,
        source=source,
    )
    return ModelRegistry(
        providers={"openai": Provider(id="openai", display_name="OpenAI")},
        accounts={"account": Account(id="account", provider_id="openai", label="codex-cli")},
        plans={
            "plan": Plan(
                id="plan",
                account_id="account",
                name="Codex existing login",
                kind=PlanKind.SUBSCRIPTION,
            )
        },
        quota_pools={
            "pool": QuotaPool(
                id="pool",
                plan_id="plan",
                name="unknown subscription quota",
                snapshot=snapshot,
            )
        },
        models={
            "gpt-5.5": ModelSKU(
                id="gpt-5.5",
                provider_id="openai",
                display_name="GPT-5.5",
                capabilities=CapabilityProfile(scores={"implementation": 0.9}),
            )
        },
        execution_targets={
            "codex-cli-gpt-5.5": ExecutionTarget(
                id="codex-cli-gpt-5.5",
                model_sku_id="gpt-5.5",
                account_id="account",
                runtime_id="codex-cli",
                execution_verified=True,
            )
        },
        quota_bindings=(
            QuotaBinding(
                id="binding",
                model_sku_id="gpt-5.5",
                execution_target_id="codex-cli-gpt-5.5",
                quota_pool_id="pool",
                effective_from=NOW - timedelta(days=1),
                recorded_at=NOW - timedelta(days=1),
                confidence=EvidenceConfidence.UNKNOWN,
                source=source,
            ),
        ),
        pool_memberships=(
            PoolMembership(
                pool=PoolKind.WORKER,
                model_sku_id="gpt-5.5",
                execution_target_id="codex-cli-gpt-5.5",
            ),
        ),
    )


def _start_server(service: RoutingService):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _route_payload(request_id: str) -> bytes:
    return json.dumps(
        {
            "request_id": request_id,
            "session_id": "session-http",
            "task_id": "missing-task",
            "mode": "SHADOW",
            "requested_at": NOW.isoformat(),
        }
    ).encode("utf-8")


def test_service_unknown_task_returns_no_switch_and_is_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    request = RoutingRequest(
        request_id="req-1",
        session_id="session-1",
        task_id="missing-task",
        mode=RoutingMode.SHADOW,
        requested_at=NOW,
    )
    first = service.route(request, now=NOW)
    second = service.route(request, now=NOW + timedelta(minutes=5))
    assert first == second
    assert first.decided_at == NOW
    assert first.switch_requested is False
    assert first.selected_model is None
    assert "no authoritative TaskProfile" in (first.fallback_reason or "")


def test_profile_without_durable_task_state_fails_closed(tmp_path) -> None:
    service = _service(tmp_path)
    service.set_task_profile(TaskProfile(task_id="task-1"))
    decision = service.route(
        RoutingRequest(
            request_id="req-no-state",
            session_id="session-1",
            task_id="task-1",
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )
    assert decision.selected_model is None
    assert "not backed by durable Safety Kernel" in (decision.fallback_reason or "")


def test_shadow_routing_rejects_stale_task_version(tmp_path) -> None:
    service = _service(tmp_path)
    service.set_task_profile(TaskProfile(task_id="task-1"))
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    ready = service.store.transition_task("task-1", TaskState.READY)
    decision = service.route(
        RoutingRequest(
            request_id="req-stale-state",
            session_id="session-1",
            task_id="task-1",
            task_state_version=ready.state_version + 1,
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )
    assert decision.selected_model is None
    assert "stale task state version" in (decision.fallback_reason or "")


def test_active_routing_requires_exact_task_state_version(tmp_path) -> None:
    service = _service(tmp_path)
    service.set_task_profile(TaskProfile(task_id="task-1"))
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    service.store.transition_task("task-1", TaskState.READY)
    decision = service.route(
        RoutingRequest(
            request_id="req-active-no-version",
            session_id="session-1",
            task_id="task-1",
            mode=RoutingMode.ACTIVE,
            requested_at=NOW,
        ),
        now=NOW,
    )
    assert decision.selected_model is None
    assert "requires an exact task state version" in (decision.fallback_reason or "")


def test_non_routable_task_state_fails_closed(tmp_path) -> None:
    service = _service(tmp_path)
    service.set_task_profile(TaskProfile(task_id="task-1"))
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    decision = service.route(
        RoutingRequest(
            request_id="req-submitted",
            session_id="session-1",
            task_id="task-1",
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )
    assert decision.selected_model is None
    assert "not eligible for model routing" in (decision.fallback_reason or "")


def test_shadow_route_writes_pending_observation_when_actual_target_is_known(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path / "shadow")
    service = RoutingService(
        registry=_registry(),
        store=SafetyKernelStore(tmp_path / "state.sqlite3"),
        catalog_snapshot_id="catalog-1",
        runtime_availability={"m3-sub": True},
        shadow_journal=journal,
        shadow_actual_execution_targets={"task-1": "m3-sub"},
    )
    service.set_task_profile(
        TaskProfile(
            task_id="task-1",
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        )
    )
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    ready = service.store.transition_task("task-1", TaskState.READY)

    decision = service.route(
        RoutingRequest(
            request_id="req-shadow-pending",
            session_id="session-1",
            task_id="task-1",
            task_state_version=ready.state_version,
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )
    pending = journal.load_pending(f"pending-{decision.decision_id}")

    assert decision.selected_execution_target_id == "m3-sub"
    assert pending.task_id == "task-1"
    assert pending.request_id == "req-shadow-pending"
    assert pending.decision_id == decision.decision_id
    assert pending.manual_execution_target_id == "m3-sub"
    assert pending.scheduler_execution_target_id == "m3-sub"
    assert pending.catalog_snapshot_id == "catalog-1"
    assert pending.quota_snapshot_ids == ("quota-1",)
    assert pending.provider_id == "minimax"
    assert pending.quota_pool_id == "pool"
    assert pending.predicted_burn_fraction == 0.05


def test_shadow_route_without_actual_target_does_not_create_pending_observation(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path / "shadow")
    service = RoutingService(
        registry=_registry(),
        store=SafetyKernelStore(tmp_path / "state.sqlite3"),
        catalog_snapshot_id="catalog-1",
        runtime_availability={"m3-sub": True},
        shadow_journal=journal,
    )
    service.set_task_profile(
        TaskProfile(
            task_id="task-1",
            required_capabilities={"debugging": 0.8},
            predicted_quota_fraction_p90=0.05,
        )
    )
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    ready = service.store.transition_task("task-1", TaskState.READY)

    service.route(
        RoutingRequest(
            request_id="req-shadow-no-actual",
            session_id="session-1",
            task_id="task-1",
            task_state_version=ready.state_version,
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )

    assert journal.load_pending_all() == ()


def test_shadow_pending_uses_actual_target_identity_when_quota_blocks_selection(tmp_path) -> None:
    journal = ShadowEvidenceJournal(tmp_path / "shadow")
    service = RoutingService(
        registry=_unknown_quota_registry(),
        store=SafetyKernelStore(tmp_path / "state.sqlite3"),
        catalog_snapshot_id="catalog-1",
        runtime_availability={"codex-cli-gpt-5.5": True},
        shadow_journal=journal,
        shadow_actual_execution_targets={"task-1": "codex-cli-gpt-5.5"},
    )
    service.set_task_profile(
        TaskProfile(
            task_id="task-1",
            required_capabilities={"implementation": 0.8},
            predicted_quota_fraction_p90=0.01,
        )
    )
    service.store.submit_task(task_id="task-1", request_id="task-submit", intent="implement")
    ready = service.store.transition_task("task-1", TaskState.READY)

    decision = service.route(
        RoutingRequest(
            request_id="req-shadow-unknown-quota",
            session_id="session-1",
            task_id="task-1",
            task_state_version=ready.state_version,
            mode=RoutingMode.SHADOW,
            requested_at=NOW,
        ),
        now=NOW,
    )
    pending = journal.load_pending(f"pending-{decision.decision_id}")

    assert decision.selected_execution_target_id is None
    assert decision.quota_snapshot_ids == ("quota-unknown",)
    assert pending.scheduler_execution_target_id is None
    assert pending.manual_execution_target_id == "codex-cli-gpt-5.5"
    assert pending.provider_id == "openai"
    assert pending.quota_pool_id == "pool"
    assert pending.quota_confidence is EvidenceConfidence.UNKNOWN
    assert pending.collector_status is QuotaCollectionStatus.UNKNOWN
    assert pending.predicted_burn_fraction == 0.01


def test_service_rejects_request_id_reuse_for_different_task_or_mode(tmp_path) -> None:
    service = _service(tmp_path)
    original = RoutingRequest(
        request_id="req-reuse",
        session_id="session-1",
        task_id="task-a",
        mode=RoutingMode.SHADOW,
        requested_at=NOW,
    )
    service.route(original, now=NOW)

    with pytest.raises(ValueError, match="different routing task"):
        service.route(original.model_copy(update={"task_id": "task-b"}), now=NOW)
    with pytest.raises(ValueError, match="different routing mode"):
        service.route(original.model_copy(update={"mode": RoutingMode.ACTIVE}), now=NOW)


def test_loopback_http_route_endpoint_returns_valid_decision(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=_route_payload("req-http"),
            headers={"content-type": "application/json", "origin": "http://localhost:3000"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read())
        assert body["request_id"] == "req-http"
        assert body["mode"] == "SHADOW"
        assert body["switch_requested"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_loopback_http_rejects_non_json(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=b"hello",
            headers={"content-type": "text/plain"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
            raise AssertionError("non-JSON request unexpectedly succeeded")
        except urllib.error.HTTPError as error:
            assert error.code == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_loopback_http_rejects_origin_prefix_spoof(tmp_path) -> None:
    service = _service(tmp_path)
    server, thread = _start_server(service)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/opencode/route",
            data=_route_payload("req-origin-spoof"),
            headers={
                "content-type": "application/json",
                "origin": "http://localhost.evil.invalid",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
            raise AssertionError("spoofed Origin unexpectedly succeeded")
        except urllib.error.HTTPError as error:
            assert error.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
