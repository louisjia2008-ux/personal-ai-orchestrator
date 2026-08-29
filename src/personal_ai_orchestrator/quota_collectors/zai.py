"""Z.AI Coding Plan quota adapter.

Z.AI's official coding-plugin repository exposes a read-only quota-limit endpoint. The
meaning of its percentage field is treated conservatively: the official plugin labels it
usage, so remaining quota is derived and therefore ESTIMATED until provider documentation
stabilizes the raw field semantics.
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

ZAI_QUOTA_ENDPOINT = "https://api.z.ai/api/monitor/usage/quota/limit"
ZAI_USAGE_DOC = "https://zcode.z.ai/en/docs/usage-stats"


def normalize_zai_quota(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    quota_pool_id: str = "zai-coding-plan",
) -> QuotaSnapshot:
    data = payload.get("data", payload)
    limits = data.get("limits") if isinstance(data, dict) else None
    entries = [item for item in limits if isinstance(item, dict)] if isinstance(limits, list) else []
    token_limit = next((item for item in entries if item.get("type") == "TOKENS_LIMIT"), None)

    remaining_fraction = None
    if token_limit is not None:
        percentage = token_limit.get("percentage")
        if isinstance(percentage, (int, float)) and 0 <= float(percentage) <= 100:
            remaining_fraction = 1.0 - (float(percentage) / 100.0)

    confidence = (
        EvidenceConfidence.ESTIMATED
        if remaining_fraction is not None
        else EvidenceConfidence.UNKNOWN
    )
    source = QuotaEvidenceSource(
        source_type=EvidenceSourceType.PROVIDER_API,
        source_uri=ZAI_QUOTA_ENDPOINT,
        observed_at=observed_at,
        confidence=confidence,
        note="Remaining fraction derived from official quota-limit usage percentage",
    )
    window = QuotaWindowSnapshot(
        window_id="5h",
        window_kind=QuotaWindowKind.FIVE_HOUR,
        duration_seconds=5 * 60 * 60,
        reset_at=None,
        remaining_fraction=remaining_fraction,
        used_fraction=(None if remaining_fraction is None else 1.0 - remaining_fraction),
        state=(
            QuotaState.EXHAUSTED
            if remaining_fraction == 0
            else QuotaState.AVAILABLE
            if remaining_fraction is not None
            else QuotaState.UNKNOWN
        ),
        confidence=confidence,
        source=source,
    )
    return QuotaSnapshot(
        quota_pool_id=quota_pool_id,
        provider_id="zai",
        plan_id="coding-plan",
        observed_at=observed_at,
        windows=(window,),
        state=window.state,
        confidence=confidence,
        source=source,
    )


class ZAIQuotaCollector:
    def __init__(
        self,
        *,
        authorization_token: str | None,
        transport: QuotaTransport | None = None,
        timeout: float = 10.0,
        quota_pool_id: str = "zai-coding-plan",
    ) -> None:
        self._authorization_token = authorization_token
        self._transport = transport or UrllibQuotaTransport()
        self._timeout = timeout
        self._quota_pool_id = quota_pool_id

    def collect(self) -> QuotaCollectionResult:
        if not self._authorization_token:
            return QuotaCollectionResult(
                status=QuotaCollectionStatus.AUTH_REQUIRED,
                error_category="AUTHENTICATION_INTEGRATION_BLOCKED",
            )
        try:
            payload = self._transport.get_json(
                ZAI_QUOTA_ENDPOINT,
                headers={
                    "Authorization": self._authorization_token,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except QuotaTransportError as exc:
            return QuotaCollectionResult(status=exc.status, error_category=exc.category)

        snapshot = normalize_zai_quota(
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
