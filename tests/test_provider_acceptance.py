from datetime import UTC, datetime

import pytest

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.provider_acceptance import (
    AuthSurface,
    LiveProviderResult,
    ProviderSurfaceEvidence,
    QuotaSurface,
    assert_sanitized,
    live_result_from_collection,
    quota_surface_from_collection,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
)
from personal_ai_orchestrator.quota_collectors.minimax import normalize_minimax_quota
from personal_ai_orchestrator.quota_collectors.zai import normalize_zai_quota

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def test_supported_exact_provider_quota_classifies_live_exact() -> None:
    snapshot = normalize_minimax_quota(
        {
            "model_remains": [
                {
                    "current_interval_remaining_percent": 70,
                    "current_weekly_remaining_percent": 50,
                }
            ]
        },
        observed_at=NOW,
    ).to_snapshot()
    result = QuotaCollectionResult(status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot)

    evidence = ProviderSurfaceEvidence(
        provider_id="minimax",
        plan_id="token-plan",
        auth_surface=AuthSurface.EXISTING_SUPPORTED_TOKEN_REFERENCE,
        quota_surface=quota_surface_from_collection(result),
        live_result=live_result_from_collection(result),
        command_family="provider-token-plan-remains",
        observed_at=NOW,
        confidence=snapshot.confidence,
        quota_semantics="provider-reported remaining percentages for 5-hour and weekly windows",
        reset_semantics="reset timestamps absent in this fixture",
        source_method="fixture-normalized-provider-api",
        sanitized_status="SUCCESS",
        quota_snapshot_id=snapshot.id,
    )

    assert evidence.live_result is LiveProviderResult.LIVE_EXACT
    assert evidence.quota_surface is QuotaSurface.EXACT_SUPPORTED


def test_estimated_provider_quota_classifies_live_estimated() -> None:
    snapshot = normalize_zai_quota(
        {"data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 10}]}},
        observed_at=NOW,
    ).to_snapshot()
    result = QuotaCollectionResult(status=QuotaCollectionStatus.SUCCESS, snapshot=snapshot)

    evidence = ProviderSurfaceEvidence(
        provider_id="zai",
        plan_id="coding-plan",
        auth_surface=AuthSurface.PROVIDER_NATIVE_ACCOUNT_API,
        quota_surface=quota_surface_from_collection(result),
        live_result=live_result_from_collection(result),
        command_family="quota-limit",
        observed_at=NOW,
        confidence=snapshot.confidence,
        quota_semantics="remaining fraction derived from provider usage percentage",
        reset_semantics="reset timestamp unknown",
        source_method="fixture-normalized-provider-api",
        sanitized_status="SUCCESS",
        quota_snapshot_id=snapshot.id,
    )

    assert evidence.live_result is LiveProviderResult.LIVE_ESTIMATED
    assert evidence.quota_surface is QuotaSurface.ESTIMATED_SUPPORTED


def test_unknown_and_auth_unavailable_do_not_invent_quota_surface() -> None:
    unknown = QuotaCollectionResult(status=QuotaCollectionStatus.UNKNOWN)
    auth_required = QuotaCollectionResult(
        status=QuotaCollectionStatus.AUTH_REQUIRED,
        error_category="AUTHENTICATION_INTEGRATION_BLOCKED",
    )

    assert live_result_from_collection(unknown) is LiveProviderResult.LIVE_UNKNOWN
    assert quota_surface_from_collection(unknown) is QuotaSurface.UNKNOWN
    assert live_result_from_collection(auth_required) is LiveProviderResult.AUTH_REQUIRED
    assert quota_surface_from_collection(auth_required) is QuotaSurface.UNKNOWN


def test_provider_probe_failure_and_malformed_response_are_sanitized_categories() -> None:
    provider_error = QuotaCollectionResult(
        status=QuotaCollectionStatus.PROVIDER_ERROR,
        error_category="INVALID_PROVIDER_JSON",
    )

    evidence = ProviderSurfaceEvidence(
        provider_id="minimax",
        plan_id="token-plan",
        auth_surface=AuthSurface.EXISTING_SUPPORTED_TOKEN_REFERENCE,
        quota_surface=QuotaSurface.UNKNOWN,
        live_result=live_result_from_collection(provider_error),
        command_family="token-plan-remains",
        observed_at=NOW,
        confidence=EvidenceConfidence.UNKNOWN,
        quota_semantics="UNKNOWN",
        reset_semantics="UNKNOWN",
        source_method="fixture-transport-error",
        sanitized_status="PROVIDER_ERROR",
        error_category=provider_error.error_category,
    )

    assert evidence.live_result is LiveProviderResult.PROVIDER_ERROR
    assert evidence.error_category == "INVALID_PROVIDER_JSON"


def test_secret_sanitization_rejects_credential_keys_and_values() -> None:
    with pytest.raises(ValueError, match="credential-like field"):
        assert_sanitized({"authorization": "redacted"})
    with pytest.raises(ValueError, match="credential-like value"):
        assert_sanitized({"message": "Bearer abcdefghijklmnopqrstuvwxyz"})
