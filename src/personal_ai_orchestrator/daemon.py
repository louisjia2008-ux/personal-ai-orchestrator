"""Headless local daemon entrypoint for recommendation-only Shadow routing."""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneServer, ControlPlaneService
from personal_ai_orchestrator.dispatch_executor import (
    DispatchExecutorConfig,
    OwnerDispatchExecutor,
)
from personal_ai_orchestrator.execution_controller import reconcile_workspace_truth
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.local_api import serve
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.policy_snapshot import PolicySnapshotJournal
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal
from personal_ai_orchestrator.quota_collectors.minimax import MiniMaxQuotaCollector
from personal_ai_orchestrator.quota_collectors.zai import ZAIQuotaCollector
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.runtime_config import RuntimeConfig
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
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
        store=store,
        catalog_snapshot_id=config.catalog_snapshot_id,
        policy=config.policy,
        # Production ACTIVE is intentionally impossible from static config alone.
        activation_gate=ActiveRoutingGate(),
        policy_journal=PolicySnapshotJournal(runtime_state_root),
        runtime_availability=dict(config.runtime_availability),
        telemetry=dict(config.telemetry),
    )
    for profile in config.task_profiles:
        service.set_task_profile(profile)
    return service


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Personal AI Orchestrator Shadow routing daemon")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--runtime-state-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "::1", "localhost"))
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
    return parser.parse_args(argv)


def default_quota_collectors() -> dict[str, object]:
    """Collectors keyed by discovery provider family.

    Tokens come ONLY from the operator environment. auth.json is never
    read; when a token is absent the collector reports AUTH_REQUIRED and
    quota admission fails closed or records UNKNOWN truthfully.
    """

    collectors: dict[str, object] = {}
    zai_token = os.environ.get("ZAI_API_KEY")
    if zai_token:
        collectors["zai-coding-plan"] = ZAIQuotaCollector(authorization_token=zai_token)
    minimax_token = os.environ.get("MINIMAX_API_KEY")
    if minimax_token:
        collectors["minimax-coding-plan"] = MiniMaxQuotaCollector(bearer_token=minimax_token)
        collectors["minimax-cn-coding-plan"] = MiniMaxQuotaCollector(
            bearer_token=minimax_token
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
) -> ControlPlaneService:
    """Build the control-plane facade over the same durable truth.

    When ``execution_repo`` is provided (host-owned repository policy),
    owner dispatch executes real isolated worktree workers.
    """

    registry = (
        provider_registry_manager.registry()
        if provider_registry_manager is not None
        else config.registry
    )
    execution_evidence_journal = ExecutionEvidenceJournal(runtime_state_root)
    executor = None
    if execution_repo is not None:
        executor = OwnerDispatchExecutor(
            state_db=state_db,
            config=DispatchExecutorConfig(
                repo_path=execution_repo,
                worktree_root=worktree_root
                or (runtime_state_root / "worktrees"),
                opencode_bin=opencode_bin,
                verifier_profile=verifier_profile,
            ),
            registry_provider=lambda: (
                provider_registry_manager.registry()
                if provider_registry_manager is not None
                else config.registry
            ),
            verification_journal=VerificationEvidenceJournal(runtime_state_root),
            execution_evidence_journal=execution_evidence_journal,
            quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
            quota_collectors=quota_collectors or default_quota_collectors(),
        )
    return ControlPlaneService(
        registry=registry,
        store=SafetyKernelStore(state_db),
        activation_gate=ActiveRoutingGate(),
        runtime_availability=dict(config.runtime_availability),
        verification_journal=VerificationEvidenceJournal(runtime_state_root),
        quota_availability_journal=QuotaAvailabilityJournal(runtime_state_root),
        provider_registry_manager=provider_registry_manager,
        owner_execution=OwnerExecutionSettings(
            runtime_state_root / "owner-execution.json"
        ),
        execution_evidence_journal=execution_evidence_journal,
        dispatch_executor=executor,
    )


def main(
    argv: list[str] | None = None,
    *,
    provider_registry_manager: ProviderRegistryManager | None = None,
) -> int:
    args = parse_args(argv)
    config = load_runtime_config(args.config)
    # P4.2.4-A.1 single-startup-contract: when invoked from
    # ``product_daemon`` the manager has already been constructed and
    # rehydrated from disk; pass it through so we honour the contract
    # (exactly one discovery cycle on cold first launch, zero cycles on
    # subsequent boots). When invoked directly without an external
    # manager, fall back to building one here; the manager constructor
    # rehydrates from disk and does NOT run an implicit refresh.
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
    control_server: ControlPlaneServer | None = None
    control_service: ControlPlaneService | None = None
    if args.control_socket is not None:
        control_service = build_control_service(
            config=config,
            state_db=args.state_db,
            runtime_state_root=args.runtime_state_root,
            provider_registry_manager=provider_registry_manager,
            execution_repo=args.execution_repo,
            worktree_root=args.worktree_root,
        )
        control_server = ControlPlaneServer(control_service, args.control_socket)
        control_server.start_background()
    if args.control_only:
        if control_server is None:
            raise SystemExit("--control-only requires --control-socket")
        stop = threading.Event()
        previous_term = signal.getsignal(signal.SIGTERM)

        def _stop(_signum, _frame) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, _stop)
        try:
            while not stop.wait(timeout=3600):
                pass
        except KeyboardInterrupt:
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            control_server.stop()
            if control_service is not None:
                control_service.store.close()
            service.store.close()
        return 0
    try:
        serve(service, host=args.host, port=args.port)
    except KeyboardInterrupt:
        # An intentional SIGINT/Ctrl-C is a planned shutdown: exit cleanly without a
        # traceback. The finally block still stops the control plane and closes the
        # durable store; unexpected exceptions keep propagating untouched.
        return 0
    finally:
        if control_server is not None:
            control_server.stop()
        if control_service is not None:
            control_service.store.close()
        service.store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through real daemon acceptance
    raise SystemExit(main())


__all__ = [
    "build_control_service",
    "build_service",
    "load_runtime_config",
    "main",
    "parse_args",
]
