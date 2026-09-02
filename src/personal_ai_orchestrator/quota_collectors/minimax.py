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

The defect this rewrite fixes
-----------------------------
The previous implementation required every entry in ``model_remains`` to report
the same percentage. When they disagreed it returned ``QUOTA_VARIES_BY_MODEL``
and discarded the entire observation — windows, reset times, and every
per-model figure included. But under a documented shared pool, disagreeing
per-model percentages are not several balances; they are several *views* of one
balance. They are therefore preserved as :class:`ModelEquivalentView` and the
shared pool stays honestly UNKNOWN only when no plan-level figure can be
derived. Partial knowledge beats hiding everything (§24).

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
directly rather than through a model's lens. Percentages are used only as a
fallback, and only produce a plan figure when every model agrees — because a
single shared bar viewed through several models can only be read off directly
when the views coincide.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

MiniMaxRegion = Literal["global", "cn"]
MINIMAX_GLOBAL_QUOTA_ENDPOINT = "https://www.minimax.io/v1/token_plan/remains"
MINIMAX_CN_QUOTA_ENDPOINT = "https://api.minimaxi.com/v1/token_plan/remains"
MINIMAX_QUOTA_DOC = "https://platform.minimax.io/subscribe/token-plan"
MINIMAX_CN_QUOTA_DOC = "https://platform.minimaxi.com/subscribe/token-plan"
MINIMAX_OFFICIAL_CLI = "https://github.com/MiniMax-AI/cli"

MINIMAX_TOKEN_PLAN_DISPLAY_NAME = "MiniMax Token Plan"

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


def minimax_unknown_reason(payload: dict[str, Any]) -> str:
    """Explain why a *successful* read still yielded no plan-level figure.

    UNKNOWN with no reason reads as breakage. These codes distinguish "nothing
    was readable" from "the provider gave us per-model views that disagree" —
    the latter is a real answer, and one whose per-model detail is still shown.
    """

    entries = _model_entries(payload)
    if not entries:
        return "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"

    interval = _field_values(entries, "current_interval_remaining_percent")
    weekly = _field_values(entries, "current_weekly_remaining_percent")
    if not interval and not weekly:
        return "PROVIDER_FIELDS_UNAVAILABLE"
    for series in (interval, weekly):
        if len(series) > 1 and any(abs(value - series[0]) > 1e-6 for value in series[1:]):
            # Retained as a stable machine code, but it no longer suppresses the
            # observation: the per-model views are surfaced as equivalents.
            return "SHARED_POOL_VIEWED_PER_MODEL"
    return "PROVIDER_FIELDS_UNAVAILABLE"


def _model_equivalent_views(
    entries: list[dict[str, Any]],
) -> tuple[ModelEquivalentView, ...]:
    """Per-model views of the shared pool, for the advisory section of the card.

    Each view keeps the model's own name so the owner can see *why* the plan
    figure is or is not derivable, instead of being told only that it is not.
    """

    views: list[ModelEquivalentView] = []
    for entry in entries:
        model_id = entry.get("model") or entry.get("model_name")
        if not isinstance(model_id, str) or not model_id:
            continue
        for window_id, percent_key in (
            ("5h", "current_interval_remaining_percent"),
            ("weekly", "current_weekly_remaining_percent"),
        ):
            percent = _number(entry.get(percent_key))
            if percent is None or not 0.0 <= percent <= 100.0:
                continue
            views.append(
                ModelEquivalentView(
                    model_id=model_id,
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
                    model_id=model_id,
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
) -> PlanQuotaProjection:
    """Project the Token Plan response onto the shared-plan domain model."""

    body = _body(payload)
    entries = _model_entries(payload)
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

    interval_start = _millis_datetime(_agreed_number(_field_values(entries, "start_time")))
    interval_end = _millis_datetime(_agreed_number(_field_values(entries, "end_time")))
    weekly_start = _millis_datetime(
        _agreed_number(_field_values(entries, "weekly_start_time"))
    )
    weekly_end = _millis_datetime(_agreed_number(_field_values(entries, "weekly_end_time")))

    if interval_fraction is None:
        interval_fraction = _agreed_percent(
            _field_values(entries, "current_interval_remaining_percent")
        )
    if weekly_fraction is None:
        weekly_fraction = _agreed_percent(
            _field_values(entries, "current_weekly_remaining_percent")
        )

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
        if entries and len(readable) == len(windows)
        else EvidenceConfidence.UNKNOWN
    )

    covered = tuple(
        dict.fromkeys(
            model_id
            for entry in entries
            if isinstance(model_id := entry.get("model") or entry.get("model_name"), str)
            and model_id
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
        model_equivalents=_model_equivalent_views(entries),
        binding_window=determine_binding_window(windows, at=observed_at),
        state=aggregate_state(windows),
        confidence=confidence,
        source=source.model_copy(update={"confidence": confidence}),
        unknown_reason=(
            minimax_unknown_reason(payload)
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
            error_category=projection.unknown_reason or minimax_unknown_reason(payload),
        )


__all__ = [
    "MINIMAX_CN_QUOTA_DOC",
    "MINIMAX_CN_QUOTA_ENDPOINT",
    "MINIMAX_GLOBAL_QUOTA_ENDPOINT",
    "MINIMAX_OFFICIAL_CLI",
    "MINIMAX_QUOTA_DOC",
    "MINIMAX_TOKEN_PLAN_DISPLAY_NAME",
    "MiniMaxQuotaCollector",
    "MiniMaxRegion",
    "minimax_quota_endpoint",
    "minimax_unknown_reason",
    "normalize_minimax_quota",
]
