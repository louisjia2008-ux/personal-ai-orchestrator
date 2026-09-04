"""Product daemon entrypoint for the bundled macOS helper.

This entrypoint bootstraps credential-free per-user runtime state and then runs the
typed UDS control plane. P4.2.4-A adds credential-safe provider discovery:

- the static ``runtime.json`` is the legacy empty-bootstrap snapshot;
- on first launch the daemon runs ``provider_discovery.discover()`` and
  persists the sanitized result to ``provider-registry.json``;
- on subsequent launches the persisted snapshot is reused without
  re-invoking the OpenCode CLI;
- the *dynamic* :class:`ModelRegistry` and the discovery status are
  owned by :class:`ProviderRegistryManager` and projected to the
  Dashboard via the Control API.

Production ACTIVE remains ``DISABLED_BY_DESIGN``: this phase only adds
provider truth to the Dashboard, not autonomous routing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from personal_ai_orchestrator.daemon import main as daemon_main
from personal_ai_orchestrator.legacy_migration import migrate_legacy_state
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.provider_registry_store import (
    EMPTY_BOOTSTRAP_SNAPSHOT_ID,
)
from personal_ai_orchestrator.runtime_config import (
    ApplicationSupportLayout,
    RuntimeConfig,
    bootstrap_application_support,
    default_application_support_layout,
)

PRODUCT_CATALOG_SNAPSHOT_ID = "product-bootstrap-empty-registry-v1"


def default_product_config() -> RuntimeConfig:
    """Credential-free setup-required config for first launch.

    The bootstrap config intentionally starts with an empty
    :class:`ModelRegistry` so the product daemon never carries stale
    credential references in its static config. The real registry is
    populated by :class:`ProviderRegistryManager` at boot via
    credential-safe provider discovery.
    """

    return RuntimeConfig(
        catalog_snapshot_id=PRODUCT_CATALOG_SNAPSHOT_ID,
        registry=ModelRegistry(),
    )


def resolve_dynamic_registry(
    *,
    layout: ApplicationSupportLayout,
    opencode_path: Path | None = None,
) -> ProviderRegistryManager:
    """Build the dynamic provider-registry manager for the bundled daemon.

    The manager:

    1. Loads any previously persisted sanitized registry from disk.
    2. If the static config still references the empty-bootstrap
       snapshot id, runs a fresh discovery cycle to upgrade it
       (§22 upgrade-in-place behavior).
    3. Exposes the resulting ``ModelRegistry`` to the Control API.

    The function never raises; on failure the manager retains an
    observable ``FAILED`` status so the Dashboard can surface the truth.
    """

    manager = ProviderRegistryManager(
        runtime_state_root=layout.runtime_state_root,
        opencode_path=opencode_path,
    )
    # Sole owner of first-boot bootstrap. The manager decides whether
    # persisted state is MISSING (cold first install) or invalid
    # (fail-closed; no automatic discovery).
    manager.bootstrap_if_empty(catalog_snapshot_id=EMPTY_BOOTSTRAP_SNAPSHOT_ID)
    return manager


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Personal AI Orchestrator bundled daemon")
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="override home directory for isolated runtime acceptance tests",
    )
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "::1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def build_daemon_argv(
    layout: ApplicationSupportLayout,
    *,
    host: str,
    port: int,
    execution_repo: Path | None = None,
    verifier_profile: Path | None = None,
    worker_permission_config: Path | None = None,
) -> list[str]:
    argv = [
        "--config",
        str(layout.runtime_config),
        "--state-db",
        str(layout.state_db),
        "--runtime-state-root",
        str(layout.runtime_state_root),
        "--control-socket",
        str(layout.socket_path),
        "--host",
        host,
        "--port",
        str(port),
        "--control-only",
    ]
    if execution_repo is not None:
        argv += ["--execution-repo", str(execution_repo)]
    if verifier_profile is not None:
        argv += ["--verifier-profile", str(verifier_profile)]
    if worker_permission_config is not None:
        argv += ["--worker-permission-config", str(worker_permission_config)]
    return argv


#: The deterministic, no-arbitrary-commands verifier every bundled daemon
#: ships with. The owner may replace the JSON on disk; the daemon only
#: seeds it when missing.
DEFAULT_VERIFIER_PROFILE: dict[str, object] = {
    "name": "product-default",
    "commands": [],
    "allowed_paths": [],
    "require_diff_check": True,
}

#: The host-owned OpenCode worker sandbox policy seeded into every task
#: worktree: edits inside the assigned worktree are allowed; shell and
#: web access are denied outright. Non-interactive ``opencode run``
#: auto-rejects permission prompts, so without this file every worker
#: edit dies as "permission requested: edit; auto-rejecting".
DEFAULT_WORKER_PERMISSION_CONFIG: dict[str, object] = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {"edit": "allow", "bash": "deny", "webfetch": "deny"},
}


def ensure_execution_policies(layout: ApplicationSupportLayout) -> tuple[Path, Path, Path] | None:
    """Create the host-owned owner-dispatch policy artifacts, idempotently.

    Returns ``(execution_repo, verifier_profile_path, worker_permissions)``,
    or ``None`` when the host cannot support owner dispatch (no git,
    unwritable state) — in that case the daemon still boots, exactly like
    today, with dispatch reserved but never executed.
    """

    policies = layout.runtime_state_root / "policies"
    execution_repo = policies / "execution-repo"
    verifier_profile = policies / "verifier-profile.json"
    worker_permissions = policies / "worker-opencode.json"
    try:
        policies.mkdir(parents=True, exist_ok=True)
        if not (execution_repo / ".git").is_dir():
            execution_repo.mkdir(exist_ok=True)
            subprocess.run(
                ["git", "init", "-q", str(execution_repo)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        if not verifier_profile.is_file():
            verifier_profile.write_text(
                json.dumps(DEFAULT_VERIFIER_PROFILE, indent=2) + "\n",
                encoding="utf-8",
            )
        if not worker_permissions.is_file():
            worker_permissions.write_text(
                json.dumps(DEFAULT_WORKER_PERMISSION_CONFIG, indent=2) + "\n",
                encoding="utf-8",
            )
    except Exception:
        return None
    return execution_repo, verifier_profile, worker_permissions


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        layout = bootstrap_application_support(
            default_product_config(),
            layout=default_application_support_layout(args.home),
        )
    except Exception:
        print("pao-daemon: runtime_config_invalid", file=sys.stderr)
        return 78
    # §11 legacy state migration: idempotent, non-destructive, never
    # copies credentials or authority surfaces; every outcome is
    # observable in runtime-state/migration/legacy-migration-v1.json.
    legacy_home = args.home if args.home is not None else Path.home()
    migrate_legacy_state(
        legacy_home / ".personal-ai-orchestrator",
        layout.app_support_root,
    )
    # P4.2.4-A.1 single-startup-contract: build the manager once here
    # so the bootstrap upgrade (cold first launch) and the in-memory
    # rehydration (subsequent boots) happen in the same place. The
    # manager is then handed to ``daemon.main`` so the bundled daemon
    # and the manager share the exact same in-memory state.
    manager = resolve_dynamic_registry(layout=layout)
    policies = ensure_execution_policies(layout)
    if policies is None:
        print("pao-daemon: execution policies unavailable", file=sys.stderr)
    return daemon_main(
        build_daemon_argv(
            layout,
            host=args.host,
            port=args.port,
            execution_repo=policies[0] if policies else None,
            verifier_profile=policies[1] if policies else None,
            worker_permission_config=policies[2] if policies else None,
        ),
        provider_registry_manager=manager,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess/package smoke
    raise SystemExit(main())


__all__ = [
    "DEFAULT_VERIFIER_PROFILE",
    "DEFAULT_WORKER_PERMISSION_CONFIG",
    "PRODUCT_CATALOG_SNAPSHOT_ID",
    "build_daemon_argv",
    "default_product_config",
    "ensure_execution_policies",
    "main",
    "parse_args",
    "resolve_dynamic_registry",
]
