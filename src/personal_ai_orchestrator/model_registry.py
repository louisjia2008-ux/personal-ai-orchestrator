"""Typed model-resource registry for scheduler decisions.

This module deliberately models provider/account/plan/quota/model/runtime identities
separately so the scheduler cannot accidentally collapse a shared commercial quota
into fake per-model quota state.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuotaState(StrEnum):
    """Normalized availability state for a quota pool."""

    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    CRITICAL = "CRITICAL"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class EvidenceConfidence(StrEnum):
    """How trustworthy a quota/usage value is."""

    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class PlanKind(StrEnum):
    """Commercial/resource entitlement type."""

    SUBSCRIPTION = "SUBSCRIPTION"
    PAY_AS_YOU_GO = "PAY_AS_YOU_GO"
    PREPAID = "PREPAID"
    LOCAL_UNMETERED = "LOCAL_UNMETERED"
    UNKNOWN = "UNKNOWN"


class PoolKind(StrEnum):
    """Logical scheduler candidate pools."""

    WORKER = "WORKER"
    REASONING = "REASONING"
    REVIEW = "REVIEW"
    ESCALATION = "ESCALATION"
    FALLBACK = "FALLBACK"


class RegistryModel(BaseModel):
    """Shared model configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Provider(RegistryModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class Account(RegistryModel):
    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    credential_ref: str | None = None


class Plan(RegistryModel):
    id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: PlanKind = PlanKind.UNKNOWN


class QuotaSnapshot(RegistryModel):
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    used_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    remaining_units: float | None = Field(default=None, ge=0.0)
    reset_at: datetime | None = None
    source: str | None = None

    @model_validator(mode="after")
    def validate_unknown_precision(self) -> "QuotaSnapshot":
        if self.confidence is EvidenceConfidence.UNKNOWN and (
            self.used_fraction is not None or self.remaining_units is not None
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise quota values")
        return self


class QuotaPool(RegistryModel):
    id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    snapshot: QuotaSnapshot = Field(default_factory=QuotaSnapshot)
    reserve_fraction: float = Field(default=0.0, ge=0.0, le=1.0)


class CapabilityProfile(RegistryModel):
    """Task-relevant capability priors/posteriors normalized to [0, 1]."""

    scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_score_range(self) -> "CapabilityProfile":
        invalid = {name: value for name, value in self.scores.items() if not 0.0 <= value <= 1.0}
        if invalid:
            raise ValueError(f"capability scores must be within [0, 1]: {invalid}")
        return self


class ModelSKU(RegistryModel):
    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    capabilities: CapabilityProfile = Field(default_factory=CapabilityProfile)
    enabled: bool = True


class RuntimeVariant(RegistryModel):
    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    api_model_name: str = Field(min_length=1)
    context_window: int | None = Field(default=None, gt=0)
    input_cost_per_million: float | None = Field(default=None, ge=0.0)
    output_cost_per_million: float | None = Field(default=None, ge=0.0)
    enabled: bool = True


class PoolMembership(RegistryModel):
    pool: PoolKind
    model_sku_id: str = Field(min_length=1)
    runtime_variant_id: str | None = None
    priority: int = Field(default=1, ge=1)
    weight: float = Field(default=1.0, gt=0.0)


class ModelRegistry(RegistryModel):
    providers: dict[str, Provider] = Field(default_factory=dict)
    accounts: dict[str, Account] = Field(default_factory=dict)
    plans: dict[str, Plan] = Field(default_factory=dict)
    quota_pools: dict[str, QuotaPool] = Field(default_factory=dict)
    models: dict[str, ModelSKU] = Field(default_factory=dict)
    runtime_variants: dict[str, RuntimeVariant] = Field(default_factory=dict)
    pool_memberships: tuple[PoolMembership, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "ModelRegistry":
        self._validate_key_identity(self.providers)
        self._validate_key_identity(self.accounts)
        self._validate_key_identity(self.plans)
        self._validate_key_identity(self.quota_pools)
        self._validate_key_identity(self.models)
        self._validate_key_identity(self.runtime_variants)

        for account in self.accounts.values():
            if account.provider_id not in self.providers:
                raise ValueError(f"account {account.id!r} references unknown provider")

        for plan in self.plans.values():
            if plan.account_id not in self.accounts:
                raise ValueError(f"plan {plan.id!r} references unknown account")

        for quota_pool in self.quota_pools.values():
            if quota_pool.plan_id not in self.plans:
                raise ValueError(f"quota pool {quota_pool.id!r} references unknown plan")

        for model in self.models.values():
            if model.provider_id not in self.providers:
                raise ValueError(f"model {model.id!r} references unknown provider")
            if model.quota_pool_id not in self.quota_pools:
                raise ValueError(f"model {model.id!r} references unknown quota pool")
            quota_provider_id = self.provider_id_for_quota_pool(model.quota_pool_id)
            if quota_provider_id != model.provider_id:
                raise ValueError(
                    f"model {model.id!r} provider does not match its quota pool provider"
                )

        for runtime in self.runtime_variants.values():
            if runtime.model_sku_id not in self.models:
                raise ValueError(f"runtime {runtime.id!r} references unknown model SKU")

        for membership in self.pool_memberships:
            if membership.model_sku_id not in self.models:
                raise ValueError("pool membership references unknown model SKU")
            if membership.runtime_variant_id is not None:
                runtime = self.runtime_variants.get(membership.runtime_variant_id)
                if runtime is None:
                    raise ValueError("pool membership references unknown runtime variant")
                if runtime.model_sku_id != membership.model_sku_id:
                    raise ValueError("pool membership runtime belongs to a different model SKU")

        return self

    @staticmethod
    def _validate_key_identity(items: dict[str, RegistryModel]) -> None:
        for key, item in items.items():
            item_id = getattr(item, "id", None)
            if key != item_id:
                raise ValueError(f"registry key {key!r} does not match object id {item_id!r}")

    def provider_id_for_quota_pool(self, quota_pool_id: str) -> str:
        quota_pool = self.quota_pools[quota_pool_id]
        plan = self.plans[quota_pool.plan_id]
        account = self.accounts[plan.account_id]
        return account.provider_id

    def quota_pool_for_model(self, model_sku_id: str) -> QuotaPool:
        """Return the shared scarcity boundary consumed by a concrete model SKU."""

        model = self.models[model_sku_id]
        return self.quota_pools[model.quota_pool_id]

    def runtimes_for_model(self, model_sku_id: str) -> tuple[RuntimeVariant, ...]:
        return tuple(
            runtime
            for runtime in self.runtime_variants.values()
            if runtime.model_sku_id == model_sku_id
        )

    def candidates_for_pool(self, pool: PoolKind) -> tuple[PoolMembership, ...]:
        return tuple(
            sorted(
                (membership for membership in self.pool_memberships if membership.pool is pool),
                key=lambda membership: (membership.priority, -membership.weight),
            )
        )
