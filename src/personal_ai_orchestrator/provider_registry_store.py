"""Credential-safe provider-registry persistence for the product runtime.

The product runtime stores a sanitized provider registry at::

    ~/Library/Application Support/Personal AI Orchestrator/provider-registry.json

The store is **credential-free**: no token, no Authorization header, no
``credential_ref`` is ever written to disk. The only thing it persists is
metadata produced by :mod:`provider_discovery` and validated by
``assert_sanitized``.

Atomic writes
-------------
The store always writes to a ``.tmp`` sibling and ``os.replace``'s into
place. A crash mid-write never leaves a half-written file on disk.

Schema versioning
-----------------
Every persisted payload carries a ``schema_version``. Bumping the schema
requires a migration step in :func:`load_or_upgrade`. Unknown future
schemas are rejected fail-closed.

Empty bootstrap upgrade
-----------------------
If the runtime config currently carries the legacy empty-bootstrap
snapshot id (``product-bootstrap-empty-registry-v1``), the bootstrap
detector in :mod:`product_daemon` calls
:func:`upgrade_from_empty_bootstrap` which runs a discovery cycle and
writes the sanitized registry. This module never assumes a user-defined
registry may be silently overwritten: it only upgrades from the
explicitly empty-bootstrap snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.provider_discovery import (
    DiscoveryResult,
    discover,
)

CURRENT_SCHEMA_VERSION = 1
PERSISTED_FILENAME = "provider-registry.json"
EMPTY_BOOTSTRAP_SNAPSHOT_ID = "product-bootstrap-empty-registry-v1"


@dataclass(frozen=True)
class PersistedRegistry:
    """A loaded provider-registry snapshot.

    The ``payload`` is the raw JSON dict. ``assert_sanitized`` has
    already been applied at load time, so callers can project fields
    to the Dashboard without further scrubbing.
    """

    payload: dict[str, Any]
    source_path: Path

    @property
    def discovered_at(self) -> datetime | None:
        raw = self.payload.get("generated_at")
        if not isinstance(raw, str):
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    @property
    def discovery_state(self) -> str | None:
        value = self.payload.get("discovery_state")
        return value if isinstance(value, str) else None

    @property
    def last_error_code(self) -> str | None:
        value = self.payload.get("last_error_code")
        return value if isinstance(value, str) else None


def registry_path(runtime_state_root: Path) -> Path:
    """Return the canonical registry path inside the runtime state root."""

    return runtime_state_root / PERSISTED_FILENAME


def save(
    result: DiscoveryResult,
    *,
    runtime_state_root: Path,
) -> Path:
    """Persist a sanitized discovery result atomically.

    Returns the path that was written.

    The payload is rejected if it contains anything that looks like a
    secret (``assert_sanitized`` raises ``ValueError``). The caller is
    expected to surface that error so the operator can re-run discovery
    with a sanitized input.
    """

    if not isinstance(result, DiscoveryResult):
        raise TypeError("save() requires a DiscoveryResult")
    payload = dict(result.to_dict())
    payload["schema_version"] = CURRENT_SCHEMA_VERSION
    assert_sanitized(payload)
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    target = registry_path(runtime_state_root)
    tmp_descriptor, tmp_path = tempfile.mkstemp(
        prefix=f"{PERSISTED_FILENAME}.",
        suffix=".tmp",
        dir=str(runtime_state_root),
    )
    try:
        with os.fdopen(tmp_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup; ignore secondary errors here.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return target


def load(runtime_state_root: Path) -> PersistedRegistry | None:
    """Load the persisted registry, or ``None`` if missing/corrupt.

    A corrupt payload is treated the same as a missing file: the product
    runtime falls back to a fresh discovery cycle. We deliberately do not
    raise — load() is on the daemon hot path and must never panic on a
    partial-write left over from an earlier crash.
    """

    target = registry_path(runtime_state_root)
    if not target.is_file():
        return None
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
        # Unknown future schema — refuse to load to avoid silent
        # misinterpretation. Operator must run a discovery cycle to
        # regenerate the file under the current schema.
        return None
    try:
        assert_sanitized(payload)
    except ValueError:
        return None
    return PersistedRegistry(payload=payload, source_path=target)


def is_empty_bootstrap_catalog(catalog_snapshot_id: str | None) -> bool:
    """True if the runtime config still carries the legacy bootstrap-empty
    snapshot id and is therefore due for an automatic upgrade.
    """

    return catalog_snapshot_id == EMPTY_BOOTSTRAP_SNAPSHOT_ID


def upgrade_from_empty_bootstrap(
    *,
    runtime_state_root: Path,
    opencode_path: Path | None = None,
) -> tuple[PersistedRegistry | None, str | None]:
    """Run a discovery cycle and persist the sanitized result.

    Called by ``product_daemon`` when the loaded runtime config still
    references the legacy empty-bootstrap snapshot id. Returns the
    freshly-persisted registry plus an optional error code.

    The function never raises for ordinary failures (missing CLI,
    timeout). On hard failure it returns ``(None, error_code)`` so the
    caller can persist the error alongside the bootstrap snapshot and
    surface the truth to the Dashboard.
    """

    outcome = discover(opencode_path=opencode_path)
    if outcome.error_code is not None or outcome.result is None:
        return None, outcome.error_code or "DISCOVERY_FAILED"
    try:
        save(outcome.result, runtime_state_root=runtime_state_root)
    except Exception as exc:  # pragma: no cover — defensive
        return None, f"PERSIST_FAILED:{exc}"
    persisted = load(runtime_state_root)
    return persisted, None


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EMPTY_BOOTSTRAP_SNAPSHOT_ID",
    "PERSISTED_FILENAME",
    "PersistedRegistry",
    "is_empty_bootstrap_catalog",
    "load",
    "registry_path",
    "save",
    "upgrade_from_empty_bootstrap",
]