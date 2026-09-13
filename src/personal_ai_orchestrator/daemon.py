"""Headless local daemon entrypoint for recommendation-only Shadow routing."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneServer, ControlPlaneService
from personal_ai_orchestrator.daemon_supervisor import (
    DaemonSupervisor,
    build_default_supervisor,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from personal_ai_orchestrator.dispatch_executor import (
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
)
from personal_ai_orchestrator.dispatch_recommendation_service import DispatchRecommendationService
from personal_ai_orchestrator.execution_controller import reconcile_workspace_truth
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.local_api import serve
from personal_ai_orchestrator.model_tiers import (
    DEFAULT_TIER_TABLE_JSON,
    TierTable,
    parse_tier_table,
)
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.pi5_child_execution import PAODelegationChildPort
from personal_ai_orchestrator.pi_dispatch_executor import PiOwnerDispatchExecutor
from personal_ai_orchestrator.pi_runtime import PiRuntimeConfig
from personal_ai_orchestrator.policy_snapshot import PolicySnapshotJournal
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.quota_collectors.unmetered import UnmeteredQuotaCollector
from personal_ai_orchestrator.quota_credentials import SecretValue
from personal_ai_orchestrator.quota_refresh import QUOTA_SOURCES, QuotaRefreshService
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.runtime_config import RuntimeConfig
from personal_ai_orchestrator.runtime_dispatch_executor import RuntimeDispatchExecutor
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.scheduling_settings import SchedulingSettings
from personal_ai_orchestrator.shadow_evidence import ShadowEvidenceJournal
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal


def load_runtime_config(path: Path) -> RuntimeConfig:
    return RuntimeConfig.model_validate_json(path.read_text(encoding="utf-8"))


def build_service(
    *,
    config: RuntimeConfig,
    state_db: Path,
    runtime_state_root: Path,
    provider_registry_manager: ProviderRegistryManager | None = None,
) -> RoutingService:
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    store = SafetyKernelStore(state_db)
    # A daemon restart destroys live process truth. Reconcile before exposing any routing path so
    # stale RUNNING/WORKER_FINISHED/VERIFYING tasks cannot continue to influence model selection.
    store.reconcile_startup()
    # Startup blocking is also the point at which stale writer ownership becomes invalid. Clear
    # those exact persisted locks and fail closed any still-routable task whose worktree vanished.
    reconcile_workspace_truth(store)
    registry = (
        provider_registry_manager.registry()
        if provider_registry_manager is not None
        else config.registry
    )
    service = RoutingService(
        registry=registry,
        registry_provider=(
            provider_registry_manager.registry
            if provider_registry_manager is not None
            else None
        ),
        store=store,
        catalog_snapshot_id=config.catalog_snapshot_id,
        runtime_availability=dict(config.runtime_availability),
        connected_provider_ids_provider=(
            provider_registry_manager.connected_provider_ids
            if provider_registry_manager is not None
            else None
        ),
        policy_journal=PolicySnapshotJournal(runtime_state_root),
        activation_gate=ActiveRoutingGate(),
        shadow_journal=ShadowEvidenceJournal(runtime_state_root),
        scheduling_settings=SchedulingSettings(runtime_state_root / "scheduling-settings.json"),
    )
    return service


def _resolved_state_paths(
    *,
    state_db: Path,
    runtime_state_root: Path,
) -> tuple[Path, Path]:
    return state_db.expanduser().resolve(), runtime_state_root.expanduser().resolve()


def _state_paths_are_safe(*, state_db: Path, runtime_state_root: Path) -> bool:
    db, root = _resolved_state_paths(state_db=state_db, runtime_state_root=runtime_state_root)
    return db != root and root not in db.parents and db not in root.parents


def _configured_tier_table(
    path: Path | None,
    *,
    audit: SafetyKernelStore | None = None,
) -> tuple[TierTable, str]:
    if path is None:
        return parse_tier_table(DEFAULT_TIER_TABLE_JSON), "default"
    try:
        return parse_tier_table(path.read_text(encoding="utf-8")), str(path)
    except (OSError, ValueError) as error:
        if audit is not None:
            audit.record_system_event(
                "MODEL_TIERS_INVALID",
                {"path": str(path), "reason_code": type(error).__name__},
            )
        return parse_tier_table(DEFAULT_TIER_TABLE_JSON), "default"


def load_model_tiers(
    path: Path | None,
    *,
    audit: SafetyKernelStore | None = None,
) -> tuple[TierTable, str]:
    return _configured_tier_table(path, audit=audit)


def default_quota_collectors() -> dict[str, object]:
    """Build read-only collectors for credentials already present in the daemon env.

    The secret values never enter SQLite/logs/UI. They are wrapped and only passed
    to the documented provider read-only collector factory.
    """

    collectors: dict[str, object] = {}
    for spec in QUOTA_SOURCES:
        if spec.provider_id not in {
            "zai-coding-plan", "minimax-coding-plan", "minimax-cn-coding-plan",
        }:
            continue
        token = os.environ.get(spec.credential_env_var)
        if token:
            collectors[spec.provider_id] = spec.factory(SecretValue(token), spec.quota_pool_id)
    collectors["opencode"] = UnmeteredQuotaCollector(
        provider_id="opencode",
        quota_pool_id="opencode",
    )
    return collectors


def build_control_service(
    *,
    config: RuntimeConfig,
    state_db: Path,
    runtime_state_root: Path,
    provider_registry_manager: ProviderRegistryManager | None = None,
    execution_repo: Path | None = None,
    worktree_root: Path | None = None,
    opencode_bin: str = "opencode",
    quota_collectors: dict[str, object] | None = None,
    verifier_profile=None,
    worker_permission_config: Path | None = None,
    model_tiers_path: Path | None = None,
    pi_runtime: PiRuntimeConfig | None = None,
    supervisor: DaemonSupervisor | None = None,
) -> ControlPlaneService:
    """Build the control-plane facade over the same durable truth.

    When ``execution_repo`` is provided (host-owned repository policy),
    owner dispatch executes real isolated worktree workers. The optional
    ``supervisor`` (WP0) is wired into the control plane so ``/v1/health``
    can surface the heartbeat status; callers that do not run a supervisor
    (CLI tools, ad-hoc scripts) can omit it and ``/v1/health`` still works,
    just without ``last_tick_at`` / ``supervisor_steps``.
    """

    registry = (
        provider_registry_manager.registry()
        if provider_registry_manager is not None
        else config.registry
    )
    store = SafetyKernelStore(state_db)
    tier_table, model_tiers_source = load_model_tiers(
        model_tiers_path, audit=store
    )
    execution_evidence_journal = ExecutionEvidenceJournal(runtime_state_root)
    if provider_registry_manager is not None:
        provider_registry_manager.set_verified_execution_lookup(
            lambda provider_id: (
                lambda evidence: evidence.observed_at if evidence is not None else None
            )(execution_evidence_journal.latest_verified_for_provider(provider_id))
        )
    executor = None
    pi_executor = None
    if execution_repo is not None:
        registry_provider = (
            provider_registry_manager.registry
            if provider_registry_manager is not None
            else lambda: config.registry
        )
        dispatch_config = DispatchExecutorConfig(
            repo_path=execution_repo,
            worktree_root=worktree_root
            or (runtime_state_root / "worktrees"),
            opencode_bin=opencode_bin,
            verifier_profile=verifier_profile,
            worker_permission_config=worker_permission_config,
        )
        collectors = quota_collectors or default_quota_collectors()
        verification_journal = VerificationEvidenceJournal(runtime_state_root)
        quota_availability_journal = QuotaAvailabilityJournal(runtime_state_root)
        shadow_journal = ShadowEvidenceJournal(runtime_state_root)

        opencode_executor = OwnerDispatchExecutor(
            state_db=state_db,
            config=dispatch_config,
            registry_provider=registry_provider,
            verification_journal=verification_journal,
            execution_evidence_journal=execution_evidence_journal,
            quota_availability_journal=quota_availability_journal,
            quota_collectors=collectors,
            shadow_journal=shadow_journal,
        )

        pi_executor = None
        if (
            provider_registry_manager is not None
            and provider_registry_manager.pi_runtime_manager() is not None
        ):
            pi_executor = PiOwnerDispatchExecutor(
                state_db=state_db,
                config=dispatch_config,
                registry_provider=registry_provider,
                verification_journal=verification_journal,
                execution_evidence_journal=execution_evidence_journal,
                quota_availability_journal=quota_availability_journal,
                quota_collectors=collectors,
                shadow_journal=shadow_journal,
                pi_runtime=pi_runtime or PiRuntimeConfig(),
            )

        runtimes = {"opencode": opencode_executor}
        if pi_executor is not None:
            runtimes["pi"] = pi_executor
        executor = RuntimeDispatchExecutor(
            state_db=state_db,
            registry_provider=registry_provider,
            executors=runtimes,
        )
    quota_refresh_service = QuotaRefreshService(
        runtime_state_root=runtime_state_root,
        connected_provider_ids=(
            provider_registry_manager.connected_provider_ids
            if provider_registry_manager is not None
            else tuple
        ),
    )
    if pi_executor is not None:
        def child_runtime_available(target_id: str) -> bool:
            if target_id in config.runtime_availability:
                return config.runtime_availability[target_id]
            return bool(provider_registry_manager.runtime_available(target_id))

        pi_executor.delegation_child_port = PAODelegationChildPort(
            state_db=state_db,
            executor=executor,
            recommendation_factory=lambda child_store: DispatchRecommendationService(
                child_store,
                registry_provider=registry_provider,
                quota_refresh_service=quota_refresh_service,
                execution_evidence_journal=execution_evidence_journal,
                quota_availability_journal=quota_availability_journal,
                tier_table=tier_table,
                runtime_availability=dict(config.runtime_availability),
                runtime_availability_fallback=child_runtime_available,
            ),
            registry_provider=registry_provider,
            runtime_available_provider=child_runtime_available,
            provider_registry_manager=provider_registry_manager,
            execution_evidence_journal=execution_evidence_journal,
            delegation_shadow_journal=DelegationShadowJournal(runtime_state_root),
        )
    return ControlPlaneService(
        registry=registry,
        store=store,
        activation_gate=ActiveRoutingGate(),
        runtime_availability=dict(config.runtime_availability),
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
        provider_registry_manager=provider_registry_manager,
        owner_execution=OwnerExecutionSettings(
            runtime_state_root / "owner-execution.json"
        ),
        scheduling_settings=SchedulingSettings(
            runtime_state_root / "scheduling-settings.json"
        ),
        execution_evidence_journal=execution_evidence_journal,
        dispatch_executor=executor,
        quota_refresh_service=quota_refresh_service,
        tier_table=tier_table,
        model_tiers_source=model_tiers_source,
        supervisor=supervisor,
    )


def _run_daemon(args: argparse.Namespace) -> int:
    state_db, runtime_state_root = _resolved_state_paths(
        state_db=args.state_db,
        runtime_state_root=args.runtime_state_root,
    )
    if not _state_paths_are_safe(state_db=state_db, runtime_state_root=runtime_state_root):
        raise SystemExit("state db and runtime state root must not overlap")
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    config = load_runtime_config(args.config)
    provider_registry_manager = ProviderRegistryManager(
        runtime_state_root=runtime_state_root,
        audit_store=SafetyKernelStore(state_db),
    )
    supervisor = build_default_supervisor(
        runtime_state_root=runtime_state_root,
        state_db=state_db,
        provider_registry_manager=provider_registry_manager,
        tick_interval_seconds=args.tick_interval_seconds,
    )
    service = build_control_service(
        config=config,
        state_db=state_db,
        runtime_state_root=runtime_state_root,
        provider_registry_manager=provider_registry_manager,
        execution_repo=args.execution_repo,
        worktree_root=args.worktree_root,
        opencode_bin=args.opencode_bin,
        model_tiers_path=args.model_tiers,
        supervisor=supervisor,
    )
    server = ControlPlaneServer(service, control_socket=args.control_socket)
    stop_event = threading.Event()

    def _signal_handler(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    supervisor.start()
    server.start()
    try:
        while not stop_event.wait(0.2):
            pass
    finally:
        server.stop()
        supervisor.stop()
        service.store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal AI Orchestrator daemon")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--runtime-state-root", type=Path, required=True)
    parser.add_argument("--control-socket", type=Path, required=True)
    parser.add_argument("--execution-repo", type=Path)
    parser.add_argument("--worktree-root", type=Path)
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--model-tiers", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--control-only", action="store_true")
    parser.add_argument("--tick-interval-seconds", type=float, default=15.0)
    args = parser.parse_args()
    del args.port, args.control_only
    return _run_daemon(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
