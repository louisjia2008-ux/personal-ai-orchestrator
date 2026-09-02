"""MiniMax Token Plan quota collector.

MiniMax publishes both Global and CN service regions. The current Personal AI
Orchestrator MVP provider is ``minimax-cn-coding-plan``, so the collector
defaults to the CN endpoint. Credentials and raw provider responses are never
persisted by this module.

What the Token Plan actually sells
----------------------------------
Officially documented (platform.minimax.io/docs/token-plan/intro): one
subscription covers eligible MiniMax resources through a **shared usage bar**,
metered over a 5-hour rolling window and a weekly window. Per-model "calls per
5 hours" figures published on the pricing page are *reference equivalents*
assuming that model is used exclusively — they are not independent per-model
quota buckets.

Workload scopes, not models
---------------------------
This account's ``model_remains`` entries are named ``general`` and ``video``.
They are neither models nor competing views of one balance: they meter two
different resources. Personal AI Orchestrator schedules coding, text, and
agentic software-engineering work, so ``general`` is the scope its MiniMax
targets draw on and ``video`` is out of the product's workload scope entirely.

Two successive defects are fixed here:

* The original collector required every ``model_remains`` entry to report the
  same percentage, and answered ``QUOTA_VARIES_BY_MODEL`` when they disagreed,
  discarding windows, reset times, and every figure it had just read.
* Its replacement kept the disagreement rule and merely renamed the outcome. So
  ``general = 95%`` beside ``video = 60%`` still collapsed **coding** quota to
  UNKNOWN — a video balance suppressing a coding figure it says nothing about.

The plan-level fraction is therefore derived from the scopes belonging to the
workload being projected (:mod:`quota_workload_scope`). ``video`` is preserved
verbatim as a :class:`ModelEquivalentView` carrying its workload classification,
but it never supplies, caps, averages with, or invalidates the coding figure —
and because it never enters ``windows``, it can never reach the snapshot the
scheduler, temporal scarcity, or QUOTA_SAVER read.

Response shapes
---------------
Two shapes are accepted, because the account surfaces differ and neither is
formally published:

*Plan-level counts* (community-documented, ``data``-wrapped)::

    {"data": {"current_interval_total_count": ..., "current_interval_usage_count": ...,
              "current_interval_reset_time": "<ISO 8601>",
              "current_weekly_total_count": ..., "current_weekly_usage_count": ...,
              "current_weekly_reset_time": "<ISO 8601>",
              "model_remains": [{"model": "MiniMax-M3", "remains": ..., "total": ...}]}}

*Per-model percentages* (the shape this deployment observed)::

    {"model_remains": [{"model": ..., "current_interval_remaining_percent": ...,
                        "current_weekly_remaining_percent": ...,
                        "start_time": <ms>, "end_time": <ms>,
                        "weekly_start_time": <ms>, "weekly_end_time": <ms>}]}

Plan-level counts are authoritative when present: they describe the pool
directly rather than through one scope's lens. Percentages are the fallback,
read from the scopes relevant to the projected workload; when several such
scopes report the same window they must still coincide, because one bar viewed
through several lenses can only be read off directly when the views agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSourceType,
    QuotaState,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
    QuotaTransport,
    QuotaTransportError,
    UrllibQuotaTransport,
)
from personal_ai_orchestrator.quota_credentials import SecretValue
from personal_ai_orchestrator.quota_observability import (
    QuotaEvidenceSource,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_plan import (
    ConsumptionUnitKind,
    EquivalentScopeKind,
    MeasurementSource,
    ModelEquivalentView,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
    aggregate_state,
    determine_binding_window,
    state_for_fraction,
)
from personal_ai_orchestrator.quota_workload_scope import (
    ACTIVE_CODING_WORKLOAD,
    QuotaWorkloadScope,
    classify_provider_scope,
    excluded_workloads,
    select_for_workload,
)

MiniMaxRegion = Literal["global", "cn"]
MINIMAX_GLOBAL_QUOTA_ENDPOINT = "https://www.minimax.io/v1/token_plan/remains"
MINIMAX_CN_QUOTA_ENDPOINT = "https://api.minimaxi.com/v1/token_plan/remains"
MINIMAX_QUOTA_DOC = "https://platform.minimax.io/subscribe/token-plan"
MINIMAX_CN_QUOTA_DOC = "https://platform.minimaxi.com/subscribe/token-plan"
MINIMAX_OFFICIAL_CLI = "https://github.com/MiniMax-AI/cli"

MINIMAX_TOKEN_PLAN_DISPLAY_NAME = "MiniMax Token Plan"


class MiniMaxQuotaReason(StrEnum):
    """Sanitized machine codes for a read that yielded no coding figure.

    They name what is actually missing. None of them may be raised because a
    scope outside the projected workload disagreed: that is not a reason for
    anything, and a code implying it (``QUOTA_VARIES_BY_MODEL``,
    ``SHARED_POOL_VIEWED_PER_MODEL``) is no longer produced here.

    These are Advanced-Details codes. The primary card renders a localized
    sentence, never the raw value.
    """

    #: The response listed no quota entries at all.
    NO_QUOTA_ENTRIES = "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"
    #: Entries exist, but none of them meters the projected workload — every
    #: one belongs to a workload this product does not schedule, or the coding
    #: scope carries no remaining figure.
    GENERAL_QUOTA_NOT_AVAILABLE = "GENERAL_QUOTA_NOT_AVAILABLE"
    #: The coding scope carried remaining fields we could not read. Fail closed:
    #: a malformed figure is not a figure.
    GENERAL_QUOTA_READ_FAILED = "GENERAL_QUOTA_READ_FAILED"
    #: One window of the coding scope is readable and the other's window
    #: semantics are not identifiable in this response. The readable one is
    #: still shown; nothing is invented for the other.
    GENERAL_WINDOW_SEMANTICS_UNKNOWN = "GENERAL_WINDOW_SEMANTICS_UNKNOWN"
    #: Several scopes of the projected workload disagree about one window, so no
    #: single figure for that window is derivable from them.
    CODING_SCOPE_VIEWS_DISAGREE = "CODING_SCOPE_VIEWS_DISAGREE"


#: Advanced-Details note, not a failure: a real provider balance exists for a
#: workload this build does not schedule, and is therefore not on the card.
MINIMAX_VIDEO_SCOPE_NOTE = "VIDEO_SCOPE_IGNORED_FOR_CODING"

_WORKLOAD_SCOPE_NOTES: dict[QuotaWorkloadScope, str] = {
    QuotaWorkloadScope.VIDEO_GENERATION: MINIMAX_VIDEO_SCOPE_NOTE,
    QuotaWorkloadScope.IMAGE_GENERATION: "IMAGE_SCOPE_IGNORED_FOR_CODING",
    QuotaWorkloadScope.AUDIO: "AUDIO_SCOPE_IGNORED_FOR_CODING",
}

_INTERVAL_PERCENT_KEY = "current_interval_remaining_percent"
_WEEKLY_PERCENT_KEY = "current_weekly_remaining_percent"

_FIVE_HOUR_SECONDS = 5 * 60 * 60.0
_WEEKLY_SECONDS = 7 * 24 * 60 * 60.0


def minimax_quota_endpoint(region: MiniMaxRegion) -> str:
    if region == "cn":
        return MINIMAX_CN_QUOTA_ENDPOINT
    return MINIMAX_GLOBAL_QUOTA_ENDPOINT


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _millis_datetime(value: object) -> datetime | None:
    numeric = _number(value)
    if numeric is None or numeric <= 0:
        return None
    try:
        return datetime.fromtimestamp(numeric / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _iso_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 reset time, requiring an explicit offset.

    A reset time without a zone is ambiguous by up to a day; treating it as UTC
    would draw a countdown that is simply wrong, so it is rejected instead.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    """The response body, whether or not it is ``data``-wrapped."""

    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _model_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    remains = _body(payload).get("model_remains")
    if not isinstance(remains, list):
        return []
    return [item for item in remains if isinstance(item, dict)]


def _agreed_percent(values: list[float]) -> float | None:
    """A single fraction only when every model's view coincides.

    Views that disagree are not averaged: the mean of several equivalents is
    not the pool's remainder, and presenting it as one would be exactly the
    fabrication this phase exists to remove.
    """

    if not values:
        return None
    first = values[0]
    if any(abs(value - first) > 1e-6 for value in values[1:]):
        return None
    if not 0.0 <= first <= 100.0:
        return None
    return first / 100.0


def _agreed_number(values: list[float]) -> float | None:
    if not values:
        return None
    first = values[0]
    if any(abs(value - first) > 1e-6 for value in values[1:]):
        return None
    return first


def _field_values(entries: list[dict[str, Any]], name: str) -> list[float]:
    return [
        value
        for value in (_number(entry.get(name)) for entry in entries)
        if value is not None
    ]


def _plan_level_window(
    body: dict[str, Any],
    *,
    total_key: str,
    usage_key: str,
    reset_key: str,
) -> tuple[float | None, float | None, float | None, datetime | None]:
    """Read one plan-level window: ``(fraction, remaining, total, reset_at)``."""

    total = _number(body.get(total_key))
    used = _number(body.get(usage_key))
    reset_at = _iso_datetime(body.get(reset_key)) or _millis_datetime(body.get(reset_key))
    if total is None or total <= 0 or used is None:
        return None, None, total, reset_at
    remaining = max(0.0, total - used)
    return max(0.0, min(1.0, remaining / total)), remaining, total, reset_at


def _entry_scope_id(entry: dict[str, Any]) -> str | None:
    scope_id = entry.get("model") or entry.get("model_name")
    return scope_id if isinstance(scope_id, str) and scope_id else None


def _classified_entries(
    entries: list[dict[str, Any]],
    provider_id: str,
) -> tuple[tuple[dict[str, Any], str | None, QuotaWorkloadScope], ...]:
    """Pair every entry with the workload its scope meters.

    An entry the provider left unnamed is kept with a ``None`` scope id and an
    UNKNOWN workload. Some account surfaces return a single anonymous bar, and
    dropping it would turn a readable plan into UNKNOWN.
    """

    classified: list[tuple[dict[str, Any], str | None, QuotaWorkloadScope]] = []
    for entry in entries:
        scope_id = _entry_scope_id(entry)
        workload = (
            QuotaWorkloadScope.UNKNOWN
            if scope_id is None
            else classify_provider_scope(provider_id, scope_id)
        )
        classified.append((entry, scope_id, workload))
    return tuple(classified)


def _entries_for_workload(
    classified: tuple[tuple[dict[str, Any], str | None, QuotaWorkloadScope], ...],
    workload: QuotaWorkloadScope,
) -> list[dict[str, Any]]:
    """The entries a projection for ``workload`` is allowed to read.

    Entries for another workload are not merely deprioritized — they are not
    read at all. A ``video`` balance is real, and it is not evidence about
    coding capacity in either direction.
    """

    return list(
        select_for_workload([(entry, kind) for entry, _, kind in classified], workload)
    )


def _has_unreadable_percent(entries: list[dict[str, Any]]) -> bool:
    """Whether a remaining-percent field is present but not a usable percentage.

    Present-and-garbage is a different fact from absent, and it must fail closed
    rather than fall through to some other scope's number.
    """

    for entry in entries:
        for key in (_INTERVAL_PERCENT_KEY, _WEEKLY_PERCENT_KEY):
            if key not in entry:
                continue
            value = _number(entry.get(key))
            if value is None or not 0.0 <= value <= 100.0:
                return True
    return False


def minimax_unknown_reason(
    payload: dict[str, Any],
    *,
    provider_id: str = "minimax",
    workload: QuotaWorkloadScope = ACTIVE_CODING_WORKLOAD,
) -> str:
    """Explain why a *successful* read still yielded no figure for ``workload``.

    UNKNOWN with no reason reads as breakage. Each code names what is actually
    missing from the projected workload's own scope. Crucially, none of them can
    be raised because a scope outside that workload disagreed: a video balance
    is not a reason for a coding figure to be unknown.
    """

    entries = _model_entries(payload)
    if not entries:
        return MiniMaxQuotaReason.NO_QUOTA_ENTRIES.value

    classified = _classified_entries(entries, provider_id)
    relevant = _entries_for_workload(classified, workload)
    if not relevant:
        # Every entry belongs to a workload this product does not schedule.
        return MiniMaxQuotaReason.GENERAL_QUOTA_NOT_AVAILABLE.value

    interval = _field_values(relevant, _INTERVAL_PERCENT_KEY)
    weekly = _field_values(relevant, _WEEKLY_PERCENT_KEY)
    if not interval and not weekly:
        return (
            MiniMaxQuotaReason.GENERAL_QUOTA_READ_FAILED.value
            if _has_unreadable_percent(relevant)
            else MiniMaxQuotaReason.GENERAL_QUOTA_NOT_AVAILABLE.value
        )
    for series in (interval, weekly):
        if len(series) > 1 and any(abs(value - series[0]) > 1e-6 for value in series[1:]):
            return MiniMaxQuotaReason.CODING_SCOPE_VIEWS_DISAGREE.value
    if _has_unreadable_percent(relevant):
        return MiniMaxQuotaReason.GENERAL_QUOTA_READ_FAILED.value
    # One window is readable and the other's window semantics are not
    # identifiable here. The readable one is still reported; the other is not
    # invented.
    return MiniMaxQuotaReason.GENERAL_WINDOW_SEMANTICS_UNKNOWN.value


def _scope_kind(scope_id: str, known_model_ids: frozenset[str]) -> EquivalentScopeKind:
    """Classify a ``model_remains`` entry name.

    MiniMax calls the array ``model_remains``, but on the observed account its
    entries are named ``general`` and ``video`` while the routable models are
    ``MiniMax-M2.7`` and similar. The provider's field name is therefore not
    evidence that an entry names a model.

    An entry counts as a model only when the account's own catalog confirms it.
    Anything else is a provider resource scope, and is labelled as one rather
    than being offered to the owner as something they can route to.
    """

    if not known_model_ids:
        return EquivalentScopeKind.UNKNOWN
    lowered = {value.lower() for value in known_model_ids}
    candidate = scope_id.lower()
    if candidate in lowered or any(candidate == value.split("/")[-1] for value in lowered):
        return EquivalentScopeKind.MODEL
    return EquivalentScopeKind.PROVIDER_RESOURCE_SCOPE


def _model_equivalent_views(
    classified: tuple[tuple[dict[str, Any], str | None, QuotaWorkloadScope], ...],
    known_model_ids: frozenset[str] = frozenset(),
) -> tuple[ModelEquivalentView, ...]:
    """Per-scope views of the provider's quota, for Advanced Details.

    Every scope the provider reported is preserved here, including the ones the
    current workload does not read (§21). Deleting ``video`` because this build
    ignores it would destroy a real observation that a future build — one that
    schedules video — would need. Relevance is decided by ``workload_scope``,
    not by what is kept.
    """

    views: list[ModelEquivalentView] = []
    for entry, scope_id, workload_scope in classified:
        if scope_id is None:
            # An anonymous bar has no scope to attribute a view to; it is read
            # as the plan figure above and needs no per-scope row here.
            continue
        scope_kind = _scope_kind(scope_id, known_model_ids)
        for window_id, percent_key in (
            ("5h", _INTERVAL_PERCENT_KEY),
            ("weekly", _WEEKLY_PERCENT_KEY),
        ):
            percent = _number(entry.get(percent_key))
            if percent is None or not 0.0 <= percent <= 100.0:
                continue
            views.append(
                ModelEquivalentView(
                    scope_id=scope_id,
                    scope_kind=scope_kind,
                    workload_scope=workload_scope,
                    window_id=window_id,
                    remaining_fraction=percent / 100.0,
                    confidence=EvidenceConfidence.EXACT,
                    measurement_source=MeasurementSource.PROVIDER_QUOTA_API,
                )
            )
        remains = _number(entry.get("remains"))
        total = _number(entry.get("total"))
        if remains is not None and total is not None and total > 0:
            views.append(
                ModelEquivalentView(
                    scope_id=scope_id,
                    scope_kind=scope_kind,
                    workload_scope=workload_scope,
                    window_id="5h-units",
                    remaining_fraction=max(0.0, min(1.0, remains / total)),
                    remaining_units=remains,
                    total_units=total,
                    unit_kind=ConsumptionUnitKind.PROVIDER_UNITS,
                    confidence=EvidenceConfidence.EXACT,
                    measurement_source=MeasurementSource.PROVIDER_QUOTA_API,
                )
            )
    return tuple(views)


def normalize_minimax_quota(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    quota_pool_id: str = "minimax-token-plan-cn",
    source_uri: str = MINIMAX_CN_QUOTA_ENDPOINT,
    provider_id: str = "minimax",
    plan_id: str = "token-plan",
    known_model_ids: frozenset[str] = frozenset(),
    workload_scope: QuotaWorkloadScope = ACTIVE_CODING_WORKLOAD,
) -> PlanQuotaProjection:
    """Project the Token Plan response onto the shared-plan domain model.

    ``known_model_ids`` is the account's own catalog. It is the only thing that
    lets a ``model_remains`` entry be reported as a model; without it every
    entry stays an unclassified provider scope.

    ``workload_scope`` selects which provider scopes the returned ``windows``
    describe. It defaults to the workload this build schedules — coding and
    text — so ``video`` never reaches the windows, and therefore never reaches
    the snapshot the scheduler, temporal scarcity, and QUOTA_SAVER consume.
    """

    body = _body(payload)
    entries = _model_entries(payload)
    classified = _classified_entries(entries, provider_id)
    # The only entries this projection is permitted to read. Everything else is
    # still preserved below as an equivalent view.
    relevant = _entries_for_workload(classified, workload_scope)
    source = QuotaEvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        reference=source_uri,
        observed_at=observed_at,
        confidence=EvidenceConfidence.EXACT,
        note="Official MiniMax Token Plan remains API",
    )

    # Plan-level counts describe the shared bar directly and therefore win over
    # any per-model view of the same bar.
    interval_fraction, interval_remaining, interval_total, interval_reset = _plan_level_window(
        body,
        total_key="current_interval_total_count",
        usage_key="current_interval_usage_count",
        reset_key="current_interval_reset_time",
    )
    weekly_fraction, weekly_remaining, weekly_total, weekly_reset = _plan_level_window(
        body,
        total_key="current_weekly_total_count",
        usage_key="current_weekly_usage_count",
        reset_key="current_weekly_reset_time",
    )

    # Window boundaries come from the same scopes as the figures they bound. A
    # video window's reset time is not this plan's coding reset time.
    interval_start = _millis_datetime(_agreed_number(_field_values(relevant, "start_time")))
    interval_end = _millis_datetime(_agreed_number(_field_values(relevant, "end_time")))
    weekly_start = _millis_datetime(
        _agreed_number(_field_values(relevant, "weekly_start_time"))
    )
    weekly_end = _millis_datetime(_agreed_number(_field_values(relevant, "weekly_end_time")))

    if interval_fraction is None:
        interval_fraction = _agreed_percent(_field_values(relevant, _INTERVAL_PERCENT_KEY))
    if weekly_fraction is None:
        weekly_fraction = _agreed_percent(_field_values(relevant, _WEEKLY_PERCENT_KEY))

    interval_reset = interval_reset or interval_end
    weekly_reset = weekly_reset or weekly_end

    def build(
        window_id: str,
        window_kind: QuotaWindowKind,
        duration: float,
        fraction: float | None,
        remaining_units: float | None,
        total_units: float | None,
        started_at: datetime | None,
        reset_at: datetime | None,
    ) -> QuotaWindowSnapshot:
        confidence = (
            EvidenceConfidence.EXACT if fraction is not None else EvidenceConfidence.UNKNOWN
        )
        known = confidence is not EvidenceConfidence.UNKNOWN
        if started_at is None and reset_at is not None:
            started_at = reset_at - timedelta(seconds=duration)
        return QuotaWindowSnapshot(
            window_id=window_id,
            window_kind=window_kind,
            duration_seconds=duration,
            remaining_fraction=fraction,
            used_fraction=(None if fraction is None else 1.0 - fraction),
            remaining_units=remaining_units if known else None,
            used_units=(
                total_units - remaining_units
                if known and total_units is not None and remaining_units is not None
                else None
            ),
            total_units=total_units if known else None,
            unit=("provider_units" if known and total_units is not None else None),
            window_started_at=started_at,
            reset_at=reset_at,
            state=state_for_fraction(fraction),
            confidence=confidence,
            source=source.model_copy(update={"confidence": confidence}),
        )

    windows = (
        build(
            "5h",
            QuotaWindowKind.FIVE_HOUR,
            _FIVE_HOUR_SECONDS,
            interval_fraction,
            interval_remaining,
            interval_total,
            interval_start,
            interval_reset,
        ),
        build(
            "weekly",
            QuotaWindowKind.WEEKLY,
            _WEEKLY_SECONDS,
            weekly_fraction,
            weekly_remaining,
            weekly_total,
            weekly_start,
            weekly_reset,
        ),
    )

    readable = [
        window for window in windows if window.confidence is not EvidenceConfidence.UNKNOWN
    ]
    confidence = (
        EvidenceConfidence.EXACT
        # Every window must be readable before the plan as a whole is EXACT:
        # a known 5-hour window beside an unknown weekly one is partial truth,
        # and routing must not read partial truth as complete.
        if len(readable) == len(windows)
        else EvidenceConfidence.UNKNOWN
    )

    equivalents = _model_equivalent_views(classified, known_model_ids)
    # Scopes the provider reported for workloads this build does not schedule.
    # Recorded so the owner is told why a real balance is absent from the coding
    # card, rather than the evidence simply vanishing.
    notes = tuple(
        _WORKLOAD_SCOPE_NOTES[kind]
        for kind in excluded_workloads(
            [(scope_id, kind) for _, scope_id, kind in classified], workload_scope
        )
        if kind in _WORKLOAD_SCOPE_NOTES
    )
    # Only entries the account's catalog confirms as models are listed as
    # sharing this pool. Listing "video" as a model the owner can route to
    # would be a fabrication dressed up as provider evidence.
    covered = tuple(
        dict.fromkeys(
            view.scope_id
            for view in equivalents
            if view.scope_kind is EquivalentScopeKind.MODEL
        )
    )

    return PlanQuotaProjection(
        plan=PlanQuota(
            provider_id=provider_id,
            plan_id=plan_id,
            display_name=MINIMAX_TOKEN_PLAN_DISPLAY_NAME,
            # Officially documented: one subscription covers eligible resources
            # through a shared usage bar.
            quota_semantics=PlanQuotaSemantics.SHARED_POOL,
            observed_at=observed_at,
        ),
        pool=SharedQuotaPool(
            pool_id=quota_pool_id,
            provider_id=provider_id,
            plan_id=plan_id,
            resource_kind=QuotaResourceKind.TOKEN_PLAN_INCLUDED_QUOTA,
            shared_across_models=True,
            covered_model_ids=covered,
            unit_kind=ConsumptionUnitKind.PROVIDER_UNITS,
            provider_unit_label="token_plan_units",
        ),
        windows=windows,
        model_equivalents=equivalents,
        # Only workload-relevant windows are candidates, so an excluded scope
        # can never become the binding constraint on coding work.
        binding_window=determine_binding_window(windows, at=observed_at),
        active_workload_scope=workload_scope,
        workload_scope_notes=notes,
        state=aggregate_state(windows),
        confidence=confidence,
        source=source.model_copy(update={"confidence": confidence}),
        unknown_reason=(
            minimax_unknown_reason(
                payload, provider_id=provider_id, workload=workload_scope
            )
            if confidence is EvidenceConfidence.UNKNOWN
            else None
        ),
    )


class MiniMaxQuotaCollector:
    """Read-only MiniMax Token Plan collector.

    One authenticated HTTP ``GET`` against the documented remains endpoint. No
    completion, generation, or "ping" request is ever issued to discover quota.
    """

    def __init__(
        self,
        *,
        bearer_token: SecretValue | str | None,
        region: MiniMaxRegion = "cn",
        transport: QuotaTransport | None = None,
        timeout: float = 10.0,
        quota_pool_id: str = "minimax-token-plan-cn",
        provider_id: str = "minimax",
        plan_id: str = "token-plan",
        known_model_ids: frozenset[str] = frozenset(),
        #: The workload the resulting snapshot describes. Defaults to the one
        #: this build schedules; a video-scheduling build would pass its own.
        workload_scope: QuotaWorkloadScope = ACTIVE_CODING_WORKLOAD,
        now: object = None,
    ) -> None:
        self._token = (
            bearer_token
            if isinstance(bearer_token, SecretValue) or bearer_token is None
            else SecretValue(bearer_token)
        )
        self._endpoint = minimax_quota_endpoint(region)
        self._transport = transport or UrllibQuotaTransport()
        self._timeout = timeout
        self._quota_pool_id = quota_pool_id
        self._provider_id = provider_id
        self._plan_id = plan_id
        self._known_model_ids = known_model_ids
        self._workload_scope = workload_scope
        self._now = now if callable(now) else (lambda: datetime.now(tz=UTC))

    def collect(self) -> QuotaCollectionResult:
        if self._token is None or not self._token:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.AUTH_REQUIRED,
                error_category="CREDENTIAL_NOT_AVAILABLE",
            )
        try:
            payload = self._transport.get_json(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._token.reveal()}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except QuotaTransportError as exc:
            return QuotaCollectionResult(status=exc.status, error_category=exc.category)

        projection = normalize_minimax_quota(
            payload,
            observed_at=self._now(),
            quota_pool_id=self._quota_pool_id,
            source_uri=self._endpoint,
            provider_id=self._provider_id,
            plan_id=self._plan_id,
            known_model_ids=self._known_model_ids,
            workload_scope=self._workload_scope,
        )
        snapshot = projection.to_snapshot(quota_pool_id=self._quota_pool_id)
        if projection.confidence is not EvidenceConfidence.UNKNOWN:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.SUCCESS,
                snapshot=snapshot,
                projection=projection,
            )
        return QuotaCollectionResult(
            status=QuotaCollectionStatus.UNKNOWN,
            snapshot=snapshot if snapshot.state is not QuotaState.UNKNOWN else None,
            projection=projection,
            error_category=(
                projection.unknown_reason
                or minimax_unknown_reason(
                    payload,
                    provider_id=self._provider_id,
                    workload=self._workload_scope,
                )
            ),
        )


__all__ = [
    "MINIMAX_CN_QUOTA_DOC",
    "MINIMAX_CN_QUOTA_ENDPOINT",
    "MINIMAX_GLOBAL_QUOTA_ENDPOINT",
    "MINIMAX_OFFICIAL_CLI",
    "MINIMAX_QUOTA_DOC",
    "MINIMAX_TOKEN_PLAN_DISPLAY_NAME",
    "MINIMAX_VIDEO_SCOPE_NOTE",
    "MiniMaxQuotaCollector",
    "MiniMaxQuotaReason",
    "MiniMaxRegion",
    "minimax_quota_endpoint",
    "minimax_unknown_reason",
    "normalize_minimax_quota",
]
