"""Credential-free owner provider connections.

Discovery answers what the local runtime/catalog knows about. A provider
connection answers what the owner has explicitly registered for scheduling.
This module deliberately stores no tokens, no credential values, and no
OpenCode auth payloads.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.provider_discovery import AuthStatus, DiscoveryResult

CONNECTIONS_FILENAME = "provider-connections.json"
CURRENT_SCHEMA_VERSION = 1


class ProviderConnectionState(StrEnum):
    DISCOVERED = "DISCOVERED"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    EXECUTION_VERIFIED = "EXECUTION_VERIFIED"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DISCONNECTED = "DISCONNECTED"


class ProviderAuthState(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_UNKNOWN = "AUTH_UNKNOWN"
    AUTH_REQUIRED = "AUTH_REQUIRED"


class ProviderRuntimeState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class CredentialReferenceType(StrEnum):
    OPENCODE_AUTH = "OPENCODE_AUTH"
    ENV_PRESENCE = "ENV_PRESENCE"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class ProviderConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    connection_state: ProviderConnectionState = ProviderConnectionState.CONNECTED
    auth_state: ProviderAuthState = ProviderAuthState.AUTH_UNKNOWN
    execution_verified: bool = False
    runtime_state: ProviderRuntimeState = ProviderRuntimeState.UNKNOWN
    credential_reference_type: CredentialReferenceType = CredentialReferenceType.UNKNOWN
    region: str | None = None
    plan_surface: str | None = None
    model_skus: tuple[str, ...] = ()
    connected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_validated_at: datetime | None = None
    last_reason_code: str | None = None

    @property
    def scheduler_connected(self) -> bool:
        return self.connection_state is not ProviderConnectionState.DISCONNECTED


class ProviderConnectionRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = CURRENT_SCHEMA_VERSION
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    connections: dict[str, ProviderConnection] = Field(default_factory=dict)

    def connected_provider_ids(self) -> frozenset[str]:
        return frozenset(
            provider_id
            for provider_id, connection in self.connections.items()
            if connection.scheduler_connected
        )


class ImportEvidenceKind(StrEnum):
    """Why a historical provider surface may be offered for import.

    Catalog discovery is deliberately absent. Seeing a provider in
    ``opencode models`` says the binary knows the name, not that this owner ever
    had a working connection to it.
    """

    CREDENTIAL_REGION_SCOPED = "CREDENTIAL_REGION_SCOPED"
    CREDENTIAL_PLAN_SURFACE_SCOPED = "CREDENTIAL_PLAN_SURFACE_SCOPED"
    PRIOR_VERIFIED_EXECUTION = "PRIOR_VERIFIED_EXECUTION"


class ImportEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ImportEvidenceKind
    #: Owner-facing sanitized sentence. Never a credential value, path, or token.
    detail: str = Field(min_length=1)
    observed_at: datetime | None = None


class ProviderImportCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    region: str | None = None
    plan_surface: str | None = None
    model_skus: tuple[str, ...] = ()
    execution_verified: bool = False
    auth_state: ProviderAuthState = ProviderAuthState.AUTH_UNKNOWN
    credential_reference_type: CredentialReferenceType = CredentialReferenceType.UNKNOWN
    evidence: tuple[ImportEvidenceItem, ...] = ()


@dataclass(frozen=True)
class ProviderConnectionProjection:
    connected: tuple[ProviderConnection, ...]
    available_to_add: tuple[dict[str, Any], ...]
    import_candidates: tuple[ProviderImportCandidate, ...] = ()


def connections_path(runtime_state_root: Path) -> Path:
    return runtime_state_root / CONNECTIONS_FILENAME


def load_connections(runtime_state_root: Path) -> ProviderConnectionRegistry:
    target = connections_path(runtime_state_root)
    if not target.is_file():
        return ProviderConnectionRegistry()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ProviderConnectionRegistry()
    if not isinstance(payload, dict):
        return ProviderConnectionRegistry()
    if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
        return ProviderConnectionRegistry()
    try:
        assert_sanitized(payload)
        return ProviderConnectionRegistry.model_validate(payload)
    except Exception:
        return ProviderConnectionRegistry()


def save_connections(
    registry: ProviderConnectionRegistry,
    *,
    runtime_state_root: Path,
) -> Path:
    payload = json.loads(registry.model_dump_json())
    assert_sanitized(payload)
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    target = connections_path(runtime_state_root)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{CONNECTIONS_FILENAME}.",
        suffix=".tmp",
        dir=str(runtime_state_root),
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _plan_surface(provider_id: str) -> str | None:
    """Owner-facing plan surface for a provider id.

    MiniMax must never collapse its Coding Plan and Token Plan surfaces, and the
    region already lives in the provider id, so this stays a pure lookup.
    """

    if provider_id.endswith("coding-plan"):
        return "Coding Plan"
    if provider_id in {"minimax", "minimax-cn"}:
        return "Token Plan"
    return None


def _auth_state(auth_status: AuthStatus) -> ProviderAuthState:
    if auth_status is AuthStatus.AUTH_FROM_ENV_PRESENCE:
        return ProviderAuthState.AUTH_UNKNOWN
    if auth_status is AuthStatus.AUTH_REQUIRED:
        return ProviderAuthState.AUTH_REQUIRED
    return ProviderAuthState.AUTH_UNKNOWN


def _credential_reference_type(auth_status: AuthStatus) -> CredentialReferenceType:
    if auth_status is AuthStatus.AUTH_FROM_ENV_PRESENCE:
        return CredentialReferenceType.OPENCODE_AUTH
    if auth_status is AuthStatus.AUTH_REQUIRED:
        return CredentialReferenceType.NONE
    return CredentialReferenceType.UNKNOWN


def connect_from_discovery(
    current: ProviderConnectionRegistry,
    discovery: DiscoveryResult,
    *,
    provider_id: str,
    now: datetime | None = None,
) -> ProviderConnectionRegistry:
    record = next(
        (provider for provider in discovery.providers if provider.provider_id == provider_id),
        None,
    )
    if record is None or not record.catalog_discovered:
        raise LookupError("provider_not_catalog_discovered")
    timestamp = now or datetime.now(UTC)
    plan_surface = _plan_surface(provider_id)
    connection = ProviderConnection(
        provider_id=record.provider_id,
        display_name=record.display_name,
        connection_state=ProviderConnectionState.CONNECTED,
        auth_state=_auth_state(record.auth_status),
        execution_verified=record.execution_verified,
        runtime_state=ProviderRuntimeState.UNKNOWN,
        credential_reference_type=_credential_reference_type(record.auth_status),
        region=record.region,
        plan_surface=plan_surface,
        model_skus=record.model_skus,
        connected_at=current.connections.get(provider_id, None).connected_at
        if provider_id in current.connections
        else timestamp,
        last_validated_at=record.observed_at,
        last_reason_code=("EXECUTION_UNVERIFIED" if not record.execution_verified else None),
    )
    connections = dict(current.connections)
    connections[provider_id] = connection
    return ProviderConnectionRegistry(
        updated_at=timestamp,
        connections=connections,
    )


def disconnect_provider(
    current: ProviderConnectionRegistry,
    *,
    provider_id: str,
    now: datetime | None = None,
) -> ProviderConnectionRegistry:
    existing = current.connections.get(provider_id)
    if existing is None:
        raise LookupError("provider_not_connected")
    timestamp = now or datetime.now(UTC)
    connections = dict(current.connections)
    connections[provider_id] = existing.model_copy(
        update={
            "connection_state": ProviderConnectionState.DISCONNECTED,
            "runtime_state": ProviderRuntimeState.UNAVAILABLE,
            "last_validated_at": timestamp,
            "last_reason_code": "OWNER_DISCONNECTED",
        }
    )
    return ProviderConnectionRegistry(updated_at=timestamp, connections=connections)


def build_import_candidates(
    current: ProviderConnectionRegistry,
    discovery: DiscoveryResult | None,
    *,
    verified_execution_lookup: Callable[[str], datetime | None] | None = None,
) -> tuple[ProviderImportCandidate, ...]:
    """Offer historical surfaces the owner plausibly already connected, and only those.

    A strict connection registry is correct but hostile on upgrade: an owner who has
    been running GLM 5.3 for weeks should not open a blank Connected tab. This bridges
    that gap **without** weakening the registry, by proposing — never performing — an
    import.

    Admission requires evidence tied to this exact surface:

    * region-scoped credential evidence, plus plan-surface evidence where the family
      distinguishes plans, or
    * a prior VERIFIED real worker execution recorded for this exact ``provider_id``.

    ``catalog_discovered`` alone is explicitly insufficient, and no evidence is ever
    transferred between surfaces: Z.AI evidence cannot admit MiniMax, and MiniMax CN
    Coding Plan evidence cannot admit MiniMax International or its Token Plan.

    Catalog discovery is also not *required*. A surface the owner demonstrably used
    can be missing from the current ``opencode models`` snapshot, and excluding it
    would recreate the very problem this exists to solve. Such a surface is offered
    for import but never appears under available-to-add, and importing it grants no
    scheduling reach on its own: with no execution targets in the registry it simply
    has nothing to schedule.
    """

    if discovery is None:
        return ()
    connected_ids = current.connected_provider_ids()
    candidates: list[ProviderImportCandidate] = []
    for provider in sorted(discovery.providers, key=lambda item: item.provider_id):
        if provider.provider_id in connected_ids:
            continue
        # An owner-disconnected surface stays gone until they add it back explicitly.
        existing = current.connections.get(provider.provider_id)
        if existing is not None and not existing.scheduler_connected:
            continue

        evidence: list[ImportEvidenceItem] = []
        if provider.credential_region_verified:
            evidence.append(
                ImportEvidenceItem(
                    kind=ImportEvidenceKind.CREDENTIAL_REGION_SCOPED,
                    detail="已检测到与该区域匹配的现有认证",
                    observed_at=provider.observed_at,
                )
            )
        if provider.credential_plan_surface_verified:
            evidence.append(
                ImportEvidenceItem(
                    kind=ImportEvidenceKind.CREDENTIAL_PLAN_SURFACE_SCOPED,
                    detail="已检测到与该套餐类型匹配的现有认证",
                    observed_at=provider.observed_at,
                )
            )
        verified_at = (
            verified_execution_lookup(provider.provider_id)
            if verified_execution_lookup is not None
            else None
        )
        if verified_at is not None:
            evidence.append(
                ImportEvidenceItem(
                    kind=ImportEvidenceKind.PRIOR_VERIFIED_EXECUTION,
                    detail="曾成功完成真实执行验证",
                    observed_at=verified_at,
                )
            )

        credential_scoped = provider.credential_scope_verified
        if not (credential_scoped or verified_at is not None):
            continue

        candidates.append(
            ProviderImportCandidate(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                region=provider.region,
                plan_surface=_plan_surface(provider.provider_id),
                model_skus=provider.model_skus,
                execution_verified=verified_at is not None,
                auth_state=(
                    ProviderAuthState.AUTHENTICATED
                    if credential_scoped
                    else _auth_state(provider.auth_status)
                ),
                credential_reference_type=_credential_reference_type(provider.auth_status),
                evidence=tuple(evidence),
            )
        )
    return tuple(candidates)


def import_connections(
    current: ProviderConnectionRegistry,
    discovery: DiscoveryResult,
    *,
    provider_ids: Sequence[str],
    verified_execution_lookup: Callable[[str], datetime | None] | None = None,
    now: datetime | None = None,
) -> ProviderConnectionRegistry:
    """Write connection records for owner-chosen import candidates.

    Called only from an explicit owner action. Every id must still qualify as a
    candidate at write time, so a stale App view cannot import a surface whose
    evidence has since disappeared. Nothing about credentials is copied: the records
    carry a reference *type*, never a value.
    """

    candidates = {
        candidate.provider_id: candidate
        for candidate in build_import_candidates(
            current,
            discovery,
            verified_execution_lookup=verified_execution_lookup,
        )
    }
    requested = tuple(dict.fromkeys(provider_ids))
    if not requested:
        raise ValueError("no_import_candidates_requested")
    unknown = [provider_id for provider_id in requested if provider_id not in candidates]
    if unknown:
        raise LookupError("provider_not_import_candidate")

    timestamp = now or datetime.now(UTC)
    connections = dict(current.connections)
    for provider_id in requested:
        candidate = candidates[provider_id]
        connections[provider_id] = ProviderConnection(
            provider_id=candidate.provider_id,
            display_name=candidate.display_name,
            connection_state=ProviderConnectionState.CONNECTED,
            auth_state=candidate.auth_state,
            execution_verified=candidate.execution_verified,
            runtime_state=ProviderRuntimeState.UNKNOWN,
            credential_reference_type=candidate.credential_reference_type,
            region=candidate.region,
            plan_surface=candidate.plan_surface,
            model_skus=candidate.model_skus,
            connected_at=timestamp,
            last_validated_at=timestamp,
            last_reason_code="IMPORTED_EXISTING_CONNECTION",
        )
    return ProviderConnectionRegistry(updated_at=timestamp, connections=connections)


def project_connections(
    registry: ProviderConnectionRegistry,
    discovery: DiscoveryResult | None,
    *,
    verified_execution_lookup: Callable[[str], datetime | None] | None = None,
) -> ProviderConnectionProjection:
    connected = tuple(
        connection
        for _, connection in sorted(registry.connections.items())
        if connection.scheduler_connected
    )
    connected_ids = {connection.provider_id for connection in connected}
    available: list[dict[str, Any]] = []
    if discovery is not None:
        for provider in sorted(discovery.providers, key=lambda item: item.provider_id):
            if not provider.catalog_discovered or provider.provider_id in connected_ids:
                continue
            available.append(
                {
                    "provider_id": provider.provider_id,
                    "display_name": provider.display_name,
                    "connection_state": ProviderConnectionState.DISCOVERED.value,
                    "auth_state": _auth_state(provider.auth_status).value,
                    "execution_verified": provider.execution_verified,
                    "runtime_state": ProviderRuntimeState.UNKNOWN.value,
                    "region": provider.region,
                    "plan_surface": _plan_surface(provider.provider_id),
                    "model_skus": list(provider.model_skus),
                    "last_checked": provider.observed_at.isoformat(),
                }
            )
    return ProviderConnectionProjection(
        connected=connected,
        available_to_add=tuple(available),
        import_candidates=build_import_candidates(
            registry,
            discovery,
            verified_execution_lookup=verified_execution_lookup,
        ),
    )


__all__ = [
    "CredentialReferenceType",
    "ImportEvidenceItem",
    "ImportEvidenceKind",
    "ProviderImportCandidate",
    "ProviderAuthState",
    "ProviderConnection",
    "ProviderConnectionProjection",
    "ProviderConnectionRegistry",
    "ProviderConnectionState",
    "ProviderRuntimeState",
    "build_import_candidates",
    "connect_from_discovery",
    "connections_path",
    "import_connections",
    "disconnect_provider",
    "load_connections",
    "project_connections",
    "save_connections",
]
