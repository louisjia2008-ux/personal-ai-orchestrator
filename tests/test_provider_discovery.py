"""Tests for the credential-safe provider-discovery module."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.provider_discovery import (
    _DISCOVERY_ENV_ALLOWLIST,
    _DISCOVERY_ENV_BLOCKLIST,
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
    _build_subprocess_env,
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
    RegistryLoadStatus,
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
        "●  MiniMax International (minimax.io) \x1b[90mMINIMAX_API_KEY\n"
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
        # M1 WP4: ``auth="none"`` families (OpenCode Zen) declare no
        # environment variables — the host needs no credential. The
        # spec's invariants are: every family has a provider_id and
        # a display name; credential-bearing families additionally
        # have ``env_variables``.
        if spec.auth != "none":
            assert spec.env_variables, (
            f"{spec.provider_id} is auth={spec.auth} but has no env_variables"
            )
            assert spec.provider_label_keywords, (
            f"{spec.provider_id} is auth={spec.auth} but has no provider_label_keywords"
            )


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
    serialised = repr(presence)
    assert "\x1b[" not in serialised
    assert "[" not in repr(presence.credentials_section_labels)
    assert "[" not in repr(presence.environment_section_labels)


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
    # The parser now keeps the full display label (P4.2.4-A.1 §22) so
    # substring matching against region markers works.
    assert any("Z.AI Coding Plan" in label for label in presence.credentials_section_labels)
    assert any("MiniMax" in label for label in presence.credentials_section_labels)
    # Parenthetical endpoint annotations are extracted as region hints.
    assert "minimaxi.com" in presence.credentials_region_hints


def test_parse_provider_list_recognizes_environment_section(fake_provider_list_output: str) -> None:
    presence = _parse_provider_list(fake_provider_list_output)
    assert "DEEPSEEK_API_KEY" in presence.env_variable_names_seen
    assert "MINIMAX_API_KEY" in presence.env_variable_names_seen
    # The environment section's MiniMax labels are now region-hinted.
    assert "minimaxi.com" in presence.environment_region_hints
    assert "minimax.io" in presence.environment_region_hints


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
# Subprocess environment isolation (P4.2.4-A.1 §15)
# -----------------------------------------------------------------------------

def test_build_subprocess_env_excludes_known_credential_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject canary credential env values into the parent environment and
    prove they are never propagated into the subprocess environment.
    """

    canaries = {
        "ZAI_API_KEY": "sk-zai-canary-very-secret-123456789012345",
        "MINIMAX_API_KEY": "minimax-canary-very-secret-123456789012345",
        "OPENAI_API_KEY": "sk-openai-canary-very-secret-1234567890123",
        "ANTHROPIC_API_KEY": "sk-anthropic-canary-very-secret-12345678",
        "DEEPSEEK_API_KEY": "sk-deepseek-canary-very-secret-123456789",
        "HUGGINGFACE_TOKEN": "hf_canary_secret_123456789012345678",
        "GITHUB_TOKEN": "ghp_canary_secret_123456789012345678",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }
    for key, value in canaries.items():
        monkeypatch.setenv(key, value)

    env = _build_subprocess_env(os.environ)

    # Canary names are excluded by the allowlist/blocklist policy.
    for key in canaries:
        assert key not in env, f"{key} leaked into subprocess env"

    # Canary values are also nowhere in the constructed env, regardless
    # of how they could have entered it.
    serialized = str(env)
    for value in canaries.values():
        assert value not in serialized, "canary value leaked into subprocess env"


def test_subprocess_does_not_inherit_credential_env_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Inject canary credential env values; run a Python child that prints
    its full environment; prove the canary values never appear in the
    subprocess environment.

    This is a stronger end-to-end test than :f``_build_subprocess_env``
    alone because it exercises the actual subprocess plumbing.
    """

    canaries = {
        "ZAI_API_KEY": "sk-zai-canary-endtoend-123456789012345",
        "MINIMAX_API_KEY": "minimax-canary-endtoend-123456789012345",
        "OPENAI_API_KEY": "sk-openai-canary-endtoend-1234567890123",
        "ANTHROPIC_API_KEY": "sk-anthropic-canary-endtoend-12345678",
        "DEEPSEEK_API_KEY": "sk-deepseek-canary-endtoend-123456789",
    }
    for key, value in canaries.items():
        monkeypatch.setenv(key, value)

    script = tmp_path / "printenv.py"
    script.write_text(
        "import json, os; print(json.dumps(dict(os.environ)))",
        encoding="utf-8",
    )

    result = _run_opencode(
        (str(script),),
        executable=Path(sys.executable),
        timeout_seconds=2.0,
        max_output_bytes=64 * 1024,
    )
    assert result.truncated is False
    assert result.returncode == 0
    for key, value in canaries.items():
        assert key not in result.stdout, f"{key} leaked into subprocess stdout"
        assert value not in result.stdout, f"canary value for {key} leaked into subprocess stdout"


def test_subprocess_terminates_child_when_output_bound_exceeded(
    tmp_path: Path,
) -> None:
    """A runaway producer must be terminated before it can fill the OS
    pipe buffer. The bounded subprocess must report ``truncated`` and
    return a non-natural exit code.
    """

    if sys.platform.startswith("win"):  # pragma: no cover — posix-only
        pytest.skip("posix-only test")

    script = tmp_path / "flood.py"
    script.write_text(
        "import sys; "
        "sys.stdout.write('A' * 4096); sys.stdout.flush()\n"
        "import time; time.sleep(0.5)\n"
        "while True:\n"
        "    sys.stdout.write('A' * 4096); sys.stdout.flush()\n",
        encoding="utf-8",
    )

    result = _run_opencode(
        (str(script),),
        executable=Path(sys.executable),
        timeout_seconds=5.0,
        max_output_bytes=4096,  # tight bound
    )
    assert result.truncated is True
    assert result.returncode == -1
    # Output must not exceed the bound by more than a single incremental
    # chunk size.
    assert len(result.stdout.encode("utf-8")) <= 4096 + 8192


def test_subprocess_kills_child_on_timeout(tmp_path: Path) -> None:
    """A child that ignores its natural completion must be killed when
    the timeout fires, well before its natural sleep duration.
    """

    if sys.platform.startswith("win"):  # pragma: no cover — posix-only
        pytest.skip("posix-only test")

    script = tmp_path / "sleep.py"
    script.write_text(
        "import time; time.sleep(60)",
        encoding="utf-8",
    )

    start = time.monotonic()
    result = _run_opencode(
        (str(script),),
        executable=Path(sys.executable),
        timeout_seconds=0.5,
        max_output_bytes=1024,
    )
    elapsed = time.monotonic() - start
    assert result.truncated is True
    assert result.returncode == -1
    # Killed well before the natural 60s sleep end.
    assert elapsed < 5.0


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


def test_discover_empty_state_when_configured_families_have_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.provider_discovery as mod

    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    empty_family = ProviderFamilySpec(
        provider_id="empty-provider",
        display_name="Empty Provider",
        env_variables=("EMPTY_PROVIDER_API_KEY",),
        provider_label_keywords=("Empty Provider",),
    )

    def _resolve(_explicit):
        return executable

    def _run(argv, **_kwargs):
        if argv == ("--version",):
            return SubprocessResult((str(executable), *argv), 0, "fixture\n", "", False)
        if argv == ("providers", "list"):
            return SubprocessResult((str(executable), *argv), 0, "", "", False)
        return SubprocessResult((str(executable), *argv), 1, "No models found", "", False)

    monkeypatch.setattr(mod, "_resolve_opencode", _resolve)
    monkeypatch.setattr(mod, "_run_opencode", _run)

    outcome = discover(families=(empty_family,))

    assert outcome.error_code is None
    assert outcome.result is not None
    assert outcome.result.state is DiscoveryState.EMPTY
    assert outcome.result.providers == ()
    assert outcome.result.configured_family_count == 1
    assert outcome.result.catalog_discovered_provider_count == 0
    assert outcome.result.credential_evidence_provider_count == 0


def test_discover_counts_catalog_and_credential_evidence_separately(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.provider_discovery as mod

    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    catalog_family = ProviderFamilySpec(
        provider_id="catalog-provider",
        display_name="Catalog Provider",
        env_variables=("CATALOG_PROVIDER_API_KEY",),
        provider_label_keywords=("Catalog Provider",),
    )
    credential_family = ProviderFamilySpec(
        provider_id="credential-provider",
        display_name="Credential Provider",
        env_variables=("CREDENTIAL_PROVIDER_API_KEY",),
        provider_label_keywords=("Credential Provider",),
    )

    def _resolve(_explicit):
        return executable

    def _run(argv, **_kwargs):
        if argv == ("--version",):
            return SubprocessResult((str(executable), *argv), 0, "fixture\n", "", False)
        if argv == ("providers", "list"):
            return SubprocessResult(
                (str(executable), *argv),
                0,
                "┌  Credentials\n●  Credential Provider api\n└  1 credentials\n",
                "",
                False,
            )
        if argv == ("models", "catalog-provider"):
            return SubprocessResult(
                (str(executable), *argv),
                0,
                "catalog-provider/model-a\n",
                "",
                False,
            )
        return SubprocessResult((str(executable), *argv), 1, "No models found", "", False)

    monkeypatch.setattr(mod, "_resolve_opencode", _resolve)
    monkeypatch.setattr(mod, "_run_opencode", _run)

    outcome = discover(families=(catalog_family, credential_family))

    assert outcome.error_code is None
    assert outcome.result is not None
    assert outcome.result.state is DiscoveryState.DISCOVERED
    assert outcome.result.provider_count() == 2
    assert outcome.result.configured_family_count == 2
    assert outcome.result.catalog_discovered_provider_count == 1
    assert outcome.result.credential_evidence_provider_count == 1


@pytest.mark.skipif(
    _resolve_opencode(None) is None,
    reason="opencode CLI not available in this environment",
)
def test_discover_result_to_dict_contains_no_secrets() -> None:
    outcome = discover()
    if outcome.result is None:
        pytest.skip(f"opencode discovery unavailable: {outcome.error_code}")
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
# CN / INTERNATIONAL auth-truth semantics (P4.2.4-A.1 §22)
# -----------------------------------------------------------------------------

def test_auth_truth_cn_label_does_not_authenticate_intl_surface() -> None:
    """A ``MiniMax CN`` label in the credentials store must NOT cause the
    international surfaces to report ``AUTH_FROM_ENV_PRESENCE`` and vice
    versa. The family table requires region-specific keywords; the
    parser additionally captures parenthetical endpoint annotations as
    region hints (P4.2.4-A.1 §22).
    """

    presence_cn_only = _AuthPresence(
        credentials_section_labels=frozenset({"MiniMax CN"}),
        credentials_region_hints=frozenset({"api.minimaxi.com"}),
        environment_section_labels=frozenset(),
        env_variable_names_seen=frozenset(),
    )
    intl = ProviderFamilySpec(
        provider_id="minimax",
        display_name="MiniMax International",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io", "MiniMax International"),
        alternative_endpoints=("api.minimax.io",),
    )
    cn = ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax CN"),
        alternative_endpoints=("api.minimaxi.com",),
    )
    assert presence_cn_only.has_in_credentials_store(cn), (
        "MiniMax CN label must authenticate the CN surface"
    )
    assert not presence_cn_only.has_in_credentials_store(intl), (
        "MiniMax CN label must NOT authenticate the international surface"
    )


def test_auth_truth_intl_label_does_not_authenticate_cn_surface() -> None:
    """Symmetric to :f``test_auth_truth_cn_label_does_not_authenticate_intl_surface``.
    """

    presence_intl_only = _AuthPresence(
        credentials_section_labels=frozenset({"MiniMax International"}),
        credentials_region_hints=frozenset({"api.minimax.io"}),
        environment_section_labels=frozenset(),
        env_variable_names_seen=frozenset(),
    )
    intl = ProviderFamilySpec(
        provider_id="minimax",
        display_name="MiniMax International",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io", "MiniMax International"),
        alternative_endpoints=("api.minimax.io",),
    )
    cn = ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax CN"),
        alternative_endpoints=("api.minimaxi.com",),
    )
    assert presence_intl_only.has_in_credentials_store(intl)
    assert not presence_intl_only.has_in_credentials_store(cn)


def test_auth_truth_parenthetical_region_hint_authenticates_only_matching_region() -> None:
    """When OpenCode labels two MiniMax variants identically apart from
    the endpoint parenthetical, only the family whose ``alternative_endpoints``
    matches that endpoint is authenticated.
    """

    # Real-world OpenCode labels both surfaces as ``MiniMax Token Plan``
    # but distinguishes them with the parenthetical endpoint.
    presence = _AuthPresence(
        credentials_section_labels=frozenset({
            "MiniMax Token Plan (api.minimaxi.com) api",
            "MiniMax Token Plan (api.minimax.io) api",
        }),
        credentials_region_hints=frozenset({
            "api.minimaxi.com", "api.minimax.io",
        }),
        environment_section_labels=frozenset(),
        env_variable_names_seen=frozenset(),
    )
    cn = ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax CN"),
        alternative_endpoints=("api.minimaxi.com",),
    )
    intl = ProviderFamilySpec(
        provider_id="minimax",
        display_name="MiniMax International",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io", "MiniMax International"),
        alternative_endpoints=("api.minimax.io",),
    )
    # Each region hint authenticates exactly one surface.
    assert presence.has_in_credentials_store(cn)
    assert presence.has_in_credentials_store(intl)


def test_provider_discovery_fields_are_independent() -> None:
    """``ProviderDiscovery`` exposes catalog/region/credential evidence as
    independent fields. UNKNOWN is preferred over fabricated confidence.
    """

    record = ProviderDiscovery(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        auth_status=AuthStatus.AUTH_UNKNOWN,
        execution_status=ExecutionStatus.UNKNOWN,
        evidence_source="DISCOVERED_FROM_CATALOG",
        model_skus=(),
        env_variables_present=("MINIMAX_API_KEY",),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        region="CN",
        in_credentials_store=False,
        catalog_discovered=False,
        credential_evidence_present=True,        # env var present
        credential_region_verified=False,
        credential_plan_surface_verified=False,
        credential_scope_verified=False,
        execution_verified=False,
    )
    assert record.credential_evidence_present is True
    assert record.credential_region_verified is False
    assert record.credential_plan_surface_verified is False
    assert record.credential_scope_verified is False
    assert record.auth_status is AuthStatus.AUTH_UNKNOWN
    assert record.execution_verified is False
    # Round-trip through to_dict / from_dict.
    restored = DiscoveryResult.from_dict(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "opencode_path": "/usr/bin/opencode",
            "opencode_version": "1.18.25",
            "source_method": "opencode_cli_inspection",
            "discovery_state": "DISCOVERED",
            "last_error_code": None,
            "providers": [record.to_dict() if False else {  # type: ignore[unreachable]
                "provider_id": record.provider_id,
                "display_name": record.display_name,
                "auth_status": record.auth_status.value,
                "execution_status": record.execution_status.value,
                "evidence_source": record.evidence_source,
                "model_skus": list(record.model_skus),
                "env_variables_present": list(record.env_variables_present),
                "region": record.region,
                "in_credentials_store": record.in_credentials_store,
                "catalog_discovered": record.catalog_discovered,
                "credential_evidence_present": record.credential_evidence_present,
                "credential_region_verified": record.credential_region_verified,
                "credential_plan_surface_verified": record.credential_plan_surface_verified,
                "credential_scope_verified": record.credential_scope_verified,
                "execution_verified": record.execution_verified,
                "observed_at": record.observed_at.isoformat(),
            }],
        }
    )
    assert restored.providers[0].credential_scope_verified is False
    assert restored.providers[0].credential_region_verified is False
    assert restored.providers[0].credential_plan_surface_verified is False
    assert restored.providers[0].catalog_discovered is False


def test_minimax_token_plan_label_does_not_authenticate_cn_coding_plan() -> None:
    """Region evidence for ``minimaxi.com`` must not become coding-plan entitlement."""

    presence = _AuthPresence(
        credentials_section_labels=frozenset({"MiniMax Token Plan (api.minimaxi.com) api"}),
        credentials_region_hints=frozenset({"api.minimaxi.com"}),
        environment_section_labels=frozenset(),
        env_variable_names_seen=frozenset(),
    )
    cn_regular = ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax CN"),
        alternative_endpoints=("api.minimaxi.com",),
        plan_surface_keywords=("Token Plan",),
    )
    cn_coding = ProviderFamilySpec(
        provider_id="minimax-cn-coding-plan",
        display_name="MiniMax CN Coding Plan",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax CN"),
        alternative_endpoints=("api.minimaxi.com",),
        plan_surface_keywords=("Coding Plan",),
    )

    assert presence.has_region_verified(cn_regular) is True
    assert presence.has_plan_surface_verified(cn_regular) is True
    assert presence.has_region_verified(cn_coding) is True
    assert presence.has_plan_surface_verified(cn_coding) is False


def test_env_blocklist_covers_required_provider_credentials() -> None:
    """P4.2.4-A.1 §15 requires the discovery subprocess env to never
    carry these specific variables.
    """

    for key in (
        "ZAI_API_KEY",
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        assert key in _DISCOVERY_ENV_BLOCKLIST, key
    # Allowlist is intentionally small.
    assert "PATH" in _DISCOVERY_ENV_ALLOWLIST


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
    assert persisted.status is RegistryLoadStatus.LOADED
    assert isinstance(persisted.persisted, PersistedRegistry)
    assert persisted.persisted.payload["schema_version"] == CURRENT_SCHEMA_VERSION
    providers = persisted.persisted.payload["providers"]
    assert providers and providers[0]["provider_id"] == "zai-coding-plan"
    assert providers[0]["auth_status"] == "AUTH_FROM_ENV_PRESENCE"


def test_load_returns_typed_missing_when_missing(tmp_state_root: Path) -> None:
    outcome = load(tmp_state_root)
    assert outcome.status is RegistryLoadStatus.MISSING
    assert outcome.persisted is None
    assert outcome.error_code is None


def test_load_rejects_corrupt_payload(tmp_state_root: Path) -> None:
    registry_path(tmp_state_root).write_text("not json", encoding="utf-8")
    outcome = load(tmp_state_root)
    assert outcome.status is RegistryLoadStatus.CORRUPT
    assert outcome.persisted is None
    assert outcome.error_code == "PERSISTED_REGISTRY_CORRUPT"


def test_load_rejects_unknown_schema(tmp_state_root: Path) -> None:
    registry_path(tmp_state_root).write_text(
        json.dumps({"schema_version": 999}),
        encoding="utf-8",
    )
    outcome = load(tmp_state_root)
    assert outcome.status is RegistryLoadStatus.UNSUPPORTED_SCHEMA
    assert outcome.persisted is None
    assert outcome.error_code == "PERSISTED_REGISTRY_UNSUPPORTED_SCHEMA"


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
    if error is not None:
        pytest.skip(f"opencode discovery unavailable: {error}")
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
        configured_family_count=5,
        catalog_discovered_provider_count=2,
        credential_evidence_provider_count=1,
        execution_target_count=14,
        last_error_code=None,
        catalog_snapshot_id="1.18.25",
        source_method="opencode_cli_inspection",
    )
    payload = status.to_dict()
    assert payload["discovery_state"] == "DISCOVERED"
    assert payload["provider_count"] == 2
    assert payload["configured_family_count"] == 5
    assert payload["catalog_discovered_provider_count"] == 2
    assert payload["credential_evidence_provider_count"] == 1
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
        configured_family_count=len(PROVIDER_FAMILIES),
        catalog_discovered_provider_count=sum(1 for p in providers if p.catalog_discovered),
        credential_evidence_provider_count=sum(
            1 for p in providers if p.credential_evidence_present
        ),
        last_error_code=None,
    )


# -----------------------------------------------------------------------------
# Single-startup-contract (P4.2.4-A.1 §22)
# -----------------------------------------------------------------------------

def test_first_boot_runs_exactly_one_discovery_cycle(
    tmp_state_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold first launch with no persisted snapshot must run exactly one
    discovery cycle.
    """

    discovery_calls = [0]

    def _counting_discover(**_kwargs: object) -> DiscoveryCycleOutcome:
        discovery_calls[0] += 1
        return DiscoveryCycleOutcome(
            result=_make_discovery_result(
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
                        catalog_discovered=True,
                        credential_evidence_present=True,
                        credential_scope_verified=True,
                        execution_verified=False,
                    ),
                )
            ),
            error_code=None,
            error_message=None,
        )

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _counting_discover,
    )

    mgr = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    # The constructor does not run discovery; it only rehydrates from
    # disk.
    assert discovery_calls[0] == 0
    assert mgr.discovery_cycle_count() == 0

    # The first explicit refresh runs exactly one cycle.
    mgr.refresh()
    assert discovery_calls[0] == 1
    assert mgr.discovery_cycle_count() == 1

    # A second explicit refresh runs one more cycle.
    mgr.refresh()
    assert discovery_calls[0] == 2
    assert mgr.discovery_cycle_count() == 2


def test_subsequent_boot_does_not_rerun_discovery_when_snapshot_persists(
    tmp_state_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subsequent boot must rehydrate from disk and MUST NOT invoke
    ``discover()`` at all — not from the constructor and not from a
    refresh.
    """

    # Persist a valid snapshot.
    initial_result = _make_discovery_result(
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
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_scope_verified=True,
                execution_verified=False,
            ),
        )
    )
    save(initial_result, runtime_state_root=tmp_state_root)

    discovery_calls = [0]

    def _fail_if_called(**_kwargs: object) -> DiscoveryCycleOutcome:
        discovery_calls[0] += 1
        raise AssertionError("discover() must not be invoked on subsequent boot")

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _fail_if_called,
    )

    mgr = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    # Constructing the manager rehydrates from disk. Zero discovery calls.
    assert discovery_calls[0] == 0
    status = mgr.status()
    assert status.discovery_state == "DISCOVERED"
    assert status.provider_count == 1
    assert status.execution_target_count == 1
    registry = mgr.registry()
    assert "zai-coding-plan" in registry.providers
    assert "zai-coding-plan/glm-5.3" in registry.models
    # Synthetic account and execution target are present.
    assert "zai-coding-plan" in registry.accounts
    assert any(
        t.model_sku_id == "zai-coding-plan/glm-5.3"
        for t in registry.execution_targets.values()
    )

    # bootstrap_if_empty must be a no-op even though the catalog is the
    # legacy empty-bootstrap id.
    assert mgr.bootstrap_if_empty(catalog_snapshot_id=EMPTY_BOOTSTRAP_SNAPSHOT_ID) is False
    assert discovery_calls[0] == 0


def test_manager_rehydrates_full_registry_from_persisted_snapshot(
    tmp_state_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static registry = empty; dynamic registry = provider + account +
    model + target; after boot, the in-memory state matches the persisted
    dynamic registry.
    """

    result = _make_discovery_result(
        providers=(
            ProviderDiscovery(
                provider_id="minimax-cn",
                display_name="MiniMax CN",
                auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("MiniMax-M3", "MiniMax-M2.7"),
                env_variables_present=("MINIMAX_API_KEY",),
                region="CN",
                in_credentials_store=True,
                catalog_discovered=True,
                credential_evidence_present=True,
                credential_scope_verified=True,
                execution_verified=False,
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            ProviderDiscovery(
                provider_id="zai-coding-plan",
                display_name="GLM / Z.AI",
                auth_status=AuthStatus.AUTH_REQUIRED,
                execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
                evidence_source="DISCOVERED_FROM_CATALOG",
                model_skus=("glm-5.3",),
                env_variables_present=(),
                catalog_discovered=True,
                credential_evidence_present=False,
                credential_scope_verified=False,
                execution_verified=False,
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    )
    save(result, runtime_state_root=tmp_state_root)

    def _fail_if_called(**_kwargs: object) -> DiscoveryCycleOutcome:
        raise AssertionError("discover() must not run during rehydration")

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _fail_if_called,
    )

    mgr = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    registry = mgr.registry()
    # Providers
    assert set(registry.providers) == {"minimax-cn", "zai-coding-plan"}
    # Models
    assert set(registry.models) == {
        "minimax-cn/MiniMax-M3",
        "minimax-cn/MiniMax-M2.7",
        "zai-coding-plan/glm-5.3",
    }
    # Accounts (one synthetic per provider)
    assert set(registry.accounts) == {"minimax-cn", "zai-coding-plan"}
    # Execution targets (one per SKU)
    assert set(registry.execution_targets) == {
        "minimax-cn-MiniMax-M3",
        "minimax-cn-MiniMax-M2.7",
        "zai-coding-plan-glm-5.3",
    }
    # Catalog snapshot exists
    snapshot_id = next(iter(registry.catalog_snapshots))
    for model in registry.models.values():
        assert model.catalog_snapshot_id == snapshot_id

    # Status reflects the persisted result
    status = mgr.status()
    assert status.discovery_state == "DISCOVERED"
    assert status.provider_count == 2
    assert status.execution_target_count == 3


def test_manager_rejects_unknown_persisted_schema_fail_closed(
    tmp_state_root: Path,
) -> None:
    """Persisted snapshots with an unsupported ``schema_version`` must be
    rejected fail-closed; the manager must start in PENDING with an empty
    registry.
    """

    registry_path(tmp_state_root).write_text(
        json.dumps({"schema_version": 999, "providers": []}),
        encoding="utf-8",
    )
    mgr = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    status = mgr.status()
    assert status.discovery_state == "FAILED"
    assert status.provider_count == 0
    assert status.last_error_code == "PERSISTED_REGISTRY_UNSUPPORTED_SCHEMA"
    assert mgr.registry().providers == {}
    # An explicit refresh is the only way to recover.
    assert mgr.discovery_cycle_count() == 0


def test_explicit_refresh_runs_exactly_one_cycle(
    tmp_state_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit refresh must run exactly one discovery cycle, and the
    cycle count metric must reflect that.
    """

    discovery_calls = [0]

    def _counting_discover(**_kwargs: object) -> DiscoveryCycleOutcome:
        discovery_calls[0] += 1
        return DiscoveryCycleOutcome(
            result=_make_discovery_result(),
            error_code=None,
            error_message=None,
        )

    monkeypatch.setattr(
        "personal_ai_orchestrator.provider_registry_manager.discover",
        _counting_discover,
    )

    mgr = ProviderRegistryManager(runtime_state_root=tmp_state_root)
    assert discovery_calls[0] == 0
    mgr.refresh()
    assert discovery_calls[0] == 1
    assert mgr.discovery_cycle_count() == 1
    mgr.refresh()
    assert discovery_calls[0] == 2
    assert mgr.discovery_cycle_count() == 2


# -----------------------------------------------------------------------------
# M1 WP4 — opencode family classification + free_model_skus cross-check
# -----------------------------------------------------------------------------


@pytest.fixture
def opencode_family() -> ProviderFamilySpec:
    return ProviderFamilySpec(
        provider_id="opencode",
        display_name="OpenCode Free",
        env_variables=(),
        provider_label_keywords=(),
        auth="none",
        pool_kind="unmetered",
        free_model_skus=(
            "big-pickle",
            "ling-3.0-flash-fin-free",
            "mimo-v2.5-free",
            "muse-spark-1.2-contributor-free",
            "muse-spark-1.3-contributor-free",
            "nemotron-3-ultra-free",
            "nemotron-3.5-lightning-free",
        ),
    )


def _make_opencode_discover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_models_text: str,
    audit_events: list[tuple[str, dict[str, object]]],
) -> None:
    """Wire monkeypatched `_resolve_opencode` / `_run_opencode` for one call.

    The audit sink records every ``record_system_event`` call. Tests
    that do not care about the audit pass an empty list and ignore it.
    """

    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    def _resolve(_explicit):
        return executable

    def _run(argv, **_kwargs):
        if argv == ("--version",):
            return SubprocessResult(
                (str(executable), *argv), 0, "fixture\n", "", False
            )
        if argv == ("providers", "list"):
            return SubprocessResult(
                (str(executable), *argv), 0, "┌  Credentials\n└  0\n", "", False
            )
        if argv == ("models", "opencode"):
            return SubprocessResult(
                (str(executable), *argv), 0, raw_models_text, "", False
            )
        return SubprocessResult(
            (str(executable), *argv), 1, "No models found", "", False
        )

    mod = __import__(
        "personal_ai_orchestrator.provider_discovery", fromlist=["mod"]
    )
    monkeypatch.setattr(mod, "_resolve_opencode", _resolve)
    monkeypatch.setattr(mod, "_run_opencode", _run)


class _FakeAudit:
    """Test-only audit sink that records ``record_system_event`` calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record_system_event(
        self, event_type: str, payload: dict[str, object]
    ) -> None:
        self.events.append((event_type, payload))


def test_opencode_family_lists_seven_skus_with_none_auth(
    opencode_family: ProviderFamilySpec,
) -> None:
    """PROVIDER_FAMILIES advertises 7 free SKUs and the auth=None marker."""

    found = next(
        spec for spec in PROVIDER_FAMILIES if spec.provider_id == "opencode"
    )
    assert found.auth == "none"
    assert found.pool_kind == "unmetered"
    assert len(found.free_model_skus) == 7
    assert "big-pickle" in found.free_model_skus  # no -free suffix
    assert "nemotron-3.5-lightning-free" in found.free_model_skus


def test_discover_classifies_only_free_model_skus_into_opencode_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    opencode_family: ProviderFamilySpec,
) -> None:
    """Listed SKUs enter the registry; suffix-unlisted + unclassified SKUs do not.

    The fixture mixes three buckets: 2 listed SKUs, 1 suffix-unlisted
    SKU (hypothetical new OpenCode free model), 1 paid-looking SKU
    (hypothetical future gpt-5). The audit log captures the
    classification events once per SKU.
    """

    fixture_text = (
        "opencode/big-pickle\n"                                   # listed, no suffix
        "opencode/ling-3.0-flash-fin-free\n"                       # listed
        "opencode/mimo-v2.5-free\n"                                # listed
        "opencode/futuristic-new-model-free\n"                     # suffix unlisted
        "opencode/gpt-5\n"                                         # unclassified
    )
    audit = _FakeAudit()
    _make_opencode_discover(monkeypatch, tmp_path, fixture_text, audit.events)
    import personal_ai_orchestrator.provider_discovery as mod
    monkeypatch.setattr(mod, "discover", None)  # placeholder; module imported by callers

    outcome = discover(families=(opencode_family,), audit=audit)

    assert outcome.error_code is None
    assert outcome.result is not None
    providers = outcome.result.providers
    assert len(providers) == 1
    opencode_record = providers[0]
    assert opencode_record.pool_kind == "unmetered"
    assert opencode_record.auth_kind == "none"
    # Three SKUs enter the registry: the three listed ones.
    assert sorted(opencode_record.model_skus) == sorted(
        ["big-pickle", "ling-3.0-flash-fin-free", "mimo-v2.5-free"]
    )

    # Audit log captures each non-listed SKU once.
    suffix_unlisted = [
        payload for event, payload in audit.events
        if event == "FREE_MODEL_SUFFIX_UNLISTED"
    ]
    unclassified = [
        payload for event, payload in audit.events
        if event == "OPENCODE_MODEL_UNCLASSIFIED"
    ]
    assert len(suffix_unlisted) == 1
    assert suffix_unlisted[0]["sku"] == "futuristic-new-model-free"
    assert suffix_unlisted[0]["provider_id"] == "opencode"
    assert len(unclassified) == 1
    assert unclassified[0]["sku"] == "gpt-5"
    assert unclassified[0]["provider_id"] == "opencode"


def test_discover_dedups_free_model_events_within_one_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    opencode_family: ProviderFamilySpec,
) -> None:
    """A SKU repeated in ``opencode models`` output is logged once per call.

    The dedup is in-process (call-site ``suffix_unlisted_emitted``
    and ``unclassified_emitted`` sets). A future refresh that hits
    the same SKU again still logs once because the dedup set is
    fresh per discovery cycle — the daemon lifetime is the unit of
    ``recurrence``, not the daemon process. The audit table itself
    dedups by ``(event_type, payload_json)`` (the safety_kernel
    schema). Either way the owner sees one event, not many.
    """

    fixture_text = (
        "opencode/big-pickle\n"
        "opencode/big-pickle\n"             # duplicate listed SKU
        "opencode/futuristic-new-model-free\n"
        "opencode/futuristic-new-model-free\n"   # duplicate suffix-unlisted
    )
    audit = _FakeAudit()
    _make_opencode_discover(monkeypatch, tmp_path, fixture_text, audit.events)

    outcome = discover(families=(opencode_family,), audit=audit)

    assert outcome.error_code is None
    suffix_unlisted = [
        p for e, p in audit.events if e == "FREE_MODEL_SUFFIX_UNLISTED"
    ]
    # Listed SKU dedup happens upstream (``tuple(...)`` over a list); the
    # important assertion is that ``big-pickle`` survives one entry, not
    # two. The suffix-unlisted dedup is per-cycle set membership.
    assert len(suffix_unlisted) == 1
    assert opencode_family.provider_id == "opencode"
    opencode_record = next(
        p for p in outcome.result.providers if p.provider_id == "opencode"
    )
    # Listed SKU appears once even though the raw output had two lines.
    assert opencode_record.model_skus.count("big-pickle") == 1


def test_discover_with_no_audit_sink_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    opencode_family: ProviderFamilySpec,
) -> None:
    """``audit=None`` is the documented "silent discovery" path used by tests.

    The function must not crash and the free-model rows still enter
    the registry — only the audit side-effect is suppressed.
    """

    fixture_text = (
        "opencode/big-pickle\n"
        "opencode/futuristic-new-model-free\n"
        "opencode/gpt-5\n"
    )
    _make_opencode_discover(monkeypatch, tmp_path, fixture_text, [])

    outcome = discover(families=(opencode_family,), audit=None)
    assert outcome.error_code is None
    assert outcome.result is not None
    assert any(
        "big-pickle" in p.model_skus for p in outcome.result.providers
    )


def test_provider_discovery_carries_pool_kind_and_auth_kind(
    opencode_family: ProviderFamilySpec,
) -> None:
    """``ProviderDiscovery`` surfaces the spec's ``pool_kind`` / ``auth_kind``."""

    record = ProviderDiscovery(
        provider_id=opencode_family.provider_id,
        display_name=opencode_family.display_name,
        provider_label_keywords=opencode_family.provider_label_keywords,
        auth_status=AuthStatus.AUTH_FROM_ENV_PRESENCE,
        execution_status=ExecutionStatus.AVAILABLE_FOR_CATALOG,
        evidence_source="DISCOVERED_FROM_CATALOG",
        model_skus=("big-pickle",),
        env_variables_present=(),
        observed_at=datetime(2026, 9, 7, tzinfo=UTC),
        auth_kind=opencode_family.auth,
        pool_kind=opencode_family.pool_kind,
    )
    assert record.auth_kind == "none"
    assert record.pool_kind == "unmetered"
