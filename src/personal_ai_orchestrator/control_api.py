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
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from personal_ai_orchestrator.execution_controller import validate_execution_target_launch
from personal_ai_orchestrator.execution_evidence import ExecutionEvidenceJournal
from personal_ai_orchestrator.model_registry import EvidenceConfidence, ModelRegistry
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.provider_registry_manager import (
    ProviderRegistryManager,
)
from personal_ai_orchestrator.quota_availability import QuotaAvailabilityJournal, QuotaAvailabilityState
from personal_ai_orchestrator.scheduler import RoutingObjective
from personal_ai_orchestrator.dispatch_recommender import (
    DispatchCandidateInput,
    recommend_owner_dispatch,
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
    TaskState,
)
from personal_ai_orchestrator.scheduling_settings import (
    SELECTABLE_GLOBAL_POLICIES,
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


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: A task may additionally pick MANUAL, which a *global* default cannot: only a
#: single task knows a concrete execution target that is valid for itself.
SELECTABLE_TASK_POLICIES = (*SELECTABLE_GLOBAL_POLICIES, "MANUAL")


class TaskSubmitRequest(_ViewModel):
    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    intent: str = Field(min_length=1, max_length=MAX_INTENT_LENGTH)
    scheduling_policy: str | None = Field(default=None, min_length=1, max_length=64)
    manual_execution_target_id: str | None = Field(default=None, min_length=1, max_length=128)


class CancelRequest(_ViewModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


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
    name: str
    contribution: float


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
    quota_state: str | None = None
    score_components: tuple[DispatchRecommendationScoreComponent, ...] = ()
    reasons: tuple[str, ...] = ()


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


class ExecutionTargetHealthView(_ViewModel):
    execution_target_id: str
    model_sku_id: str
    runtime_id: str
    enabled: bool
    execution_verified: bool
    runtime_available: bool | None = None
    observed_availability: ObservedAvailabilityView | None = None


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


class ProviderHealthListView(_ViewModel):
    providers: tuple[ProviderHealthView, ...]


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


class QuotaPlanWindowView(_ViewModel):
    """One quota window of a shared plan pool.

    ``remaining_fraction`` is populated only for EXACT/ESTIMATED windows, so a
    client cannot draw a bar for a figure the provider never gave us.
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


class SchedulingSettingsUpdateRequest(_ViewModel):
    default_scheduling_policy: str = Field(min_length=1, max_length=64)


class ProjectSchedulingPolicyRequest(_ViewModel):
    """``scheduling_policy=None`` restores the global default for this project."""

    scheduling_policy: str | None = Field(default=None, min_length=1, max_length=64)
    manual_execution_target_id: str | None = Field(default=None, min_length=1, max_length=128)


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


class HealthView(_ViewModel):
    status: str
    api_version: str


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
        """Static availability map wins; discovered opencode targets derive
        availability from the local runtime surface truthfully."""

        if execution_target_id in self.runtime_availability:
            return self.runtime_availability[execution_target_id]
        target = self._effective_registry().execution_targets.get(execution_target_id)
        if target is None:
            return False
        runtime_provider = target.runtime_provider_id or "opencode"
        if runtime_provider != "opencode":
            return False
        return _opencode_binary_available()

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
            git_root = Path(
                self._git(selected, "rev-parse", "--show-toplevel")
            ).resolve(strict=True)
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
        if (
            availability is not project.storage_availability
            or (head is not None and head != project.last_known_head)
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
        )

    def resolve_project(self, payload: dict[str, Any]) -> ProjectView:
        request = ProjectResolveRequest.model_validate(payload)
        probe = self._project_probe(request.path)
        project_id = self._project_id_for(
            str(probe["git_root"]), probe["working_subpath"]
        )
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
        project_id = self._project_id_for(
            str(probe["git_root"]), probe["working_subpath"]
        )
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
                self._refresh_project_view(project)
                for project in self.store.list_projects()
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
        )

    def update_scheduling_settings(self, payload: dict[str, Any]) -> SchedulingSettingsView:
        request = SchedulingSettingsUpdateRequest.model_validate(payload)
        try:
            self.scheduling_settings.set_default_policy(request.default_scheduling_policy)
        except ValueError:
            raise ControlPlaneError(400, "unsupported_global_scheduling_policy") from None
        return self.scheduling_settings_view()

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
            connections = self.provider_registry_manager.import_connections(
                request.provider_ids
            )
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
        return RunView(
            run_id=row["run_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            pid=row["pid"],
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
        dispatch_id = f"owner-dispatch-{request.request_id}"
        try:
            dispatch, created = self.store.reserve_owner_dispatch(
                dispatch_id=dispatch_id,
                request_id=request.request_id,
                task_id=task_id,
                task_state_version=request.task_state_version,
                execution_target_id=request.execution_target_id,
                authority=DispatchAuthority.OWNER_INITIATED_EXECUTION.value,
            )
        except ValueError:
            raise ControlPlaneError(409, "conflicting_dispatch_request_id") from None
        if not created:
            return self._dispatch_view(dispatch)
        if task.state_version != request.task_state_version:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code="STALE_TASK_STATE_VERSION",
                failure_reason="dispatch task_state_version did not match authoritative task",
            )
            raise ControlPlaneError(409, "stale_task_state_version")
        if task.state not in {TaskState.SUBMITTED, TaskState.READY}:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code="TASK_STATE_NOT_DISPATCHABLE",
                failure_reason=f"task state {task.state.value} is not dispatchable",
            )
            raise ControlPlaneError(409, "task_state_not_dispatchable")
        if task.project_id is None:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code="MISSING_PROJECT_ID",
                failure_reason="coding tasks require an explicit registered project",
            )
            raise ControlPlaneError(409, "missing_project_id")
        if task.base_sha is None:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code="PROJECT_BASE_SHA_MISSING",
                failure_reason="coding tasks require a durable base_sha",
            )
            raise ControlPlaneError(409, "project_base_sha_missing")
        project_view = self.get_project(task.project_id)
        if project_view.storage_availability != ProjectAvailability.ONLINE.value:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code=f"PROJECT_{project_view.storage_availability}",
                failure_reason="registered project is not currently available",
            )
            raise ControlPlaneError(409, "project_not_available")

        effective_registry = (
            self.provider_registry_manager.registry()
            if self.provider_registry_manager is not None
            else self.registry
        )
        if self.provider_registry_manager is not None:
            target = effective_registry.execution_targets.get(request.execution_target_id)
            model = (
                effective_registry.models.get(target.model_sku_id)
                if target is not None
                else None
            )
            connected_provider_ids = self.provider_registry_manager.connected_provider_ids()
            if model is None or model.provider_id not in connected_provider_ids:
                self.store.mark_owner_dispatch_blocked(
                    request.request_id,
                    failure_code="PROVIDER_NOT_CONNECTED",
                    failure_reason="execution target provider is not connected by owner",
                )
                raise ControlPlaneError(409, "provider_not_connected") from None
        try:
            validate_execution_target_launch(
                effective_registry,
                execution_target_id=request.execution_target_id,
                runtime_available=self._runtime_available(request.execution_target_id),
                execution_evidence_journal=self.execution_evidence_journal,
            )
        except RuntimeError as error:
            self.store.mark_owner_dispatch_blocked(
                request.request_id,
                failure_code="EXECUTION_TARGET_NOT_LAUNCHABLE",
                failure_reason=str(error),
            )
            raise ControlPlaneError(409, "execution_target_not_launchable") from None

        if task.state is TaskState.SUBMITTED:
            task = self.store.transition_task(
                task_id,
                TaskState.READY,
                expected_version=task.state_version,
                reason="owner initiated execution dispatch reserved",
            )
        if self.dispatch_executor is not None:
            thread = threading.Thread(
                target=self.dispatch_executor.execute,
                args=(request.request_id,),
                name=f"owner-dispatch-{request.request_id}",
                daemon=True,
            )
            thread.start()
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
        except KeyError as error:
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
            raise ControlPlaneError(
                400, f"unknown_scheduling_policy:{policy_name}"
            ) from error

        candidates = self._collect_dispatch_candidates()
        recommendation = recommend_owner_dispatch(
            candidates, policy=policy, now=datetime.now(UTC)
        )

        candidate_views = tuple(
            self._dispatch_recommendation_candidate_view(evaluation, candidates)
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

    def _collect_dispatch_candidates(self) -> list[DispatchCandidateInput]:
        """Gather one DispatchCandidateInput per execution target.

        The provider view is the single source of truth for runtime
        availability, verification, and quota window observations; the
        quota-availability journal supplies the dispatch-relevant state.
        """

        registry = (
            self.provider_registry_manager.registry()
            if self.provider_registry_manager is not None
            else self.registry
        )

        provider_by_target: dict[str, str] = {
            target_id: registry.models[target.model_sku_id].provider_id
            for target_id, target in registry.execution_targets.items()
            if target.model_sku_id in registry.models
        }

        # Mirror the providers() view's plan → provider resolution so the
        # recommendation sees the same windows the UI does. Pool windows
        # are pool-scoped; every target of one provider inherits the
        # windows of every pool that belongs to that provider's plans.
        plans_by_account: dict[str, list[str]] = {}
        for plan in registry.plans.values():
            plans_by_account.setdefault(plan.account_id, []).append(plan.id)

        provider_plan_ids: dict[str, set[str]] = {}
        for provider_id, provider in sorted(registry.providers.items()):
            account_ids = [
                account.id
                for account in registry.accounts.values()
                if account.provider_id == provider_id
            ]
            plan_ids = {
                plan_id
                for account_id in account_ids
                for plan_id in plans_by_account.get(account_id, [])
            }
            provider_plan_ids[provider_id] = plan_ids

        remaining_by_provider: dict[str, list[float]] = {}
        for pool in registry.quota_pools.values():
            provider_id = next(
                (
                    pid
                    for pid, plan_ids in provider_plan_ids.items()
                    if pool.plan_id in plan_ids
                ),
                None,
            )
            if provider_id is None:
                continue
            # Windows live under pool.snapshot on the registry model.
            snapshot = getattr(pool, "snapshot", None)
            if snapshot is None:
                continue
            for window in snapshot.windows:
                fraction = window.remaining_fraction
                if fraction is None:
                    continue
                remaining_by_provider.setdefault(provider_id, []).append(fraction)

        candidates: list[DispatchCandidateInput] = []
        now = datetime.now(UTC)
        for target_id, target in sorted(registry.execution_targets.items()):
            provider_id = provider_by_target.get(target_id, "")
            remaining = tuple(remaining_by_provider.get(provider_id, ()))

            runtime_available = (
                self.runtime_availability.get(target_id)
                if target_id in self.runtime_availability
                else self._runtime_available(target_id)
            )
            verified = target.execution_verified
            evidence_observed_at: datetime | None = None
            if not verified and self.execution_evidence_journal is not None:
                latest = self.execution_evidence_journal.latest_for_target(target_id)
                if latest is not None and latest.establishes_verified:
                    evidence_observed_at = latest.observed_at
                    verified = True

            availability_state = QuotaAvailabilityState.UNKNOWN
            if self.quota_availability_journal is not None:
                evidence = self.quota_availability_journal.load(target_id)
                if evidence is not None:
                    availability_state = evidence.state_at(now=now)

            candidates.append(
                DispatchCandidateInput(
                    execution_target_id=target_id,
                    model_sku_id=target.model_sku_id,
                    runtime_available=bool(runtime_available),
                    verified=bool(verified),
                    remaining_fractions=remaining,
                    evidence_observed_at=evidence_observed_at,
                    availability_state=availability_state,
                )
            )
        return candidates

    @staticmethod
    def _dispatch_recommendation_candidate_view(
        evaluation, candidates: list[DispatchCandidateInput]
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
            evidence_fresh=bool(
                inputs and inputs.evidence_observed_at is not None
            ),
            runtime_available=bool(inputs and inputs.runtime_available),
            verified=bool(inputs and inputs.verified),
            quota_state=(
                inputs.availability_state.value
                if inputs else None
            ),
            score_components=tuple(
                DispatchRecommendationScoreComponent(
                    name=component.name,
                    contribution=component.contribution,
                )
                for component in evaluation.score_components
            ),
            reasons=tuple(evaluation.reasons),
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
            if payload.get("from") == TaskState.VERIFYING.value and payload.get(
                "to"
            ) == TaskState.BLOCKED.value:
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

    def _task_state_distribution(
        self, by_state: dict[str, int]
    ) -> tuple[TaskStateSliceView, ...]:
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
                )
        execution_verified = target.execution_verified
        if not execution_verified and self.execution_evidence_journal is not None:
            try:
                execution_verified = (
                    self.execution_evidence_journal.target_has_verified_evidence(target.id)
                )
            except Exception:
                execution_verified = False
        return ExecutionTargetHealthView(
            execution_target_id=target.id,
            model_sku_id=target.model_sku_id,
            runtime_id=target.runtime_id,
            enabled=target.enabled,
            execution_verified=execution_verified,
            runtime_available=(
                self.runtime_availability.get(target.id)
                if target.id in self.runtime_availability
                else self._runtime_available(target.id)
            ),
            observed_availability=observed,
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
                AvailableProviderView.model_validate(item)
                for item in projection.available_to_add
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
        return {
            record.provider_id: record
            for record in result.providers
        }

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
                            window.reset_at.isoformat()
                            if window.reset_at is not None
                            else None
                        ),
                    )
                    for window in snapshot.windows
                ),
            ),
        )

    @staticmethod
    def _plan_view(observation: QuotaProviderObservation) -> QuotaPlanView | None:
        """Render the shared-plan projection the Quota page leads with.

        Returns ``None`` only when there is no plan evidence at all. A plan
        whose *balance* is UNKNOWN still renders, because the per-model
        consumption and equivalents beside it are real, and dropping the whole
        card to hide the one unknown reports less than we know.
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
            )
            for window in projection.windows
        )
        binding = projection.binding_window
        return QuotaPlanView(
            provider_id=projection.plan.provider_id,
            plan_id=projection.plan.plan_id,
            display_name=(
                observation.plan_display_name or projection.plan.display_name
            ),
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
            equivalent_capacity=ControlPlaneService._equivalent_capacity_views(
                projection
            ),
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

    def _quota_provider_card(
        self,
        connection,
        observation: QuotaProviderObservation | None,
    ) -> QuotaProviderCardView:
        pools = (
            self._quota_pool_views_from_snapshot(observation)
            if observation is not None
            else ()
        )
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
            confidence=(
                observation.confidence if observation is not None else "UNKNOWN"
            ),
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
            last_refresh_at=(
                observation.last_refresh_at if observation is not None else None
            ),
            failure_reason=(
                observation.failure_reason if observation is not None else None
            ),
            credential_source=(
                observation.credential_source if observation is not None else "NONE"
            ),
            quota_pools=pools,
            plan=(self._plan_view(observation) if observation is not None else None),
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
                    reset_at=(
                        window.reset_at.isoformat() if window.reset_at is not None else None
                    ),
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
        return HealthView(status="ok", api_version=CONTROL_API_VERSION)

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
            except Exception:
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
                if count == 4 and rest[2] == "dispatch" and rest[3] == "recommendation" and method == "POST":
                    payload = self._read_json() or {}
                    self._view(200, request_service.recommend_dispatch(task_id, payload))
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
