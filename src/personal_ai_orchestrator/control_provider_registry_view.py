"""Read-only provider-registry view used by the local Control API.

The persistent ProviderConnection registry answers an owner-action question:
which OpenCode/provider surfaces did the owner explicitly connect?  Pi has a
different contract: a Pi provider is routing-connected when metadata-only Pi
discovery reports READY auth and a catalogued model.  Persisting a fake
ProviderConnection for Pi would collapse those two authorities.

This adapter therefore leaves every mutation/projection method on the real
ProviderRegistryManager untouched and overrides only ``connection_registry()``
for control-plane operational projections.  The Control API uses that method to
build provider health and quota cards; it uses ``connection_projection()`` for
the owner-managed Connected/Available UI, so Pi never masquerades as a persisted
owner connection.
"""

from __future__ import annotations

from typing import Any

from personal_ai_orchestrator.provider_connections import (
    CredentialReferenceType,
    ProviderAuthState,
    ProviderConnection,
    ProviderConnectionRegistry,
    ProviderConnectionState,
    ProviderRuntimeState,
)
from personal_ai_orchestrator.provider_registry_manager import ProviderRegistryManager


class ControlPlaneProviderRegistryView:
    """Delegate to ``ProviderRegistryManager`` with routing-aware read projection."""

    def __init__(self, manager: ProviderRegistryManager) -> None:
        self._manager = manager

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    def connection_registry(self) -> ProviderConnectionRegistry:
        """Return persistent connections plus ephemeral READY Pi surfaces.

        No returned Pi row is persisted.  If an ordinary connection with the
        same commercial provider id is DISCONNECTED, the ephemeral Pi row wins
        in this *operational* projection because Pi remains independently
        routing-connected; the owner-managed connection projection still shows
        the persisted DISCONNECTED row unchanged.
        """

        persistent = self._manager.connection_registry()
        connections = dict(persistent.connections)
        pi_manager = self._manager.pi_runtime_manager()
        if pi_manager is None:
            return persistent

        result = pi_manager.last_discovery_result()
        if result is None:
            return persistent

        pi_registry = pi_manager.registry()
        for provider in result.providers:
            if provider.auth_status.value != "READY" or not provider.model_skus:
                continue

            target_ids = [
                target.id
                for target in pi_registry.execution_targets.values()
                if (
                    pi_registry.models.get(target.model_sku_id) is not None
                    and pi_registry.models[target.model_sku_id].provider_id == provider.provider_id
                )
            ]
            runtime_available = any(
                pi_manager.runtime_available(target_id) for target_id in target_ids
            )
            connections[provider.provider_id] = ProviderConnection(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                connection_state=ProviderConnectionState.CONNECTED,
                auth_state=ProviderAuthState.AUTHENTICATED,
                # Execution verification is target-scoped evidence, not a
                # provider-discovery fact. Never promote READY auth to verified.
                execution_verified=False,
                runtime_state=(
                    ProviderRuntimeState.AVAILABLE
                    if runtime_available
                    else ProviderRuntimeState.UNAVAILABLE
                ),
                credential_reference_type=CredentialReferenceType.UNKNOWN,
                region=_region_for(provider.provider_id),
                plan_surface=_plan_surface_for(provider.provider_id),
                model_skus=provider.model_skus,
                connected_at=provider.observed_at,
                last_validated_at=provider.observed_at,
                last_reason_code="PI_RUNTIME_AUTH_READY",
            )

        return ProviderConnectionRegistry(
            schema_version=persistent.schema_version,
            updated_at=persistent.updated_at,
            connections=connections,
        )


def _region_for(provider_id: str) -> str | None:
    if provider_id.startswith("minimax-cn"):
        return "CN"
    return None


def _plan_surface_for(provider_id: str) -> str | None:
    if provider_id.endswith("coding-plan"):
        return "Coding Plan"
    if provider_id in {"minimax", "minimax-cn"}:
        return "Token Plan"
    return None


__all__ = ["ControlPlaneProviderRegistryView"]
