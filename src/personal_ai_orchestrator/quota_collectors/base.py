"""Provider-neutral collector contract and bounded HTTP transport."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from personal_ai_orchestrator.quota_observability import QuotaSnapshot
from personal_ai_orchestrator.quota_plan import PlanQuotaProjection


class QuotaCollectionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class QuotaCollectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QuotaCollectionStatus
    snapshot: QuotaSnapshot | None = None
    last_known_good: QuotaSnapshot | None = None
    error_category: str | None = None
    #: Shared-plan view of the same observation: pool identity, covered models,
    #: per-model consumption, and per-model equivalents. Carried beside the
    #: snapshot rather than inside it so the scheduler's authoritative inputs
    #: stay exactly the provider-reported windows they were.
    projection: PlanQuotaProjection | None = None


class QuotaCollector(Protocol):
    def collect(self) -> QuotaCollectionResult: ...


class QuotaTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]: ...


class QuotaTransportError(RuntimeError):
    def __init__(self, status: QuotaCollectionStatus, category: str) -> None:
        self.status = status
        self.category = category
        super().__init__(category)


def http_status_to_error(status_code: int) -> QuotaTransportError:
    if status_code in (401, 403):
        return QuotaTransportError(QuotaCollectionStatus.AUTH_REQUIRED, f"HTTP_{status_code}")
    if status_code == 429:
        return QuotaTransportError(QuotaCollectionStatus.RATE_LIMITED, "HTTP_429")
    return QuotaTransportError(QuotaCollectionStatus.PROVIDER_ERROR, f"HTTP_{status_code}")


class UrllibQuotaTransport:
    """Read-only JSON transport that never includes response bodies in errors."""

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise http_status_to_error(exc.code) from exc
        except TimeoutError as exc:
            raise QuotaTransportError(QuotaCollectionStatus.PROVIDER_ERROR, "TIMEOUT") from exc
        except urllib.error.URLError as exc:
            category = "TIMEOUT" if isinstance(exc.reason, TimeoutError) else "PROVIDER_UNAVAILABLE"
            raise QuotaTransportError(QuotaCollectionStatus.PROVIDER_ERROR, category) from exc

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise QuotaTransportError(
                QuotaCollectionStatus.PROVIDER_ERROR,
                "INVALID_PROVIDER_JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise QuotaTransportError(
                QuotaCollectionStatus.PROVIDER_ERROR,
                "INVALID_PROVIDER_JSON",
            )
        return payload
