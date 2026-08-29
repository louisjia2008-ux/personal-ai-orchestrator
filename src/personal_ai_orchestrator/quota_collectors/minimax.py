"""MiniMax Token Plan quota collector.

The endpoint and Bearer authentication are documented by MiniMax's Token Plan page.
The collector only persists normalized quota fields; the credential and raw response are
never written by this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from personal_ai_orchestrator.quota_observability import (
    QuotaEvidenceSource,
    QuotaSnapshot,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)

MINIMAX_QUOTA_ENDPOINT = "https://www.minimax.io/v1/token_plan/remains"
MINIMAX_QUOTA_DOC = "https://platform.minimax.io/subscribe/token-plan"


def _percent(values: list[object]) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    first = numeric[0]
    if any(abs(value - first) > 1e-6 for value in numeric[1:]):
        return None
    if not 0.0 <= first <= 100.0:
        return None
    return first / 100.0


def _same_number(values: list[object]) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    first = numeric[0]
    if any(abs(value - first) > 1e-6 for value in numeric[1:]):
        return None
    return first


def _millis_datetime(value: float | None) -> datetime | None:
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _state_for_fraction(value: float | None) -> QuotaState:
    if value is None:
        return QuotaState.UNKNOWN
    if value == 0:
        return QuotaState.EXHAUSTED
    return QuotaState.AVAILABLE


def normalize_minimax_quota(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    quota_pool_id: str = "minimax-token-plan",
) -> QuotaSnapshot:
    remains = payload.get("model_remains")
    entries = [item for item in remains if isinstance(item, dict)] if isinstance(remains, list) else []

    def field(name: str) -> list[object]:
        return [entry.get(name) for entry in entries]

    current_remaining = _percent(field("current_interval_remaining_percent"))
    weekly_remaining = _percent(field("current_weekly_remaining_percent"))
    current_start = _millis_datetime(_same_number(field("start_time")))
    current_end = _millis_datetime(_same_number(field("end_time")))
    weekly_start = _millis_datetime(_same_number(field("weekly_start_time")))
    weekly_end = _millis_datetime(_same_number(field("weekly_end_time")))

    exact_source = QuotaEvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        source_uri=MINIMAX_QUOTA_ENDPOINT,
        observed_at=observed_at,
        confidence=EvidenceConfidence.EXACT,
        note="Official MiniMax Token Plan remains API",
    )
    unknown_source = exact_source.model_copy(update={"confidence": EvidenceConfidence.UNKNOWN})

    windows = (
        QuotaWindowSnapshot(
            window_id="5h",
            window_kind=QuotaWindowKind.FIVE_HOUR,
            duration_seconds=5 * 60 * 60,
            window_started_at=current_start,
            reset_at=current_end,
            remaining_fraction=current_remaining,
            state=_state_for_fraction(current_remaining),
            confidence=(
                EvidenceConfidence.EXACT
                if current_remaining is not None
                else EvidenceConfidence.UNKNOWN
            ),
            source=exact_source if current_remaining is not None else unknown_source,
        ),
        QuotaWindowSnapshot(
            window_id="weekly",
            window_kind=QuotaWindowKind.WEEKLY,
            duration_seconds=7 * 24 * 60 * 60,
            window_started_at=weekly_start,
            reset_at=weekly_end,
            remaining_fraction=weekly_remaining,
            state=_state_for_fraction(weekly_remaining),
            confidence=(
                EvidenceConfidence.EXACT
                if weekly_remaining is not None
                else EvidenceConfidence.UNKNOWN
            ),
            source=exact_source if weekly_remaining is not None else unknown_source,
        ),
    )
    snapshot_confidence = (
        EvidenceConfidence.EXACT
        if entries and all(window.confidence is EvidenceConfidence.EXACT for window in windows)
        else EvidenceConfidence.UNKNOWN
    )
    known_fractions = [
        value for value in (current_remaining, weekly_remaining) if value is not None
    ]
    state = (
        QuotaState.EXHAUSTED
        if known_fractions and min(known_fractions) == 0
        else QuotaState.AVAILABLE
        if known_fractions
        else QuotaState.UNKNOWN
    )
    snapshot_source = exact_source.model_copy(update={"confidence": snapshot_confidence})
    return QuotaSnapshot(
        quota_pool_id=quota_pool_id,
        provider_id="minimax",
        plan_id="token-plan",
        observed_at=observed_at,
        windows=windows,
        state=state,
        confidence=snapshot_confidence,
        source=snapshot_source,
    )


class MiniMaxQuotaCollector:
    def __init__(
        self,
        *,
        bearer_token: str | None,
        transport: QuotaTransport | None = None,
        timeout: float = 10.0,
        quota_pool_id: str = "minimax-token-plan",
    ) -> None:
        self._bearer_token = bearer_token
        self._transport = transport or UrllibQuotaTransport()
        self._timeout = timeout
        self._quota_pool_id = quota_pool_id

    def collect(self) -> QuotaCollectionResult:
        if not self._bearer_token:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.AUTH_REQUIRED,
                error_category="AUTHENTICATION_INTEGRATION_BLOCKED",
            )
        try:
            payload = self._transport.get_json(
                MINIMAX_QUOTA_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except QuotaTransportError as exc:
            return QuotaCollectionResult(status=exc.status, error_category=exc.category)

        snapshot = normalize_minimax_quota(
            payload,
            observed_at=datetime.now(tz=UTC),
            quota_pool_id=self._quota_pool_id,
        )
        status = (
            QuotaCollectionStatus.SUCCESS
            if snapshot.confidence is not EvidenceConfidence.UNKNOWN
            else QuotaCollectionStatus.UNKNOWN
        )
        return QuotaCollectionResult(status=status, snapshot=snapshot)
