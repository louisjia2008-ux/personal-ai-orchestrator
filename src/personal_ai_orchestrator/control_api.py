"""P4.0 typed local control-plane API over a Unix Domain Socket.

The control plane is a read-mostly, host-owned surface. Clients may submit tasks, inspect
task/run/verification/routing truth, request bounded cancellation of non-running tasks, and read
sanitized provider/quota health plus Production ACTIVE status. Clients can never mark results
VERIFIED, forge evidence, mutate Safety Kernel state directly, resolve approvals, or enable
Production ACTIVE.

The daemon remains authoritative: every write goes through the same Safety Kernel transactions
the execution path uses, and every response is rendered from explicitly whitelisted models so
credential material (for example account ``credential_ref`` values) can never transit this API.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.approval import ApprovalAuthority
from personal_ai_orchestrator.build_identity import resolve_build_identity
from personal_ai_orchestrator.daemon_supervisor import (
    DaemonSupervisor,
    DaemonSupervisorSnapshot,
)
from personal_ai_orchestrator.dispatch_initiator import (
    initiate_owner_dispatch,
)
from personal_ai_orchestrator.dispatch_recommendation_service import (
    DispatchRecommendationService,
)
from personal_ai_orchestrator.dispatch_recommender import (
    DispatchCandidateInput,
    source_pressure_for,
)
from personal_ai_orchestrator.execution_controller import (
    project_execution_target_verification,
)
from personal_ai_orchestrator.execution_evidence import (
    ExecutionEvidenceJournal,
    ExecutionVerificationEvidence,
    ExecutionVerificationOutcome,
)
from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.provider_registry_manager import (
    ProviderRegistryManager,
)
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
    QuotaAvailabilityState,
)
from personal_ai_orchestrator.quota_equivalent_capacity import (
    EquivalentCapacityEstimate,
    estimate_equivalent_capacity,
)
from personal_ai_orchestrator.quota_plan import PlanQuotaProjection
from personal_ai_orchestrator.quota_refresh import (
    QuotaObservationState,
    QuotaPageState,
    QuotaProviderObservation,
    QuotaRefreshService,
    has_readonly_quota_source,
)
from personal_ai_orchestrator.safety_kernel import (
    OwnerDispatchRecord,
    ProjectAvailability,
    ProjectRecord,
    SafetyKernelStore,
    TaskRecord,
    TaskState,
)
from personal_ai_orchestrator.scheduler import RoutingObjective
from personal_ai_orchestrator.scheduling_settings import (
    SELECTABLE_GLOBAL_POLICIES,
    SELECTABLE_MODES,
    SchedulingSettings,
)
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult

CONTROL_API_VERSION = "v1"
MAX_REQUEST_BYTES = 64 * 1024
MAX_DRAIN_BYTES = 1024 * 1024
MAX_LIST_LIMIT = 200
DEFAULT_LIST_LIMIT = 50
MAX_INTENT_LENGTH = 8192
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def datetime_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _opencode_binary_available() -> bool:
    """Whether a real opencode worker binary can be launched on this host.

    ``shutil.which`` alone only sees the daemon's own ``PATH``. A daemon
    started by a GUI app inherits the launch-time environment, which on
    macOS is routinely ``/usr/bin:/bin:/usr/sbin:/sbin`` — the canonical
    ``~/.opencode/bin/opencode`` install location would be invisible even
    though discovery itself resolves exactly that path. Mirror discovery's
    resolution so runtime availability never contradicts the catalog that
    was discovered through the same binary.
    """

    if shutil.which("opencode") is not None:
        return True
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    try:
        return fallback.is_file() and os.access(fallback, os.X_OK)
    except OSError:
        return False


class ControlPlaneError(Exception):
    """Sanitized control-plane failure mapped to an HTTP status and error code."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class DispatchAuthority(StrEnum):
    OWNER_INITIATED_EXECUTION = "OWNER_INITIATED_EXECUTION"
    #: M1 WP5a-2: the host-owned supervised-auto dispatch path. Distinct
    #: from OWNER_INITIATED_EXECUTION — nobody clicked an owner button.
    SUPERVISED_AUTO = "SUPERVISED_AUTO"


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: A task may additionally pick MANUAL, which a *global* default cannot: only a
#: single task knows a concrete execution target that is valid for itself.
SELECTABLE_TASK_POLICIES = (*SELECTABLE_GLOBAL_POLICIES, "MANUAL")

# Opaque per-process identity for exact macOS lifecycle ownership. This carries
# no credential or host path and intentionally changes on every daemon start.
_PROCESS_INSTANCE_ID = uuid.uuid4().hex


class TaskSubmitRequest(_ViewModel):
    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    intent: str = Field(min_length=1, max_length=MAX_INTENT_LENGTH)
    scheduling_policy: str | None = Field(default=None, min_length=1, max_length=64)
    manual_execution_target_id: str | None = Field(default=None, min_length=1, max_length=128)
    # M1 WP2: tier floor for the dispatch target. ``None`` is normalised
    # to ``"T1"`` (workhorse) at storage time. Strings outside the four
    # known tier values are rejected with a 400 by the submit handler
    # so the storage layer never sees a typo. We do NOT add a
    # ``max_length`` here — the dispatch handler owns the
    # member-of-enum check and emits the user-friendly error code.
    min_tier: str | None = Field(default=None)


class CancelRequest(_ViewModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class AutoAckRequest(_ViewModel):
    """M1 WP5a-2: owner ack of an AUTO_GRACE planning decision.

    ``task_state_version`` pins optimistic concurrency — a stale client
    cannot ack a state version it has not seen.
    """

    task_state_version: int = Field(ge=0)


class AutoVetoRequest(_ViewModel):
    """M1 WP5a-2: owner veto of an AUTO_* lifecycle.

    ``request_id`` is the durable idempotency key: a replayed veto of the
    same request returns the current task view instead of 409.
    """

    request_id: str = Field(min_length=1, max_length=128)
    task_state_version: int = Field(ge=0)


class AutoDispatchNowRequest(_ViewModel):
    """M1 WP5a-2: owner explicit acceleration (skip remaining grace)."""

    task_state_version: int = Field(ge=0)


class DispatchTaskRequest(_ViewModel):
    request_id: str = Field(min_length=1, max_length=128)
    task_state_version: int = Field(ge=0)
    execution_target_id: str = Field(min_length=1, max_length=128)


class TaskView(_ViewModel):
    task_id: str
    request_id: str
    intent: str
    project_id: str | None = None
    base_sha: str | None = None
    working_subpath: str | None = None
    state: str
    state_version: int
    created_at: str
    updated_at: str
    scheduling_policy: str | None = None
    manual_execution_target_id: str | None = None
    # M1 WP2: defaults to T1 in the view so the Swift dashboard can
    # render the picker at the same default without a separate
    # backwards-compat round-trip.
    min_tier: str = "T1"
    # M1 WP5a-2: AUTO lifecycle control state. All four default to
    # ``None`` so pre-WP5a payloads and non-AUTO tasks decode cleanly
    # (the Swift ``TaskView`` already lenient-decodes them since WP5a-1).
    auto_decision_id: str | None = None
    auto_grace_deadline_at: str | None = None
    auto_acked_at: str | None = None
    auto_reason: str | None = None


class CancelView(_ViewModel):
    task: TaskView
    cancelled_now: bool


class DispatchTaskView(_ViewModel):
    dispatch_id: str
    task: TaskView
    request_id: str
    authority: str
    execution_target_id: str
    status: str
    accepted: bool
    reason: str | None = None
    failure_code: str | None = None


class OwnerExecutionSettingsView(_ViewModel):
    owner_initiated_execution_enabled: bool
    production_active: str


class OwnerExecutionSettingsUpdateRequest(_ViewModel):
    owner_initiated_execution_enabled: bool


class DispatchRecommendationRequest(_ViewModel):
    """Optional override for the task's archived scheduling_policy."""

    scheduling_policy: str | None = Field(default=None, min_length=1, max_length=64)


class DispatchRecommendationScoreComponent(_ViewModel):
    """One ``Σ weight × value`` row on the wire.

    M1 WP3 fix (F4): the wire shape carries the **raw unweighted**
    value (``value``) and the multiplier (``weight``) separately.
    The UI multiplies them at display time so a future tuning
    commit that changes ``FRESHNESS_WEIGHT`` (or any other weight
    in :class:`scheduler.ScoreWeights`) does not have to push a
    new ``contribution`` field — the panel just re-renders. The
    ``Σ weight × value == score`` identity is now a property of
    the raw rows, not of a pre-multiplied value the server wrote
    on the wire.
    """

    name: str
    value: float
    #: M1 WP3: weight the recommender applied to ``value``. ``None``
    #: for the auxiliary ``legacy_nudge`` rows the scheduler and
    #: recommender both append for shadow-campaign compatibility —
    #: they do not enter the Σ identity and the UI should label
    #: them as "weight: unknown" rather than guessing.
    weight: float | None = None


class DispatchRecommendationCandidate(_ViewModel):
    execution_target_id: str
    model_sku_id: str
    eligible: bool
    admitted: bool
    score: float | None = None
    headroom_mean: float | None = None
    evidence_fresh: bool = False
    runtime_available: bool = False
    verified: bool = False
    #: Historical verification exists but no longer carries current launch
    #: authority (demoted by newer evidence or expired by launch-age policy).
    execution_verified_stale: bool = False
    # M1 WP3: binding-window headroom. Drives ``headroom_term`` on the
    # score path; ``None`` when every window has missing data so the
    # dashboard can render the row as "headroom unmetered" with the
    # ``burn_unmetered`` chip. ``headroom_mean`` stays alongside as
    # the average — the Swift UI shows the min as the headline number.
    headroom_min: float | None = None
    quota_state: str | None = None
    #: M1 WP1 burn pressure for this candidate's WEEKLY window at the
    #: handler's ``now``. Mirrors the provider-card-level
    #: ``source_pressure`` field. ``None`` until commit 4 wires the
    #: recommender pipeline; we still surface it here so the dashboard
    #: can render the chip alongside the score without a second API call.
    source_pressure: str | None = None
    score_components: tuple[DispatchRecommendationScoreComponent, ...] = ()
    reasons: tuple[str, ...] = ()
    # M1 WP2: capability tier for this candidate. ``None`` when the
    # host-owned tier table could not classify the target (the
    # recommender assumes T1 in scoring and the reasons tuple records
    # ``tier_unknown_assumed_T1``). ``tier_match_reason`` is one of
    # ``\"exact\"`` / ``\"glob\"`` / ``\"default\"``.
    tier: str | None = None
    tier_match_reason: str | None = None


class DispatchRecommendationView(_ViewModel):
    task_id: str
    scheduling_policy: str
    candidates: tuple[DispatchRecommendationCandidate, ...]
    top_pick: str | None
    decision_reason: str


class RunView(_ViewModel):
    run_id: str
    task_id: str
    worker_id: str
    pid: int | None
    #: True iff ``pid`` was just probed and the OS confirms the process is
    #: still alive. ``None`` when ``pid`` is unknown (no probe ran) or when
    #: the run is already in a terminal status (the process is by
    #: definition gone). Lets the UI replace "running pid 40618" with
    #: "exited" the moment the worker really is gone, even before the
    #: database catches up.
    pid_alive: bool | None = None
    status: str
    started_at: str
    finished_at: str | None
    result: Any = None


class TaskListView(_ViewModel):
    tasks: tuple[TaskView, ...]
    total: int


class RunListView(_ViewModel):
    runs: tuple[RunView, ...]


class VerificationReportView(_ViewModel):
    task_id: str
    task_state: str
    status: str
    evidence_id: str | None = None
    failure_reason: str | None = None
    result: VerificationResult | None = None


class RoutingDecisionView(_ViewModel):
    task_id: str | None
    decision_id: str
    request_id: str
    created_at: str
    decision: dict[str, Any]


# Routing plan / role / decision contract (Gate-R).
#
# See docs/ROUTING_ROLE_CONTRACT.md. These models freeze the wire shape so the
# client can be built against it before the execution layer plans roles. The
# daemon does not populate `TaskDetailView.routing_plan` yet: role assignments
# are authoritative execution state and are never fabricated here.


class RoutingPlanCandidateView(_ViewModel):
    execution_target_id: str | None = None
    provider_id: str | None = None
    model_display_name: str | None = None
    model_sku_id: str | None = None
    score: float | None = None
    eligible: bool | None = None
    admitted: bool | None = None
    selected: bool | None = None
    why_not_selected: str | None = None
    quota_snapshot_id: str | None = None


class RoutingDecisionRecordView(_ViewModel):
    """One selection for one role at one moment, independent of every other."""

    decision_id: str
    role: str
    created_at: str
    mode: str | None = None
    selected_execution_target_id: str | None = None
    provider_id: str | None = None
    model_display_name: str | None = None
    why_selected: str | None = None
    supersedes_decision_id: str | None = None
    reroute_reason: str | None = None
    quota_snapshot_id: str | None = None
    candidates: tuple[RoutingPlanCandidateView, ...] = ()


class RoutingRoleStateView(_ViewModel):
    """Where one declared role stands, plus every selection made for it.

    `status` is lifecycle and `outcome` is result; collapsing them would make a
    review that rejected the work indistinguishable from one that crashed.
    """

    role: str
    status: str
    outcome: str = "NONE"
    active_decision_id: str | None = None
    decisions: tuple[RoutingDecisionRecordView, ...] = ()


class RoutingPlanView(_ViewModel):
    """One immutable execution plan revision for a task.

    `declared_roles` is authoritative orchestrator policy state: it is frozen
    when the plan is created, and a later policy change produces a new revision
    rather than mutating this one.
    """

    plan_id: str
    revision: int = 1
    task_id: str
    created_at: str
    policy_id: str | None = None
    resolved_policy: str | None = None
    policy_resolution_source: str | None = None
    superseded_by_plan_id: str | None = None
    declared_roles: tuple[str, ...] = ()
    roles: tuple[RoutingRoleStateView, ...] = ()


class DashboardCountsView(_ViewModel):
    running: int
    ready: int
    blocked: int
    verifying: int
    verified: int
    completed: int
    total: int


class TaskTrendBucketView(_ViewModel):
    bucket_start: str
    submitted: int
    completed: int
    blocked: int


class TaskStateSliceView(_ViewModel):
    state: str
    count: int


class DashboardBasicInfoView(_ViewModel):
    daemon_connection: str
    registered_projects: int
    discovered_providers: int
    available_execution_targets: int
    running_tasks: int
    tasks_today: int
    routing_decisions_today: int
    quota_warning_count: int
    last_refresh_sync: str


class RiskItemView(_ViewModel):
    """A dashboard risk.

    ``title``/``detail`` carry an English fallback so non-UI clients (CLI,
    scripts) stay readable. Owner-facing clients localize from ``raw_code``
    plus ``count`` instead, so the risk text follows the product language.
    """

    title: str
    detail: str
    severity: str
    destination: str | None = None
    raw_code: str | None = None
    #: How many entities the risk covers, when it is a counting risk.
    count: int | None = None


class QuotaObservationView(_ViewModel):
    provider_id: str
    quota_pool_id: str
    window_id: str
    observed_at: str
    remaining_fraction: float | None = None
    confidence: str
    measurement_source: str
    reset_at: str | None = None
    state: str


class QuotaHistoryView(_ViewModel):
    observations: tuple[QuotaObservationView, ...]
    retention_limit: int


class ActivityEventView(_ViewModel):
    event_type: str
    task_id: str | None
    created_at: str
    summary: str


class DashboardSummaryView(_ViewModel):
    connection: HealthView
    counts: DashboardCountsView
    recent_tasks: tuple[TaskView, ...]
    projects: ProjectListView
    basic_info: DashboardBasicInfoView
    task_trend: tuple[TaskTrendBucketView, ...]
    task_state_distribution: tuple[TaskStateSliceView, ...]
    risks: tuple[RiskItemView, ...]
    quota_history: QuotaHistoryView
    providers: ProviderHealthListView
    active_status: ActiveStatusView
    important_blockers: tuple[str, ...]
    recent_events: tuple[ActivityEventView, ...]


class WorkspaceView(_ViewModel):
    task_id: str
    project_id: str | None = None
    repo_path: str
    worktree_path: str
    branch: str
    base_sha: str
    working_subpath: str | None = None
    writer_locked: bool


class TaskDetailView(_ViewModel):
    task: TaskView
    runs: tuple[RunView, ...]
    routing: RoutingDecisionView | None = None
    verification: VerificationReportView
    approvals: ApprovalListView
    workspace: WorkspaceView | None = None
    events: tuple[ActivityEventView, ...]
    #: Authoritative multi-role routing plan. ``None`` until the execution layer
    #: plans roles; clients then fall back to ``routing`` for the primary.
    routing_plan: RoutingPlanView | None = None


class SanitizedEvidenceSourceView(_ViewModel):
    source_type: str
    observed_at: str | None = None


class QuotaWindowHealthView(_ViewModel):
    window_id: str
    window_kind: str
    state: str
    confidence: str
    remaining_fraction: float | None = None
    reset_at: str | None = None


class QuotaPoolHealthView(_ViewModel):
    quota_pool_id: str
    name: str
    plan_id: str
    state: str
    confidence: str
    measurement_source_type: str
    observed_at: str | None = None
    windows: tuple[QuotaWindowHealthView, ...] = ()


class ObservedAvailabilityView(_ViewModel):
    state: str
    observed_at: str
    measurement_source: str
    confidence: str
    sanitized_reason_code: str | None = None
    #: Consecutive failed quota collections since the last successful
    #: observation. ``None`` means the journal has no record (no streak).
    #: The UI surfaces this as a "consecutive failures: N" badge so the
    #: owner can spot a target the host has been unable to probe.
    consecutive_failures: int | None = None


class ExecutionTargetHealthView(_ViewModel):
    execution_target_id: str
    model_sku_id: str
    runtime_id: str
    enabled: bool
    execution_verified: bool
    #: True when historical VERIFIED evidence remains useful diagnostically
    #: but no longer carries current launch authority, either because newer
    #: evidence demoted it or because it exceeded the launch-age cap.
    execution_verified_stale: bool = False
    #: Current full launch authority from the same verification rule used by
    #: owner dispatch, plus enabled/runtime availability. Historical VERIFIED
    #: evidence may stay visible while this is false.
    launch_authorized: bool = False
    #: Timestamp of the historical VERIFIED evidence when evidence-backed.
    #: Static registry verification has no observation timestamp.
    execution_verification_observed_at: str | None = None
    runtime_available: bool | None = None
    observed_availability: ObservedAvailabilityView | None = None
    # M1 WP2: capability tier for this target. ``None`` means the
    # host-owned tier table could not classify the target — the
    # Swift dashboard falls back to its "unknown" label and the recommender
    # assumes T1 in scoring.
    tier: str | None = None
    tier_match_reason: str | None = None
    # M1 WP4: how this target's provider family authenticates.
    # ``"env"`` means an API credential is required; ``"none"``
    # means the OpenCode Zen family routes through its own
    # proxy and the host needs no credential. Surfaced for the
    # Resources page so the owner can tell at a glance which
    # targets the daemon can dispatch to without setup.
    auth_kind: str = "env"
    # M1 WP4: ``"windowed"`` (the pre-WP4 default — the family
    # reports quota windows) or ``"unmetered"`` (no quota window,
    # only locally observed rate limits). The Resources page
    # uses this to group "free / unmetered" targets separately.
    pool_kind: str = "windowed"


class ProviderHealthView(_ViewModel):
    provider_id: str
    display_name: str
    account_count: int
    quota_pools: tuple[QuotaPoolHealthView, ...] = ()
    execution_targets: tuple[ExecutionTargetHealthView, ...] = ()
    evidence_source: str | None = None
    auth_status: str | None = None
    execution_status: str | None = None
    connection_state: str | None = None
    auth_state: str | None = None
    runtime_state: str | None = None
    plan_surface: str | None = None
    region: str | None = None
    last_checked: str | None = None
    #: M1 WP4: ``"windowed"`` (default) or ``"unmetered"``.
    # Surfaced so the Resources page can group providers by
    # ``pool_kind`` without inspecting every target. The
    # family-level pool_kind is constant per provider so any
    # target's pool_kind is authoritative.
    pool_kind: str | None = None


class ProviderHealthListView(_ViewModel):
    providers: tuple[ProviderHealthView, ...]


class UnmeteredObservationView(_ViewModel):
    """M1 WP4: locally observed metrics for an unmetered provider.

    The unmetered path has no upstream quota window to read. The
    three metrics the Resources page shows are derived at read
    time from existing stores (``runs``, ``ExecutionEvidenceJournal``,
    ``QuotaAvailabilityJournal.cooldown_until``).
    """

    #: Number of run records for this provider's targets in the
    #: last 60 seconds. ``None`` when the runs store has no
    #: records for this provider.
    rpm_observed: int
    #: Ratio of non-VERIFIED execution-evidence rows to total rows
    #: in the last hour. ``None`` when no evidence rows exist in
    #: the window (the value ``0.0`` is reserved for "ran and
    #: all-verified"; ``None`` means "no data"). V3 acceptance.
    error_rate_1h: float | None = None
    #: ISO timestamp at which the cooldown (set by the worker
    #: outcome classifier) expires. ``None`` when no target is
    #: currently in COOLDOWN.
    cooldown_until: str | None = None


class QuotaProviderCardView(_ViewModel):
    """One connected provider on the Quota page.

    A connected provider always renders a card. ``quota_state`` distinguishes
    "we have reliable evidence" from "we do not"; UNKNOWN never carries a
    fabricated ``remaining_fraction`` (P4.2.6.4 §13).
    """

    provider_id: str
    display_name: str
    connection_state: str
    auth_state: str | None = None
    plan_surface: str | None = None
    region: str | None = None
    #: OBSERVED | UNKNOWN
    quota_state: str
    #: EXACT | ESTIMATED | UNKNOWN
    confidence: str
    measurement_source: str | None = None
    observed_at: str | None = None
    #: Whether a documented read-only quota endpoint exists for this surface at
    #: all, regardless of whether a credential is currently configured.
    readonly_source_available: bool = False
    #: Whether a usable collector could be constructed right now.
    collector_available: bool = False
    last_refresh_status: str | None = None
    last_refresh_at: str | None = None
    failure_reason: str | None = None
    #: Sanitized provenance of the credential used for this surface's quota
    #: read. Never the credential itself.
    credential_source: str = "NONE"
    quota_pools: tuple[QuotaPoolHealthView, ...] = ()
    #: Plan-first projection. Present whenever any plan evidence exists — the
    #: plan balance may be UNKNOWN while model consumption is known, and that
    #: combination is exactly what this field exists to carry.
    plan: QuotaPlanView | None = None
    #: Burn pressure of the plan's WEEKLY window at the handler's ``now``.
    #: ``None`` when the plan carries no WEEKLY window — the dashboard
    #: renders the card without a pressure chip in that case rather than
    #: inventing one. Mirrors what the recommender will eventually score
    #: against; both paths read the same ``assess`` primitive.
    source_pressure: str | None = None
    #: M1 WP3 fix (F5): the maximum ``consecutive_failures`` streak
    #: across this provider's targets. The per-target streak already
    #: lives on :class:`ObservedAvailabilityView`; this card-level
    #: rollup lets the quota page surface a single "we have not
    #: been able to read this provider for N attempts" badge even
    #: when only one of the provider's targets is in the journal
    #: (or when the owner looks at the page before drilling into a
    #: target row). ``0`` when no journal entry exists for any
    #: target under this provider — the absence of a value, not
    #: the absence of a problem.
    collection_failure_streak: int = 0
    #: M1 WP4: ``"windowed"`` (default — collector reads quota windows)
    # or ``"unmetered"`` (no upstream quota endpoint). The Resources
    # page groups ``unmetered`` cards into a "Free / unmetered"
    # section with a different visual chrome (no 5h / weekly
    # progress bars, no ideal-pace tick).
    pool_kind: str = "windowed"
    #: M1 WP4: read-time metrics for unmetered providers. ``None``
    #: for windowed providers (their quota page surfaces the
    #: existing quota_pools + pool_p table metrics). The
    # Resources page renders the metrics in the unmetered
    # card.
    unmetered: UnmeteredObservationView | None = None


class QuotaBurnView(_ViewModel):
    """M1 WP1 burn assessment for one plan window.

    All five numerical fields are ``None`` for ``UNMETERED`` (the window
    was never read) and carry the real values for ``STALE`` so the bar
    can still draw on cached data. ``pressure`` is always present so the
    UI can pick a chip without branching on Optional. ``pressure_score``
    is signed: positive = orchestrator should consume less, negative =
    consume more; WP3 reads it through ``pressure_weight * -pressure_score``.
    """

    expected_used_fraction: float | None = None
    actual_used_fraction: float | None = None
    deviation: float | None = None
    remaining_fraction: float | None = None
    seconds_to_reset: float | None = None
    pressure: str = "UNMETERED"
    pressure_score: float = 0.0
    window_start_inferred: bool = False


class QuotaPlanWindowView(_ViewModel):
    """One quota window of a shared plan pool.

    ``remaining_fraction`` is populated only for EXACT/ESTIMATED windows, so a
    client cannot draw a bar for a figure the provider never gave us.
    ``burn`` carries the M1 WP1 burn assessment (expected vs actual used,
    deviation, pressure, score). All fields are optional so a pre-WP1
    daemon still decodes.
    """

    window_id: str
    window_kind: str
    state: str
    confidence: str
    remaining_fraction: float | None = None
    remaining_units: float | None = None
    total_units: float | None = None
    unit: str | None = None
    reset_at: str | None = None
    burn: QuotaBurnView | None = None


class BindingWindowView(_ViewModel):
    """Which window currently limits the plan, and how soon it resets.

    Scarcity and reset horizon are separate fields on purpose: 20% remaining
    that resets in 30 minutes is not the same situation as 20% remaining that
    resets in six days.
    """

    window_id: str | None = None
    window_kind: str | None = None
    remaining_fraction: float | None = None
    reset_at: str | None = None
    seconds_until_reset: float | None = None
    reason: str
    confidence: str


class ModelConsumptionView(_ViewModel):
    """What one model consumed of the shared pool.

    Contribution, never entitlement. There is deliberately no remaining field:
    the shape itself makes a per-model balance unrepresentable.
    """

    model_id: str
    consumed_units: float
    unit_kind: str
    provider_unit_label: str | None = None
    call_count: int | None = None
    period_start: str | None = None
    period_end: str | None = None
    confidence: str
    measurement_source: str


class ModelEquivalentWindowView(_ViewModel):
    """A provider's per-scope *view* of one shared pool.

    Rendered under an explicit "equivalent" heading; never as a balance.
    ``scope_id`` is not called ``model_id`` because MiniMax's ``model_remains``
    entries are workload scopes on the observed account; ``scope_kind`` says
    whether the name is a model, and ``workload_scope`` says what it meters.
    """

    scope_id: str
    scope_kind: str
    #: CODING_TEXT | VIDEO_GENERATION | IMAGE_GENERATION | AUDIO | UNKNOWN.
    #: A scope outside the plan's active workload is real evidence the coding
    #: dashboard does not read; the client must not render it as a balance
    #: alongside the ones it does.
    workload_scope: str = "UNKNOWN"
    window_id: str
    remaining_fraction: float | None = None
    remaining_units: float | None = None
    total_units: float | None = None
    unit_kind: str
    confidence: str


class EquivalentCapacityView(_ViewModel):
    """Derived "roughly how many more tasks fit". Always ESTIMATED.

    ``estimated_remaining_tasks`` is ``None`` whenever we lack the history to
    support a figure; ``unavailable_reason`` then says why. Absent is not zero.
    """

    model_id: str
    window_id: str
    task_class: str
    estimated_remaining_tasks: float | None = None
    sample_count: int = 0
    small_sample: bool = False
    #: Always "ESTIMATED" when a figure is present; never EXACT.
    confidence: str = "ESTIMATED"
    unavailable_reason: str | None = None


class QuotaPlanView(_ViewModel):
    """Plan-first projection of one connected subscription.

    The three concepts stay separate all the way to the client: ``windows``
    is the shared plan balance, ``model_consumption`` is what each model spent,
    and ``equivalent_capacity`` is a derived estimate. A client that merged
    them would be merging facts of different kinds.
    """

    provider_id: str
    plan_id: str
    #: The provider's own product name — "GLM Coding Plan", not a renamed
    #: symmetric label.
    display_name: str
    plan_level: str | None = None
    quota_semantics: str
    pool_id: str
    resource_kind: str
    shared_across_models: bool
    unit_kind: str
    covered_model_ids: tuple[str, ...] = ()
    state: str
    confidence: str
    observed_at: str | None = None
    unknown_reason: str | None = None
    #: The workload every figure below is scoped to. Today the orchestrator
    #: schedules CODING_TEXT, so a MiniMax plan projects its ``general`` scope
    #: and leaves ``video`` out of ``windows`` entirely.
    active_workload_scope: str = "UNKNOWN"
    #: Sanitized Advanced-Details codes for observed scopes this workload does
    #: not read, e.g. ``VIDEO_SCOPE_IGNORED_FOR_CODING``. Not failures.
    workload_scope_notes: tuple[str, ...] = ()
    windows: tuple[QuotaPlanWindowView, ...] = ()
    binding_window: BindingWindowView | None = None
    model_consumption: tuple[ModelConsumptionView, ...] = ()
    model_equivalents: tuple[ModelEquivalentWindowView, ...] = ()
    equivalent_capacity: tuple[EquivalentCapacityView, ...] = ()


class QuotaSummaryView(_ViewModel):
    """Summary derived from *connected providers*, not merely observed pools.

    A provider with no observation counts as connected but not
    quota-observable; UNKNOWN is never classified as healthy.
    """

    connected_provider_count: int
    quota_observable_provider_count: int
    quota_unknown_provider_count: int
    quota_warning_count: int
    quota_exhausted_count: int


class QuotaOverviewView(_ViewModel):
    #: NO_CONNECTED_PROVIDER | CONNECTED_BUT_QUOTA_UNKNOWN
    #: | CONNECTED_WITH_QUOTA_OBSERVATIONS
    state: str
    summary: QuotaSummaryView
    providers: tuple[QuotaProviderCardView, ...] = ()
    history: QuotaHistoryView


class QuotaRefreshRequest(_ViewModel):
    provider_id: str | None = None


class QuotaRefreshResultView(_ViewModel):
    """Typed sanitized outcome of an explicit owner-triggered quota refresh."""

    refreshed_provider_ids: tuple[str, ...] = ()
    overview: QuotaOverviewView


class ProviderConnectionView(_ViewModel):
    provider_id: str
    display_name: str
    connection_state: str
    auth_state: str
    execution_verified: bool
    runtime_state: str
    credential_reference_type: str
    region: str | None = None
    plan_surface: str | None = None
    model_skus: tuple[str, ...] = ()
    connected_at: str | None = None
    last_validated_at: str | None = None
    last_reason_code: str | None = None


class AvailableProviderView(_ViewModel):
    provider_id: str
    display_name: str
    connection_state: str
    auth_state: str
    execution_verified: bool
    runtime_state: str
    region: str | None = None
    plan_surface: str | None = None
    model_skus: tuple[str, ...] = ()
    last_checked: str | None = None


class ImportEvidenceItemView(_ViewModel):
    kind: str
    detail: str
    observed_at: str | None = None


class ProviderImportCandidateView(_ViewModel):
    provider_id: str
    display_name: str
    region: str | None = None
    plan_surface: str | None = None
    model_skus: tuple[str, ...] = ()
    execution_verified: bool = False
    auth_state: str
    credential_reference_type: str
    evidence: tuple[ImportEvidenceItemView, ...] = ()


class ProviderConnectionListView(_ViewModel):
    connected: tuple[ProviderConnectionView, ...]
    available_to_add: tuple[AvailableProviderView, ...]
    import_candidates: tuple[ProviderImportCandidateView, ...] = ()


class ConnectProviderRequest(_ViewModel):
    provider_id: str = Field(min_length=1, max_length=128)


class ImportConnectionsRequest(_ViewModel):
    provider_ids: tuple[str, ...] = Field(min_length=1, max_length=32)


class ImportConnectionsView(_ViewModel):
    imported: tuple[ProviderConnectionView, ...]


class SchedulingSettingsView(_ViewModel):
    default_scheduling_policy: str
    selectable_policies: tuple[str, ...]
    #: M1 WP5a-1: orchestrator scheduling mode. ``"MANUAL"`` is the
    #: pre-WP5a-1 default — owner explicitly dispatches each task.
    #: ``"SUPERVISED_AUTO"`` opts the daemon into host-owned planning
    #: with a per-task grace window the owner can veto. ``"ACTIVE"``
    #: remains production-disabled; ``PUT /v1/settings/scheduling``
    #: rejects ``mode="ACTIVE"`` with 409 when activation authority
    #: is not authorised (fail-closed).
    mode: str = "MANUAL"
    selectable_modes: tuple[str, ...] = (
        "MANUAL",
        "SUPERVISED_AUTO",
        "ACTIVE",
    )


class SchedulingSettingsUpdateRequest(_ViewModel):
    default_scheduling_policy: str = Field(min_length=1, max_length=64)
    #: M1 WP5a-1: optional mode field on the update request. ``None``
    #: (legacy callers) leaves the mode unchanged.
    mode: str | None = Field(default=None, min_length=1, max_length=64)


class SchedulingMode(StrEnum):
    """M1 WP5a-1: orchestrator scheduling mode values.

    ``MANUAL`` is the only mode that exercises the full dispatch
    path today. ``SUPERVISED_AUTO`` is the host-owned planning mode
    this WP enables the foundation for (the tick step itself lands
    in WP5a-2). ``ACTIVE`` remains production-disabled; selecting
    it without activation authority returns 409.
    """

    MANUAL = "MANUAL"
    SUPERVISED_AUTO = "SUPERVISED_AUTO"
    ACTIVE = "ACTIVE"


class ProjectSchedulingPolicyRequest(_ViewModel):
    """``scheduling_policy=None`` restores the global default for this project."""

    scheduling_policy: str | None = Field(default=None, min_length=1, max_length=64)
    manual_execution_target_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProjectSupervisedAutoSettingsRequest(_ViewModel):
    """M1 WP5a-1: project-level supervised-auto settings.

    All three fields are required on a PUT — there is no
    field-by-field partial update. The store validates ``grace_seconds``
    range (1..86_400); the Pydantic constraint is intentionally loose
    so the store's ``ValueError`` is the single source of truth for
    the range error and the facade can map it to 400
    ``invalid_grace_seconds`` rather than catching pydantic errors.
    """

    supervised_auto_allowed: bool
    unattended_allowed: bool
    grace_seconds: int


class DisconnectProviderRequest(_ViewModel):
    confirm: bool


class ProviderDiscoveryStatusView(_ViewModel):
    discovery_state: str
    last_discovered_at: str | None = None
    provider_count: int = 0
    configured_family_count: int = 0
    catalog_discovered_provider_count: int = 0
    credential_evidence_provider_count: int = 0
    execution_target_count: int = 0
    last_error_code: str | None = None
    catalog_snapshot_id: str | None = None
    source_method: str | None = None


class ActiveStatusView(_ViewModel):
    production_active: str
    authorized: bool
    blocking_reasons: tuple[str, ...]
    gate: dict[str, bool]


class ApprovalView(_ViewModel):
    approval_id: str
    task_id: str
    kind: str
    status: str
    created_at: str
    resolved_at: str | None = None


class ApprovalListView(_ViewModel):
    approvals: tuple[ApprovalView, ...]


class SupervisorStepView(_ViewModel):
    """One periodic step registered with the daemon supervisor (WP0)."""

    name: str
    last_run_at: str | None = None
    last_duration_ms: float | None = None
    consecutive_failures: int = 0
    in_backoff: bool = False


class HealthView(_ViewModel):
    status: str
    api_version: str
    #: Exact process identity for lifecycle ownership. ``process_instance_id``
    #: is regenerated on every daemon process start, so a client can re-read
    #: health immediately before signaling and refuse a PID-reuse/identity
    #: mismatch instead of falling back to broad process-name matching.
    process_id: int = Field(ge=1)
    process_instance_id: str = Field(min_length=16, max_length=128)
    #: ``/v1/health`` is the single source of truth for whether the
    #: bundled daemon is still ticking. ``None`` until the first tick
    #: has run (typically within one ``tick_interval_seconds`` of boot).
    last_tick_at: str | None = None
    tick_interval_seconds: float | None = None
    supervisor_steps: tuple[SupervisorStepView, ...] = ()
    #: M1 WP2: which tier table the daemon is running. ``"owner_file"``
    #: means a host-owned JSON was loaded; ``"default_fallback"`` means
    #: the shipped defaults were used (either because no file was
    #: provided or because the file failed to parse). ``None`` means
    #: tier-table wiring is not active (e.g. an ad-hoc CLI that did
    #: not inject one).
    model_tiers_source: str | None = None


class BuildView(_ViewModel):
    """Sanitized daemon build identity.

    Carries commit identity only, so the dashboard can prove the UI and the
    daemon came from the same source revision. Never carries credentials,
    paths, or repository content.
    """

    commit_sha: str
    short_sha: str
    api_version: str
    configuration: str
    built_at: str


class ProjectResolveRequest(_ViewModel):
    path: str = Field(min_length=1, max_length=4096)


class ProjectRegisterRequest(_ViewModel):
    path: str = Field(min_length=1, max_length=4096)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    security_bookmark_b64: str | None = Field(default=None, max_length=128 * 1024)


class ProjectRemoveView(_ViewModel):
    project: ProjectView
    removed_from_orchestrator: bool


class ProjectView(_ViewModel):
    project_id: str
    display_name: str
    canonical_repo_root: str
    git_root: str
    default_branch: str
    last_known_head: str
    created_at: str
    updated_at: str
    working_subpath: str | None = None
    remote_url: str | None = None
    last_opened_at: str | None = None
    storage_availability: str
    recent_task_count: int = 0
    current_branch: str | None = None
    scheduling_policy: str | None = None
    manual_execution_target_id: str | None = None
    #: M1 WP5a-1: project-level supervised-auto settings. See
    #: ``SafetyKernelStore.set_project_settings`` and
    #: ``ProjectRecord`` for the contract.
    supervised_auto_allowed: bool = False
    unattended_allowed: bool = False
    grace_seconds: int = 120


class ProjectListView(_ViewModel):
    projects: tuple[ProjectView, ...]


def _task_view(record) -> TaskView:
    return TaskView(
        task_id=record.task_id,
        request_id=record.request_id,
        intent=record.intent,
        project_id=record.project_id,
        base_sha=record.base_sha,
        working_subpath=record.working_subpath,
        state=record.state.value,
        state_version=record.state_version,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        scheduling_policy=record.scheduling_policy,
        manual_execution_target_id=record.manual_execution_target_id,
        min_tier=record.min_tier,
        auto_decision_id=record.auto_decision_id,
        auto_grace_deadline_at=record.auto_grace_deadline_at,
        auto_acked_at=record.auto_acked_at,
        auto_reason=record.auto_reason,
    )


@dataclass
class ControlPlaneService:
    """Host-owned facade rendering sanitized orchestrator truth for local clients."""

    registry: ModelRegistry
    store: SafetyKernelStore
    activation_gate: ActiveRoutingGate = field(default_factory=ActiveRoutingGate)
    runtime_availability: dict[str, bool] = field(default_factory=dict)
    verification_journal: VerificationEvidenceJournal | None = None
    quota_availability_journal: QuotaAvailabilityJournal | None = None
    provider_registry_manager: ProviderRegistryManager | None = None
    owner_execution: OwnerExecutionSettings = field(default_factory=OwnerExecutionSettings)
    scheduling_settings: SchedulingSettings = field(default_factory=SchedulingSettings)
    execution_evidence_journal: ExecutionEvidenceJournal | None = None
    dispatch_executor: Any = None
    #: Read-only quota collection for connected providers. Optional so the
    #: control plane still works (reporting UNKNOWN truthfully) before the
    #: daemon wires a runtime-state root.
    quota_refresh_service: QuotaRefreshService | None = None
    #: Optional DaemonSupervisor reference (WP0). ``/v1/health`` reads its
    #: snapshot to surface ``last_tick_at``, ``tick_interval_seconds`` and
    #: per-step status. ``None`` is allowed — the bundled daemon always
    #: wires one in, but unit tests / ad-hoc CLIs do not have to.
    supervisor: DaemonSupervisor | None = None
    #: M1 WP2 tier table. ``None`` is allowed so ad-hoc tests / CLIs
    #: that don't care about tier gating still build a service. The
    #: recommender treats ``None`` as "default to T1 with no caps".
    tier_table: Any = None
    #: Where the active ``tier_table`` came from — surfaces on
    #: ``/v1/health.model_tiers_source`` so the owner can spot a
    #: malformed host file that fell back to the shipped defaults.
    #: ``None`` when ``tier_table`` is ``None``.
    model_tiers_source: str | None = None
    #: M1 WP5a-2: pending-shadow journal for the SUPERVISED_AUTO
    #: lifecycle. ``None`` keeps the pre-WP5a-2 behavior (no pending
    #: shadows are recorded; the tick still runs gate-safe).
    shadow_journal: Any = None
    #: M1 WP5a-2: catalog snapshot id stamped into the frozen routing
    #: decisions the tick creates. The daemon wires the runtime config's
    #: value; ad-hoc services fall back to a stable local default.
    catalog_snapshot_id: str = "control-catalog-v1"

    @property
    def owner_initiated_execution_enabled(self) -> bool:
        return self.owner_execution.enabled

    def open_request(self) -> ControlPlaneService:
        """Return a request-local service bound to a fresh SQLite connection."""

        if self.store.path == ":memory:":
            raise RuntimeError("threaded control plane requires a file-backed SafetyKernelStore")
        return ControlPlaneService(
            registry=self.registry,
            store=SafetyKernelStore(self.store.path),
            activation_gate=self.activation_gate,
            runtime_availability=self.runtime_availability,
            verification_journal=self.verification_journal,
            quota_availability_journal=self.quota_availability_journal,
            provider_registry_manager=self.provider_registry_manager,
            owner_execution=self.owner_execution,
            scheduling_settings=self.scheduling_settings,
            execution_evidence_journal=self.execution_evidence_journal,
            dispatch_executor=self.dispatch_executor,
            quota_refresh_service=self.quota_refresh_service,
            supervisor=self.supervisor,
            tier_table=self.tier_table,
            model_tiers_source=self.model_tiers_source,
            shadow_journal=self.shadow_journal,
            catalog_snapshot_id=self.catalog_snapshot_id,
        )

    @staticmethod
    def _validate_identifier(kind: str, value: str) -> None:
        if _IDENTIFIER.match(value) is None:
            raise ControlPlaneError(400, f"invalid_{kind}")

    def _effective_registry(self) -> ModelRegistry:
        return (
            self.provider_registry_manager.registry()
            if self.provider_registry_manager is not None
            else self.registry
        )

    def _runtime_available(self, execution_target_id: str) -> bool:
        """Resolve target availability from the selected runtime's host truth."""

        if execution_target_id in self.runtime_availability:
            return self.runtime_availability[execution_target_id]
        if self.provider_registry_manager is not None:
            return self.provider_registry_manager.runtime_available(execution_target_id)
        target = self._effective_registry().execution_targets.get(execution_target_id)
        if target is None:
            return False
        runtime_provider = target.runtime_provider_id or "opencode"
        if runtime_provider == "opencode":
            return _opencode_binary_available()
        return False

    def _recommendation_service(self) -> DispatchRecommendationService:
        """Build the host recommendation service on demand.

        WP5a-1 commit 1 — pure refactor. The service is stateless and
        cheap to build; constructing it on every call avoids threading
        cache invalidation concerns across ``open_request`` service
        instances. The constructor takes the same references the inline
        ``_collect_dispatch_candidates`` method used, byte-equivalent.
        """

        return DispatchRecommendationService(
            self.store,
            registry_provider=self._effective_registry,
            quota_refresh_service=self.quota_refresh_service,
            execution_evidence_journal=self.execution_evidence_journal,
            quota_availability_journal=self.quota_availability_journal,
            tier_table=self.tier_table,
            runtime_availability=self.runtime_availability,
            runtime_availability_fallback=self._runtime_available,
        )

    def _supervised_auto_step(self):
        """Build the WP5a-2 supervised-auto tick step on demand.

        The step is stateless; constructing it per call keeps ``open_request``
        service clones correct without cache invalidation. The same factory
        backs the periodic supervisor step and the owner abort helpers.
        """

        from personal_ai_orchestrator.supervised_auto_step import SupervisedAutoStep

        return SupervisedAutoStep(
            store=self.store,
            scheduling_settings=self.scheduling_settings,
            owner_execution_enabled=lambda: self.owner_initiated_execution_enabled,
            recommendation_service_factory=self._recommendation_service,
            project_provider=self.get_project,
            registry_provider=self._effective_registry,
            provider_registry_manager=self.provider_registry_manager,
            runtime_available_provider=self._runtime_available,
            execution_evidence_journal=self.execution_evidence_journal,
            executor=self.dispatch_executor,
            shadow_journal=self.shadow_journal,
            catalog_snapshot_id=self.catalog_snapshot_id,
        )

    def supervised_auto_tick(self, now) -> None:
        """Run one bounded SUPERVISED_AUTO sweep (supervisor step body)."""

        self._supervised_auto_step().tick(now)

    def build_supervised_auto_step(self):
        """Return the supervisor-callable tick (``fn(now) -> None``).

        Registered by the daemon on the ``DaemonSupervisor`` under
        ``supervised-auto`` — but ONLY on the non-control-only daemon
        (§19: control-only must never execute autonomous steps).

        Each invocation opens a request-scoped service (fresh SQLite
        connection) because the supervisor drives steps on its own thread
        while the long-lived service store is bound to the constructing
        thread — the same per-request discipline the UDS server applies.
        ``:memory:`` services (tests, ad-hoc CLIs) share the single
        connection instead.
        """

        def _step(now) -> None:
            if self.store.path == ":memory:":
                self.supervised_auto_tick(now)
                return
            request_service = self.open_request()
            try:
                request_service.supervised_auto_tick(now)
            finally:
                request_service.store.close()

        return _step

    @staticmethod
    def _project_id_for(git_root: str, working_subpath: str | None) -> str:
        payload = f"{git_root}\0{working_subpath or ''}".encode()
        return f"project-{sha256(payload).hexdigest()[:16]}"

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return completed.stdout.strip()

    def _project_probe(self, path: str) -> dict[str, str | None]:
        try:
            selected = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise ControlPlaneError(400, "project_path_missing") from None
        if not selected.is_dir():
            raise ControlPlaneError(400, "project_path_not_directory")
        try:
            git_root = Path(self._git(selected, "rev-parse", "--show-toplevel")).resolve(
                strict=True
            )
            is_work_tree = self._git(git_root, "rev-parse", "--is-inside-work-tree")
            if is_work_tree != "true":
                raise ValueError("not a work tree")
            head = self._git(git_root, "rev-parse", "HEAD")
        except Exception:
            raise ControlPlaneError(400, "invalid_repository") from None
        try:
            rel = selected.relative_to(git_root)
            working_subpath = None if str(rel) == "." else rel.as_posix()
        except ValueError:
            raise ControlPlaneError(400, "project_path_outside_git_root") from None
        try:
            default_branch = self._git(
                git_root,
                "symbolic-ref",
                "--short",
                "refs/remotes/origin/HEAD",
            ).removeprefix("origin/")
        except Exception:
            try:
                default_branch = self._git(git_root, "branch", "--show-current") or "HEAD"
            except Exception:
                default_branch = "HEAD"
        try:
            current_branch = self._git(git_root, "branch", "--show-current") or "HEAD"
        except Exception:
            current_branch = None
        try:
            remote_url = self._git(git_root, "config", "--get", "remote.origin.url") or None
        except Exception:
            remote_url = None
        return {
            "canonical_repo_root": str(selected),
            "git_root": str(git_root),
            "working_subpath": working_subpath,
            "default_branch": default_branch,
            "last_known_head": head,
            "remote_url": remote_url,
            "current_branch": current_branch,
        }

    def _availability_for_project(
        self, project: ProjectRecord
    ) -> tuple[ProjectAvailability, str | None, str | None, str | None]:
        git_root = Path(project.git_root)
        if not git_root.exists():
            return ProjectAvailability.MISSING, None, None, None
        if not git_root.is_dir():
            return ProjectAvailability.INVALID_REPOSITORY, None, None, None
        try:
            top = Path(self._git(git_root, "rev-parse", "--show-toplevel")).resolve()
            if top != git_root.resolve():
                return ProjectAvailability.INVALID_REPOSITORY, None, None, None
            head = self._git(git_root, "rev-parse", "HEAD")
            branch = self._git(git_root, "branch", "--show-current") or "HEAD"
        except Exception:
            return ProjectAvailability.INVALID_REPOSITORY, None, None, None
        return ProjectAvailability.ONLINE, head, project.default_branch, branch

    def _refresh_project_view(self, project: ProjectRecord) -> ProjectView:
        availability, head, default_branch, current_branch = self._availability_for_project(project)
        if availability is not project.storage_availability or (
            head is not None and head != project.last_known_head
        ):
            project = self.store.update_project_availability(
                project.project_id,
                storage_availability=availability,
                last_known_head=head,
                default_branch=default_branch,
            )
        return self._project_view(project, current_branch=current_branch)

    def _project_view(
        self, project: ProjectRecord, *, current_branch: str | None = None
    ) -> ProjectView:
        return ProjectView(
            project_id=project.project_id,
            display_name=project.display_name,
            canonical_repo_root=project.canonical_repo_root,
            git_root=project.git_root,
            default_branch=project.default_branch,
            last_known_head=project.last_known_head,
            created_at=project.created_at.isoformat(),
            updated_at=project.updated_at.isoformat(),
            working_subpath=project.working_subpath,
            remote_url=project.remote_url,
            last_opened_at=project.last_opened_at.isoformat() if project.last_opened_at else None,
            storage_availability=project.storage_availability.value,
            recent_task_count=self.store.task_count_for_project(project.project_id),
            current_branch=current_branch,
            scheduling_policy=project.scheduling_policy,
            manual_execution_target_id=project.manual_execution_target_id,
            supervised_auto_allowed=project.supervised_auto_allowed,
            unattended_allowed=project.unattended_allowed,
            grace_seconds=project.grace_seconds,
        )

    def resolve_project(self, payload: dict[str, Any]) -> ProjectView:
        request = ProjectResolveRequest.model_validate(payload)
        probe = self._project_probe(request.path)
        project_id = self._project_id_for(str(probe["git_root"]), probe["working_subpath"])
        now = datetime_now_iso()
        return ProjectView(
            project_id=project_id,
            display_name=Path(str(probe["canonical_repo_root"])).name,
            canonical_repo_root=str(probe["canonical_repo_root"]),
            git_root=str(probe["git_root"]),
            default_branch=str(probe["default_branch"]),
            last_known_head=str(probe["last_known_head"]),
            created_at=now,
            updated_at=now,
            working_subpath=probe["working_subpath"],
            remote_url=probe["remote_url"],
            storage_availability=ProjectAvailability.ONLINE.value,
            current_branch=probe["current_branch"],
        )

    def register_project(self, payload: dict[str, Any]) -> ProjectView:
        request = ProjectRegisterRequest.model_validate(payload)
        probe = self._project_probe(request.path)
        project_id = self._project_id_for(str(probe["git_root"]), probe["working_subpath"])
        display_name = request.display_name or Path(str(probe["canonical_repo_root"])).name
        try:
            project = self.store.register_project(
                project_id=project_id,
                display_name=display_name,
                canonical_repo_root=str(probe["canonical_repo_root"]),
                git_root=str(probe["git_root"]),
                default_branch=str(probe["default_branch"]),
                last_known_head=str(probe["last_known_head"]),
                working_subpath=probe["working_subpath"],
                remote_url=probe["remote_url"],
                storage_availability=ProjectAvailability.ONLINE,
                security_bookmark_b64=request.security_bookmark_b64,
            )
        except ValueError:
            raise ControlPlaneError(409, "project_already_registered") from None
        return self._project_view(project, current_branch=probe["current_branch"])

    def list_projects(self) -> ProjectListView:
        return ProjectListView(
            projects=tuple(
                self._refresh_project_view(project) for project in self.store.list_projects()
            )
        )

    def get_project(self, project_id: str) -> ProjectView:
        self._validate_identifier("project_id", project_id)
        try:
            return self._refresh_project_view(self.store.get_project(project_id))
        except KeyError:
            raise ControlPlaneError(404, "project_not_found") from None

    def remove_project(self, project_id: str) -> ProjectRemoveView:
        self._validate_identifier("project_id", project_id)
        try:
            project = self.store.remove_project(project_id)
        except KeyError:
            raise ControlPlaneError(404, "project_not_found") from None
        return ProjectRemoveView(
            project=self._project_view(project),
            removed_from_orchestrator=True,
        )

    def mark_project_opened(self, project_id: str) -> ProjectView:
        self._validate_identifier("project_id", project_id)
        try:
            project = self.store.mark_project_opened(project_id)
        except KeyError:
            raise ControlPlaneError(404, "project_not_found") from None
        return self._refresh_project_view(project)

    def _validated_task_policy(
        self,
        scheduling_policy: str | None,
        manual_execution_target_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Validate an owner-supplied task scheduling override.

        ``None`` means "no task override" and defers to project/global resolution.
        MANUAL is the one policy that carries a payload: without an explicit target
        there is nothing to honour, and silently falling back to another model is
        exactly the behaviour MANUAL exists to prevent — so it is rejected here
        rather than quietly downgraded.
        """

        if scheduling_policy is None:
            if manual_execution_target_id is not None:
                raise ControlPlaneError(400, "manual_target_requires_manual_policy")
            return None, None
        if scheduling_policy not in SELECTABLE_TASK_POLICIES:
            raise ControlPlaneError(400, "unsupported_scheduling_policy")
        if scheduling_policy == "MANUAL":
            if not manual_execution_target_id:
                raise ControlPlaneError(400, "manual_policy_requires_execution_target")
            self._validate_identifier("execution_target_id", manual_execution_target_id)
            return scheduling_policy, manual_execution_target_id
        if manual_execution_target_id is not None:
            raise ControlPlaneError(400, "manual_target_requires_manual_policy")
        return scheduling_policy, None

    def scheduling_settings_view(self) -> SchedulingSettingsView:
        return SchedulingSettingsView(
            default_scheduling_policy=self.scheduling_settings.default_policy,
            selectable_policies=SELECTABLE_GLOBAL_POLICIES,
            mode=self.scheduling_settings.mode,
            selectable_modes=SELECTABLE_MODES,
        )

    def update_scheduling_settings(self, payload: dict[str, Any]) -> SchedulingSettingsView:
        request = SchedulingSettingsUpdateRequest.model_validate(payload)
        try:
            self.scheduling_settings.set_default_policy(request.default_scheduling_policy)
        except ValueError:
            raise ControlPlaneError(400, "unsupported_global_scheduling_policy") from None
        if request.mode is not None:
            try:
                SchedulingMode(request.mode)
            except ValueError as error:
                raise ControlPlaneError(400, "unsupported_scheduling_mode") from error
            # Production ACTIVE is reachable on the wire only when
            # activation authority is authorised. ``SUPERVISED_AUTO``
            # and ``MANUAL`` always succeed.
            if request.mode == SchedulingMode.ACTIVE and (
                self.activation_gate is None or not self.activation_gate.authorized
            ):
                raise ControlPlaneError(
                    409,
                    "production_active_not_authorized",
                )
            try:
                previous_mode = self.scheduling_settings.mode
                self.scheduling_settings.set_mode(request.mode)
            except ValueError:
                raise ControlPlaneError(400, "unsupported_scheduling_mode") from None
            # M1 WP5a-2 (§3.1): leaving SUPERVISED_AUTO aborts every
            # AUTO_PLANNED / AUTO_GRACE task in the same handler — the
            # owner's mode change takes effect immediately, no in-flight
            # grace window may outlive it. The tick's revocation sweep is
            # the crash-recovery backstop for an abort write lost to a
            # crash between these two writes.
            if (
                previous_mode != request.mode
                and request.mode != SchedulingMode.SUPERVISED_AUTO.value
            ):
                self._abort_auto_tasks(reason="mode_changed")
        return self.scheduling_settings_view()

    def _abort_auto_tasks(self, *, reason: str, project_id: str | None = None) -> None:
        """Fail-closed owner abort of every AUTO_* lifecycle (§3.1).

        AUTO_PLANNED / AUTO_GRACE → READY, pending shadows discarded, auto
        metadata cleared and the abort audited. RUNNING tasks are never
        touched — their worker is governed by the executor + verifier path.
        Shared by the mode-change handler, the project-settings disable
        handler, the veto endpoint and the cancel-as-veto path.
        """

        step = self._supervised_auto_step()
        rows = self.store.connection.execute(
            "SELECT task_id FROM tasks WHERE state IN (?,?) ORDER BY task_id",
            (
                TaskState.AUTO_PLANNED.value,
                TaskState.AUTO_GRACE.value,
            ),
        ).fetchall()
        now = datetime.now(UTC)
        for row in rows:
            try:
                task = self.store.get_task(row["task_id"])
            except KeyError:
                continue
            if project_id is not None and task.project_id != project_id:
                continue
            step._abort_auto_task(task, now, reason=reason)  # noqa: SLF001 — same-package lifecycle helper

    def set_project_scheduling_policy(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> ProjectView:
        self._validate_identifier("project_id", project_id)
        request = ProjectSchedulingPolicyRequest.model_validate(payload)
        policy, manual_target = self._validated_task_policy(
            request.scheduling_policy,
            request.manual_execution_target_id,
        )
        try:
            project = self.store.set_project_scheduling_policy(
                project_id,
                scheduling_policy=policy,
                manual_execution_target_id=manual_target,
            )
        except KeyError:
            raise ControlPlaneError(404, "project_not_found") from None
        return self._project_view(project)

    def set_project_supervised_auto_settings(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> ProjectView:
        """M1 WP5a-1: persist the project-level supervised-auto settings.

        The store validates ``grace_seconds`` (1..86_400) and raises
        ``ValueError`` on out-of-range input — mapped to 400 here.
        ``KeyError`` from the underlying ``get_project`` propagates
        as 404 ``project_not_found``.
        """

        self._validate_identifier("project_id", project_id)
        request = ProjectSupervisedAutoSettingsRequest.model_validate(payload)
        try:
            previous = self.store.get_project(project_id)
        except KeyError:
            raise ControlPlaneError(404, "project_not_found") from None
        try:
            project = self.store.set_project_settings(
                project_id,
                supervised_auto_allowed=request.supervised_auto_allowed,
                unattended_allowed=request.unattended_allowed,
                grace_seconds=request.grace_seconds,
            )
        except ValueError as error:
            raise ControlPlaneError(400, "invalid_grace_seconds") from error
        # M1 WP5a-2 (§3.1 裁决 5): revoking the project opt-in aborts the
        # project's AUTO_* lifecycles immediately — same fail-closed
        # contract as the global mode change.
        if previous.supervised_auto_allowed and not request.supervised_auto_allowed:
            self._abort_auto_tasks(reason="project_auto_disabled", project_id=project_id)
        return self._project_view(project)

    def import_provider_connections(self, payload: dict[str, Any]) -> ImportConnectionsView:
        """Materialise owner-approved import candidates.

        This is the owner action referenced by the Connected tab. It never runs
        implicitly on discovery, startup, or refresh.
        """

        request = ImportConnectionsRequest.model_validate(payload)
        for provider_id in request.provider_ids:
            self._validate_identifier("provider_id", provider_id)
        if self.provider_registry_manager is None:
            raise ControlPlaneError(503, "provider_registry_manager_not_configured")
        try:
            connections = self.provider_registry_manager.import_connections(request.provider_ids)
        except LookupError as error:
            code = str(error) or "provider_not_import_candidate"
            raise ControlPlaneError(404, code) from None
        except ValueError as error:
            raise ControlPlaneError(400, str(error) or "invalid_import_request") from None
        return ImportConnectionsView(
            imported=tuple(self._connection_view(item) for item in connections)
        )

    def submit_task(self, payload: dict[str, Any]) -> TaskView:
        request = TaskSubmitRequest.model_validate(payload)
        for kind, value in (
            ("task_id", request.task_id),
            ("request_id", request.request_id),
            ("project_id", request.project_id),
        ):
            self._validate_identifier(kind, value)
        try:
            project = self.store.get_project(request.project_id)
        except KeyError:
            raise ControlPlaneError(400, "project_not_registered") from None
        project_view = self._refresh_project_view(project)
        if project_view.storage_availability != ProjectAvailability.ONLINE.value:
            raise ControlPlaneError(409, "project_not_available")
        try:
            base_sha = self._git(Path(project.git_root), "rev-parse", "HEAD")
        except Exception:
            self.store.update_project_availability(
                project.project_id,
                storage_availability=ProjectAvailability.INVALID_REPOSITORY,
            )
            raise ControlPlaneError(409, "project_not_available") from None
        scheduling_policy, manual_target = self._validated_task_policy(
            request.scheduling_policy,
            request.manual_execution_target_id,
        )
        # M1 WP2: validate the tier floor here so a typo lands as a
        # 400 instead of an opaque error from the storage layer.
        # ``None`` is the UI-default; storage normalises to "T1".
        # ``is None`` (not ``or "T1"``) so empty strings still hit the
        # invalid-tier path below.
        if request.min_tier is None:
            min_tier = "T1"
        else:
            min_tier = request.min_tier
        if min_tier not in {"T0", "T1", "T2", "T3"}:
            raise ControlPlaneError(
                400,
                f"invalid_min_tier: must be one of T0/T1/T2/T3, got {min_tier!r}",
            )
        try:
            record = self.store.submit_task(
                task_id=request.task_id,
                request_id=request.request_id,
                intent=request.intent,
                project_id=request.project_id,
                base_sha=base_sha,
                working_subpath=project.working_subpath,
                scheduling_policy=scheduling_policy,
                manual_execution_target_id=manual_target,
                min_tier=min_tier,
            )
        except ValueError:
            raise ControlPlaneError(400, "conflicting_request_id") from None
        return _task_view(record)

    def list_tasks(self, *, limit: int = DEFAULT_LIST_LIMIT) -> TaskListView:
        bounded = max(1, min(limit, MAX_LIST_LIMIT))
        rows = self.store.connection.execute(
            "SELECT task_id FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (bounded,),
        ).fetchall()
        total = self.store.connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        tasks = tuple(_task_view(self.store.get_task(row["task_id"])) for row in rows)
        return TaskListView(tasks=tasks, total=total)

    def get_task(self, task_id: str) -> TaskView:
        self._validate_identifier("task_id", task_id)
        try:
            return _task_view(self.store.get_task(task_id))
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None

    def task_runs(self, task_id: str) -> RunListView:
        self.get_task(task_id)
        rows = self.store.connection.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY rowid", (task_id,)
        ).fetchall()
        return RunListView(runs=tuple(self._run_view(row) for row in rows))

    def get_run(self, run_id: str) -> RunView:
        self._validate_identifier("run_id", run_id)
        row = self.store.connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ControlPlaneError(404, "run_not_found")
        return self._run_view(row)

    @staticmethod
    def _run_view(row) -> RunView:
        result: Any = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError:
                result = None
        rendered = json.dumps(result, default=str) if result is not None else ""
        if len(rendered) > MAX_REQUEST_BYTES:
            result = {"truncated": True}
        pid = row["pid"]
        # pid_alive is None when there is no pid to probe or the run is
        # already terminal (the worker can't still be running). When the
        # run is still RUNNING we ask the OS — ProcessLookupError means
        # gone, PermissionError means alive-but-ours (other entries
        # returned by the same lookup would be different processes).
        pid_alive: bool | None = None
        if pid is not None and row["status"] == "RUNNING":
            try:
                os.kill(pid, 0)
                pid_alive = True
            except ProcessLookupError:
                pid_alive = False
            except PermissionError:
                pid_alive = True
            except OSError:
                pid_alive = False
        return RunView(
            run_id=row["run_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            pid=pid,
            pid_alive=pid_alive,
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result=result,
        )

    def cancel_task(self, task_id: str, payload: dict[str, Any] | None) -> CancelView:
        self._validate_identifier("task_id", task_id)
        request = CancelRequest.model_validate(payload or {})
        if request.request_id is not None:
            self._validate_identifier("request_id", request.request_id)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        # M1 WP5a-2 (§3.5): cancel on an AUTO_* task is exactly a veto —
        # back to READY, policy locked MANUAL, pending shadow discarded,
        # auto metadata cleared, audited. No new semantics.
        if task.state in {TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE}:
            self._veto_auto_lifecycle(
                task,
                request_id=request.request_id or f"cancel-{task.task_id}",
                audit_reason="owner_cancel",
            )
            return CancelView(
                task=_task_view(self.store.get_task(task_id)),
                cancelled_now=True,
            )
        if task.state is TaskState.CANCELLED:
            return CancelView(task=_task_view(task), cancelled_now=False)
        if task.state is TaskState.RUNNING:
            executor = self.dispatch_executor
            if executor is not None and executor.execution_supervisor.get(task_id) is not None:
                cancelled = executor.cancel_active(task_id)
                task = self.store.get_task(task_id)
                if task.state is TaskState.CANCELLED:
                    return CancelView(task=_task_view(task), cancelled_now=cancelled)
                if task.state is not TaskState.RUNNING:
                    # The exact child died while cancellation was in
                    # flight; the worker-exit path owns the outcome.
                    return CancelView(task=_task_view(task), cancelled_now=False)
            raise ControlPlaneError(
                409,
                "running_task_cancellation_requires_execution_supervisor",
            )
        if task.state in {TaskState.FAILED, TaskState.COMPLETED}:
            raise ControlPlaneError(409, "task_state_is_terminal")
        try:
            updated = self.store.transition_task(
                task_id,
                TaskState.CANCELLED,
                expected_version=task.state_version,
                reason="control-plane client-requested cancellation",
            )
        except (ValueError, RuntimeError) as error:
            raise ControlPlaneError(409, "task_state_cannot_be_cancelled") from error
        return CancelView(task=_task_view(updated), cancelled_now=True)

    def _veto_auto_lifecycle(
        self,
        task: TaskRecord,
        *,
        request_id: str,
        audit_reason: str,
    ) -> None:
        """Shared veto core (endpoint veto + cancel-as-veto, §3.5/§23).

        ONE crash-atomic SQLite transaction (§10 of the closeout
        ruling): ``AUTO_* → READY``, task policy forced MANUAL (the tick
        will not re-plan it), all four auto columns cleared, exactly one
        ``state_version`` bump, ``AUTO_VETOED`` audited with the durable
        ``request_id`` and the pending-cleanup intent recorded — all
        before COMMIT. The owner-visible truth (READY + MANUAL + clean
        metadata) holds even if the shadow journal is unavailable; the
        pending discard runs after COMMIT via the idempotent outbox
        drain.
        """

        fresh = self.store.get_task(task.task_id)
        if fresh.state not in {TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE}:
            raise ControlPlaneError(409, "task_state_not_auto")
        target = self._auto_frozen_target(fresh)
        try:
            self.store.abort_auto_lifecycle(
                task.task_id,
                expected_version=fresh.state_version,
                reason=audit_reason,
                event_type="AUTO_VETOED",
                request_id=request_id,
                target=target,
                force_manual=True,
            )
        except ValueError:
            raise ControlPlaneError(409, "task_state_not_auto") from None
        except RuntimeError as error:
            raise ControlPlaneError(409, "stale_task_state_version") from error
        # Best-effort immediate cleanup; the tick's drain is the
        # crash-recovery backstop. A journal failure never rolls back
        # the already-committed SQLite truth above.
        from personal_ai_orchestrator.supervised_auto_step import (
            drain_auto_shadow_cleanup_outbox,
        )

        drain_auto_shadow_cleanup_outbox(self.store, self.shadow_journal)

    def _auto_frozen_target(self, task: TaskRecord) -> str | None:
        from personal_ai_orchestrator.supervised_auto_step import (
            supervised_auto_routing_request_id,
        )

        if task.auto_decision_id is None:
            return None
        row = self.store.routing_decision_by_request_id(
            supervised_auto_routing_request_id(task.auto_decision_id)
        )
        if row is None:
            return None
        import json as _json

        try:
            decision = _json.loads(row["payload_json"])
        except ValueError:
            return None
        return decision.get("selected_execution_target_id")

    def auto_ack(self, task_id: str, payload: dict[str, Any]) -> TaskView:
        """POST /v1/tasks/{id}/auto/ack (§22).

        Only an unacked AUTO_GRACE task may be acked; the deadline is
        computed once from the ack time; a retry returns the current
        task unchanged (never extends the deadline). Stale versions are
        rejected unless the task is already acked (idempotent replay).
        """

        self._validate_identifier("task_id", task_id)
        request = AutoAckRequest.model_validate(payload)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        if task.state is not TaskState.AUTO_GRACE:
            raise ControlPlaneError(409, "task_state_not_auto_grace")
        if task.auto_acked_at is not None:
            return _task_view(task)
        if task.state_version != request.task_state_version:
            raise ControlPlaneError(409, "stale_task_state_version")
        if task.project_id is None:
            raise ControlPlaneError(409, "task_has_no_project")
        try:
            project = self.store.get_project(task.project_id)
        except KeyError:
            raise ControlPlaneError(409, "project_not_registered") from None
        now = datetime.now(UTC)
        try:
            updated = self.store.ack_auto_grace(
                task_id,
                acked_at=now.isoformat(),
                grace_deadline=(now + timedelta(seconds=project.grace_seconds)).isoformat(),
                expected_version=request.task_state_version,
            )
        except RuntimeError as error:
            raise ControlPlaneError(409, "stale_task_state_version") from error
        return _task_view(updated)

    def auto_veto(self, task_id: str, payload: dict[str, Any]) -> TaskView:
        """POST /v1/tasks/{id}/auto/veto (§23).

        Idempotent via the durable ``request_id``: a replay of the same
        veto returns the current task view. A different request id on a
        non-AUTO task is a 409 (nothing to veto).
        """

        self._validate_identifier("task_id", task_id)
        request = AutoVetoRequest.model_validate(payload)
        self._validate_identifier("request_id", request.request_id)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        for event in self.store.audit_events(task_id):
            if event["event_type"] == "AUTO_VETOED" and (
                event["payload"].get("request_id") == request.request_id
            ):
                return _task_view(self.store.get_task(task_id))
        if task.state not in {TaskState.AUTO_PLANNED, TaskState.AUTO_GRACE}:
            raise ControlPlaneError(409, "task_state_not_auto")
        if task.state_version != request.task_state_version:
            raise ControlPlaneError(409, "stale_task_state_version")
        self._veto_auto_lifecycle(task, request_id=request.request_id, audit_reason="owner_veto")
        return _task_view(self.store.get_task(task_id))

    def auto_dispatch_now(self, task_id: str, payload: dict[str, Any]) -> DispatchTaskView:
        """POST /v1/tasks/{id}/auto/dispatch-now (§24).

        Owner explicit acceleration: skips the remaining grace window
        only. Every execution-admission gate still applies — the frozen
        decision's exact target, mode revalidation, project opt-in,
        lease truth, writer/quota/verifier discipline inside the shared
        dispatch initiator. Not a Safety Kernel bypass.
        """

        self._validate_identifier("task_id", task_id)
        request = AutoDispatchNowRequest.model_validate(payload)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        if task.state is not TaskState.AUTO_GRACE:
            raise ControlPlaneError(409, "task_state_not_auto_grace")
        if task.auto_decision_id is None:
            raise ControlPlaneError(409, "auto_decision_missing")
        if task.state_version != request.task_state_version:
            raise ControlPlaneError(409, "stale_task_state_version")
        if self.scheduling_settings.mode != "SUPERVISED_AUTO":
            raise ControlPlaneError(409, "scheduling_mode_not_supervised_auto")
        if not self.owner_initiated_execution_enabled:
            raise ControlPlaneError(403, "owner_initiated_execution_disabled")
        if task.project_id is None:
            raise ControlPlaneError(409, "task_has_no_project")
        try:
            project = self.store.get_project(task.project_id)
        except KeyError:
            raise ControlPlaneError(409, "project_not_registered") from None
        if not project.supervised_auto_allowed:
            raise ControlPlaneError(409, "project_supervised_auto_disabled")
        target = self._auto_frozen_target(task)
        if target is None:
            raise ControlPlaneError(409, "frozen_decision_missing")

        from personal_ai_orchestrator.dispatch_initiator import (
            initiate_owner_dispatch,
        )
        from personal_ai_orchestrator.supervised_auto_step import (
            supervised_auto_dispatch_request_id,
        )
        from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority

        if SwitchLeaseAuthority(self.store).has_active_lease(task_id):
            raise ControlPlaneError(409, "active_switch_lease")
        active_run = self.store.connection.execute(
            "SELECT 1 FROM runs WHERE task_id=? AND status='RUNNING' LIMIT 1",
            (task_id,),
        ).fetchone()
        if active_run is not None:
            raise ControlPlaneError(409, "active_run")

        request_id = supervised_auto_dispatch_request_id(task.auto_decision_id)
        try:
            dispatch, _created, _transitioned = initiate_owner_dispatch(
                self.store,
                self.dispatch_executor,
                task=task,
                request_id=request_id,
                task_state_version=task.state_version,
                execution_target_id=target,
                authority=DispatchAuthority.SUPERVISED_AUTO.value,
                project_provider=self.get_project,
                registry_provider=self._effective_registry,
                provider_registry_manager=self.provider_registry_manager,
                runtime_available_provider=self._runtime_available,
                execution_evidence_journal=self.execution_evidence_journal,
                expected_state=TaskState.AUTO_GRACE,
            )
        except ValueError:
            raise ControlPlaneError(409, "conflicting_dispatch_request_id") from None
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self.store._audit(
                task_id,
                "AUTO_DISPATCHED",
                {
                    "auto_decision_id": task.auto_decision_id,
                    "target": target,
                    "trigger": "dispatch_now",
                    "request_id": request_id,
                },
            )
            self.store.connection.execute("COMMIT")
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.execute("ROLLBACK")
        return self._dispatch_view(dispatch)

    def dispatch_task(self, task_id: str, payload: dict[str, Any]) -> DispatchTaskView:
        self._validate_identifier("task_id", task_id)
        request = DispatchTaskRequest.model_validate(payload)
        self._validate_identifier("request_id", request.request_id)
        self._validate_identifier("execution_target_id", request.execution_target_id)
        if not self.owner_initiated_execution_enabled:
            raise ControlPlaneError(403, "owner_initiated_execution_disabled")
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        # All validation guards → delegate the irreversible
        # reservation + validation + transition + thread-spawn
        # sequence to the extracted service helper (WP5a-1
        # commit 1 — pure refactor). ``initiate_owner_dispatch``
        # returns ``created=False`` when a previous dispatch with
        # the same ``request_id`` already exists (idempotent retry),
        # in which case we short-circuit straight to the view —
        # matching the pre-refactor behavior. ``ValueError`` from
        # ``reserve_owner_dispatch`` propagates and the handler maps
        # it to 409 ``conflicting_dispatch_request_id`` (same as the
        # pre-refactor handler).
        try:
            dispatch, _created, _transitioned = initiate_owner_dispatch(
                self.store,
                self.dispatch_executor,
                task=task,
                request_id=request.request_id,
                task_state_version=request.task_state_version,
                execution_target_id=request.execution_target_id,
                authority=DispatchAuthority.OWNER_INITIATED_EXECUTION.value,
                project_provider=self.get_project,
                registry_provider=self._effective_registry,
                provider_registry_manager=self.provider_registry_manager,
                runtime_available_provider=self._runtime_available,
                execution_evidence_journal=self.execution_evidence_journal,
            )
        except ValueError:
            raise ControlPlaneError(409, "conflicting_dispatch_request_id") from None
        return self._dispatch_view(dispatch)

    def recommend_dispatch(
        self, task_id: str, payload: dict[str, Any]
    ) -> DispatchRecommendationView:
        """Rank all dispatchable targets by the task's archived policy.

        The ranking is the same value object the rest of the scheduler
        produces, but the capability fit is uniformly 1.0 — owner dispatch
        does not yet infer task intent into required capabilities, and a
        guessed number would be less honest than a declared one.
        """

        self._validate_identifier("task_id", task_id)
        try:
            self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None

        try:
            request = DispatchRecommendationRequest.model_validate(payload or {})
        except ValidationError as error:
            raise ControlPlaneError(400, "invalid_json_schema") from error

        task = self.store.get_task(task_id)
        policy_name = request.scheduling_policy or task.scheduling_policy
        policy_name = policy_name or RoutingObjective.BALANCED.value
        try:
            policy = RoutingObjective(policy_name)
        except ValueError as error:
            raise ControlPlaneError(400, f"unknown_scheduling_policy:{policy_name}") from error

        # One ``now`` shared by the score path and the per-candidate
        # source_pressure projection, so the score and the chip agree on
        # the same instant even if the request takes ~1 ms to render.
        now = datetime.now(UTC)
        # Delegate the candidate-gathering + ranking to the host
        # application/service layer (WP5a-1 commit 1 — pure refactor).
        # The service owns the ``min_tier`` validation gate and the
        # ``TASK_MIN_TIER_INVALID`` system event so the handler stays
        # focused on view-model construction.
        recommendation, candidates, invalid_min_tier = (
            self._recommendation_service().recommend_for_task(
                task,
                policy=policy,
                now=now,
            )
        )

        candidate_views = tuple(
            self._dispatch_recommendation_candidate_view(evaluation, candidates, now=now)
            for evaluation in recommendation.evaluations
        )
        top = recommendation.top_pick
        decision_reason = (
            f"policy={policy.value} admitted {sum(1 for c in candidate_views if c.admitted)} "
            f"of {len(candidate_views)} candidates; "
            f"hard eligibility and quota admission gates ran before scoring"
        )
        return DispatchRecommendationView(
            task_id=task_id,
            scheduling_policy=policy.value,
            candidates=candidate_views,
            top_pick=top.execution_target_id if top is not None else None,
            decision_reason=decision_reason,
        )

    @staticmethod
    def _dispatch_recommendation_candidate_view(
        evaluation,
        candidates: list[DispatchCandidateInput],
        *,
        now: datetime,
    ) -> DispatchRecommendationCandidate:
        inputs = next(
            (c for c in candidates if c.execution_target_id == evaluation.execution_target_id),
            None,
        )
        return DispatchRecommendationCandidate(
            execution_target_id=evaluation.execution_target_id,
            model_sku_id=evaluation.model_sku_id,
            eligible=evaluation.eligible,
            admitted=evaluation.admitted,
            score=evaluation.score,
            headroom_mean=(
                sum(inputs.remaining_fractions) / len(inputs.remaining_fractions)
                if inputs and inputs.remaining_fractions
                else None
            ),
            # M1 WP3: binding-window minimum. Reuses the same data the
            # scheduler's ``minimum_remaining_fraction`` reads from
            # ``QuotaSnapshot``. ``None`` propagates the "missing
            # data" signal end-to-end.
            headroom_min=(
                min(inputs.remaining_fractions) if inputs and inputs.remaining_fractions else None
            ),
            evidence_fresh=bool(inputs and inputs.evidence_observed_at is not None),
            runtime_available=bool(inputs and inputs.runtime_available),
            verified=bool(inputs and inputs.verified),
            execution_verified_stale=bool(inputs and inputs.verified_stale),
            quota_state=(inputs.availability_state.value if inputs else None),
            source_pressure=(
                source_pressure_for(inputs.windows, now=now) if inputs is not None else None
            ).value,
            score_components=tuple(
                DispatchRecommendationScoreComponent(
                    name=component.name,
                    # M1 WP3 fix (F4): wire shape carries the raw
                    # unweighted ``value`` plus the per-row ``weight``.
                    # The UI multiplies them at display time so the
                    # ``Σ weight × value == score`` identity is
                    # visible end-to-end (and a tuning commit does
                    # not have to push a new ``contribution``).
                    value=(
                        float(component.value) if isinstance(component.value, (int, float)) else 0.0
                    ),
                    weight=component.weight,
                )
                for component in evaluation.score_components
            ),
            reasons=tuple(evaluation.reasons),
            tier=(inputs.tier.value if inputs is not None and inputs.tier is not None else None),
            tier_match_reason=(inputs.tier_match_reason if inputs is not None else None),
        )

    def owner_execution_settings(self) -> OwnerExecutionSettingsView:
        return OwnerExecutionSettingsView(
            owner_initiated_execution_enabled=self.owner_execution.enabled,
            production_active=self.active_status().production_active,
        )

    def update_owner_execution_settings(
        self, payload: dict[str, Any]
    ) -> OwnerExecutionSettingsView:
        request = OwnerExecutionSettingsUpdateRequest.model_validate(payload)
        self.owner_execution.set_enabled(request.owner_initiated_execution_enabled)
        return self.owner_execution_settings()

    def get_owner_dispatch(self, request_id: str) -> DispatchTaskView:
        self._validate_identifier("request_id", request_id)
        try:
            dispatch = self.store.get_owner_dispatch_by_request_id(request_id)
        except KeyError:
            raise ControlPlaneError(404, "dispatch_not_found") from None
        return self._dispatch_view(dispatch)

    def _dispatch_view(self, dispatch: OwnerDispatchRecord) -> DispatchTaskView:
        task = self.store.get_task(dispatch.task_id)
        return DispatchTaskView(
            dispatch_id=dispatch.dispatch_id,
            task=_task_view(task),
            request_id=dispatch.request_id,
            authority=dispatch.authority,
            execution_target_id=dispatch.execution_target_id,
            status=dispatch.status.value,
            accepted=dispatch.failure_code is None,
            reason=dispatch.failure_reason or "owner initiated execution dispatch reserved",
            failure_code=dispatch.failure_code,
        )

    def verification_report(self, task_id: str) -> VerificationReportView:
        try:
            record = self.store.get_task(task_id)
        except KeyError:
            raise ControlPlaneError(404, "task_not_found") from None
        evidence_id: str | None = None
        failure_reason: str | None = None
        prefix = "deterministic verification passed: "
        for event in self.store.audit_events(task_id):
            if event["event_type"] != "TASK_STATE_CHANGED":
                continue
            payload = event["payload"]
            reason = payload.get("reason")
            if payload.get("to") == TaskState.VERIFIED.value and isinstance(reason, str):
                if reason.startswith(prefix):
                    evidence_id = reason[len(prefix) :]
            if (
                payload.get("from") == TaskState.VERIFYING.value
                and payload.get("to") == TaskState.BLOCKED.value
            ):
                failure_reason = reason if isinstance(reason, str) else None
        result: VerificationResult | None = None
        if evidence_id is not None and self.verification_journal is not None:
            result = self.verification_journal.load(evidence_id)
        if record.state is TaskState.VERIFIED or evidence_id is not None:
            if evidence_id is None:
                status = "VERIFIED_EVIDENCE_MISSING"
            elif result is None and self.verification_journal is not None:
                status = "VERIFIED_EVIDENCE_UNAVAILABLE"
            else:
                status = "VERIFIED"
        elif failure_reason is not None:
            status = "FAILED_VERIFICATION"
        elif record.state is TaskState.VERIFYING:
            status = "IN_PROGRESS"
        else:
            status = "NOT_VERIFIED"
        return VerificationReportView(
            task_id=task_id,
            task_state=record.state.value,
            status=status,
            evidence_id=evidence_id,
            failure_reason=failure_reason,
            result=result,
        )

    def routing_decision(self, task_id: str) -> RoutingDecisionView:
        self.get_task(task_id)
        row = self.store.connection.execute(
            "SELECT * FROM routing_decisions WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneError(404, "routing_decision_not_found")
        return RoutingDecisionView(
            task_id=row["task_id"],
            decision_id=row["decision_id"],
            request_id=row["request_id"],
            created_at=row["created_at"],
            decision=json.loads(row["payload_json"]),
        )

    @staticmethod
    def _event_summary(event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "TASK_SUBMITTED":
            return "task submitted"
        if event_type == "TASK_STATE_CHANGED":
            before = payload.get("from", "UNKNOWN")
            after = payload.get("to", "UNKNOWN")
            reason = payload.get("reason")
            suffix = f": {reason}" if isinstance(reason, str) and reason else ""
            return f"{before} -> {after}{suffix}"
        if event_type == "ROUTING_DECISION_RECORDED":
            return f"routing decision {payload.get('decision_id', 'UNKNOWN')} recorded"
        if event_type == "RUN_STARTED":
            return f"worker {payload.get('worker_id', 'UNKNOWN')} started"
        if event_type == "RUN_FINISHED":
            run_id = payload.get("run_id", "UNKNOWN")
            status = payload.get("status", "UNKNOWN")
            return f"run {run_id} finished as {status}"
        if event_type == "WORKSPACE_REGISTERED":
            return "workspace registered"
        if event_type == "WRITER_ACQUIRED":
            return "writer lock acquired"
        if event_type == "WRITER_RELEASED":
            return "writer lock released"
        return event_type.lower().replace("_", " ")

    def _activity_events(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ActivityEventView, ...]:
        bounded = max(1, min(limit, MAX_LIST_LIMIT))
        if task_id is None:
            rows = self.store.connection.execute(
                """
                SELECT task_id,event_type,payload_json,created_at
                FROM audit_events
                ORDER BY audit_id DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        else:
            rows = self.store.connection.execute(
                """
                SELECT task_id,event_type,payload_json,created_at
                FROM audit_events
                WHERE task_id=?
                ORDER BY audit_id
                LIMIT ?
                """,
                (task_id, bounded),
            ).fetchall()
        events: list[ActivityEventView] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            events.append(
                ActivityEventView(
                    event_type=row["event_type"],
                    task_id=row["task_id"],
                    created_at=row["created_at"],
                    summary=self._event_summary(row["event_type"], payload),
                )
            )
        return tuple(events)

    def _today_prefix(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _task_trend(self) -> tuple[TaskTrendBucketView, ...]:
        rows = self.store.connection.execute(
            """
            SELECT substr(created_at, 1, 13) AS bucket,
                   SUM(CASE WHEN event_type='TASK_SUBMITTED' THEN 1 ELSE 0 END) AS submitted,
                   SUM(CASE
                       WHEN event_type='TASK_STATE_CHANGED'
                        AND payload_json LIKE '%"to":"COMPLETED"%'
                       THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE
                       WHEN event_type='TASK_STATE_CHANGED'
                        AND payload_json LIKE '%"to":"BLOCKED"%'
                       THEN 1 ELSE 0 END) AS blocked
            FROM audit_events
            WHERE event_type IN ('TASK_SUBMITTED', 'TASK_STATE_CHANGED')
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT 24
            """
        ).fetchall()
        return tuple(
            TaskTrendBucketView(
                bucket_start=f"{row['bucket']}:00:00Z",
                submitted=int(row["submitted"] or 0),
                completed=int(row["completed"] or 0),
                blocked=int(row["blocked"] or 0),
            )
            for row in reversed(rows)
        )

    def _task_state_distribution(self, by_state: dict[str, int]) -> tuple[TaskStateSliceView, ...]:
        return tuple(
            TaskStateSliceView(state=state, count=int(count))
            for state, count in sorted(by_state.items())
            if int(count) > 0
        )

    def _quota_warning_count(self, providers: ProviderHealthListView) -> int:
        warnings = 0
        for provider in providers.providers:
            for pool in provider.quota_pools:
                if pool.confidence == "UNKNOWN":
                    warnings += 1
                if pool.state in {"EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN"}:
                    warnings += 1
                for window in pool.windows:
                    if window.confidence == "UNKNOWN":
                        warnings += 1
                    if (
                        window.confidence in {"EXACT", "ESTIMATED"}
                        and window.remaining_fraction is not None
                        and window.remaining_fraction <= 0.15
                    ):
                        warnings += 1
        return warnings

    def _record_quota_history(self, providers: ProviderHealthListView) -> None:
        for provider in providers.providers:
            for pool in provider.quota_pools:
                observed = pool.observed_at
                for window in pool.windows:
                    observed_at = observed or window.reset_at
                    if observed_at is None:
                        continue
                    try:
                        self.store.record_quota_observation(
                            provider_id=provider.provider_id,
                            quota_pool_id=pool.quota_pool_id,
                            window_id=window.window_id,
                            observed_at=observed_at,
                            remaining_fraction=window.remaining_fraction,
                            confidence=window.confidence,
                            measurement_source=pool.measurement_source_type,
                            reset_at=window.reset_at,
                            state=window.state,
                        )
                    except Exception:
                        pass

    def quota_history(self) -> QuotaHistoryView:
        rows = self.store.quota_observation_history(limit=200)
        return QuotaHistoryView(
            observations=tuple(
                QuotaObservationView(
                    provider_id=row["provider_id"],
                    quota_pool_id=row["quota_pool_id"],
                    window_id=row["window_id"],
                    observed_at=row["observed_at"],
                    remaining_fraction=row["remaining_fraction"],
                    confidence=row["confidence"],
                    measurement_source=row["measurement_source"],
                    reset_at=row["reset_at"],
                    state=row["state"],
                )
                for row in rows
            ),
            retention_limit=500,
        )

    def _risk_items(
        self,
        *,
        blockers: list[str],
        providers: ProviderHealthListView,
        unavailable_projects: list[ProjectView],
    ) -> tuple[RiskItemView, ...]:
        risks: list[RiskItemView] = []
        if unavailable_projects:
            risks.append(
                RiskItemView(
                    title=f"{len(unavailable_projects)} project(s) unavailable",
                    detail="Tasks for missing or invalid projects cannot be dispatched.",
                    severity="BLOCKED",
                    destination="projects",
                    raw_code="PROJECT_UNAVAILABLE",
                    count=len(unavailable_projects),
                )
            )
        unverified_targets = [
            target
            for provider in providers.providers
            for target in provider.execution_targets
            if target.enabled and not target.execution_verified
        ]
        if unverified_targets:
            risks.append(
                RiskItemView(
                    title=f"{len(unverified_targets)} execution target(s) are not verified",
                    detail="Unverified targets cannot run real owner-dispatched tasks.",
                    severity="WARNING",
                    destination="models_providers",
                    raw_code="EXECUTION_TARGET_UNVERIFIED",
                    count=len(unverified_targets),
                )
            )
        exhausted = [
            pool
            for provider in providers.providers
            for pool in provider.quota_pools
            if pool.state in {"EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN"}
        ]
        if exhausted:
            risks.append(
                RiskItemView(
                    title=f"{len(exhausted)} quota pool(s) are blocked",
                    detail="Some models may be unavailable until quota recovers.",
                    severity="BLOCKED",
                    destination="quota",
                    raw_code="QUOTA_EXHAUSTED",
                    count=len(exhausted),
                )
            )
        unknown = [
            pool
            for provider in providers.providers
            for pool in provider.quota_pools
            if pool.confidence == "UNKNOWN"
        ]
        if unknown:
            risks.append(
                RiskItemView(
                    title=f"{len(unknown)} quota pool(s) have unknown limits",
                    detail="Unknown quota is valid, but the UI cannot show reliable percentages.",
                    severity="UNKNOWN",
                    destination="quota",
                    raw_code="QUOTA_UNKNOWN",
                    count=len(unknown),
                )
            )
        for raw in blockers:
            if len(risks) >= 4:
                break
            if "owner approval" in raw:
                risks.append(
                    RiskItemView(
                        title="Production automation is not authorized",
                        detail="Owner approval is still required before ACTIVE mode.",
                        severity="WARNING",
                        destination="settings",
                        raw_code=raw,
                    )
                )
            elif "Shadow evidence" in raw:
                risks.append(
                    RiskItemView(
                        title="Shadow evidence is still incomplete",
                        detail=(
                            "Production routing remains disabled until acceptance evidence "
                            "is complete."
                        ),
                        severity="WARNING",
                        destination="verification",
                        raw_code=raw,
                    )
                )
        return tuple(risks[:4])

    def dashboard_summary(self) -> DashboardSummaryView:
        task_rows = self.store.connection.execute(
            "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"
        ).fetchall()
        by_state = {row["state"]: row["n"] for row in task_rows}
        blockers = list(self.active_status().blocking_reasons)
        providers = self.providers()
        self._record_quota_history(providers)
        unavailable_projects = [
            project
            for project in self.list_projects().projects
            if project.storage_availability != ProjectAvailability.ONLINE.value
        ]
        for project in unavailable_projects:
            blockers.append(f"{project.display_name} project {project.storage_availability}")
        blocked_count = int(by_state.get(TaskState.BLOCKED.value, 0))
        if blocked_count:
            blockers.insert(0, f"{blocked_count} task(s) blocked")
        for provider in providers.providers:
            if not provider.quota_pools and not provider.execution_targets:
                continue
            unknown_pools = [
                pool.name for pool in provider.quota_pools if pool.confidence == "UNKNOWN"
            ]
            if unknown_pools:
                blockers.append(f"{provider.display_name} quota UNKNOWN")
        return DashboardSummaryView(
            connection=self.health(),
            counts=DashboardCountsView(
                running=int(by_state.get(TaskState.RUNNING.value, 0)),
                ready=int(
                    by_state.get(TaskState.READY.value, 0)
                    + by_state.get(TaskState.SUBMITTED.value, 0)
                ),
                blocked=blocked_count,
                verifying=int(by_state.get(TaskState.VERIFYING.value, 0)),
                verified=int(by_state.get(TaskState.VERIFIED.value, 0)),
                completed=int(by_state.get(TaskState.COMPLETED.value, 0)),
                total=sum(int(value) for value in by_state.values()),
            ),
            recent_tasks=self.list_tasks(limit=10).tasks,
            projects=self.list_projects(),
            basic_info=DashboardBasicInfoView(
                daemon_connection="CONNECTED",
                registered_projects=len(self.list_projects().projects),
                discovered_providers=len(providers.providers),
                available_execution_targets=sum(
                    1
                    for provider in providers.providers
                    for target in provider.execution_targets
                    if target.enabled and target.runtime_available is True
                ),
                running_tasks=int(by_state.get(TaskState.RUNNING.value, 0)),
                tasks_today=int(
                    self.store.connection.execute(
                        "SELECT COUNT(*) AS n FROM tasks WHERE created_at LIKE ?",
                        (f"{self._today_prefix()}%",),
                    ).fetchone()["n"]
                ),
                routing_decisions_today=int(
                    self.store.connection.execute(
                        "SELECT COUNT(*) AS n FROM routing_decisions WHERE created_at LIKE ?",
                        (f"{self._today_prefix()}%",),
                    ).fetchone()["n"]
                ),
                quota_warning_count=self._quota_warning_count(providers),
                last_refresh_sync=datetime_now_iso(),
            ),
            task_trend=self._task_trend(),
            task_state_distribution=self._task_state_distribution(by_state),
            risks=self._risk_items(
                blockers=blockers,
                providers=providers,
                unavailable_projects=unavailable_projects,
            ),
            quota_history=self.quota_history(),
            providers=providers,
            active_status=self.active_status(),
            important_blockers=tuple(blockers),
            recent_events=self._activity_events(limit=20),
        )

    def _workspace_view(self, task_id: str) -> WorkspaceView | None:
        row = self.store.connection.execute(
            "SELECT * FROM workspaces WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkspaceView(
            task_id=row["task_id"],
            project_id=row["project_id"],
            repo_path=row["repo_path"],
            worktree_path=row["worktree_path"],
            branch=row["branch"],
            base_sha=row["base_sha"],
            working_subpath=row["working_subpath"],
            writer_locked=row["writer_token"] is not None,
        )

    def task_detail(self, task_id: str) -> TaskDetailView:
        task = self.get_task(task_id)
        try:
            routing = self.routing_decision(task_id)
        except ControlPlaneError as error:
            if error.status != 404:
                raise
            routing = None
        return TaskDetailView(
            task=task,
            runs=self.task_runs(task_id).runs,
            routing=routing,
            verification=self.verification_report(task_id),
            approvals=self.approvals_for_task(task_id),
            workspace=self._workspace_view(task_id),
            events=self._activity_events(task_id=task_id, limit=100),
        )

    def _quota_pool_view(self, pool) -> QuotaPoolHealthView:
        snapshot = pool.snapshot
        windows = tuple(
            QuotaWindowHealthView(
                window_id=window.window_id,
                window_kind=window.window_kind.value,
                state=window.state.value,
                confidence=window.confidence.value,
                remaining_fraction=window.remaining_fraction,
                reset_at=window.reset_at.isoformat() if window.reset_at else None,
            )
            for window in snapshot.windows
        )
        observed = snapshot.observed_at or snapshot.source.observed_at
        return QuotaPoolHealthView(
            quota_pool_id=pool.id,
            name=pool.name,
            plan_id=pool.plan_id,
            state=snapshot.state.value,
            confidence=snapshot.confidence.value,
            measurement_source_type=snapshot.source.source_type.value,
            observed_at=observed.isoformat() if observed else None,
            windows=windows,
        )

    def _execution_target_view(self, target) -> ExecutionTargetHealthView:
        observed: ObservedAvailabilityView | None = None
        if self.quota_availability_journal is not None:
            evidence = self.quota_availability_journal.load(target.id)
            if evidence is not None:
                observed = ObservedAvailabilityView(
                    state=evidence.state.value,
                    observed_at=evidence.observed_at.isoformat(),
                    measurement_source=evidence.measurement_source.value,
                    confidence=evidence.confidence.value,
                    sanitized_reason_code=evidence.sanitized_reason_code,
                    consecutive_failures=evidence.consecutive_failures,
                )
        # Historical verification remains a diagnostic surface, while the
        # launch bit below is derived from the same latest-row + age rule as
        # owner dispatch. This prevents age-expired or demoted history from
        # being presented as currently actionable.
        verification = project_execution_target_verification(
            self._effective_registry(),
            execution_target_id=target.id,
            execution_evidence_journal=self.execution_evidence_journal,
            now=datetime.now(UTC),
        )
        execution_verified = verification.historical_verified
        execution_verified_stale = verification.verified_stale
        runtime_available = (
            self.runtime_availability.get(target.id)
            if target.id in self.runtime_availability
            else self._runtime_available(target.id)
        )
        launch_authorized = bool(
            target.enabled and verification.launch_verified and runtime_available
        )
        # M1 WP2: classify the target against the host-owned tier
        # table. ``None`` means the table is not wired (ad-hoc CLI) or
        # could not classify the target — both surface as ``tier=None``
        # on the view-model.
        tier_value: str | None = None
        tier_match_reason: str | None = None
        if self.tier_table is not None:
            entry, tier_match_reason = self.tier_table.lookup(target.id)
            tier_value = entry.tier.value
        return ExecutionTargetHealthView(
            execution_target_id=target.id,
            model_sku_id=target.model_sku_id,
            runtime_id=target.runtime_id,
            enabled=target.enabled,
            execution_verified=execution_verified,
            execution_verified_stale=execution_verified_stale,
            launch_authorized=launch_authorized,
            execution_verification_observed_at=(
                verification.verified_observed_at.isoformat()
                if verification.verified_observed_at is not None
                else None
            ),
            runtime_available=runtime_available,
            observed_availability=observed,
            tier=tier_value,
            tier_match_reason=tier_match_reason,
            # M1 WP4: surface the family-level ``auth_kind`` and
            # ``pool_kind`` so the Resources page can group targets
            # by family without round-tripping through
            # ``provider_discovery``. The defaults keep the
            # pre-WP4 behaviour for ad-hoc CLI invocations
            # (no registry → ``env`` + ``windowed``).
            auth_kind=self._auth_kind_for_target(target),
            pool_kind=self._pool_kind_for_target(target),
        )

    def providers(self) -> ProviderHealthListView:
        views: list[ProviderHealthView] = []
        plans_by_account: dict[str, list[str]] = {}
        # P4.2.4-A: prefer the dynamic registry owned by the
        # ProviderRegistryManager when one is configured. This is how
        # the bundled product daemon surfaces real GLM / MiniMax CN
        # discovery results even though the static runtime.json still
        # carries the legacy empty-bootstrap snapshot.
        effective_registry = (
            self.provider_registry_manager.registry()
            if self.provider_registry_manager is not None
            else self.registry
        )
        for plan in effective_registry.plans.values():
            plans_by_account.setdefault(plan.account_id, []).append(plan.id)
        evidence_by_provider = self._evidence_by_provider()
        for provider_id, provider in sorted(effective_registry.providers.items()):
            account_ids = [
                account.id
                for account in effective_registry.accounts.values()
                if account.provider_id == provider_id
            ]
            plan_ids = {
                plan_id
                for account_id in account_ids
                for plan_id in plans_by_account.get(account_id, [])
            }
            pools = tuple(
                self._quota_pool_view(pool)
                for pool_id, pool in sorted(effective_registry.quota_pools.items())
                if pool.plan_id in plan_ids
            )
            targets = tuple(
                self._execution_target_view(target)
                for target_id, target in sorted(effective_registry.execution_targets.items())
                if effective_registry.models[target.model_sku_id].provider_id == provider_id
            )
            evidence = evidence_by_provider.get(provider_id)
            connection = (
                self.provider_registry_manager.connection_registry().connections.get(provider_id)
                if self.provider_registry_manager is not None
                else None
            )
            views.append(
                ProviderHealthView(
                    provider_id=provider_id,
                    display_name=provider.display_name,
                    account_count=len(account_ids),
                    quota_pools=pools,
                    execution_targets=targets,
                    evidence_source=evidence.evidence_source if evidence else None,
                    auth_status=evidence.auth_status if evidence else None,
                    execution_status=evidence.execution_status if evidence else None,
                    connection_state=(
                        connection.connection_state.value if connection is not None else None
                    ),
                    auth_state=connection.auth_state.value if connection is not None else None,
                    runtime_state=(
                        connection.runtime_state.value if connection is not None else None
                    ),
                    plan_surface=connection.plan_surface if connection is not None else None,
                    region=connection.region if connection is not None else None,
                    last_checked=(
                        evidence.observed_at.isoformat()
                        if evidence is not None and evidence.observed_at is not None
                        else None
                    ),
                    # M1 WP4: carry the family pool_kind on the
                    # provider-level view so the Resources page can
                    # group by it without iterating targets.
                    pool_kind=self._pool_kind_for_provider(provider_id),
                )
            )
        return ProviderHealthListView(providers=tuple(views))

    @staticmethod
    def _connection_view(connection) -> ProviderConnectionView:
        return ProviderConnectionView(
            provider_id=connection.provider_id,
            display_name=connection.display_name,
            connection_state=connection.connection_state.value,
            auth_state=connection.auth_state.value,
            execution_verified=connection.execution_verified,
            runtime_state=connection.runtime_state.value,
            credential_reference_type=connection.credential_reference_type.value,
            region=connection.region,
            plan_surface=connection.plan_surface,
            model_skus=connection.model_skus,
            connected_at=connection.connected_at.isoformat(),
            last_validated_at=(
                connection.last_validated_at.isoformat()
                if connection.last_validated_at is not None
                else None
            ),
            last_reason_code=connection.last_reason_code,
        )

    def provider_connections(self) -> ProviderConnectionListView:
        if self.provider_registry_manager is None:
            return ProviderConnectionListView(connected=(), available_to_add=())
        projection = self.provider_registry_manager.connection_projection()
        return ProviderConnectionListView(
            connected=tuple(self._connection_view(item) for item in projection.connected),
            available_to_add=tuple(
                AvailableProviderView.model_validate(item) for item in projection.available_to_add
            ),
            import_candidates=tuple(
                self._import_candidate_view(item) for item in projection.import_candidates
            ),
        )

    @staticmethod
    def _import_candidate_view(candidate) -> ProviderImportCandidateView:
        return ProviderImportCandidateView(
            provider_id=candidate.provider_id,
            display_name=candidate.display_name,
            region=candidate.region,
            plan_surface=candidate.plan_surface,
            model_skus=candidate.model_skus,
            execution_verified=candidate.execution_verified,
            auth_state=candidate.auth_state.value,
            credential_reference_type=candidate.credential_reference_type.value,
            evidence=tuple(
                ImportEvidenceItemView(
                    kind=item.kind.value,
                    detail=item.detail,
                    observed_at=item.observed_at.isoformat() if item.observed_at else None,
                )
                for item in candidate.evidence
            ),
        )

    def connect_provider(self, payload: dict[str, Any]) -> ProviderConnectionView:
        request = ConnectProviderRequest.model_validate(payload)
        self._validate_identifier("provider_id", request.provider_id)
        if self.provider_registry_manager is None:
            raise ControlPlaneError(503, "provider_registry_manager_not_configured")
        try:
            connection = self.provider_registry_manager.connect_provider(request.provider_id)
        except LookupError as error:
            code = str(error) or "provider_not_catalog_discovered"
            raise ControlPlaneError(404, code) from None
        return self._connection_view(connection)

    def disconnect_provider(
        self,
        provider_id: str,
        payload: dict[str, Any],
    ) -> ProviderConnectionView:
        self._validate_identifier("provider_id", provider_id)
        request = DisconnectProviderRequest.model_validate(payload)
        if not request.confirm:
            raise ControlPlaneError(400, "disconnect_confirmation_required")
        if self.provider_registry_manager is None:
            raise ControlPlaneError(503, "provider_registry_manager_not_configured")
        try:
            connection = self.provider_registry_manager.disconnect_provider(provider_id)
        except LookupError:
            raise ControlPlaneError(404, "provider_not_connected") from None
        return self._connection_view(connection)

    def _evidence_by_provider(self) -> dict[str, Any]:
        """Map ``provider_id`` to the latest sanitized discovery record.

        Returns an empty mapping when no manager is attached. This is
        intentionally permissive: the Control API must continue to
        work even before P4.2.4-A discovery completes for the very
        first time.
        """

        if self.provider_registry_manager is None:
            return {}
        result = self.provider_registry_manager.last_discovery_result()
        if result is None:
            return {}
        return {record.provider_id: record for record in result.providers}

    def provider_discovery_status(self) -> ProviderDiscoveryStatusView:
        if self.provider_registry_manager is None:
            return ProviderDiscoveryStatusView(
                discovery_state="PENDING",
                provider_count=0,
                configured_family_count=0,
                catalog_discovered_provider_count=0,
                credential_evidence_provider_count=0,
                execution_target_count=0,
            )
        status = self.provider_registry_manager.status()
        return ProviderDiscoveryStatusView(
            discovery_state=status.discovery_state,
            last_discovered_at=status.last_discovered_at,
            provider_count=status.provider_count,
            configured_family_count=status.configured_family_count,
            catalog_discovered_provider_count=status.catalog_discovered_provider_count,
            credential_evidence_provider_count=status.credential_evidence_provider_count,
            execution_target_count=status.execution_target_count,
            last_error_code=status.last_error_code,
            catalog_snapshot_id=status.catalog_snapshot_id,
            source_method=status.source_method,
        )

    def refresh_providers(self) -> ProviderDiscoveryStatusView:
        """Run a fresh discovery cycle and return the new status.

        Concurrency: the manager coalesces overlapping calls so the
        OpenCode CLI is invoked at most once per refresh.
        """

        if self.provider_registry_manager is None:
            return ProviderDiscoveryStatusView(
                discovery_state="UNAVAILABLE",
                provider_count=0,
                configured_family_count=0,
                catalog_discovered_provider_count=0,
                credential_evidence_provider_count=0,
                execution_target_count=0,
                last_error_code="PROVIDER_REGISTRY_MANAGER_NOT_CONFIGURED",
            )
        status = self.provider_registry_manager.refresh()
        if status is None:
            status = self.provider_registry_manager.status()
        return ProviderDiscoveryStatusView(
            discovery_state=status.discovery_state,
            last_discovered_at=status.last_discovered_at,
            provider_count=status.provider_count,
            configured_family_count=status.configured_family_count,
            catalog_discovered_provider_count=status.catalog_discovered_provider_count,
            credential_evidence_provider_count=status.credential_evidence_provider_count,
            execution_target_count=status.execution_target_count,
            last_error_code=status.last_error_code,
            catalog_snapshot_id=status.catalog_snapshot_id,
            source_method=status.source_method,
        )

    # ------------------------------------------------------------------
    # Quota (connection-based)
    # ------------------------------------------------------------------

    @staticmethod
    def _quota_pool_views_from_snapshot(
        observation: QuotaProviderObservation,
    ) -> tuple[QuotaPoolHealthView, ...]:
        """Render only values the provider actually reported.

        A window the collector could not read stays UNKNOWN with a null
        ``remaining_fraction`` so the Dashboard cannot draw a fake bar.
        """

        snapshot = observation.snapshot
        if snapshot is None:
            return ()
        observed_at = observation.observed_at
        return (
            QuotaPoolHealthView(
                quota_pool_id=snapshot.quota_pool_id or observation.provider_id,
                name=snapshot.quota_pool_id or observation.provider_id,
                plan_id=snapshot.plan_id or observation.plan_id or "UNKNOWN",
                state=snapshot.state.value,
                confidence=snapshot.confidence.value,
                measurement_source_type=snapshot.source.source_type.value,
                observed_at=observed_at,
                windows=tuple(
                    QuotaWindowHealthView(
                        window_id=window.window_id,
                        window_kind=window.window_kind.value,
                        state=window.state.value,
                        confidence=window.confidence.value,
                        remaining_fraction=(
                            window.remaining_fraction
                            if window.confidence.value in {"EXACT", "ESTIMATED"}
                            else None
                        ),
                        reset_at=(
                            window.reset_at.isoformat() if window.reset_at is not None else None
                        ),
                    )
                    for window in snapshot.windows
                ),
            ),
        )

    @staticmethod
    def _burn_view_for_window(window, *, now: datetime) -> QuotaBurnView | None:
        """Render the M1 WP1 burn view-model for one plan window.

        Returns ``None`` when the window has no ``reset_at`` (UNMETERED
        short-circuits inside `` ``burn``); the UI renders no chip in
        that case rather than drawing an empty one.
        """

        if window.reset_at is None:
            return None
        assessment, inferred = window.burn(now=now)
        return QuotaBurnView(
            expected_used_fraction=assessment.expected_used_fraction,
            actual_used_fraction=assessment.actual_used_fraction,
            deviation=assessment.deviation,
            remaining_fraction=assessment.remaining_fraction,
            seconds_to_reset=assessment.seconds_to_reset,
            pressure=assessment.pressure.value,
            pressure_score=assessment.pressure_score,
            window_start_inferred=inferred,
        )

    @staticmethod
    def _plan_view(observation: QuotaProviderObservation, *, now: datetime) -> QuotaPlanView | None:
        """Render the shared-plan projection the Quota page leads with.

        Returns ``None`` only when there is no plan evidence at all. A plan
        whose *balance* is UNKNOWN still renders, because the per-model
        consumption and equivalents beside it are real, and dropping the whole
        card to hide the one unknown reports less than we know.
        ``now`` is injected by the caller so all per-window burn figures and
        the card-level ``source_pressure`` agree on the same instant.
        """

        projection = observation.projection
        if projection is None:
            return None

        def iso(value) -> str | None:
            return value.isoformat() if value is not None else None

        windows = tuple(
            QuotaPlanWindowView(
                window_id=window.window_id,
                window_kind=window.window_kind.value,
                state=window.state.value,
                confidence=window.confidence.value,
                # A window we could not read draws no bar.
                remaining_fraction=(
                    window.remaining_fraction
                    if window.confidence is not EvidenceConfidence.UNKNOWN
                    else None
                ),
                remaining_units=window.remaining_units,
                total_units=window.total_units,
                unit=window.unit,
                reset_at=iso(window.reset_at),
                burn=ControlPlaneService._burn_view_for_window(window, now=now),
            )
            for window in projection.windows
        )
        binding = projection.binding_window
        return QuotaPlanView(
            provider_id=projection.plan.provider_id,
            plan_id=projection.plan.plan_id,
            display_name=(observation.plan_display_name or projection.plan.display_name),
            plan_level=projection.plan.plan_level,
            quota_semantics=projection.plan.quota_semantics.value,
            pool_id=projection.pool.pool_id,
            resource_kind=projection.pool.resource_kind.value,
            shared_across_models=projection.pool.shared_across_models,
            unit_kind=projection.pool.unit_kind.value,
            covered_model_ids=projection.covered_model_ids(),
            state=projection.state.value,
            confidence=projection.confidence.value,
            observed_at=iso(projection.plan.observed_at),
            unknown_reason=projection.unknown_reason,
            active_workload_scope=projection.active_workload_scope.value,
            workload_scope_notes=projection.workload_scope_notes,
            windows=windows,
            binding_window=BindingWindowView(
                window_id=binding.window_id,
                window_kind=(
                    binding.window_kind.value if binding.window_kind is not None else None
                ),
                remaining_fraction=binding.remaining_fraction,
                reset_at=iso(binding.reset_at),
                seconds_until_reset=binding.seconds_until_reset,
                reason=binding.reason.value,
                confidence=binding.confidence.value,
            ),
            model_consumption=tuple(
                ModelConsumptionView(
                    model_id=item.model_id,
                    consumed_units=item.consumed_units,
                    unit_kind=item.unit_kind.value,
                    provider_unit_label=item.provider_unit_label,
                    call_count=item.call_count,
                    period_start=iso(item.period_start),
                    period_end=iso(item.period_end),
                    confidence=item.confidence.value,
                    measurement_source=item.measurement_source.value,
                )
                for item in projection.model_consumption
            ),
            model_equivalents=tuple(
                ModelEquivalentWindowView(
                    scope_id=item.scope_id,
                    scope_kind=item.scope_kind.value,
                    workload_scope=item.workload_scope.value,
                    window_id=item.window_id,
                    remaining_fraction=item.remaining_fraction,
                    remaining_units=item.remaining_units,
                    total_units=item.total_units,
                    unit_kind=item.unit_kind.value,
                    confidence=item.confidence.value,
                )
                for item in projection.model_equivalents
            ),
            equivalent_capacity=ControlPlaneService._equivalent_capacity_views(projection),
        )

    @staticmethod
    def _equivalent_capacity_views(
        projection: PlanQuotaProjection,
    ) -> tuple[EquivalentCapacityView, ...]:
        """Advisory capacity estimates for models covered by this pool.

        The orchestrator does not yet record per-task provider consumption in
        the pool's own unit, so today every model truthfully reports
        ``INSUFFICIENT_SAMPLE`` rather than a number. That is the designed
        outcome, not a stub: the estimator refuses to divide a credit-metered
        remainder by token-metered history, and the UI renders the absence as
        "历史数据不足，暂不估算" rather than as zero.
        """

        binding = projection.binding_window
        if binding.window_id is None:
            return ()
        window = next(
            (item for item in projection.windows if item.window_id == binding.window_id),
            None,
        )
        remaining_units = window.remaining_units if window is not None else None
        views: list[EquivalentCapacityView] = []
        for model_id in projection.covered_model_ids():
            result = estimate_equivalent_capacity(
                provider_id=projection.plan.provider_id,
                plan_id=projection.plan.plan_id,
                pool_id=projection.pool.pool_id,
                window_id=binding.window_id,
                model_id=model_id,
                remaining_units=remaining_units,
                remaining_unit_kind=projection.pool.unit_kind,
                samples=[],
                observed_at=projection.plan.observed_at,
            )
            if isinstance(result, EquivalentCapacityEstimate):
                views.append(
                    EquivalentCapacityView(
                        model_id=model_id,
                        window_id=result.window_id,
                        task_class=result.task_class,
                        estimated_remaining_tasks=result.estimated_remaining_tasks,
                        sample_count=result.sample_count,
                        small_sample=result.small_sample,
                        confidence=result.confidence.value,
                    )
                )
            else:
                views.append(
                    EquivalentCapacityView(
                        model_id=model_id,
                        window_id=result.window_id,
                        task_class=result.task_class,
                        sample_count=result.sample_count,
                        unavailable_reason=result.reason.value,
                    )
                )
        return tuple(views)

    def _connected_connections(self) -> dict[str, Any]:
        """Provider connection records the owner has explicitly registered."""

        if self.provider_registry_manager is None:
            return {}
        registry = self.provider_registry_manager.connection_registry()
        return {
            provider_id: connection
            for provider_id, connection in registry.connections.items()
            if connection.scheduler_connected
        }

    # M1 WP4: target → family ``auth_kind`` / ``pool_kind`` helpers.
    # The default fallback (``"env"`` / ``"windowed"``) preserves
    # the pre-WP4 behaviour for ad-hoc CLI invocations that do
    # not run ``provider_discovery``. When the registry knows the
    # target's provider family, the discovery-set fields win.
    def _auth_kind_for_target(self, target) -> str:
        registry = self._effective_registry()
        model = registry.models.get(target.model_sku_id)
        if model is None:
            return "env"
        from personal_ai_orchestrator.provider_discovery import (
            PROVIDER_FAMILIES as _PF,
        )

        for spec in _PF:
            if spec.provider_id == model.provider_id:
                return spec.auth
        return "env"

    def _pool_kind_for_target(self, target) -> str:
        registry = self._effective_registry()
        model = registry.models.get(target.model_sku_id)
        if model is None:
            return "windowed"
        from personal_ai_orchestrator.provider_discovery import (
            PROVIDER_FAMILIES as _PF,
        )

        for spec in _PF:
            if spec.provider_id == model.provider_id:
                return spec.pool_kind
        return "windowed"

    def _pool_kind_for_provider(self, provider_id: str) -> str:
        """Provider-level pool_kind lookup for the quota overview card.

        The ``_pool_kind_for_target`` helper is target-scoped; the
        card builder knows the ``provider_id`` directly. Symmetric
        implementation — same family scan, same default.
        """
        from personal_ai_orchestrator.provider_discovery import (
            PROVIDER_FAMILIES as _PF,
        )

        for spec in _PF:
            if spec.provider_id == provider_id:
                return spec.pool_kind
        return "windowed"

    def _build_unmetered_observation(
        self,
        connection,
    ) -> UnmeteredObservationView | None:
        """Read-time metrics for an unmetered provider.

        No new supervisor step, no new tables. The fields are
        derived at read time from existing stores so a future
        migration to OpenCode typed errors cannot strand the
        Resources page.
        """

        from personal_ai_orchestrator.provider_discovery import (
            PROVIDER_FAMILIES as _PF,
        )

        family = next(
            (spec for spec in _PF if spec.provider_id == connection.provider_id),
            None,
        )
        if family is None or family.pool_kind != "unmetered":
            return None
        targets = (
            [
                target
                for target in self._effective_registry().execution_targets.values()
                if self._effective_registry().models.get(target.model_sku_id)
                and self._effective_registry().models[target.model_sku_id].provider_id
                == connection.provider_id
            ]
            if self.registry is not None
            else []
        )
        target_ids = {target.id for target in targets}

        rpm_observed = 0
        if self.store is not None:
            now = datetime.now(UTC)
            cutoff = now - timedelta(seconds=60)
            try:
                rows = self.store.connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE started_at >= ?",
                    (cutoff,),
                ).fetchone()
                if rows is not None:
                    rpm_observed = int(rows[0])
            except Exception:
                rpm_observed = 0

        error_rate: float | None = None
        if self.execution_evidence_journal is not None:
            cutoff = datetime.now(UTC) - timedelta(seconds=3600)
            total = 0
            non_verified = 0
            try:
                for path in sorted(
                    self.execution_evidence_journal.directory.glob("exec-verify-*.json")
                ):
                    try:
                        evidence = ExecutionVerificationEvidence.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                    except Exception:
                        continue
                    if evidence.execution_target_id not in target_ids:
                        continue
                    if evidence.observed_at < cutoff:
                        continue
                    total += 1
                    if evidence.result is not ExecutionVerificationOutcome.VERIFIED:
                        non_verified += 1
            except Exception:
                pass
            if total > 0:
                error_rate = non_verified / total

        cooldown_until: str | None = None
        if self.quota_availability_journal is not None:
            earliest: datetime | None = None
            for target in targets:
                try:
                    evidence = self.quota_availability_journal.load(target.id)
                except Exception:
                    continue
                if evidence is None:
                    continue
                if (
                    evidence.state is QuotaAvailabilityState.COOLDOWN
                    and evidence.cooldown_until is not None
                ):
                    if earliest is None or evidence.cooldown_until < earliest:
                        earliest = evidence.cooldown_until
            if earliest is not None:
                cooldown_until = earliest.isoformat()

        return UnmeteredObservationView(
            rpm_observed=rpm_observed,
            error_rate_1h=error_rate,
            cooldown_until=cooldown_until,
        )

    def _quota_provider_card(
        self,
        connection,
        observation: QuotaProviderObservation | None,
    ) -> QuotaProviderCardView:
        pools = self._quota_pool_views_from_snapshot(observation) if observation is not None else ()
        # One ``now`` for the entire card so the per-window ``burn`` figures
        # and the provider-level ``source_pressure`` compare like-for-like
        # — and so two providers rendered in the same response agree on
        # "now" rather than disagreeing across the ~1 ms gap between two
        # ``datetime.now(UTC)`` calls.
        now = datetime.now(UTC)
        plan_view = self._plan_view(observation, now=now) if observation is not None else None
        source_pressure: str | None = None
        if observation is not None and observation.projection is not None:
            source_pressure = observation.projection.source_pressure(now=now).value
        # M1 WP3 fix (F5): card-level collection failure streak.
        # Walk every target under this provider (model_sku_id ->
        # model.provider_id == connection.provider_id) and pick
        # the maximum ``consecutive_failures`` from the per-target
        # journal. ``0`` when no journal entry exists.
        collection_failure_streak = 0
        if self.quota_availability_journal is not None:
            registry = self._effective_registry()
            for target in registry.execution_targets.values():
                target_model = registry.models.get(target.model_sku_id)
                if target_model is None:
                    continue
                if target_model.provider_id != connection.provider_id:
                    continue
                evidence = self.quota_availability_journal.load(target.id)
                if evidence is None:
                    continue
                if evidence.consecutive_failures > collection_failure_streak:
                    collection_failure_streak = evidence.consecutive_failures
        return QuotaProviderCardView(
            provider_id=connection.provider_id,
            display_name=connection.display_name,
            connection_state=connection.connection_state.value,
            auth_state=connection.auth_state.value,
            plan_surface=connection.plan_surface,
            region=connection.region,
            quota_state=(
                observation.observation_state.value
                if observation is not None
                else QuotaObservationState.UNKNOWN.value
            ),
            confidence=(observation.confidence if observation is not None else "UNKNOWN"),
            measurement_source=(
                observation.measurement_source if observation is not None else None
            ),
            observed_at=observation.observed_at if observation is not None else None,
            readonly_source_available=has_readonly_quota_source(connection.provider_id),
            collector_available=(
                observation.collector_available if observation is not None else False
            ),
            last_refresh_status=(
                observation.last_refresh_status if observation is not None else None
            ),
            last_refresh_at=(observation.last_refresh_at if observation is not None else None),
            failure_reason=(observation.failure_reason if observation is not None else None),
            credential_source=(
                observation.credential_source if observation is not None else "NONE"
            ),
            quota_pools=pools,
            plan=plan_view,
            source_pressure=source_pressure,
            collection_failure_streak=collection_failure_streak,
            # M1 WP4: surface pool_kind + the unmetered observation
            # object so the Resources page can render a
            # separate "Free / unmetered" group without
            # round-tripping through provider_discovery. The
            # ``unmetered`` block is None for windowed
            # providers — their quota surfaces are surfaced
            # through ``quota_pools`` + ``plan``.
            pool_kind=self._pool_kind_for_provider(connection.provider_id),
            unmetered=self._build_unmetered_observation(connection),
        )

    def quota(self) -> QuotaOverviewView:
        """Connection-based quota projection.

        The top-level list is built from *connected providers*, never from the
        subset that happens to carry quota pools. A provider the owner just
        added is therefore visible immediately, showing UNKNOWN until a real
        read-only observation exists.
        """

        connections = self._connected_connections()
        observations = {
            item.provider_id: item
            for item in (
                self.quota_refresh_service.observations()
                if self.quota_refresh_service is not None
                else ()
            )
        }
        cards = tuple(
            self._quota_provider_card(connection, observations.get(provider_id))
            for provider_id, connection in sorted(connections.items())
        )

        observable = sum(
            1 for card in cards if card.quota_state == QuotaObservationState.OBSERVED.value
        )
        warnings = 0
        exhausted = 0
        for card in cards:
            for pool in card.quota_pools:
                if pool.state in {"EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN"}:
                    exhausted += 1
                for window in pool.windows:
                    if (
                        window.confidence in {"EXACT", "ESTIMATED"}
                        and window.remaining_fraction is not None
                        and window.remaining_fraction <= 0.15
                    ):
                        warnings += 1

        if not cards:
            state = QuotaPageState.NO_CONNECTED_PROVIDER
        elif observable:
            state = QuotaPageState.CONNECTED_WITH_QUOTA_OBSERVATIONS
        else:
            state = QuotaPageState.CONNECTED_BUT_QUOTA_UNKNOWN

        return QuotaOverviewView(
            state=state.value,
            summary=QuotaSummaryView(
                connected_provider_count=len(cards),
                quota_observable_provider_count=observable,
                quota_unknown_provider_count=len(cards) - observable,
                quota_warning_count=warnings,
                quota_exhausted_count=exhausted,
            ),
            providers=cards,
            history=self.quota_history(),
        )

    def refresh_quota(self, provider_id: str | None = None) -> QuotaRefreshResultView:
        """Owner-triggered read-only quota collection.

        Distinct from ``refresh_providers`` (catalog/credential discovery):
        this contacts documented read-only quota endpoints only, and never
        issues a model generation to discover quota.
        """

        if provider_id is not None:
            self._validate_identifier("provider_id", provider_id)
        refreshed: tuple[str, ...] = ()
        if self.quota_refresh_service is not None:
            observations = self.quota_refresh_service.refresh(provider_id)
            for observation in observations:
                if observation.snapshot is not None:
                    self._record_quota_snapshot_history(observation)
            refreshed = tuple(item.provider_id for item in observations)
        return QuotaRefreshResultView(
            refreshed_provider_ids=refreshed,
            overview=self.quota(),
        )

    def _record_quota_snapshot_history(self, observation: QuotaProviderObservation) -> None:
        """Feed the existing bounded history with real observations only."""

        snapshot = observation.snapshot
        observed_at = observation.observed_at
        if snapshot is None or observed_at is None:
            return
        for window in snapshot.windows:
            if window.confidence.value == "UNKNOWN":
                continue
            try:
                self.store.record_quota_observation(
                    provider_id=observation.provider_id,
                    quota_pool_id=snapshot.quota_pool_id or observation.provider_id,
                    window_id=window.window_id,
                    observed_at=observed_at,
                    remaining_fraction=window.remaining_fraction,
                    confidence=window.confidence.value,
                    measurement_source=snapshot.source.source_type.value,
                    reset_at=(window.reset_at.isoformat() if window.reset_at is not None else None),
                    state=window.state.value,
                )
            except Exception:
                continue

    def active_status(self) -> ActiveStatusView:
        gate = self.activation_gate
        return ActiveStatusView(
            production_active="ACTIVE" if gate.authorized else "DISABLED_BY_DESIGN",
            authorized=gate.authorized,
            blocking_reasons=gate.blocking_reasons(),
            gate={
                "p0_safety_kernel_authoritative": gate.p0_safety_kernel_authoritative,
                "p1_verifier_authoritative": gate.p1_verifier_authoritative,
                "adapter_fail_closed_validated": gate.adapter_fail_closed_validated,
                "shadow_evidence_accepted": gate.shadow_evidence_accepted,
                "safe_bypass_validated": gate.safe_bypass_validated,
                "owner_approved": gate.owner_approved,
            },
        )

    def approvals_for_task(self, task_id: str) -> ApprovalListView:
        self.get_task(task_id)
        rows = self.store.connection.execute(
            "SELECT approval_id FROM approvals WHERE task_id=? ORDER BY rowid", (task_id,)
        ).fetchall()
        authority = ApprovalAuthority(self.store)
        views = tuple(self._approval_view(authority.get(row["approval_id"])) for row in rows)
        return ApprovalListView(approvals=views)

    def get_approval(self, approval_id: str) -> ApprovalView:
        self._validate_identifier("approval_id", approval_id)
        authority = ApprovalAuthority(self.store)
        try:
            return self._approval_view(authority.get(approval_id))
        except KeyError:
            raise ControlPlaneError(404, "approval_not_found") from None

    @staticmethod
    def _approval_view(record) -> ApprovalView:
        return ApprovalView(
            approval_id=record.approval_id,
            task_id=record.task_id,
            kind=record.kind.value,
            status=record.status.value,
            created_at=record.created_at.isoformat(),
            resolved_at=record.resolved_at.isoformat() if record.resolved_at else None,
        )

    def health(self) -> HealthView:
        if self.supervisor is None:
            return HealthView(
                status="ok",
                api_version=CONTROL_API_VERSION,
                process_id=os.getpid(),
                process_instance_id=_PROCESS_INSTANCE_ID,
                model_tiers_source=self.model_tiers_source,
            )
        snapshot: DaemonSupervisorSnapshot = self.supervisor.snapshot()
        steps_view: tuple[SupervisorStepView, ...] = tuple(
            SupervisorStepView(
                name=step.name,
                last_run_at=(
                    step.last_run_at.isoformat() if step.last_run_at is not None else None
                ),
                last_duration_ms=step.last_duration_ms,
                consecutive_failures=step.consecutive_failures,
                in_backoff=step.in_backoff,
            )
            for step in snapshot.steps
        )
        return HealthView(
            status="ok",
            api_version=CONTROL_API_VERSION,
            process_id=os.getpid(),
            process_instance_id=_PROCESS_INSTANCE_ID,
            last_tick_at=(
                snapshot.last_tick_at.isoformat() if snapshot.last_tick_at is not None else None
            ),
            tick_interval_seconds=snapshot.interval_seconds,
            supervisor_steps=steps_view,
            model_tiers_source=self.model_tiers_source,
        )

    def build(self) -> BuildView:
        """Report which commit produced this daemon, or ``unknown`` if unresolvable."""
        identity = resolve_build_identity()
        return BuildView(
            commit_sha=identity.commit_sha,
            short_sha=identity.short_sha,
            api_version=CONTROL_API_VERSION,
            configuration=identity.configuration,
            built_at=identity.built_at,
        )


def _host_allowed(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(f"http://{value}")
    except ValueError:
        return False
    return parsed.hostname in _ALLOWED_HOSTS


def _origin_allowed(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in _ALLOWED_HOSTS


def handler_for_control(service: ControlPlaneService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalAIOrchestratorControl/0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            rendered = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(rendered)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(rendered)

        def _view(self, status: int, view: BaseModel) -> None:
            self._json(status, json.loads(view.model_dump_json()))

        def _segments(self) -> tuple[tuple[str, ...], dict[str, list[str]]]:
            split = urlsplit(self.path)
            segments = tuple(part for part in split.path.split("/") if part)
            return segments, parse_qs(split.query)

        def _guard_headers(self) -> bool:
            if not _host_allowed(self.headers.get("host")):
                self._json(403, {"error": "invalid_host"})
                return False
            if not _origin_allowed(self.headers.get("origin")):
                self._json(403, {"error": "invalid_origin"})
                return False
            return True

        def _drain(self, length: int) -> None:
            """Discard a bounded rejected body so local clients can finish sending."""

            remaining = min(length, MAX_DRAIN_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _read_json(self) -> dict[str, Any] | None:
            content_type = self.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(415, {"error": "json_required"})
                return None
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid_content_length"})
                return None
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._drain(max(length, 0))
                self._json(413, {"error": "request_too_large_or_empty"})
                return None
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_json"})
                return None
            if not isinstance(payload, dict):
                self._json(400, {"error": "invalid_json_object"})
                return None
            return payload

        def _dispatch(self, method: str) -> None:
            if not self._guard_headers():
                return
            segments, query = self._segments()
            request_service = None
            try:
                if not segments or segments[0] != CONTROL_API_VERSION:
                    self._json(404, {"error": "not_found"})
                    return
                rest = segments[1:]
                if method not in ("GET", "POST", "PUT"):
                    self._json(405, {"error": "method_not_allowed"})
                    return
                request_service = service.open_request()
                self._route(method, rest, query, request_service)
            except ControlPlaneError as error:
                self._json(error.status, {"error": error.code})
            except ValidationError:
                self._json(400, {"error": "invalid_json_schema"})
            except Exception as exc:
                # Operator-supports-us-visible failure: when an unhandled
                # exception escapes into the catch-all the daemon would
                # otherwise return a bare 503, swallowing the cause.
                # A small file under /tmp is the simplest durable trace.
                try:
                    import traceback

                    with open("/tmp/pao_control_plane_unavailable.log", "a") as fh:
                        fh.write(f"{type(exc).__name__}: {exc}\n")
                        fh.write(traceback.format_exc())
                        fh.write("\n---\n")
                except Exception:
                    pass
                self._json(503, {"error": "control_plane_unavailable"})

        def _route(
            self,
            method: str,
            rest: tuple[str, ...],
            query: dict[str, list[str]],
            request_service: ControlPlaneService,
        ) -> None:
            count = len(rest)

            if rest == ("health",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.health())
                return

            if rest == ("build",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.build())
                return

            if rest == ("tasks",):
                if method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(201, request_service.submit_task(payload))
                    return
                if method == "GET":
                    limit = DEFAULT_LIST_LIMIT
                    if "limit" in query:
                        try:
                            limit = int(query["limit"][0])
                        except (ValueError, IndexError):
                            self._json(400, {"error": "invalid_limit"})
                            return
                    self._view(200, request_service.list_tasks(limit=limit))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if rest == ("projects",):
                if method == "GET":
                    self._view(200, request_service.list_projects())
                    return
                if method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(201, request_service.register_project(payload))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if rest == ("projects", "resolve"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                payload = self._read_json()
                if payload is None:
                    return
                self._view(200, request_service.resolve_project(payload))
                return

            if count >= 2 and rest[0] == "projects":
                project_id = rest[1]
                sub = rest[2] if count == 3 else None
                if count == 2:
                    if method != "GET":
                        self._json(405, {"error": "method_not_allowed"})
                        return
                    self._view(200, request_service.get_project(project_id))
                    return
                if count == 3 and sub == "remove" and method == "POST":
                    self._view(200, request_service.remove_project(project_id))
                    return
                if count == 3 and sub == "opened" and method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.mark_project_opened(project_id))
                    return
                if count == 3 and sub == "scheduling" and method == "PUT":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(
                        200,
                        request_service.set_project_scheduling_policy(project_id, payload),
                    )
                    return
                if count == 3 and sub == "settings" and method == "PUT":
                    # M1 WP5a-1: PUT /v1/projects/{id}/settings persists
                    # the project-level supervised-auto toggle,
                    # unattended toggle, and grace window.
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(
                        200,
                        request_service.set_project_supervised_auto_settings(
                            project_id,
                            payload,
                        ),
                    )
                    return
                self._json(404, {"error": "not_found"})
                return

            if count >= 2 and rest[0] == "tasks":
                task_id = rest[1]
                sub = rest[2] if count == 3 else None
                if count == 2:
                    if method != "GET":
                        self._json(405, {"error": "method_not_allowed"})
                        return
                    self._view(200, request_service.get_task(task_id))
                    return
                if count == 3 and sub == "runs" and method == "GET":
                    self._view(200, request_service.task_runs(task_id))
                    return
                if count == 3 and sub == "verification" and method == "GET":
                    self._view(200, request_service.verification_report(task_id))
                    return
                if count == 3 and sub == "routing" and method == "GET":
                    self._view(200, request_service.routing_decision(task_id))
                    return
                if count == 3 and sub == "cancel" and method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.cancel_task(task_id, payload))
                    return
                if count == 3 and sub == "dispatch" and method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.dispatch_task(task_id, payload))
                    return
                if (
                    count == 4
                    and rest[2] == "dispatch"
                    and rest[3] == "recommendation"
                    and method == "POST"
                ):
                    payload = self._read_json() or {}
                    self._view(200, request_service.recommend_dispatch(task_id, payload))
                    return
                # M1 WP5a-2: supervised-auto owner controls.
                if count == 4 and rest[2] == "auto" and method == "POST":
                    if rest[3] == "ack":
                        payload = self._read_json()
                        if payload is None:
                            return
                        self._view(200, request_service.auto_ack(task_id, payload))
                        return
                    if rest[3] == "veto":
                        payload = self._read_json()
                        if payload is None:
                            return
                        self._view(200, request_service.auto_veto(task_id, payload))
                        return
                    if rest[3] == "dispatch-now":
                        payload = self._read_json()
                        if payload is None:
                            return
                        self._view(200, request_service.auto_dispatch_now(task_id, payload))
                        return
                if count == 3 and sub == "approvals" and method == "GET":
                    self._view(200, request_service.approvals_for_task(task_id))
                    return
                if count == 3 and sub == "detail" and method == "GET":
                    self._view(200, request_service.task_detail(task_id))
                    return
                self._json(404, {"error": "not_found"})
                return

            if count == 2 and rest[0] == "runs" and method == "GET":
                self._view(200, request_service.get_run(rest[1]))
                return

            if count == 2 and rest[0] == "approvals" and method == "GET":
                self._view(200, request_service.get_approval(rest[1]))
                return

            if count == 2 and rest[0] == "dispatches" and method == "GET":
                self._view(200, request_service.get_owner_dispatch(rest[1]))
                return

            if rest == ("settings", "owner-execution"):
                if method == "GET":
                    self._view(200, request_service.owner_execution_settings())
                    return
                if method == "PUT":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.update_owner_execution_settings(payload))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if rest == ("settings", "scheduling"):
                if method == "GET":
                    self._view(200, request_service.scheduling_settings_view())
                    return
                if method == "PUT":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(200, request_service.update_scheduling_settings(payload))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if rest == ("providers",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.providers())
                return

            if rest == ("provider-connections",):
                if method == "GET":
                    self._view(200, request_service.provider_connections())
                    return
                if method == "POST":
                    payload = self._read_json()
                    if payload is None:
                        return
                    self._view(201, request_service.connect_provider(payload))
                    return
                self._json(405, {"error": "method_not_allowed"})
                return

            if rest == ("provider-connections", "import"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                payload = self._read_json()
                if payload is None:
                    return
                self._view(200, request_service.import_provider_connections(payload))
                return

            if count == 3 and rest[0] == "provider-connections" and rest[2] == "disconnect":
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                payload = self._read_json()
                if payload is None:
                    return
                self._view(200, request_service.disconnect_provider(rest[1], payload))
                return

            if rest == ("providers", "status"):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.provider_discovery_status())
                return

            if rest == ("providers", "refresh"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                # Refresh is intentionally idempotent: a missing/empty
                # body is treated the same as an explicit empty JSON
                # object, since the operation has no parameters.
                length = int(self.headers.get("content-length", "0") or 0)
                if length > 0:
                    payload = self._read_json()
                    if payload is None:
                        return
                self._view(200, request_service.refresh_providers())
                return

            if rest == ("quota",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.quota())
                return

            if rest == ("quota", "refresh"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                # Idempotent and parameterless by default, like
                # /v1/providers/refresh: an absent body means "all connected
                # providers". An explicit body may scope it to one provider.
                target: str | None = None
                length = int(self.headers.get("content-length", "0") or 0)
                if length > 0:
                    payload = self._read_json()
                    if payload is None:
                        return
                    target = QuotaRefreshRequest.model_validate(payload).provider_id
                self._view(200, request_service.refresh_quota(target))
                return

            if (
                count == 4
                and rest[0] == "providers"
                and rest[2] == "quota"
                and rest[3] == "refresh"
            ):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                length = int(self.headers.get("content-length", "0") or 0)
                if length > 0:
                    payload = self._read_json()
                    if payload is None:
                        return
                self._view(200, request_service.refresh_quota(rest[1]))
                return

            if rest == ("active-status",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.active_status())
                return

            if rest == ("dashboard",):
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, request_service.dashboard_summary())
                return

            self._json(404, {"error": "not_found"})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

    return Handler


class UnixThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server bound to a permission-restricted Unix Domain Socket."""

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str | Path,
        handler: type[BaseHTTPRequestHandler],
        *,
        socket_mode: int = 0o600,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._socket_mode = socket_mode
        super().__init__(str(self._socket_path), handler)

    def server_bind(self) -> None:
        path = self._socket_path
        if path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.5)
                probe.connect(str(path))
            except OSError:
                path.unlink(missing_ok=True)
            else:
                raise RuntimeError("control socket is already served by another process")
            finally:
                probe.close()
        super().server_bind()
        os.chmod(path, self._socket_mode)

    def server_close(self) -> None:
        super().server_close()
        try:
            self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass


class ControlPlaneServer:
    """Runnable UDS control-plane server bound to one host-owned service."""

    def __init__(
        self,
        service: ControlPlaneService,
        socket_path: str | Path,
        *,
        socket_mode: int = 0o600,
    ) -> None:
        if service.store.path == ":memory:":
            raise ValueError("threaded control plane requires a durable file-backed state store")
        self.service = service
        self.httpd = UnixThreadingHTTPServer(
            socket_path, handler_for_control(service), socket_mode=socket_mode
        )
        self._thread: threading.Thread | None = None

    @property
    def socket_path(self) -> Path:
        return self.httpd._socket_path

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.httpd.server_close()


__all__ = [
    "CONTROL_API_VERSION",
    "ControlPlaneError",
    "ControlPlaneServer",
    "ControlPlaneService",
    "MAX_REQUEST_BYTES",
    "UnixThreadingHTTPServer",
    "handler_for_control",
]
