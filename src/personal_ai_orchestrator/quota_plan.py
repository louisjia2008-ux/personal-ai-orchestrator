"""Subscription-plan quota semantics shared by MiniMax and GLM.

Why this layer exists
---------------------
Before P4.2.6.5 the quota domain had exactly one shape: a pool with windows.
That shape cannot express the thing both of our providers actually sell — a
*subscription plan* whose quota is one pool shared by several models, where the
provider separately reports how much each model consumed. Collapsing those two
facts into one number produced the two defects this module removes:

* MiniMax reported per-scope figures that disagreed, and the collector answered
  ``QUOTA_VARIES_BY_MODEL`` and threw away every window, reset time, and
  per-scope figure it had just read.
* GLM's plan-level windows were flattened into a single ``5h`` window, silently
  discarding the weekly window that is usually the binding one.

The hierarchy modelled here is:

    PlanQuota                  the subscription product (MiniMax Token Plan, GLM Coding Plan)
    └── SharedQuotaPool        the resource its models draw from
        ├── QuotaWindowSnapshot   5-hour / weekly / monthly-MCP windows (model_registry type)
        ├── ModelConsumptionObservation   what each model consumed
        └── ModelEquivalentView           a provider's per-model *view* of the shared pool

A projection is additionally scoped to *one workload*
(:mod:`quota_workload_scope`). MiniMax's ``general`` and ``video`` scopes are not
two views of one balance; they meter different resources, and only the scopes
belonging to the workload this build schedules become ``windows``. Everything
else is kept as a classified :class:`ModelEquivalentView` — preserved as
evidence, read by nothing.

Four invariants hold everywhere below:

1. Shared plan remaining quota is **not** model remaining quota. A model never
   gets a ``remaining_fraction`` of its own unless the provider documents an
   independent model-scoped pool (:class:`PlanQuotaSemantics.MODEL_SCOPED`).
2. Consumption is **not** entitlement. ``ModelConsumptionObservation`` says how
   much was spent; it can never be read back as how much is left.
3. Heterogeneous provider metrics keep their own unit. GLM meters its pool in
   plan credits while reporting model usage in tokens; forcing both into
   "tokens" would invent a conversion the provider never published.
4. A scope outside the projected workload is never a limiter. It cannot supply,
   cap, average with, or invalidate the projected figure, and because it never
   reaches ``windows`` it never reaches the snapshot the scheduler reads.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    QuotaSnapshot,
    QuotaState,
    QuotaWindowKind,
    QuotaWindowSnapshot,
    RegistryModel,
)
from personal_ai_orchestrator.quota_burn import BurnPressure
from personal_ai_orchestrator.quota_workload_scope import QuotaWorkloadScope


def _require_aware(value: datetime, field_name: str) -> None:
    """Reject naive datetimes: a quota reset without a zone is not a fact."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class PlanQuotaSemantics(StrEnum):
    """How a provider's plan allocates quota across its models.

    ``SHARED_POOL`` is the only value that authorizes rendering one plan-level
    remaining figure for several models. ``MODEL_SCOPED`` would authorize
    per-model remaining balances — no provider we integrate documents that
    today, and nothing may set it without provider documentation.
    """

    SHARED_POOL = "SHARED_POOL"
    MODEL_SCOPED = "MODEL_SCOPED"
    UNKNOWN = "UNKNOWN"


class QuotaResourceKind(StrEnum):
    """What the pool meters, in the provider's own terms."""

    TOKEN_PLAN_INCLUDED_QUOTA = "TOKEN_PLAN_INCLUDED_QUOTA"
    CODING_PLAN_USAGE_POOL = "CODING_PLAN_USAGE_POOL"
    MCP_TOOL_CALLS = "MCP_TOOL_CALLS"
    UNKNOWN = "UNKNOWN"


class ConsumptionUnitKind(StrEnum):
    """The unit a provider counts in.

    Deliberately *not* normalized to tokens. GLM's pool is denominated in plan
    credits (``CREDIT_LIMIT``) while its model usage is reported in tokens; the
    provider publishes no conversion, so pretending one exists would fabricate
    precision.
    """

    TOKENS = "TOKENS"
    PLAN_CREDITS = "PLAN_CREDITS"
    PLAN_POINTS = "PLAN_POINTS"
    PROMPT_EQUIVALENTS = "PROMPT_EQUIVALENTS"
    PROVIDER_UNITS = "PROVIDER_UNITS"
    UNKNOWN = "UNKNOWN"


class EquivalentScopeKind(StrEnum):
    """What a provider's per-entry quota view is actually scoped to.

    MiniMax returns ``model_remains`` entries named ``general`` and ``video`` on
    this account, while its real routable models are ``MiniMax-M2.7`` and the
    like. The field name says "model"; the values are resource categories. An
    owner told "video shares this quota" would look for a model they cannot
    route to, so the distinction is recorded rather than assumed away.
    """

    MODEL = "MODEL"
    PROVIDER_RESOURCE_SCOPE = "PROVIDER_RESOURCE_SCOPE"
    UNKNOWN = "UNKNOWN"


class MeasurementSource(StrEnum):
    """Where a figure came from, for the owner-facing confidence hierarchy."""

    PROVIDER_QUOTA_API = "PROVIDER_QUOTA_API"
    PROVIDER_USAGE_API = "PROVIDER_USAGE_API"
    PROVIDER_DOCUMENTATION = "PROVIDER_DOCUMENTATION"
    LOCAL_EXECUTION_HISTORY = "LOCAL_EXECUTION_HISTORY"
    UNKNOWN = "UNKNOWN"


class PlanQuota(RegistryModel):
    """Identity and quota semantics of one subscription plan.

    ``display_name`` keeps the provider's real product name. "GLM Coding Plan"
    is not renamed to "GLM Token Plan" for symmetry with MiniMax: the owner
    reconciles this screen against the provider's own console, and a renamed
    product breaks that reconciliation.
    """

    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    quota_semantics: PlanQuotaSemantics = PlanQuotaSemantics.UNKNOWN
    plan_level: str | None = None
    observed_at: datetime

    @model_validator(mode="after")
    def validate_observed_at(self) -> PlanQuota:
        _require_aware(self.observed_at, "observed_at")
        return self


class SharedQuotaPool(RegistryModel):
    """The resource a plan's models draw from.

    ``covered_model_ids`` lists only models for which we hold actual evidence —
    provider response, connected catalog, or account entitlement. It is never
    padded from a marketing page, because an owner reading "M2.7 shares this
    pool" will act on it.
    """

    pool_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    resource_kind: QuotaResourceKind = QuotaResourceKind.UNKNOWN
    shared_across_models: bool = True
    covered_model_ids: tuple[str, ...] = ()
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.UNKNOWN
    provider_unit_label: str | None = None

    @model_validator(mode="after")
    def validate_membership(self) -> SharedQuotaPool:
        if len(set(self.covered_model_ids)) != len(self.covered_model_ids):
            raise ValueError("covered_model_ids contains duplicates")
        return self


class ModelConsumptionObservation(RegistryModel):
    """How much one model consumed of a shared pool over a period.

    This is contribution, never entitlement. There is intentionally no
    ``remaining_*`` field on this type: the shape itself makes "M3 has 95%
    left" unrepresentable, so no future caller can accidentally derive an
    independent per-model balance from consumption data.
    """

    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    pool_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    observed_at: datetime

    consumed_units: float = Field(ge=0.0)
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.UNKNOWN
    provider_unit_label: str | None = None
    call_count: int | None = Field(default=None, ge=0)
    period_start: datetime | None = None
    period_end: datetime | None = None

    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    measurement_source: MeasurementSource = MeasurementSource.UNKNOWN

    @model_validator(mode="after")
    def validate_period(self) -> ModelConsumptionObservation:
        _require_aware(self.observed_at, "observed_at")
        if self.period_start is not None:
            _require_aware(self.period_start, "period_start")
        if self.period_end is not None:
            _require_aware(self.period_end, "period_end")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end <= self.period_start
        ):
            raise ValueError("period_end must be after period_start")
        return self


class ModelEquivalentView(RegistryModel):
    """A provider's per-scope *view* of one shared pool.

    MiniMax reports a remaining percentage per ``model_remains`` entry. Those
    figures differ between entries, which earlier looked like several
    independent balances and caused the whole provider to be reported UNKNOWN.
    They are views of one plan, so they are modelled as a distinct type the UI
    must label as a view, never as that scope's own quota.

    ``scope_id`` is deliberately not called ``model_id``: on the observed
    MiniMax account the entries are named ``general`` and ``video`` — resource
    categories, not routable models — and naming the field after the provider's
    own misleading key would propagate the error into our UI.

    ``workload_scope`` records what kind of work the scope meters. It is what
    lets the coding projection read ``general`` and leave ``video`` alone
    without deleting the video observation.
    """

    scope_id: str = Field(min_length=1)
    scope_kind: EquivalentScopeKind = EquivalentScopeKind.UNKNOWN
    #: Which workload this scope meters. ``video`` is a real MiniMax balance and
    #: a real observation; it is simply not about coding, so it is classified
    #: rather than discarded. See :mod:`quota_workload_scope`.
    workload_scope: QuotaWorkloadScope = QuotaWorkloadScope.UNKNOWN
    window_id: str = Field(min_length=1)
    remaining_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    remaining_units: float | None = Field(default=None, ge=0.0)
    total_units: float | None = Field(default=None, ge=0.0)
    unit_kind: ConsumptionUnitKind = ConsumptionUnitKind.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    measurement_source: MeasurementSource = MeasurementSource.UNKNOWN

    @model_validator(mode="after")
    def validate_view(self) -> ModelEquivalentView:
        if self.confidence is EvidenceConfidence.UNKNOWN and any(
            value is not None
            for value in (self.remaining_fraction, self.remaining_units, self.total_units)
        ):
            raise ValueError("UNKNOWN confidence cannot carry precise equivalent values")
        return self


class BindingWindowReason(StrEnum):
    """Why a window was, or was not, selected as the binding constraint."""

    SCARCEST_COMPARABLE_WINDOW = "SCARCEST_COMPARABLE_WINDOW"
    ONLY_KNOWN_WINDOW = "ONLY_KNOWN_WINDOW"
    NO_COMPARABLE_WINDOWS = "NO_COMPARABLE_WINDOWS"
    NO_KNOWN_REMAINING = "NO_KNOWN_REMAINING"


class BindingWindow(RegistryModel):
    """Which window currently limits the plan, and why.

    Reset horizon is reported *separately* from scarcity rather than folded
    into it. A 20%-remaining 5-hour window that resets in 30 minutes and a
    20%-remaining weekly window that resets in six days are equally scarce and
    not equally urgent; the scheduler needs both facts, so this type refuses to
    collapse them into a single score.
    """

    window_id: str | None = None
    window_kind: QuotaWindowKind | None = None
    remaining_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    reset_at: datetime | None = None
    seconds_until_reset: float | None = None
    reason: BindingWindowReason = BindingWindowReason.NO_KNOWN_REMAINING
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN


def _comparable_windows(
    windows: tuple[QuotaWindowSnapshot, ...],
) -> tuple[QuotaWindowSnapshot, ...]:
    """Windows whose remaining fractions may honestly be compared.

    Only fraction-denominated windows carrying non-UNKNOWN confidence qualify.
    Two windows metered in different provider units are *not* comparable, so a
    raw ``min()`` over mixed units is never taken.
    """

    return tuple(
        window
        for window in windows
        if window.remaining_fraction is not None
        and window.confidence is not EvidenceConfidence.UNKNOWN
    )


def determine_binding_window(
    windows: tuple[QuotaWindowSnapshot, ...],
    *,
    at: datetime,
) -> BindingWindow:
    """Pick the window that currently limits the plan.

    Selection is by scarcest *comparable* remaining fraction. A window we
    cannot read does not silently drop out of the comparison — it is simply not
    comparable, and the returned ``confidence`` reflects that the answer covers
    only what was readable.
    """

    _require_aware(at, "at")
    comparable = _comparable_windows(windows)
    if not comparable:
        reason = (
            BindingWindowReason.NO_COMPARABLE_WINDOWS
            if windows
            else BindingWindowReason.NO_KNOWN_REMAINING
        )
        return BindingWindow(reason=reason)

    binding = min(comparable, key=lambda window: window.remaining_fraction or 0.0)
    seconds = (binding.reset_at - at).total_seconds() if binding.reset_at is not None else None
    reason = (
        BindingWindowReason.ONLY_KNOWN_WINDOW
        if len(comparable) == 1
        else BindingWindowReason.SCARCEST_COMPARABLE_WINDOW
    )
    # The binding answer is only as good as its coverage: if some window could
    # not be read, the scarcest *readable* window may not be the real one.
    confidence = (
        binding.confidence if len(comparable) == len(windows) else EvidenceConfidence.ESTIMATED
    )
    return BindingWindow(
        window_id=binding.window_id,
        window_kind=binding.window_kind,
        remaining_fraction=binding.remaining_fraction,
        reset_at=binding.reset_at,
        seconds_until_reset=seconds,
        reason=reason,
        confidence=confidence,
    )


class PlanQuotaProjection(RegistryModel):
    """Everything truthfully known about one subscription plan's quota.

    A projection may legitimately carry an UNKNOWN shared pool *and* real model
    consumption at the same time. That combination is the whole point: when a
    provider gives us usage but no derivable plan balance, hiding the usage too
    would report less than we know (§24).
    """

    plan: PlanQuota
    pool: SharedQuotaPool
    windows: tuple[QuotaWindowSnapshot, ...] = ()
    model_consumption: tuple[ModelConsumptionObservation, ...] = ()
    model_equivalents: tuple[ModelEquivalentView, ...] = ()
    binding_window: BindingWindow = BindingWindow()
    #: The workload these ``windows`` describe. Every figure in this projection
    #: — windows, binding window, state, and anything derived from them — is
    #: scoped to it, so a scope belonging to another workload can never reach
    #: the scheduler through this type.
    active_workload_scope: QuotaWorkloadScope = QuotaWorkloadScope.UNKNOWN
    #: Sanitized codes for scopes the provider reported that this workload does
    #: not read, e.g. ``VIDEO_SCOPE_IGNORED_FOR_CODING``. Advanced Details only:
    #: they explain an absence, they are not failures.
    workload_scope_notes: tuple[str, ...] = ()
    state: QuotaState = QuotaState.UNKNOWN
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    source: EvidenceSource
    #: Sanitized machine code explaining an UNKNOWN shared pool. Never carries a
    #: credential, URL, or provider response body.
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> PlanQuotaProjection:
        if self.pool.provider_id != self.plan.provider_id:
            raise ValueError("pool and plan must share provider_id")
        if self.pool.plan_id != self.plan.plan_id:
            raise ValueError("pool and plan must share plan_id")
        for observation in self.model_consumption:
            if observation.pool_id != self.pool.pool_id:
                raise ValueError("model consumption must reference this pool")
        window_ids = [window.window_id for window in self.windows]
        if len(window_ids) != len(set(window_ids)):
            raise ValueError("projection contains duplicate window_id values")
        # A MODEL_SCOPED claim authorizes per-model balances, so it may only be
        # asserted where the pool itself says it is not shared.
        if (
            self.plan.quota_semantics is PlanQuotaSemantics.MODEL_SCOPED
            and self.pool.shared_across_models
        ):
            raise ValueError("MODEL_SCOPED semantics contradict a shared pool")
        return self

    @property
    def has_shared_pool_truth(self) -> bool:
        """Whether any window carries a provider-reported remaining figure."""

        return bool(_comparable_windows(self.windows))

    def window(self, window_kind: QuotaWindowKind) -> QuotaWindowSnapshot | None:
        for candidate in self.windows:
            if candidate.window_kind is window_kind:
                return candidate
        return None

    def source_pressure(self, *, now: datetime) -> BurnPressure:
        """Pressure of the plan's weekly window at ``now``; UNMETERED otherwise.

        The plan-level source_pressure is what the dashboard renders
        next to the provider card. It delegates to the same ``assess``
        primitive the recommender uses (commit 3 introduces
        ``source_pressure_for`` over a sequence of ``CandidateWindowInput``;
        both paths bottom out in :func:`quota_burn.assess`), so the card
        and the recommender never disagree about which row of the truth
        table fired.
        """

        weekly = self.window(QuotaWindowKind.WEEKLY)
        if weekly is None:
            return BurnPressure.UNMETERED
        assessment, _inferred = weekly.burn(now=now)
        return assessment.pressure

    def equivalents_in_active_workload(self) -> tuple[ModelEquivalentView, ...]:
        """Provider scope views that belong to the workload this plan projects.

        Unclassified scopes are included: when a provider names its entries
        after models rather than workloads, those entries *are* the coding view.
        """

        return tuple(
            view
            for view in self.model_equivalents
            if view.workload_scope in {self.active_workload_scope, QuotaWorkloadScope.UNKNOWN}
        )

    def equivalents_outside_active_workload(self) -> tuple[ModelEquivalentView, ...]:
        """Real provider observations this workload deliberately does not read."""

        return tuple(
            view
            for view in self.model_equivalents
            if view.workload_scope not in {self.active_workload_scope, QuotaWorkloadScope.UNKNOWN}
        )

    def covered_model_ids(self) -> tuple[str, ...]:
        """Models with evidence of drawing on this pool.

        The pool's declared membership is unioned with models the provider
        actually reported consumption or an equivalent view for, so a model the
        owner is demonstrably spending through is never omitted.
        """

        seen = list(self.pool.covered_model_ids)
        # Equivalent views only contribute a model when the provider's entry
        # was actually identified as one. An unrecognized scope is not silently
        # promoted into the list of models the owner can route to.
        for extra in (
            *(item.model_id for item in self.model_consumption),
            *(
                item.scope_id
                for item in self.model_equivalents
                if item.scope_kind is EquivalentScopeKind.MODEL
            ),
        ):
            if extra not in seen:
                seen.append(extra)
        return tuple(seen)

    def to_snapshot(self, *, quota_pool_id: str | None = None) -> QuotaSnapshot:
        """Project onto the canonical snapshot the scheduler and cache consume.

        The scheduler's authoritative inputs stay exactly what they were —
        provider-reported windows, now those of ``active_workload_scope`` only.
        Model consumption, per-scope equivalents, and equivalent capacity travel
        beside the snapshot rather than inside it, so neither advisory numbers
        nor another workload's balance can become routing truth by accident.
        """

        return QuotaSnapshot(
            quota_pool_id=quota_pool_id or self.pool.pool_id,
            provider_id=self.plan.provider_id,
            plan_id=self.plan.plan_id,
            observed_at=self.plan.observed_at,
            windows=self.windows,
            state=self.state,
            confidence=self.confidence,
            source=self.source,
        )


def state_for_fraction(value: float | None) -> QuotaState:
    if value is None:
        return QuotaState.UNKNOWN
    if value <= 0.0:
        return QuotaState.EXHAUSTED
    return QuotaState.AVAILABLE


def aggregate_state(windows: tuple[QuotaWindowSnapshot, ...]) -> QuotaState:
    """Plan state from its readable windows, fail-open only when nothing is known."""

    fractions = [
        window.remaining_fraction
        for window in windows
        if window.remaining_fraction is not None
        and window.confidence is not EvidenceConfidence.UNKNOWN
    ]
    if not fractions:
        return QuotaState.UNKNOWN
    return state_for_fraction(min(fractions))


__all__ = [
    "BindingWindow",
    "BindingWindowReason",
    "ConsumptionUnitKind",
    "EquivalentScopeKind",
    "MeasurementSource",
    "ModelConsumptionObservation",
    "ModelEquivalentView",
    "PlanQuota",
    "PlanQuotaProjection",
    "PlanQuotaSemantics",
    "QuotaResourceKind",
    "QuotaWorkloadScope",
    "SharedQuotaPool",
    "aggregate_state",
    "determine_binding_window",
    "state_for_fraction",
]
