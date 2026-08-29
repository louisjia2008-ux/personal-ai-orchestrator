"""Typed subscription-resource domain for quota-aware scheduling.

The registry keeps provider/account/plan identity separate from model identity and models
quota membership/burn rules as append-only temporal facts. This prevents current provider
policy from silently rewriting historical routing explanations.

P3 extends the existing quota snapshot types in-place so collectors, the registry, and the
future scheduler share one normalized quota truth contract rather than parallel snapshot
models.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuotaState(StrEnum):
    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    CRITICAL = "CRITICAL"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class EvidenceConfidence(StrEnum):
    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class EvidenceSourceType(StrEnum):
    PROVIDER_API = "PROVIDER_API"
    PROVIDER_DOCUMENTATION = "PROVIDER_DOCUMENTATION"
    PROVIDER_ANNOUNCEMENT = "PROVIDER_ANNOUNCEMENT"
    LOCAL_OBSERVATION = "LOCAL_OBSERVATION"
    INFERRED = "INFERRED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    COMMUNITY_REPORT = "COMMUNITY_REPORT"


class PlanKind(StrEnum):
    SUBSCRIPTION = "SUBSCRIPTION"
    PAY_AS_YOU_GO = "PAY_AS_YOU_GO"
    PREPAID = "PREPAID"
    UNKNOWN = "UNKNOWN"


class PoolKind(StrEnum):
    WORKER = "WORKER"
    REASONING = "REASONING"
    REVIEW = "REVIEW"
    ESCALATION = "ESCALATION"
    FALLBACK = "FALLBACK"


class QuotaWindowKind(StrEnum):
    FIVE_HOUR = "FIVE_HOUR"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    DAILY = "DAILY"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_temporal_range(
    effective_from: datetime,
    effective_until: datetime | None,
    recorded_at: datetime,
) -> None:
    _require_aware(effective_from, "effective_from")
    _require_aware(recorded_at, "recorded_at")
    if effective_until is not None:
        _require_aware(effective_until, "effective_until")
        if effective_until <= effective_from:
            raise ValueError("effective_until must be after effective_from")


class EvidenceSource(RegistryModel):
    source_type: EvidenceSourceType
    observed_at: datetime
    reference: str | None = None
    note: str | None = None
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN

    @model_validator(mode="after")
    def validate_observed_at(self) -> EvidenceSource:
        _require_aware(self.observed_at, "observed_at")
        return self


class Provider(RegistryModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class Account(RegistryModel):
    """Lightweight authentication identity boundary for the MVP."""

    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    credential_ref: str | None = None


class Plan(RegistryModel):
    """Lightweight commercial-entitlement identity for the MVP."""

    id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: PlanKind = PlanKind.UNKNOWN


class ModelCatalogSnapshot(RegistryModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    as_of: datetime
    fetched_at: datetime
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_times(self) -> ModelCatalogSnapshot:
        _require_aware(self.as_of, "as_of")
        _require_aware(self.fetched_at, "fetched_at")
        return self


class QuotaWindowSnapshot(RegistryModel):
    """Observed truth for one simultaneous quota/reset window.

    Reset semantics are optional because some official provider surfaces expose remaining
    quota without a trustworthy reset timestamp. Missing data remains ``None``; it is never
    synthesized solely to make pace calculable.
    """

    window_id: str = Field(min_length=1)
    window_kind: QuotaWindowKind = QuotaWindowKind.UNKNOWN
    duration_seconds: float | None = Field(default=None, gt=0.0)
    remaining_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    used_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    remaining_units: float | None = Field(default=None, ge=0.0)
    used_units: float | None = Field(default=None, ge=0.0)
    unit: str | None = None
    window_started_at: datetime | None = None
    reset_at: datetime | None = None
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence
    source: EvidenceSource

    @model_validator(mode="after")
    def validate_window(self) -> QuotaWindowSnapshot:
        if self.window_started_at is not None:
            _require_aware(self.window_started_at, "window_started_at")
        if self.reset_at is not None:
            _require_aware(self.reset_at, "reset_at")
        if (
            self.window_started_at is not None
            and self.reset_at is not None
            and self.reset_at <= self.window_started_at
        ):
            raise ValueError("reset_at must be after window_started_at")

        precise_values = (
            self.remaining_fraction,
            self.used_fraction,
            self.remaining_units,
            self.used_units,
        )
        if self.confidence is EvidenceConfidence.UNKNOWN and any(
            value is not None for value in precise_values
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise quota values")

        if self.remaining_fraction is not None and self.used_fraction is not None:
            if abs((self.remaining_fraction + self.used_fraction) - 1.0) > 1e-6:
                raise ValueError("remaining_fraction + used_fraction must equal 1")
        return self

    def remaining_time_fraction(self, *, at: datetime | None = None) -> float | None:
        """Return the fraction of the reset window remaining when it is observable."""

        reference = at or self.source.observed_at
        _require_aware(reference, "at")
        if self.reset_at is None or reference >= self.reset_at:
            return None
        if self.window_started_at is not None and reference < self.window_started_at:
            return None

        duration = self.duration_seconds
        if duration is None and self.window_started_at is not None:
            duration = (self.reset_at - self.window_started_at).total_seconds()
        if duration is None or duration <= 0:
            return None

        remaining = (self.reset_at - reference).total_seconds()
        fraction = remaining / duration
        if not isfinite(fraction) or fraction <= 0:
            return None
        return min(1.0, fraction)

    def pace(self, *, at: datetime | None = None) -> float | None:
        """Return remaining quota fraction / remaining time fraction."""

        if self.remaining_fraction is None or self.confidence is EvidenceConfidence.UNKNOWN:
            return None
        remaining_time_fraction = self.remaining_time_fraction(at=at)
        if remaining_time_fraction is None or remaining_time_fraction <= 0:
            return None
        value = self.remaining_fraction / remaining_time_fraction
        return value if isfinite(value) else None


class QuotaSnapshot(RegistryModel):
    schema_version: int = Field(default=1, ge=1)
    quota_pool_id: str | None = None
    provider_id: str | None = None
    account_id: str | None = None
    plan_id: str | None = None
    observed_at: datetime | None = None
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: EvidenceSource
    remaining_units: float | None = Field(default=None, ge=0.0)
    used_units: float | None = Field(default=None, ge=0.0)
    unit: str | None = None
    windows: tuple[QuotaWindowSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> QuotaSnapshot:
        if self.observed_at is not None:
            _require_aware(self.observed_at, "observed_at")
        if self.confidence is EvidenceConfidence.UNKNOWN and (
            self.remaining_units is not None or self.used_units is not None
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise quota units")
        ids = [window.window_id for window in self.windows]
        if len(ids) != len(set(ids)):
            raise ValueError("quota snapshot contains duplicate window_id values")
        return self

    def observation_time(self) -> datetime:
        return self.observed_at or self.source.observed_at

    def effective_pace(self, *, at: datetime | None = None) -> float | None:
        """Return the most constrained observable active-window pace."""

        reference = at or self.observation_time()
        paces = [
            pace
            for window in self.windows
            if (pace := window.pace(at=reference)) is not None
        ]
        return min(paces) if paces else None

    def is_stale(self, *, as_of: datetime, max_age_seconds: float) -> bool:
        _require_aware(as_of, "as_of")
        return (as_of - self.observation_time()).total_seconds() > max_age_seconds


class QuotaPool(RegistryModel):
    id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    snapshot: QuotaSnapshot
    reserve_fraction: float = Field(default=0.0, ge=0.0, le=1.0)


class CapabilityProfile(RegistryModel):
    scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_score_range(self) -> CapabilityProfile:
        invalid = {name: value for name, value in self.scores.items() if not 0.0 <= value <= 1.0}
        if invalid:
            raise ValueError(f"capability scores must be within [0, 1]: {invalid}")
        return self


class ModelSKU(RegistryModel):
    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    catalog_snapshot_id: str | None = None
    capabilities: CapabilityProfile = Field(default_factory=CapabilityProfile)
    enabled: bool = True


class QuotaBinding(RegistryModel):
    """Append-only fact linking a model to the pool it consumes."""

    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None
    recorded_at: datetime
    supersedes_binding_id: str | None = None
    confidence: EvidenceConfidence
    source: EvidenceSource

    @model_validator(mode="after")
    def validate_times(self) -> QuotaBinding:
        _validate_temporal_range(self.effective_from, self.effective_until, self.recorded_at)
        return self


class ConsumptionRule(RegistryModel):
    """Append-only burn rule for one model/quota-pool relationship."""

    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    multiplier: float = Field(default=1.0, gt=0.0)
    native_unit: str = Field(default="provider_unit", min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None
    recorded_at: datetime
    supersedes_rule_id: str | None = None
    confidence: EvidenceConfidence
    source: EvidenceSource

    @model_validator(mode="after")
    def validate_times(self) -> ConsumptionRule:
        _validate_temporal_range(self.effective_from, self.effective_until, self.recorded_at)
        return self


class PoolMembership(RegistryModel):
    pool: PoolKind
    model_sku_id: str = Field(min_length=1)
    priority: int = Field(default=1, ge=1)
    weight: float = Field(default=1.0, gt=0.0)


class ModelRegistry(RegistryModel):
    providers: dict[str, Provider] = Field(default_factory=dict)
    accounts: dict[str, Account] = Field(default_factory=dict)
    plans: dict[str, Plan] = Field(default_factory=dict)
    quota_pools: dict[str, QuotaPool] = Field(default_factory=dict)
    catalog_snapshots: dict[str, ModelCatalogSnapshot] = Field(default_factory=dict)
    models: dict[str, ModelSKU] = Field(default_factory=dict)
    quota_bindings: tuple[QuotaBinding, ...] = ()
    consumption_rules: tuple[ConsumptionRule, ...] = ()
    pool_memberships: tuple[PoolMembership, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> ModelRegistry:
        self._validate_key_identity(self.providers)
        self._validate_key_identity(self.accounts)
        self._validate_key_identity(self.plans)
        self._validate_key_identity(self.quota_pools)
        self._validate_key_identity(self.catalog_snapshots)
        self._validate_key_identity(self.models)
        self._validate_unique_fact_ids(self.quota_bindings, "quota binding")
        self._validate_unique_fact_ids(self.consumption_rules, "consumption rule")

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
            if (
                model.catalog_snapshot_id is not None
                and model.catalog_snapshot_id not in self.catalog_snapshots
            ):
                raise ValueError(f"model {model.id!r} references unknown catalog snapshot")

        bindings_by_id = {binding.id: binding for binding in self.quota_bindings}
        for binding in self.quota_bindings:
            self._validate_model_pool_lineage(
                binding.model_sku_id,
                binding.quota_pool_id,
                "quota binding",
            )
            if binding.supersedes_binding_id is not None:
                previous = bindings_by_id.get(binding.supersedes_binding_id)
                if previous is None:
                    raise ValueError("quota binding supersedes unknown binding")
                if previous.model_sku_id != binding.model_sku_id:
                    raise ValueError("quota binding cannot supersede a different model")
                if binding.recorded_at < previous.recorded_at:
                    raise ValueError("quota binding cannot be recorded before superseded fact")

        rules_by_id = {rule.id: rule for rule in self.consumption_rules}
        for rule in self.consumption_rules:
            self._validate_model_pool_lineage(
                rule.model_sku_id,
                rule.quota_pool_id,
                "consumption rule",
            )
            if rule.supersedes_rule_id is not None:
                previous = rules_by_id.get(rule.supersedes_rule_id)
                if previous is None:
                    raise ValueError("consumption rule supersedes unknown rule")
                if (
                    previous.model_sku_id != rule.model_sku_id
                    or previous.quota_pool_id != rule.quota_pool_id
                ):
                    raise ValueError("consumption rule cannot supersede a different resource")
                if rule.recorded_at < previous.recorded_at:
                    raise ValueError("consumption rule cannot predate superseded fact")

        for membership in self.pool_memberships:
            if membership.model_sku_id not in self.models:
                raise ValueError("pool membership references unknown model SKU")

        return self

    @staticmethod
    def _validate_key_identity(items: dict[str, RegistryModel]) -> None:
        for key, item in items.items():
            item_id = getattr(item, "id", None)
            if key != item_id:
                raise ValueError(f"registry key {key!r} does not match object id {item_id!r}")

    @staticmethod
    def _validate_unique_fact_ids(items: tuple[RegistryModel, ...], label: str) -> None:
        ids = [getattr(item, "id", None) for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {label} id")

    def _provider_id_for_quota_pool(self, quota_pool_id: str) -> str:
        quota_pool = self.quota_pools[quota_pool_id]
        plan = self.plans[quota_pool.plan_id]
        account = self.accounts[plan.account_id]
        return account.provider_id

    def _validate_model_pool_lineage(
        self,
        model_sku_id: str,
        quota_pool_id: str,
        label: str,
    ) -> None:
        model = self.models.get(model_sku_id)
        if model is None:
            raise ValueError(f"{label} references unknown model SKU")
        if quota_pool_id not in self.quota_pools:
            raise ValueError(f"{label} references unknown quota pool")
        if self._provider_id_for_quota_pool(quota_pool_id) != model.provider_id:
            raise ValueError(f"{label} provider does not match quota pool provider")

    @staticmethod
    def _fact_applies(
        effective_from: datetime,
        effective_until: datetime | None,
        recorded_at: datetime,
        effective_at: datetime,
        known_at: datetime,
    ) -> bool:
        return (
            recorded_at <= known_at
            and effective_from <= effective_at
            and (effective_until is None or effective_at < effective_until)
        )

    def active_quota_binding(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> QuotaBinding:
        """Resolve the binding using only facts known at the requested knowledge time."""

        candidates = [
            binding
            for binding in self.quota_bindings
            if binding.model_sku_id == model_sku_id
            and self._fact_applies(
                binding.effective_from,
                binding.effective_until,
                binding.recorded_at,
                effective_at,
                known_at,
            )
        ]
        if not candidates:
            raise LookupError(f"no quota binding for model {model_sku_id!r}")
        return max(candidates, key=lambda item: (item.effective_from, item.recorded_at, item.id))

    def quota_pool_for_model(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> QuotaPool:
        binding = self.active_quota_binding(
            model_sku_id,
            effective_at=effective_at,
            known_at=known_at,
        )
        return self.quota_pools[binding.quota_pool_id]

    def active_consumption_rule(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> ConsumptionRule | None:
        binding = self.active_quota_binding(
            model_sku_id,
            effective_at=effective_at,
            known_at=known_at,
        )
        candidates = [
            rule
            for rule in self.consumption_rules
            if rule.model_sku_id == model_sku_id
            and rule.quota_pool_id == binding.quota_pool_id
            and self._fact_applies(
                rule.effective_from,
                rule.effective_until,
                rule.recorded_at,
                effective_at,
                known_at,
            )
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.effective_from, item.recorded_at, item.id))

    def effective_pace_for_pool(self, quota_pool_id: str) -> float | None:
        return self.quota_pools[quota_pool_id].snapshot.effective_pace()

    def candidates_for_pool(self, pool: PoolKind) -> tuple[PoolMembership, ...]:
        return tuple(
            sorted(
                (membership for membership in self.pool_memberships if membership.pool is pool),
                key=lambda membership: (membership.priority, -membership.weight),
            )
        )
