"""Typed subscription-resource domain for quota-aware scheduling.

The registry keeps provider/account/plan identity separate from model identity and models
quota membership/burn rules as append-only temporal facts. This prevents current provider
policy from silently rewriting historical routing explanations.

P3 extends the existing quota snapshot types in-place so collectors, the registry, cache,
explanations, and the scheduler share one normalized quota truth contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from uuid import uuid4

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
    """Observed truth for one simultaneous quota/reset window."""

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

    def is_active(self, *, at: datetime) -> bool:
        _require_aware(at, "at")
        if self.window_started_at is not None and at < self.window_started_at:
            return False
        return self.reset_at is None or at < self.reset_at

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
    """Immutable observation with a stable identity suitable for routing replay."""

    id: str = Field(default_factory=lambda: f"quota-{uuid4()}", min_length=1)
    schema_version: int = Field(default=1, ge=1)
    quota_pool_id: str | None = None
    provider_id: str | None = None
    account_id: str | None = None
    plan_id: str | None = None
    observed_at: datetime | None = None
    recorded_at: datetime | None = None
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
        if self.recorded_at is not None:
            _require_aware(self.recorded_at, "recorded_at")
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

    def record_time(self) -> datetime:
        return self.recorded_at or self.observation_time()

    def active_windows(
        self,
        *,
        at: datetime | None = None,
        required_window_kinds: tuple[QuotaWindowKind, ...] = (),
    ) -> tuple[QuotaWindowSnapshot, ...]:
        reference = at or self.observation_time()
        required = set(required_window_kinds)
        return tuple(
            window
            for window in self.windows
            if window.is_active(at=reference)
            and (not required or window.window_kind in required)
        )

    def known_min_pace(
        self,
        *,
        at: datetime | None = None,
        required_window_kinds: tuple[QuotaWindowKind, ...] = (),
    ) -> float | None:
        """Diagnostic lower-bound pace using only windows whose pace is known."""

        reference = at or self.observation_time()
        paces = [
            pace
            for window in self.active_windows(
                at=reference,
                required_window_kinds=required_window_kinds,
            )
            if (pace := window.pace(at=reference)) is not None
        ]
        return min(paces) if paces else None

    def effective_pace(
        self,
        *,
        at: datetime | None = None,
        required_window_kinds: tuple[QuotaWindowKind, ...] = (),
    ) -> float | None:
        """Return fail-closed routing pace across every active binding window.

        A known 5-hour window must not make routing look healthy when a simultaneous
        weekly window is unknown. If any required/active window cannot produce a reliable
        pace, the routing pace is UNKNOWN (``None``).
        """

        reference = at or self.observation_time()
        windows = self.active_windows(
            at=reference,
            required_window_kinds=required_window_kinds,
        )
        if not windows:
            return None
        paces = [window.pace(at=reference) for window in windows]
        if any(pace is None for pace in paces):
            return None
        return min(pace for pace in paces if pace is not None)

    def minimum_remaining_fraction(
        self,
        *,
        at: datetime | None = None,
        required_window_kinds: tuple[QuotaWindowKind, ...] = (),
    ) -> float | None:
        """Return conservative quota headroom across active windows.

        Missing precise remaining quota in any binding window makes admission UNKNOWN.
        """

        windows = self.active_windows(
            at=at,
            required_window_kinds=required_window_kinds,
        )
        if not windows:
            return None
        fractions = [
            window.remaining_fraction
            if window.confidence is not EvidenceConfidence.UNKNOWN
            else None
            for window in windows
        ]
        if any(value is None for value in fractions):
            return None
        return min(value for value in fractions if value is not None)

    def is_stale(self, *, as_of: datetime, max_age_seconds: float) -> bool:
        _require_aware(as_of, "as_of")
        age = (as_of - self.observation_time()).total_seconds()
        return age < 0 or age > max_age_seconds


class QuotaPool(RegistryModel):
    id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    snapshot: QuotaSnapshot
    reserve_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    required_window_kinds: tuple[QuotaWindowKind, ...] = ()


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


class ExecutionTarget(RegistryModel):
    """Concrete way to execute a logical ModelSKU through one account/runtime path."""

    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    runtime_provider_id: str | None = None
    variant: str | None = None
    enabled: bool = True
    execution_verified: bool = False


class QuotaBinding(RegistryModel):
    """Append-only fact linking a model/execution target to the pool it consumes."""

    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    execution_target_id: str | None = None
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
    """Append-only burn rule for one model/target/quota-pool relationship."""

    id: str = Field(min_length=1)
    model_sku_id: str = Field(min_length=1)
    quota_pool_id: str = Field(min_length=1)
    execution_target_id: str | None = None
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
    execution_target_id: str | None = None
    priority: int = Field(default=1, ge=1)
    weight: float = Field(default=1.0, gt=0.0)


class ModelRegistry(RegistryModel):
    providers: dict[str, Provider] = Field(default_factory=dict)
    accounts: dict[str, Account] = Field(default_factory=dict)
    plans: dict[str, Plan] = Field(default_factory=dict)
    quota_pools: dict[str, QuotaPool] = Field(default_factory=dict)
    catalog_snapshots: dict[str, ModelCatalogSnapshot] = Field(default_factory=dict)
    models: dict[str, ModelSKU] = Field(default_factory=dict)
    execution_targets: dict[str, ExecutionTarget] = Field(default_factory=dict)
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
        self._validate_key_identity(self.execution_targets)
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
            if quota_pool.snapshot.quota_pool_id not in (None, quota_pool.id):
                raise ValueError(f"quota pool {quota_pool.id!r} snapshot references another pool")

        for model in self.models.values():
            if model.provider_id not in self.providers:
                raise ValueError(f"model {model.id!r} references unknown provider")
            if (
                model.catalog_snapshot_id is not None
                and model.catalog_snapshot_id not in self.catalog_snapshots
            ):
                raise ValueError(f"model {model.id!r} references unknown catalog snapshot")

        for target in self.execution_targets.values():
            model = self.models.get(target.model_sku_id)
            account = self.accounts.get(target.account_id)
            if model is None:
                raise ValueError(f"execution target {target.id!r} references unknown model")
            if account is None:
                raise ValueError(f"execution target {target.id!r} references unknown account")
            if model.provider_id != account.provider_id:
                raise ValueError("execution target account provider does not match model provider")

        bindings_by_id = {binding.id: binding for binding in self.quota_bindings}
        for binding in self.quota_bindings:
            self._validate_model_pool_lineage(
                binding.model_sku_id,
                binding.quota_pool_id,
                "quota binding",
                execution_target_id=binding.execution_target_id,
            )
            if binding.supersedes_binding_id is not None:
                previous = bindings_by_id.get(binding.supersedes_binding_id)
                if previous is None:
                    raise ValueError("quota binding supersedes unknown binding")
                if previous.model_sku_id != binding.model_sku_id:
                    raise ValueError("quota binding cannot supersede a different model")
                if previous.execution_target_id != binding.execution_target_id:
                    raise ValueError("quota binding cannot supersede a different execution target")
                if binding.recorded_at < previous.recorded_at:
                    raise ValueError("quota binding cannot be recorded before superseded fact")

        rules_by_id = {rule.id: rule for rule in self.consumption_rules}
        for rule in self.consumption_rules:
            self._validate_model_pool_lineage(
                rule.model_sku_id,
                rule.quota_pool_id,
                "consumption rule",
                execution_target_id=rule.execution_target_id,
            )
            if rule.supersedes_rule_id is not None:
                previous = rules_by_id.get(rule.supersedes_rule_id)
                if previous is None:
                    raise ValueError("consumption rule supersedes unknown rule")
                if (
                    previous.model_sku_id != rule.model_sku_id
                    or previous.quota_pool_id != rule.quota_pool_id
                    or previous.execution_target_id != rule.execution_target_id
                ):
                    raise ValueError("consumption rule cannot supersede a different resource")
                if rule.recorded_at < previous.recorded_at:
                    raise ValueError("consumption rule cannot predate superseded fact")

        for membership in self.pool_memberships:
            if membership.model_sku_id not in self.models:
                raise ValueError("pool membership references unknown model SKU")
            if membership.execution_target_id is not None:
                target = self.execution_targets.get(membership.execution_target_id)
                if target is None:
                    raise ValueError("pool membership references unknown execution target")
                if target.model_sku_id != membership.model_sku_id:
                    raise ValueError("pool membership target does not match model SKU")

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

    def _account_id_for_quota_pool(self, quota_pool_id: str) -> str:
        quota_pool = self.quota_pools[quota_pool_id]
        plan = self.plans[quota_pool.plan_id]
        return plan.account_id

    def _validate_model_pool_lineage(
        self,
        model_sku_id: str,
        quota_pool_id: str,
        label: str,
        *,
        execution_target_id: str | None = None,
    ) -> None:
        model = self.models.get(model_sku_id)
        if model is None:
            raise ValueError(f"{label} references unknown model SKU")
        if quota_pool_id not in self.quota_pools:
            raise ValueError(f"{label} references unknown quota pool")
        if self._provider_id_for_quota_pool(quota_pool_id) != model.provider_id:
            raise ValueError(f"{label} provider does not match quota pool provider")
        if execution_target_id is not None:
            target = self.execution_targets.get(execution_target_id)
            if target is None:
                raise ValueError(f"{label} references unknown execution target")
            if target.model_sku_id != model_sku_id:
                raise ValueError(f"{label} execution target does not match model SKU")
            if target.account_id != self._account_id_for_quota_pool(quota_pool_id):
                raise ValueError(f"{label} execution target account does not match quota pool account")

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

    @staticmethod
    def _resolve_unsuperseded(
        candidates: list[RegistryModel],
        *,
        supersedes_field: str,
        label: str,
    ) -> RegistryModel:
        if not candidates:
            raise LookupError(f"no active {label}")
        candidate_ids = {getattr(item, "id") for item in candidates}
        superseded = {
            superseded_id
            for item in candidates
            if (superseded_id := getattr(item, supersedes_field)) in candidate_ids
        }
        survivors = [item for item in candidates if getattr(item, "id") not in superseded]
        if len(survivors) != 1:
            ids = sorted(getattr(item, "id") for item in survivors)
            raise LookupError(f"ambiguous active {label}: {ids}")
        return survivors[0]

    def _binding_candidates_for_target(
        self,
        model_sku_id: str,
        execution_target_id: str | None,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> list[QuotaBinding]:
        applicable = [
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
        if execution_target_id is not None:
            specific = [
                binding for binding in applicable if binding.execution_target_id == execution_target_id
            ]
            if specific:
                return specific
            return [binding for binding in applicable if binding.execution_target_id is None]
        generic = [binding for binding in applicable if binding.execution_target_id is None]
        return generic if generic else applicable

    def active_quota_binding(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
        execution_target_id: str | None = None,
    ) -> QuotaBinding:
        """Resolve one binding using applicable supersession semantics.

        Overlapping unsuperseded facts are an ambiguity and fail closed instead of being
        silently resolved by lexical ID ordering.
        """

        candidates = self._binding_candidates_for_target(
            model_sku_id,
            execution_target_id,
            effective_at=effective_at,
            known_at=known_at,
        )
        try:
            result = self._resolve_unsuperseded(
                list(candidates),
                supersedes_field="supersedes_binding_id",
                label=f"quota binding for model {model_sku_id!r}",
            )
        except LookupError as exc:
            if not candidates:
                raise LookupError(f"no quota binding for model {model_sku_id!r}") from exc
            raise
        assert isinstance(result, QuotaBinding)
        return result

    def quota_pool_for_model(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
        execution_target_id: str | None = None,
    ) -> QuotaPool:
        binding = self.active_quota_binding(
            model_sku_id,
            effective_at=effective_at,
            known_at=known_at,
            execution_target_id=execution_target_id,
        )
        return self.quota_pools[binding.quota_pool_id]

    def active_consumption_rule(
        self,
        model_sku_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
        execution_target_id: str | None = None,
    ) -> ConsumptionRule | None:
        binding = self.active_quota_binding(
            model_sku_id,
            effective_at=effective_at,
            known_at=known_at,
            execution_target_id=execution_target_id,
        )
        applicable = [
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
        if execution_target_id is not None:
            specific = [
                rule for rule in applicable if rule.execution_target_id == execution_target_id
            ]
            candidates = specific or [rule for rule in applicable if rule.execution_target_id is None]
        else:
            generic = [rule for rule in applicable if rule.execution_target_id is None]
            candidates = generic or applicable
        if not candidates:
            return None
        result = self._resolve_unsuperseded(
            list(candidates),
            supersedes_field="supersedes_rule_id",
            label=f"consumption rule for model {model_sku_id!r}",
        )
        assert isinstance(result, ConsumptionRule)
        return result

    def execution_targets_for_model(self, model_sku_id: str) -> tuple[ExecutionTarget, ...]:
        return tuple(
            sorted(
                (
                    target
                    for target in self.execution_targets.values()
                    if target.model_sku_id == model_sku_id and target.enabled
                ),
                key=lambda target: target.id,
            )
        )

    def effective_pace_for_pool(self, quota_pool_id: str) -> float | None:
        pool = self.quota_pools[quota_pool_id]
        return pool.snapshot.effective_pace(required_window_kinds=pool.required_window_kinds)

    def candidates_for_pool(self, pool: PoolKind) -> tuple[PoolMembership, ...]:
        return tuple(
            sorted(
                (membership for membership in self.pool_memberships if membership.pool is pool),
                key=lambda membership: (membership.priority, -membership.weight),
            )
        )
