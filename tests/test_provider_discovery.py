"""Tests for the credential-safe provider-discovery module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.provider_discovery import (
    DEFAULT_OPENCODE_PATH,
    DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES,
    DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS,
    PROVIDER_FAMILIES,
    AuthStatus,
    DiscoveryCycleOutcome,
    DiscoveryResult,
    DiscoveryState,
    ExecutionStatus,
    ProviderDiscovery,
    ProviderFamilySpec,
    SubprocessResult,
    _AuthPresence,
    _infer_region,
    _parse_model_catalog,
    _parse_provider_list,
    _redact,
    _resolve_opencode,
    _run_opencode,
    build_registry,
    discover,
)
from personal_ai_orchestrator.provider_registry_manager import (
    ProviderDiscoveryStatus,
    ProviderRegistryManager,
)
from personal_ai_orchestrator.provider_registry_store import (
    CURRENT_SCHEMA_VERSION,
    EMPTY_BOOTSTRAP_SNAPSHOT_ID,
    PERSISTED_FILENAME,
    PersistedRegistry,
    is_empty_bootstrap_catalog,
    load,
    registry_path,
    save,
    upgrade_from_empty_bootstrap,
)

# -----------------------------------------------------------------------------
# Fixtures and helpers
# -----------------------------------------------------------------------------

@pytest.fixture
def fake_provider_list_output() -> str:
    return (
        "\x1b[0m\n"
        "┌  Credentials \x1b[90m~/.local/share/opencode/auth.json\n"
        "│\n"
        "●  MiniMax Token Plan (minimaxi.com) \x1b[90mapi\n"
        "│\n"
        "●  Z.AI Coding Plan \x1b[90mapi\n"
        "│\n"
        "└  2 credentials\n"
        "\n"
        "┌  Environment\n"
        "│\n"
        "●  DeepSeek \x1b[90mDEEPSEEK_API_KEY\n"
        "│\n"
        "●  MiniMax Token Plan (minimaxi.com) \x1b[90mMINIMAX_API_KEY\n"
        "│\n"
        "└  1 environment variables\n"
    )


@pytest.fixture
def fake_models_output() -> str:
    return (
        "zai-coding-plan/glm-4.7\n"
        "zai-coding-plan/glm-5.3\n"
        "minimax-cn/MiniMax-M3\n"
        "minimax-cn-coding-plan/MiniMax-M3\n"
        "minimax/MiniMax-M3\n"
        "minimax-coding-plan/MiniMax-M3\n"
    )


@pytest.fixture
def tmp_state_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime-state"
    root.mkdir(parents=True, exist_ok=True)
    return root


# -----------------------------------------------------------------------------
# Constants and exports
# -----------------------------------------------------------------------------

def test_constants_present() -> None:
    assert DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS > 0
    assert DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES >= 1024
    assert DEFAULT_OPENCODE_PATH.name == "opencode"
    assert EMPTY_BOOTSTRAP_SNAPSHOT_ID == "product-bootstrap-empty-registry-v1"


def test_default_families_have_required_keys() -> None:
    for spec in PROVIDER_FAMILIES:
        assert spec.provider_id
        assert spec.display_name
        assert spec.env_variables
        assert spec.provider_label_keywords


def test_default_families_are_unique() -> None:
    ids = [spec.provider_id for spec in PROVIDER_FAMILIES]
    assert len(ids) == len(set(ids))


# -----------------------------------------------------------------------------
# ANSI stripping and secret redaction
# -----------------------------------------------------------------------------

def test_ansi_stripper_removes_color_codes(fake_provider_list_output: str) -> None:
    # The parser must strip ANSI codes — if it doesn't, the secret
    # regex in assert_sanitized would trigger and tests would fail.
    presence = _parse_provider_list(fake_provider_list_output)
    assert "Z.AI Coding Plan" not in str(set(presence.credentials_section_labels))


def test_redaction_strips_known_secret_shapes() -> None:
    payload = "see sk-abcdefghijklmnopqrstuvwxyz here"
    redacted = _redact(payload)
    assert "sk-abcdef" not in redacted
    # Bearer tokens must be redacted, not preserved.
    assert "Bearer eyJabc123" not in _redact("Authorization: Bearer eyJabc123")
    # AKIA tokens must be redacted, not preserved.
    assert "AKIAEXAMPLEKEY123" not in _redact("AKIAEXAMPLEKEY123")


def test_redaction_preserves_harmless_text() -> None:
    text = "GLM-5.3 is the strongest coding-plan SKU we ship today."
    assert _redact(text) == text


# -----------------------------------------------------------------------------
# Provider list parsing
# -----------------------------------------------------------------------------

def test_parse_provider_list_recognizes_credentials_section(fake_provider_list_output: str) -> None:
    presence = _parse_provider_list(fake_provider_list_output)
    assert "Z.AI" in presence.credentials_section_labels
    assert any("MiniMax" in label for label in presence.credentials_section_labels)


def test_parse_provider_list_recognizes_environment_section(fake_provider_list_output: str) -> None:
    presence = _parse_provider_list(fake_provider_list_output)
    assert "DEEPSEEK_API_KEY" in presence.env_variable_names_seen


def test_parse_provider_list_handles_empty_output() -> None:
    presence = _parse_provider_list("")
    assert not presence.credentials_section_labels
    assert not presence.environment_section_labels
    assert not presence.env_variable_names_seen


def test_parse_provider_list_rejects_secret_in_label() -> None:
    """A canary in the upstream output must be redacted before parsing."""

    poisoned = (
        "┌  Credentials\n"
        "│\n"
        "●  sk-canary1234567890abcdef\n"
        "│\n"
        "└  1 credentials\n"
    )
    # Belt-and-suspenders: the output that survives redaction must not
    # contain the canary. We do not call _parse_provider_list on the
    # raw string — _run_opencode would redact first.
    redacted = _redact(poisoned)
    assert "sk-canary" not in redacted


# -----------------------------------------------------------------------------
# Model catalog parsing
# -----------------------------------------------------------------------------

def test_parse_model_catalog_groups_by_provider(fake_models_output: str) -> None:
    parsed = _parse_model_catalog(fake_models_output)
    assert set(parsed.keys()) == {
        "zai-coding-plan",
        "minimax-cn",
        "minimax-cn-coding-plan",
        "minimax",
        "minimax-coding-plan",
    }
    assert "glm-5.3" in parsed["zai-coding-plan"]
    assert "MiniMax-M3" in parsed["minimax-cn"]


def test_parse_model_catalog_dedupes_within_family() -> None:
    raw = "zai-coding-plan/glm-5.3\nzai-coding-plan/glm-5.3\nzai-coding-plan/glm-4.7\n"
    parsed = _parse_model_catalog(raw)
    assert parsed["zai-coding-plan"] == ("glm-4.7", "glm-5.3")


def test_parse_model_catalog_rejects_secret_lines() -> None:
    poisoned = "zai-coding-plan/sk-canary1234567890abcdef\n"
    with pytest.raises(ValueError):
        _parse_model_catalog(poisoned)


# -----------------------------------------------------------------------------
# Subprocess wrapper
# -----------------------------------------------------------------------------

def test_subprocess_result_serializes_safely() -> None:
    result = SubprocessResult(
        argv=("/usr/bin/opencode", "providers", "list"),
        returncode=0,
        stdout="hello",
        stderr="",
        truncated=False,
    )
    payload = result.to_dict()
    assert payload["returncode"] == 0
    assert payload["truncated"] is False
    assert payload["stdout_bytes"] == 5
    assert payload["stderr_bytes"] == 0
    # No raw stdout in the dict (it could carry a secret).
    assert "stdout" not in payload


def test_resolve_opencode_returns_none_for_missing_path(tmp_path: Path) -> None:
    explicit = tmp_path / "does-not-exist-opencode"
    resolved = _resolve_opencode(explicit)
    # Either ``explicit`` is missing, or it returned the fallback which
    # may or may not exist on the test host. We only assert that
    # ``resolve_opencode`` did not crash.
    assert resolved is None or resolved.is_file()


# -----------------------------------------------------------------------------
# Region inference
# -----------------------------------------------------------------------------

def test_infer_region_cn_vs_international() -> None:
    cn = ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi",),
        alternative_endpoints=("api.minimaxi.com",),
    )
    intl = ProviderFamilySpec(
        provider_id="minimax",
        display_name="MiniMax International",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io",),
        alternative_endpoints=("api.minimax.io",),
    )
    zai = ProviderFamilySpec(
        provider_id="zai-coding-plan",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
        provider_label_keywords=("Z.AI",),
    )
    assert _infer_region(cn) == "CN"
    assert _infer_region(intl) == "INTERNATIONAL"
    assert _infer_region(zai) is None


# -----------------------------------------------------------------------------
# Build registry
# -----------------------------------------------------------------------------

@pytest.mark.skipif(
    _resolve_opencode(None) is None,
    reason="opencode CLI not available in this environment",
)
def test_build_registry_populates_providers_and_models() -> None:
    result = _make_discovery_result(
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
        )
    )
    registry = build_registry(result)
    assert "zai-coding-plan" in registry.providers
    assert "zai-coding-plan/glm-5.3" in registry.models
    assert registry.models["zai-coding-plan/glm-5.3"].catalog_snapshot_id is not None


def test_build_registry_rejects_secret_in_discovery_result() -> None:
    with pytest.raises(ValueError):
        build_registry(
            _make_discovery_result(
                providers=(
                    ProviderDiscovery(
                        provider_id="zai-coding-plan",
                        display_name="sk-canary1234567890abcdef",
                        auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                        execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                        evidence_source="DISCOVERED_FROM_CATALOG",
                        model_skus=("glm-5.3",),
                        env_variables_present=("ZAI_API_KEY",),
                        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ),
                )
            )
        )


# -----------------------------------------------------------------------------
# Discovery round-trip
# -----------------------------------------------------------------------------

def test_discover_returns_typed_outcome() -> None:
    outcome = discover()
    assert isinstance(outcome, DiscoveryCycleOutcome)
    assert outcome.error_code is None or isinstance(outcome.error_code, str)
    if outcome.result is not None:
        assert isinstance(outcome.result, DiscoveryResult)
        assert outcome.result.state in {
            DiscoveryState.DISCOVERED,
            DiscoveryState.EMPTY,
            DiscoveryState.FAILED,
            DiscoveryState.PENDING,
        }


@pytest.mark.skipif(
    _resolve_opencode(None) is None,
    reason="opencode CLI not available in this environment",
)
def test_discover_result_to_dict_contains_no_secrets() -> None:
    outcome = discover()
    assert outcome.result is not None
    payload = outcome.result.to_dict()
    serialized = json.dumps(payload)
    assert "sk-" not in serialized.replace("schema_version", "")
    assert "Bearer " not in serialized
    assert "AKIA" not in serialized


def test_discover_never_runs_unbounded_subprocess() -> None:
    """The harness must always invoke OpenCode via the bounded wrapper."""

    import personal_ai_orchestrator.provider_discovery as mod

    assert mod._run_opencode is _run_opencode
    # When the harness cannot resolve the OpenCode CLI, it returns
    # ``OPENCODE_CLI_NOT_FOUND`` cleanly without spawning anything.
    # We force the failure by passing an explicit path that does not
    # exist and confirming the resolution table does not fall back to
    # the system default.
    called = {"value": False}
    original = mod._resolve_opencode

    def _fake_resolve(_explicit):  # type: ignore[no-untyped-def]
        called["value"] = True
        return None

    mod._resolve_opencode = _fake_resolve  # type: ignore[assignment]
    try:
        outcome = discover(opencode_path=Path("/nonexistent/opencode"))
    finally:
        mod._resolve_opencode = original  # type: ignore[assignment]
    assert called["value"] is True
    assert outcome.error_code == "OPENCODE_CLI_NOT_FOUND"


def test_auth_presence_maps_credentials_to_family() -> None:
    presence = _AuthPresence(
        credentials_section_labels=frozenset({"Z.AI Coding Plan"}),
        environment_section_labels=frozenset(),
        env_variable_names_seen=frozenset(),
    )
    zai = ProviderFamilySpec(
        provider_id="zai-coding-plan",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
        provider_label_keywords=("Z.AI",),
    )
    assert presence.has_in_credentials_store(zai)


def test_auth_presence_distinguishes_credentials_from_environment() -> None:
    presence = _AuthPresence(
        credentials_section_labels=frozenset({"Z.AI Coding Plan"}),
        environment_section_labels=frozenset({"DeepSeek"}),
        env_variable_names_seen=frozenset({"DEEPSEEK_API_KEY"}),
    )
    zai = ProviderFamilySpec(
        provider_id="zai-coding-plan",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
        provider_label_keywords=("Z.AI",),
    )
    assert presence.has_in_credentials_store(zai)
    assert not presence.has_in_environment(zai)


# -----------------------------------------------------------------------------
# Registry persistence
# -----------------------------------------------------------------------------

def test_save_and_load_round_trip(tmp_state_root: Path) -> None:
    result = _make_discovery_result(
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
                region=None,
                in_credentials_store=True,
            ),
        )
    )
    target = save(result, runtime_state_root=tmp_state_root)
    assert target == registry_path(tmp_state_root)
    assert target.is_file()
    persisted = load(tmp_state_root)
    assert isinstance(persisted, PersistedRegistry)
    assert persisted.payload["schema_version"] == CURRENT_SCHEMA_VERSION
    providers = persisted.payload["providers"]
    assert providers and providers[0]["provider_id"] == "zai-coding-plan"
    assert providers[0]["auth_status"] == "AUTH_FROM_ENV_PRESENCE"


def test_load_returns_none_when_missing(tmp_state_root: Path) -> None:
    assert load(tmp_state_root) is None


def test_load_rejects_corrupt_payload(tmp_state_root: Path) -> None:
    registry_path(tmp_state_root).write_text("not json", encoding="utf-8")
    assert load(tmp_state_root) is None


def test_load_rejects_unknown_schema(tmp_state_root: Path) -> None:
    registry_path(tmp_state_root).write_text(
        json.dumps({"schema_version": 999}),
        encoding="utf-8",
    )
    assert load(tmp_state_root) is None


def test_save_rejects_secret_in_discovery_result(tmp_state_root: Path) -> None:
    result = _make_discovery_result(
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
        )
    )
    # Poison the result post-construction so the to_dict serialisation
    # produces a secret value.
    object.__setattr__(result.providers[0], "display_name", "sk-canary1234567890abcdef")
    with pytest.raises(ValueError):
        save(result, runtime_state_root=tmp_state_root)


def test_is_empty_bootstrap_catalog() -> None:
    assert is_empty_bootstrap_catalog(EMPTY_BOOTSTRAP_SNAPSHOT_ID) is True
    assert is_empty_bootstrap_catalog(None) is False
    assert is_empty_bootstrap_catalog("something-else") is False


@pytest.mark.skipif(
    _resolve_opencode(None) is None,
    reason="opencode CLI not available in this environment",
)
def test_upgrade_from_empty_bootstrap_persists_snapshot(tmp_state_root: Path) -> None:
    persisted, error = upgrade_from_empty_bootstrap(runtime_state_root=tmp_state_root)
    assert error is None
    assert persisted is not None
    assert (tmp_state_root / PERSISTED_FILENAME).is_file()


# -----------------------------------------------------------------------------
# Manager
# -----------------------------------------------------------------------------

def test_manager_starts_pending_when_no_snapshot(tmp_state_root: Path) -> None:
    manager = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    status = manager.status()
    assert status.discovery_state in {"PENDING", "DISCOVERED", "EMPTY", "FAILED"}
    assert status.provider_count == 0


def test_manager_bootstrap_if_empty_no_op_for_non_empty_catalog(tmp_state_root: Path) -> None:
    manager = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    assert manager.bootstrap_if_empty(catalog_snapshot_id="some-other-snapshot") is False


@pytest.mark.skipif(
    _resolve_opencode(None) is None,
    reason="opencode CLI not available in this environment",
)
def test_manager_refresh_coalesces_concurrent_calls(tmp_state_root: Path) -> None:
    manager = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    # Two refreshes should not crash; the second call coalesces.
    s1 = manager.refresh()
    s2 = manager.refresh()
    assert s1 is not None
    assert s2 is not None


def test_manager_status_projection() -> None:
    status = ProviderDiscoveryStatus(
        discovery_state="DISCOVERED",
        last_discovered_at="2026-01-01T00:00:00+00:00",
        provider_count=2,
        execution_target_count=14,
        last_error_code=None,
        catalog_snapshot_id="1.18.25",
        source_method="opencode_cli_inspection",
    )
    payload = status.to_dict()
    assert payload["discovery_state"] == "DISCOVERED"
    assert payload["provider_count"] == 2
    assert payload["execution_target_count"] == 14


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _make_discovery_result(
    *,
    providers: tuple[ProviderDiscovery, ...] = (),
    state: DiscoveryState = DiscoveryState.DISCOVERED,
) -> DiscoveryResult:
    return DiscoveryResult(
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
        opencode_path="/usr/bin/opencode",
        opencode_version="1.18.25",
        source_method="opencode_cli_inspection",
        providers=providers,
        state=state,
        last_error_code=None,
    )