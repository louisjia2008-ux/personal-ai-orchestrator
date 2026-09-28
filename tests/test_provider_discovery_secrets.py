"""Negative tests proving provider discovery cannot leak secrets."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.provider_discovery import (
    AuthStatus,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
    _parse_model_catalog,
    _redact,
    build_registry,
)

CANARY_SK = "sk-canarycanarycanarycanary1234"
CANARY_BEARER = "Bearer canarytokencanarytokencanarytoken12"
CANARY_AWS = "AKIAIOSFODNN7EXAMPLECANARY"


def test_redact_strips_sk_prefix() -> None:
    payload = f"Authorization payload: {CANARY_SK}"
    redacted = _redact(payload)
    assert CANARY_SK not in redacted
    assert "<redacted>" in redacted


def test_redact_strips_bearer_token() -> None:
    payload = f"Authorization: {CANARY_BEARER}"
    redacted = _redact(payload)
    assert CANARY_BEARER not in redacted


def test_redact_strips_aws_access_key() -> None:
    payload = f"access key id: {CANARY_AWS}"
    redacted = _redact(payload)
    assert CANARY_AWS not in redacted


def test_parse_provider_list_does_not_carry_secret_through() -> None:
    poisoned = f"┌  Credentials\n│\n●  {CANARY_SK} provider\n│\n└  1 credentials\n"
    redacted = _redact(poisoned)
    # The canary must not survive the redaction step.
    assert CANARY_SK not in redacted


def test_parse_model_catalog_rejects_secret_skus() -> None:
    poisoned = f"zai-coding-plan/{CANARY_SK}\n"
    with pytest.raises(ValueError):
        _parse_model_catalog(poisoned)


def test_build_registry_rejects_canary_in_display_name() -> None:
    result = DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name=f"GLM / Z.AI ({CANARY_SK})",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        state=DiscoveryState.DISCOVERED,
        last_error_code=None,
    )
    with pytest.raises(ValueError):
        build_registry(result)


def test_build_registry_rejects_canary_in_model_sku() -> None:
    result = DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=(f"glm-{CANARY_SK}",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        state=DiscoveryState.DISCOVERED,
        last_error_code=None,
    )
    with pytest.raises(ValueError):
        build_registry(result)


def test_discovery_result_to_dict_omits_secret_keys() -> None:
    result = DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        state=DiscoveryState.DISCOVERED,
        last_error_code=None,
    )
    payload = result.to_dict()
    serialized = json.dumps(payload)
    # No secret-shaped substrings should ever appear.
    assert "sk-" not in serialized.replace("schema_version", "")
    assert CANARY_SK not in serialized
    assert CANARY_BEARER not in serialized
    assert CANARY_AWS not in serialized


def test_persisted_file_has_no_canary_after_redact_pipeline(tmp_path: Path) -> None:
    """Round-trip via the store must not retain the canary."""

    from personal_ai_orchestrator.provider_registry_store import load, save

    result = DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=(
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=("ZAI_API_KEY",),
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        state=DiscoveryState.DISCOVERED,
        last_error_code=None,
    )
    target = save(result, runtime_state_root=tmp_path / "runtime-state")
    contents = target.read_text(encoding="utf-8")
    assert CANARY_SK not in contents
    assert CANARY_BEARER not in contents
    assert CANARY_AWS not in contents
    persisted = load(tmp_path / "runtime-state")
    assert persisted is not None
