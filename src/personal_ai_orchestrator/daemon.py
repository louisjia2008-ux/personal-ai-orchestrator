"""Headless local daemon entrypoint for recommendation-only Shadow routing."""

from __future__ import annotations

import argparse
from pathlib import Path

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.execution_controller import reconcile_workspace_truth
from personal_ai_orchestrator.local_api import serve
from personal_ai_orchestrator.policy_snapshot import PolicySnapshotJournal
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.runtime_config import RuntimeConfig
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore


def load_runtime_config(path: Path) -> RuntimeConfig:
    return RuntimeConfig.model_validate_json(path.read_text(encoding="utf-8"))


def build_service(
    *,
    config: RuntimeConfig,
    state_db: Path,
    runtime_state_root: Path,
) -> RoutingService:
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    store = SafetyKernelStore(state_db)
    # A daemon restart destroys live process truth. Reconcile before exposing any routing path so
    # stale RUNNING/WORKER_FINISHED/VERIFYING tasks cannot continue to influence model selection.
    store.reconcile_startup()
    # Startup blocking is also the point at which stale writer ownership becomes invalid. Clear
    # those exact persisted locks and fail closed any still-routable task whose worktree vanished.
    reconcile_workspace_truth(store)
    service = RoutingService(
        registry=config.registry,
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_runtime_config(args.config)
    service = build_service(
        config=config,
        state_db=args.state_db,
        runtime_state_root=args.runtime_state_root,
    )
    try:
        serve(service, host=args.host, port=args.port)
    finally:
        service.store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through real daemon acceptance
    raise SystemExit(main())


__all__ = ["build_service", "load_runtime_config", "main", "parse_args"]
