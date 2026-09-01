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
import sys
from pathlib import Path

from personal_ai_orchestrator.daemon import main as daemon_main
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager
from personal_ai_orchestrator.provider_registry_store import (
    EMPTY_BOOTSTRAP_SNAPSHOT_ID,
)
from personal_ai_orchestrator.provider_registry_store import (
    load as load_persisted_registry,
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
    ``EMPTY`` status so the Dashboard can surface the truth.
    """

    manager = ProviderRegistryManager(
        runtime_state_root=layout.runtime_state_root,
        opencode_path=opencode_path,
    )
    # §22: empty-bootstrap upgrade is automatic, deterministic, and
    # does not require operator intervention.
    persisted = load_persisted_registry(layout.runtime_state_root)
    if persisted is None:
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


def build_daemon_argv(layout: ApplicationSupportLayout, *, host: str, port: int) -> list[str]:
    return [
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
    # P4.2.4-A.1 single-startup-contract: build the manager once here
    # so the bootstrap upgrade (cold first launch) and the in-memory
    # rehydration (subsequent boots) happen in the same place. The
    # manager is then handed to ``daemon.main`` so the bundled daemon
    # and the manager share the exact same in-memory state.
    manager = resolve_dynamic_registry(layout=layout)
    # The empty-bootstrap upgrade is a no-op when the manager already
    # rehydrated from a persisted snapshot; otherwise it runs exactly
    # one discovery cycle and writes the sanitized result.
    manager.bootstrap_if_empty(catalog_snapshot_id=PRODUCT_CATALOG_SNAPSHOT_ID)
    return daemon_main(
        build_daemon_argv(layout, host=args.host, port=args.port),
        provider_registry_manager=manager,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess/package smoke
    raise SystemExit(main())


__all__ = [
    "PRODUCT_CATALOG_SNAPSHOT_ID",
    "build_daemon_argv",
    "default_product_config",
    "main",
    "parse_args",
    "resolve_dynamic_registry",
]
