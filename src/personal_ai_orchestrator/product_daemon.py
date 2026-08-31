"""Product daemon entrypoint for the bundled macOS helper.

This entrypoint bootstraps credential-free per-user runtime state and then runs the
typed UDS control plane. It deliberately avoids provider credential discovery and does
not enable Production ACTIVE from static config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from personal_ai_orchestrator.daemon import main as daemon_main
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import (
    ApplicationSupportLayout,
    RuntimeConfig,
    bootstrap_application_support,
    default_application_support_layout,
)

PRODUCT_CATALOG_SNAPSHOT_ID = "product-bootstrap-empty-registry-v1"


def default_product_config() -> RuntimeConfig:
    """Credential-free setup-required config for first launch."""

    return RuntimeConfig(
        catalog_snapshot_id=PRODUCT_CATALOG_SNAPSHOT_ID,
        registry=ModelRegistry(),
    )


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
    return daemon_main(build_daemon_argv(layout, host=args.host, port=args.port))


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess/package smoke
    raise SystemExit(main())


__all__ = [
    "PRODUCT_CATALOG_SNAPSHOT_ID",
    "build_daemon_argv",
    "default_product_config",
    "main",
    "parse_args",
]
