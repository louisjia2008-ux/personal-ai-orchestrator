"""Headless local daemon entrypoint for recommendation-only Shadow routing."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneService
from personal_ai_orchestrator.control_provider_registry_view import (
    ControlPlaneProviderRegistryView,
)
from personal_ai_orchestrator.daemon_supervisor import (
    DaemonSupervisor,
    build_default_supervisor,
)
from personal_ai_orchestrator.delegation_campaign import (
    DelegationCalibrationCampaignStore,
)
from personal_ai_orchestrator.delegation_campaign_control import (
    DelegationCampaignControlPlaneServer,
)
from personal_ai_orchestrator.delegation_campaign_runtime import (
    CampaignAwareDelegationChildPort,
)
from personal_ai_orchestrator.delegation_evidence import (
    DelegationOutcomeJournal,
    resolve_delegation_policy,
)
from personal_ai_orchestrator.delegation_quota_calibration import (
    DelegationQuotaCalibrationJournal,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from personal_ai_orchestrator.dispatch_executor import (
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
)
from personal_ai_orchestrator.dispatch_recommendation_service import (
    DispatchRecommendationService,
)
from personal_ai_orchestrator.execution_controller import reconcile_workspace_truth
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.local_api import serve
from personal_ai_orchestrator.model_tiers import (
    DEFAULT_TIER_TABLE_JSON,
    TierTable,
    parse_tier_table,
)
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
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
    store.reconcile_startup()
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
        policy=config.policy,
        project_policy_overrides=dict(config.project_policy_overrides),
        task_policy_overrides=dict(config.task_policy_overrides),
        activation_gate=ActiveRoutingGate(),
        policy_journal=PolicySnapshotJournal(runtime_state_root),
        runtime_availability=dict(config.runtime_availability),
        telemetry=dict(config.telemetry),
        connected_provider_ids_provider=(
            provider_registry_manager.routing_connected_provider_ids
            if provider_registry_manager is not None
            else None
        ),
        scheduling_settings=SchedulingSettings(
            runtime_state_root / "scheduling-settings.json"
        ),
    )
    for profile in config.task_profiles:
        service.set_task_profile(profile)
    return service


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Personal AI Orchestrator Shadow routing daemon"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--runtime-state-root", type=Path, required=True)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "::1", "localhost"),
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--control-socket",
        type=Path,
        default=None,
        help="also serve the P4 typed control plane on this Unix Domain Socket",
    )
    parser.add_argument(
        "--control-only",
        action="store_true",
        help="serve only the typed UDS control plane; skip the loopback routing API",
    )
    parser.add_argument(
        "--execution-repo",
        type=Path,
        default=None,
        help="host-owned repository policy enabling owner-dispatch worker execution",
    )
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=None,
        help="managed root for task worktrees (defaults under runtime state root)",
    )
    parser.add_argument(
        "--verifier-profile",
        type=Path,
        default=None,
        help=(
            "host-owned deterministic verifier profile JSON "
            "(VerifierProfile schema); without it dispatch fails closed"
        ),
    )
    parser.add_argument(
        "--worker-permission-config",
        type=Path,
        default=None,
        help=(
            "host-owned opencode.json seeded into each task worktree "
            "(edit allow, bash/webfetch deny recommended)"
        ),
    )
    parser.add_argument(
        "--model-tiers-path",
        type=Path,
        default=None,
        help=(
            "host-owned M1 WP2 tier table JSON (parsed at startup; "
            "malformed files fall back to DEFAULT_TIER_TABLE_JSON and "
            "emit a MODEL_TIERS_INVALID system event)"
        ),
    )
    parser.add_argument(
        "--tick-interval-seconds",
        type=float,
        default=None,
        help=(
            "interval between DaemonSupervisor ticks (heartbeat cadence); "
            "falls back to PAO_TICK_INTERVAL_SECONDS, then 5.0s"
        ),
    )
    return parser.parse_args(argv)


def load_verifier_profile(path: Path | None):
    """Load the host verifier profile; absent profile stays fail-closed None."""

    if path is None:
        return None
    from personal_ai_orchestrator.verifier import VerifierProfile

    return VerifierProfile.model_validate_json(path.read_text(encoding="utf-8"))


def load_model_tiers(
    path: Path | None,
    *,
    audit: SafetyKernelStore | None = None,
) -> tuple[TierTable, str]:
    """Load the host-owned tier table once at startup."""

    if path is None:
        return parse_tier_table(DEFAULT_TIER_TABLE_JSON), "default_fallback"

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return parse_tier_table(raw), "owner_file"
    except (ValueError, OSError) as exc:
        if audit is not None:
            audit.record_system_event(
                "MODEL_TIERS_INVALID",
                {
                    "path": str(path),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        return parse_tier_table(DEFAULT_TIER_TABLE_JSON), "default_fallback"


def default_quota_collectors() -> dict[str, object]:
    """Collectors keyed by discovery provider family."""

    collectors: dict[str, object] = {}
    for spec in QUOTA_SOURCES:
        if spec.provider_id not in {
            "zai-coding-plan",
            "minimax-coding-plan",
            "minimax-cn-coding-plan",
        }:
            continue
        token = os.environ.get(spec.credential_env_var)
        if token:
            collectors[spec.provider_id] = spec.factory(
                SecretValue(token),
                spec.quota_pool_id,
            )
    collectors["opencode"] = UnmeteredQuotaCollector(
        provider_id="opencode",
        pool_id="opencode",
        covered_model_ids=(
            "big-pickle",
            "ling-3.0-flash-fin-free",
            "mimo-v2.5-free",
            "muse-spark-1.2-contributor-free",
            "muse-spark-1.3-contributor-free",
            "nemotron-3-ultra-free",
            "nemotron-3.5-lightning-free",
        ),
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
    """Build the control-plane facade over the same durable truth."""

    registry = (
        provider_registry_manager.registry()
        if provider_registry_manager is not None
        else config.registry
    )
    store = SafetyKernelStore(state_db)
    tier_table, model_tiers_source = load_model_tiers(
        model_tiers_path,
        audit=store,
    )
    scheduling_settings = SchedulingSettings(
        runtime_state_root / "scheduling-settings.json"
    )
    task_profiles = {profile.task_id: profile for profile in config.task_profiles}
    delegation_campaign = DelegationCalibrationCampaignStore(
        runtime_state_root / "delegation-calibration-campaign.json"
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
            worktree_root=worktree_root or (runtime_state_root / "worktrees"),
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
            provider_registry_manager.routing_connected_provider_ids
            if provider_registry_manager is not None
            else tuple
        ),
    )
    if pi_executor is not None:

        def child_runtime_available(target_id: str) -> bool:
            if target_id in config.runtime_availability:
                return config.runtime_availability[target_id]
            return bool(provider_registry_manager.runtime_available(target_id))

        delegation_shadow_journal = DelegationShadowJournal(runtime_state_root)
        delegation_outcome_journal = DelegationOutcomeJournal(runtime_state_root)
        delegation_quota_journal = DelegationQuotaCalibrationJournal(runtime_state_root)
        pi_executor.delegation_child_port = CampaignAwareDelegationChildPort(
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
            delegation_shadow_journal=delegation_shadow_journal,
            delegation_outcome_journal=delegation_outcome_journal,
            task_profile_provider=lambda task_id: task_profiles.get(task_id),
            policy_resolution_provider=lambda child_store, parent_task: (
                resolve_delegation_policy(
                    store=child_store,
                    task=parent_task,
                    global_policy=config.policy,
                    project_policy_overrides=dict(config.project_policy_overrides),
                    task_policy_overrides=dict(config.task_policy_overrides),
                    scheduling_settings=scheduling_settings,
                )
            ),
            delegation_campaign=delegation_campaign,
            quota_refresh_service=quota_refresh_service,
            quota_calibration_journal=delegation_quota_journal,
        )

    control_provider_registry_manager = (
        cast(
            ProviderRegistryManager,
            ControlPlaneProviderRegistryView(provider_registry_manager),
        )
        if provider_registry_manager is not None
        else None
    )
    service = ControlPlaneService(
        registry=registry,
        store=store,
        activation_gate=ActiveRoutingGate(),
        runtime_availability=dict(config.runtime_availability),
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
        provider_registry_manager=control_provider_registry_manager,
        owner_execution=OwnerExecutionSettings(runtime_state_root / "owner-execution.json"),
        scheduling_settings=scheduling_settings,
        execution_evidence_journal=execution_evidence_journal,
        dispatch_executor=executor,
        quota_refresh_service=quota_refresh_service,
        supervisor=supervisor,
        tier_table=tier_table,
        model_tiers_source=model_tiers_source,
        shadow_journal=ShadowEvidenceJournal(runtime_state_root),
        catalog_snapshot_id=config.catalog_snapshot_id,
    )
    # Deliberately not a ControlPlaneService authority field: both the wrapped
    # UDS handler and campaign-aware evidence adapter share this host object.
    service.__dict__["_delegation_campaign_store"] = delegation_campaign
    return service


def _resolve_tick_interval_seconds(args: argparse.Namespace) -> float:
    raw = args.tick_interval_seconds
    if raw is None:
        env_raw = os.environ.get("PAO_TICK_INTERVAL_SECONDS")
        if env_raw is None:
            return 5.0
        raw = float(env_raw)
    if raw <= 0:
        raise ValueError("tick interval must be > 0 seconds")
    return float(raw)


def main(
    argv: list[str] | None = None,
    *,
    provider_registry_manager: ProviderRegistryManager | None = None,
) -> int:
    args = parse_args(argv)
    config = load_runtime_config(args.config)
    if provider_registry_manager is None:
        provider_registry_manager = ProviderRegistryManager(
            runtime_state_root=args.runtime_state_root,
        )
    service = build_service(
        config=config,
        state_db=args.state_db,
        runtime_state_root=args.runtime_state_root,
        provider_registry_manager=provider_registry_manager,
    )
    if provider_registry_manager._audit is None:
        provider_registry_manager._audit = service.store
    control_server: DelegationCampaignControlPlaneServer | None = None
    control_service: ControlPlaneService | None = None
    tick_interval_seconds = _resolve_tick_interval_seconds(args)
    supervisor = build_default_supervisor(
        interval_seconds=tick_interval_seconds,
        clock=lambda: datetime.now(UTC),
        audit_store=service.store,
    )
    if args.control_socket is not None:
        control_service = build_control_service(
            config=config,
            state_db=args.state_db,
            runtime_state_root=args.runtime_state_root,
            provider_registry_manager=provider_registry_manager,
            execution_repo=args.execution_repo,
            worktree_root=args.worktree_root,
            verifier_profile=load_verifier_profile(args.verifier_profile),
            worker_permission_config=args.worker_permission_config,
            model_tiers_path=args.model_tiers_path,
            supervisor=supervisor,
        )
        campaign = control_service.__dict__["_delegation_campaign_store"]
        control_server = DelegationCampaignControlPlaneServer(
            control_service,
            args.control_socket,
            campaign=campaign,
        )
        control_server.start_background()
        if not args.control_only:
            from personal_ai_orchestrator.supervised_auto_step import (
                SUPERVISED_AUTO_STEP_NAME,
            )

            supervisor.register(
                SUPERVISED_AUTO_STEP_NAME,
                control_service.build_supervised_auto_step(),
            )
    if args.control_only:
        if control_server is None:
            raise SystemExit("--control-only requires --control-socket")
        supervisor_stop = threading.Event()
        shutdown_requested = False
        previous_term = signal.getsignal(signal.SIGTERM)

        def _request_shutdown(_signum, _frame) -> None:
            # Python dispatches this handler on the main thread.  Keep it to a
            # scalar assignment: Event.set(), logging, server shutdown and
            # every other lock-backed cleanup operation belong below in the
            # ordinary control flow.
            nonlocal shutdown_requested
            shutdown_requested = True

        signal.signal(signal.SIGTERM, _request_shutdown)
        supervisor_thread = threading.Thread(
            target=supervisor.run,
            args=(supervisor_stop,),
            daemon=True,
        )
        supervisor_thread.start()
        try:
            while not shutdown_requested:
                time.sleep(0.1)
        except KeyboardInterrupt:
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            supervisor_stop.set()
            supervisor_thread.join(timeout=2.0)
            control_server.stop()
            if control_service is not None:
                control_service.store.close()
            service.store.close()
        return 0

    supervisor_stop = threading.Event()
    supervisor_thread = threading.Thread(
        target=supervisor.run,
        args=(supervisor_stop,),
        daemon=True,
    )
    supervisor_thread.start()
    try:
        serve(service, host=args.host, port=args.port)
    except KeyboardInterrupt:
        return 0
    finally:
        supervisor_stop.set()
        supervisor_thread.join(timeout=2.0)
        if control_server is not None:
            control_server.stop()
        if control_service is not None:
            control_service.store.close()
        service.store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "build_control_service",
    "build_service",
    "load_runtime_config",
    "main",
    "parse_args",
]
