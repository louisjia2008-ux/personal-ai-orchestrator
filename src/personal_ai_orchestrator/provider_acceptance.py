"""Credential-safe live provider acceptance records for P3.6."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)


class AuthSurface(StrEnum):
    EXISTING_CLI_SESSION = "EXISTING_CLI_SESSION"
    EXISTING_SUPPORTED_TOKEN_REFERENCE = "EXISTING_SUPPORTED_TOKEN_REFERENCE"
    PROVIDER_NATIVE_ACCOUNT_API = "PROVIDER_NATIVE_ACCOUNT_API"
    PROVIDER_NATIVE_CLI = "PROVIDER_NATIVE_CLI"
    NONE_SUPPORTED = "NONE_SUPPORTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


class QuotaSurface(StrEnum):
    EXACT_SUPPORTED = "EXACT_SUPPORTED"
    ESTIMATED_SUPPORTED = "ESTIMATED_SUPPORTED"
    BALANCE_ONLY = "BALANCE_ONLY"
    USAGE_ONLY = "USAGE_ONLY"
    RATE_LIMIT_ONLY = "RATE_LIMIT_ONLY"
    NONE_SUPPORTED = "NONE_SUPPORTED"
    UNKNOWN = "UNKNOWN"


class LiveProviderResult(StrEnum):
    LIVE_EXACT = "LIVE_EXACT"
    LIVE_ESTIMATED = "LIVE_ESTIMATED"
    LIVE_UNKNOWN = "LIVE_UNKNOWN"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_EXECUTED = "NOT_EXECUTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "id_token",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
)


def assert_sanitized(value: Any, *, key: str | None = None) -> None:
    """Reject credential-shaped keys or values before evidence is persisted."""

    if key is not None and key.lower() in _SENSITIVE_KEYS:
        raise ValueError(f"credential-like field {key!r} is forbidden in provider evidence")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            assert_sanitized(child_value, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_sanitized(child)
    elif isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                raise ValueError("credential-like value is forbidden in provider evidence")


class ProviderSurfaceEvidence(BaseModel):
    """One sanitized provider-surface classification.

    This records whether a safe machine-readable surface exists. It deliberately does not store
    raw provider response bodies or any credential material.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    auth_surface: AuthSurface
    quota_surface: QuotaSurface
    live_result: LiveProviderResult
    command_family: str | None = None
    observed_at: datetime
    confidence: EvidenceConfidence
    quota_semantics: str = Field(min_length=1)
    reset_semantics: str = Field(min_length=1)
    source_method: str = Field(min_length=1)
    sanitized_status: str = Field(min_length=1)
    quota_snapshot_id: str | None = None
    error_category: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> ProviderSurfaceEvidence:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        assert_sanitized(self.model_dump(mode="json"))
        if self.live_result is LiveProviderResult.LIVE_EXACT:
            if self.quota_surface is not QuotaSurface.EXACT_SUPPORTED:
                raise ValueError("LIVE_EXACT requires EXACT_SUPPORTED quota surface")
            if self.confidence is not EvidenceConfidence.EXACT:
                raise ValueError("LIVE_EXACT requires EXACT confidence")
            if self.quota_snapshot_id is None:
                raise ValueError("LIVE_EXACT requires a quota_snapshot_id")
        if self.live_result is LiveProviderResult.LIVE_ESTIMATED:
            if self.quota_surface is not QuotaSurface.ESTIMATED_SUPPORTED:
                raise ValueError("LIVE_ESTIMATED requires ESTIMATED_SUPPORTED quota surface")
            if self.confidence is not EvidenceConfidence.ESTIMATED:
                raise ValueError("LIVE_ESTIMATED requires ESTIMATED confidence")
            if self.quota_snapshot_id is None:
                raise ValueError("LIVE_ESTIMATED requires a quota_snapshot_id")
        return self


def live_result_from_collection(result: QuotaCollectionResult) -> LiveProviderResult:
    if result.status is QuotaCollectionStatus.AUTH_REQUIRED:
        return LiveProviderResult.AUTH_REQUIRED
    if result.status in {QuotaCollectionStatus.PROVIDER_ERROR, QuotaCollectionStatus.RATE_LIMITED}:
        return LiveProviderResult.PROVIDER_ERROR
    snapshot = result.snapshot or result.last_known_good
    if result.status is QuotaCollectionStatus.STALE:
        return LiveProviderResult.LIVE_UNKNOWN
    if snapshot is None:
        return LiveProviderResult.LIVE_UNKNOWN
    if snapshot.confidence is EvidenceConfidence.EXACT:
        return LiveProviderResult.LIVE_EXACT
    if snapshot.confidence is EvidenceConfidence.ESTIMATED:
        return LiveProviderResult.LIVE_ESTIMATED
    return LiveProviderResult.LIVE_UNKNOWN


def quota_surface_from_collection(result: QuotaCollectionResult) -> QuotaSurface:
    snapshot = result.snapshot or result.last_known_good
    if snapshot is None:
        return QuotaSurface.UNKNOWN
    if snapshot.confidence is EvidenceConfidence.EXACT:
        return QuotaSurface.EXACT_SUPPORTED
    if snapshot.confidence is EvidenceConfidence.ESTIMATED:
        return QuotaSurface.ESTIMATED_SUPPORTED
    return QuotaSurface.UNKNOWN


__all__ = [
    "AuthSurface",
    "LiveProviderResult",
    "ProviderSurfaceEvidence",
    "QuotaSurface",
    "assert_sanitized",
    "live_result_from_collection",
    "quota_surface_from_collection",
]
