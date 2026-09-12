"""Persistent storage for the Pi runtime discovery snapshot.

The Pi snapshot is kept separate from the OpenCode provider registry so runtime
provenance never gets flattened into the OpenCode discovery schema.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from personal_ai_orchestrator.pi_provider_discovery import PiDiscoveryResult
from personal_ai_orchestrator.provider_acceptance import assert_sanitized

CURRENT_SCHEMA_VERSION = 1
PERSISTED_FILENAME = "pi-provider-registry.json"


class PiRegistryLoadStatus(StrEnum):
    MISSING = "MISSING"
    LOADED = "LOADED"
    CORRUPT = "CORRUPT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    SANITIZATION_REJECTED = "SANITIZATION_REJECTED"


@dataclass(frozen=True)
class PersistedPiRegistry:
    payload: dict[str, Any]
    source_path: Path


@dataclass(frozen=True)
class PiRegistryLoadOutcome:
    status: PiRegistryLoadStatus
    persisted: PersistedPiRegistry | None = None
    source_path: Path | None = None
    error_code: str | None = None


def registry_path(runtime_state_root: Path) -> Path:
    return runtime_state_root / PERSISTED_FILENAME


def save(
    result: PiDiscoveryResult,
    *,
    runtime_state_root: Path,
) -> Path:
    if not isinstance(result, PiDiscoveryResult):
        raise TypeError("save() requires a PiDiscoveryResult")
    payload = dict(result.to_dict())
    payload["schema_version"] = CURRENT_SCHEMA_VERSION
    assert_sanitized(payload)
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    target = registry_path(runtime_state_root)
    descriptor, tmp_path = tempfile.mkstemp(
        prefix=f"{PERSISTED_FILENAME}.",
        suffix=".tmp",
        dir=str(runtime_state_root),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return target


def load(runtime_state_root: Path) -> PiRegistryLoadOutcome:
    target = registry_path(runtime_state_root)
    if not target.is_file():
        return PiRegistryLoadOutcome(
            status=PiRegistryLoadStatus.MISSING,
            source_path=target,
        )
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return PiRegistryLoadOutcome(
            status=PiRegistryLoadStatus.CORRUPT,
            source_path=target,
            error_code="PERSISTED_PI_REGISTRY_CORRUPT",
        )
    if not isinstance(payload, dict):
        return PiRegistryLoadOutcome(
            status=PiRegistryLoadStatus.CORRUPT,
            source_path=target,
            error_code="PERSISTED_PI_REGISTRY_CORRUPT",
        )
    if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
        return PiRegistryLoadOutcome(
            status=PiRegistryLoadStatus.UNSUPPORTED_SCHEMA,
            source_path=target,
            error_code="PERSISTED_PI_REGISTRY_UNSUPPORTED_SCHEMA",
        )
    try:
        assert_sanitized(payload)
    except ValueError:
        return PiRegistryLoadOutcome(
            status=PiRegistryLoadStatus.SANITIZATION_REJECTED,
            source_path=target,
            error_code="PERSISTED_PI_REGISTRY_SANITIZATION_REJECTED",
        )
    return PiRegistryLoadOutcome(
        status=PiRegistryLoadStatus.LOADED,
        persisted=PersistedPiRegistry(payload=payload, source_path=target),
        source_path=target,
    )
