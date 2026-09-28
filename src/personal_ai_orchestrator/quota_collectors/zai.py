"""GLM Coding Plan quota adapter (Z.AI / BigModel).

Product naming
--------------
The provider's product is **GLM Coding Plan**. It is not renamed to "Token
Plan" for symmetry with MiniMax: the owner reconciles this screen against the
Z.AI console, and a renamed product breaks that reconciliation.

Observed response schema (``GET /api/monitor/usage/quota/limit``)
-----------------------------------------------------------------
Verified against a live authenticated read on 2026-09-02::

    {"code": 200, "msg": "...", "success": true,
     "data": {"level": "lite",
              "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5,
                 "usage": 2000, "currentValue": 0, "remaining": 2000,
                 "percentage": 0},
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1,
                 "usage": 10000, "currentValue": 8387, "remaining": 1612,
                 "percentage": 83, "nextResetTime": 1788671284992}]}}

Field semantics that are easy to get wrong, and were:

``type``
    Currently ``CREDIT_LIMIT``. The widely-copied community plugin still
    matches ``TOKENS_LIMIT``, which the provider appears to have renamed; both
    are accepted here so a rename in either direction does not blank the page.
``unit``/``number``
    The window discriminator, and the *only* way to tell the two entries apart
    — both carry the same ``type``. ``unit=3, number=5`` is the 5-hour window;
    ``unit=6, number=1`` is the weekly window. The previous implementation took
    the first matching entry and labelled it ``5h``, which silently discarded
    the weekly window — usually the binding one.
``usage``
    The window's **capacity**, despite the name. Not consumption.
``currentValue``
    Consumed. ``remaining == usage - currentValue``.
``percentage``
    Percent **used**, integer-rounded. Because exact counts are present,
    remaining is derived from the counts instead, which makes the figure EXACT
    rather than a rounded-percentage estimate.

The pool is denominated in plan credits, while model usage
(``/api/monitor/usage/model-usage``) is denominated in tokens. The provider
publishes no conversion between them, so no multiplier is inferred and no
token figure is ever divided into a credit remainder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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
    ModelConsumptionObservation,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
    aggregate_state,
    determine_binding_window,
    state_for_fraction,
)

ZAI_QUOTA_ENDPOINT = "https://api.z.ai/api/monitor/usage/quota/limit"
ZAI_MODEL_USAGE_ENDPOINT = "https://api.z.ai/api/monitor/usage/model-usage"
ZAI_USAGE_DOC = "https://zcode.z.ai/en/docs/usage-stats"

GLM_CODING_PLAN_DISPLAY_NAME = "GLM Coding Plan"

#: Provider-declared limit types we recognize. ``CREDIT_LIMIT`` is what the API
#: returns today; ``TOKENS_LIMIT`` is the earlier name still shipped by the
#: community plugin. Accepting both keeps a provider rename from blanking the
#: Quota page, and an unrecognized type stays UNKNOWN rather than being guessed.
_PLAN_LIMIT_TYPES: frozenset[str] = frozenset({"CREDIT_LIMIT", "TOKENS_LIMIT"})
_MCP_LIMIT_TYPE = "TIME_LIMIT"

#: ``(unit, number)`` → window identity. These pairs are the provider's own
#: discriminator; nothing else in the entry distinguishes 5-hour from weekly.
_WINDOW_BY_UNIT_NUMBER: dict[tuple[int, int], tuple[str, QuotaWindowKind, float]] = {
    (3, 5): ("5h", QuotaWindowKind.FIVE_HOUR, 5 * 60 * 60.0),
    (6, 1): ("weekly", QuotaWindowKind.WEEKLY, 7 * 24 * 60 * 60.0),
}


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


def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(limits, list):
        return []
    return [item for item in limits if isinstance(item, dict)]


def _plan_level(payload: dict[str, Any]) -> str | None:
    data = payload.get("data", payload)
    level = data.get("level") if isinstance(data, dict) else None
    return level if isinstance(level, str) and level else None


def _window_for(entry: dict[str, Any]) -> tuple[str, QuotaWindowKind, float] | None:
    unit = _number(entry.get("unit"))
    number = _number(entry.get("number"))
    if unit is None or number is None:
        return None
    return _WINDOW_BY_UNIT_NUMBER.get((int(unit), int(number)))


def _remaining_fraction(entry: dict[str, Any]) -> tuple[float | None, EvidenceConfidence]:
    """Remaining fraction for one limit entry, with honest confidence.

    Exact counts are preferred over ``percentage`` because ``percentage`` is
    integer-rounded: at 8387/10000 it reports 83, losing the .87. Counts give
    the true fraction, so the figure earns EXACT. Falling back to the rounded
    percentage yields ESTIMATED, which is what it is.
    """

    capacity = _number(entry.get("usage"))
    remaining = _number(entry.get("remaining"))
    consumed = _number(entry.get("currentValue"))

    if capacity is not None and capacity > 0:
        if remaining is None and consumed is not None:
            remaining = capacity - consumed
        if remaining is not None:
            fraction = max(0.0, min(1.0, remaining / capacity))
            return fraction, EvidenceConfidence.EXACT

    percentage = _number(entry.get("percentage"))
    if percentage is not None and 0.0 <= percentage <= 100.0:
        return max(0.0, min(1.0, 1.0 - percentage / 100.0)), EvidenceConfidence.ESTIMATED

    return None, EvidenceConfidence.UNKNOWN


def zai_unknown_reason(payload: dict[str, Any]) -> str:
    """Explain a successful read that produced no usable plan figure."""

    entries = _entries(payload)
    if not entries:
        return "PROVIDER_REPORTED_NO_QUOTA_ENTRIES"
    plan_entries = [entry for entry in entries if entry.get("type") in _PLAN_LIMIT_TYPES]
    if not plan_entries:
        return "PROVIDER_LIMIT_TYPE_UNRECOGNIZED"
    if not any(_window_for(entry) for entry in plan_entries):
        return "PROVIDER_WINDOW_DIMENSION_UNRECOGNIZED"
    return "PROVIDER_FIELDS_UNAVAILABLE"


def normalize_zai_quota(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    quota_pool_id: str = "zai-coding-plan",
    provider_id: str = "zai",
    plan_id: str = "coding-plan",
    covered_model_ids: tuple[str, ...] = (),
) -> PlanQuotaProjection:
    """Project the quota-limit response onto the shared-plan domain model.

    Every window the provider reported is preserved as its own window. A window
    we could not read stays UNKNOWN and carries no fraction, rather than
    disqualifying the windows we *could* read.
    """

    entries = _entries(payload)
    source = QuotaEvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        reference=ZAI_QUOTA_ENDPOINT,
        observed_at=observed_at,
        confidence=EvidenceConfidence.EXACT,
        note="Official GLM Coding Plan quota-limit API",
    )

    windows: list[QuotaWindowSnapshot] = []
    for entry in entries:
        if entry.get("type") not in _PLAN_LIMIT_TYPES:
            continue
        window_identity = _window_for(entry)
        if window_identity is None:
            continue
        window_id, window_kind, duration = window_identity
        fraction, confidence = _remaining_fraction(entry)
        capacity = _number(entry.get("usage"))
        remaining_units = _number(entry.get("remaining"))
        used_units = _number(entry.get("currentValue"))
        reset_at = _millis_datetime(entry.get("nextResetTime"))
        window_started_at = reset_at - timedelta(seconds=duration) if reset_at is not None else None
        known = confidence is not EvidenceConfidence.UNKNOWN
        windows.append(
            QuotaWindowSnapshot(
                window_id=window_id,
                window_kind=window_kind,
                duration_seconds=duration,
                remaining_fraction=fraction,
                used_fraction=(None if fraction is None else 1.0 - fraction),
                remaining_units=remaining_units if known else None,
                used_units=used_units if known else None,
                total_units=capacity if known else None,
                unit=("plan_credits" if capacity is not None else None),
                window_started_at=window_started_at,
                reset_at=reset_at,
                state=state_for_fraction(fraction),
                confidence=confidence,
                source=source.model_copy(update={"confidence": confidence}),
            )
        )

    # The monthly MCP allowance is a separate resource of the same plan, not a
    # second view of the prompt pool; it is reported as its own window so it can
    # never be mistaken for the plan's remaining coding quota.
    for entry in entries:
        if entry.get("type") != _MCP_LIMIT_TYPE:
            continue
        fraction, confidence = _remaining_fraction(entry)
        known = confidence is not EvidenceConfidence.UNKNOWN
        windows.append(
            QuotaWindowSnapshot(
                window_id="monthly-mcp",
                window_kind=QuotaWindowKind.MONTHLY,
                duration_seconds=30 * 24 * 60 * 60.0,
                remaining_fraction=fraction,
                used_fraction=(None if fraction is None else 1.0 - fraction),
                remaining_units=(_number(entry.get("remaining")) if known else None),
                used_units=_number(entry.get("currentValue")) if known else None,
                total_units=_number(entry.get("usage")) if known else None,
                unit=("mcp_calls" if _number(entry.get("usage")) is not None else None),
                reset_at=_millis_datetime(entry.get("nextResetTime")),
                state=state_for_fraction(fraction),
                confidence=confidence,
                source=source.model_copy(update={"confidence": confidence}),
            )
        )
        break

    window_tuple = tuple(windows)
    state = aggregate_state(window_tuple)
    readable = [
        window for window in window_tuple if window.confidence is not EvidenceConfidence.UNKNOWN
    ]
    if not readable:
        confidence = EvidenceConfidence.UNKNOWN
    elif all(window.confidence is EvidenceConfidence.EXACT for window in readable):
        confidence = EvidenceConfidence.EXACT
    else:
        confidence = EvidenceConfidence.ESTIMATED

    return PlanQuotaProjection(
        plan=PlanQuota(
            provider_id=provider_id,
            plan_id=plan_id,
            display_name=GLM_CODING_PLAN_DISPLAY_NAME,
            # Officially documented: supported coding tools share the
            # subscription quota (zcode.z.ai usage-stats docs).
            quota_semantics=PlanQuotaSemantics.SHARED_POOL,
            plan_level=_plan_level(payload),
            observed_at=observed_at,
        ),
        pool=SharedQuotaPool(
            pool_id=quota_pool_id,
            provider_id=provider_id,
            plan_id=plan_id,
            resource_kind=QuotaResourceKind.CODING_PLAN_USAGE_POOL,
            shared_across_models=True,
            covered_model_ids=covered_model_ids,
            unit_kind=ConsumptionUnitKind.PLAN_CREDITS,
            provider_unit_label="CREDIT_LIMIT",
        ),
        windows=window_tuple,
        binding_window=determine_binding_window(window_tuple, at=observed_at),
        state=state,
        confidence=confidence,
        source=source.model_copy(update={"confidence": confidence}),
        unknown_reason=(
            zai_unknown_reason(payload) if confidence is EvidenceConfidence.UNKNOWN else None
        ),
    )


def _parse_usage_timestamp(value: object) -> datetime | None:
    """Parse the ``yyyy-MM-dd HH:mm`` bucket labels the usage API returns.

    The provider reports these in the account's local wall-clock without an
    offset. They are interpreted as local time and converted to UTC, because
    stamping them UTC directly would shift every consumption record by the
    machine's offset.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
        return naive.astimezone().astimezone(UTC)
    return None


def normalize_zai_model_usage(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    quota_pool_id: str = "zai-coding-plan",
    provider_id: str = "zai",
    plan_id: str = "coding-plan",
) -> tuple[ModelConsumptionObservation, ...]:
    """Project ``/model-usage`` onto per-model consumption observations.

    Consumption only. These figures describe each model's contribution to the
    shared pool and are never converted into a per-model remaining balance —
    they are not even denominated in the pool's unit.

    ``modelDataList[].tokensUsage`` is an hourly series aligned to ``x_time``
    and covering the requested period; it is summed for the period figure.
    ``totalTokens`` on the same entry is a larger provider-side lifetime total,
    so the two are deliberately not mixed.
    """

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return ()
    rows = data.get("modelDataList")
    if not isinstance(rows, list):
        return ()

    buckets = data.get("x_time") if isinstance(data.get("x_time"), list) else []
    period_start = _parse_usage_timestamp(buckets[0]) if buckets else None
    period_end = _parse_usage_timestamp(buckets[-1]) if buckets else None
    if period_start is not None and period_end is not None and period_end <= period_start:
        period_start = period_end = None

    observations: list[ModelConsumptionObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("modelName")
        if not isinstance(model_id, str) or not model_id:
            continue
        series = row.get("tokensUsage")
        if not isinstance(series, list):
            continue
        consumed = sum(value for value in (_number(item) for item in series) if value is not None)
        observations.append(
            ModelConsumptionObservation(
                provider_id=provider_id,
                plan_id=plan_id,
                pool_id=quota_pool_id,
                model_id=model_id,
                observed_at=observed_at,
                consumed_units=max(0.0, consumed),
                unit_kind=ConsumptionUnitKind.TOKENS,
                provider_unit_label="tokensUsage",
                period_start=period_start,
                period_end=period_end,
                confidence=EvidenceConfidence.EXACT,
                measurement_source=MeasurementSource.PROVIDER_USAGE_API,
            )
        )
    return tuple(observations)


class ZAIQuotaCollector:
    """Read-only GLM Coding Plan collector.

    Issues at most two authenticated HTTP ``GET`` requests against documented
    monitoring endpoints. It has no completion surface at all, so it cannot
    spend the quota it measures — :func:`quota_refresh.assert_read_only_collector`
    enforces that structurally.
    """

    #: How far back model usage is queried. One day keeps the response small
    #: while covering the active 5-hour window several times over.
    USAGE_LOOKBACK = timedelta(days=1)

    def __init__(
        self,
        *,
        authorization_token: SecretValue | str | None,
        transport: QuotaTransport | None = None,
        timeout: float = 10.0,
        quota_pool_id: str = "zai-coding-plan",
        provider_id: str = "zai",
        plan_id: str = "coding-plan",
        collect_model_usage: bool = True,
        now: object = None,
    ) -> None:
        self._token = (
            authorization_token
            if isinstance(authorization_token, SecretValue) or authorization_token is None
            else SecretValue(authorization_token)
        )
        self._transport = transport or UrllibQuotaTransport()
        self._timeout = timeout
        self._quota_pool_id = quota_pool_id
        self._provider_id = provider_id
        self._plan_id = plan_id
        self._collect_model_usage = collect_model_usage
        self._now = now if callable(now) else (lambda: datetime.now(tz=UTC))

    def _headers(self) -> dict[str, str]:
        # Z.AI expects the raw token with no "Bearer " prefix.
        token = self._token
        assert token is not None
        return {
            "Authorization": token.reveal(),
            "Accept-Language": "en-US,en",
            "Content-Type": "application/json",
        }

    def _model_usage(self, observed_at: datetime) -> tuple[ModelConsumptionObservation, ...]:
        """Best-effort model usage. A failure here never fails the plan read.

        Plan quota is the load-bearing figure; losing the usage breakdown
        should degrade the card, not blank it.
        """

        end = observed_at.astimezone()
        start = end - self.USAGE_LOOKBACK
        pattern = "%Y-%m-%d %H:%M:%S"
        url = (
            f"{ZAI_MODEL_USAGE_ENDPOINT}"
            f"?startTime={start.strftime(pattern).replace(' ', '%20')}"
            f"&endTime={end.strftime(pattern).replace(' ', '%20')}"
        )
        try:
            payload = self._transport.get_json(url, headers=self._headers(), timeout=self._timeout)
        except QuotaTransportError:
            return ()
        if payload.get("success") is False:
            return ()
        return normalize_zai_model_usage(
            payload,
            observed_at=observed_at,
            quota_pool_id=self._quota_pool_id,
            provider_id=self._provider_id,
            plan_id=self._plan_id,
        )

    def collect(self) -> QuotaCollectionResult:
        if self._token is None or not self._token:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.AUTH_REQUIRED,
                error_category="CREDENTIAL_NOT_AVAILABLE",
            )
        try:
            payload = self._transport.get_json(
                ZAI_QUOTA_ENDPOINT,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except QuotaTransportError as exc:
            return QuotaCollectionResult(status=exc.status, error_category=exc.category)

        # A 200 carrying ``success: false`` is a provider-level failure the HTTP
        # layer cannot see; treating it as data would persist an empty plan.
        if payload.get("success") is False:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.PROVIDER_ERROR,
                error_category="PROVIDER_REPORTED_FAILURE",
            )

        observed_at = self._now()
        projection = normalize_zai_quota(
            payload,
            observed_at=observed_at,
            quota_pool_id=self._quota_pool_id,
            provider_id=self._provider_id,
            plan_id=self._plan_id,
        )
        if self._collect_model_usage:
            consumption = self._model_usage(observed_at)
            if consumption:
                projection = projection.model_copy(update={"model_consumption": consumption})

        snapshot = projection.to_snapshot(quota_pool_id=self._quota_pool_id)
        if projection.confidence is not EvidenceConfidence.UNKNOWN:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.SUCCESS,
                snapshot=snapshot,
                projection=projection,
            )
        # A read that succeeded but yielded no usable plan figure still owes the
        # owner a reason, and still carries whatever model usage we did read.
        return QuotaCollectionResult(
            status=QuotaCollectionStatus.UNKNOWN,
            snapshot=snapshot if snapshot.state is not QuotaState.UNKNOWN else None,
            projection=projection,
            error_category=projection.unknown_reason or zai_unknown_reason(payload),
        )


__all__ = [
    "GLM_CODING_PLAN_DISPLAY_NAME",
    "ZAI_MODEL_USAGE_ENDPOINT",
    "ZAI_QUOTA_ENDPOINT",
    "ZAI_USAGE_DOC",
    "ZAIQuotaCollector",
    "normalize_zai_model_usage",
    "normalize_zai_quota",
    "zai_unknown_reason",
]
