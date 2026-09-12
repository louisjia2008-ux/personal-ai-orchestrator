from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.pi_provider_discovery import (
    PI_PROVIDER_SPECS,
    PI_SOURCE_METHOD,
    PiAuthStatus,
    PiDiscoveryState,
    PiProviderSpec,
    _parse_pi_auth_check,
    _parse_pi_model_table,
    _resolve_pi,
    _run_pi,
    build_pi_registry,
    discover_pi,
)
from personal_ai_orchestrator.provider_discovery import SubprocessResult

HARDENED_MODEL_ARGV = (
    "--offline",
    "--no-approve",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
    "--list-models",
    "zai",
)


def test_verified_pi_provider_mapping_is_explicit_and_unique() -> None:
    assert PI_PROVIDER_SPECS
    pao_ids = [spec.pao_provider_id for spec in PI_PROVIDER_SPECS]
    runtime_ids = [spec.pi_provider_id for spec in PI_PROVIDER_SPECS]
    assert len(pao_ids) == len(set(pao_ids))
    assert len(runtime_ids) == len(set(runtime_ids))
    zai = next(
        spec for spec in PI_PROVIDER_SPECS if spec.pao_provider_id == "zai-coding-plan"
    )
    assert zai.pi_provider_id == "zai"


def test_parse_pi_auth_check_ready_and_not_ready() -> None:
    ready = _parse_pi_auth_check(
        '{"status":"ready","provider":"zai","authType":"api_key"}',
        expected_provider="zai",
    )
    assert ready.status is PiAuthStatus.READY
    assert ready.auth_type == "api_key"
    assert ready.reason is None

    not_ready = _parse_pi_auth_check(
        '{"status":"not_ready","provider":"zai","reason":"credentials_not_configured"}',
        expected_provider="zai",
    )
    assert not_ready.status is PiAuthStatus.NOT_READY
    assert not_ready.reason == "credentials_not_configured"


def test_parse_pi_auth_check_rejects_credential_fields_and_provider_mismatch() -> None:
    with pytest.raises(ValueError, match="EXPOSED_CREDENTIAL_FIELD"):
        _parse_pi_auth_check(
            '{"status":"ready","provider":"zai","credential":"secret"}',
            expected_provider="zai",
        )
    with pytest.raises(ValueError, match="PROVIDER_MISMATCH"):
        _parse_pi_auth_check(
            '{"status":"ready","provider":"minimax"}',
            expected_provider="zai",
        )


def test_parse_pi_model_table_matches_real_pi_column_contract() -> None:
    stdout = "\n".join(
        (
            "provider  model              context  max-out  thinking  images",
            "zai       glm-4.7            128K     64K      yes       no",
            "zai       glm-5.3            200K     128K     yes       no",
            "zai       glm-5.3-highspeed  200K     128K     yes       no",
        )
    )
    assert _parse_pi_model_table(stdout, expected_provider="zai") == (
        "glm-4.7",
        "glm-5.3",
        "glm-5.3-highspeed",
    )


def test_parse_pi_model_table_accepts_canonical_provider_model_fallback() -> None:
    stdout = "zai/glm-5.3\nzai/glm-5.3\nminimax/MiniMax-M3\n"
    assert _parse_pi_model_table(stdout, expected_provider="zai") == ("glm-5.3",)


def test_parse_pi_model_table_ignores_other_providers_and_invalid_rows() -> None:
    stdout = "\n".join(
        (
            "provider  model  context max-out thinking images",
            "minimax MiniMax-M3 200K 64K yes no",
            "zai ../../secret 200K 64K yes no",
            "not a model row",
        )
    )
    assert _parse_pi_model_table(stdout, expected_provider="zai") == ()


def test_resolve_pi_returns_explicit_executable(tmp_path: Path) -> None:
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    assert _resolve_pi(executable) == executable


def test_pi_discovery_subprocess_does_not_inherit_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "sk-zai-canary-should-not-reach-child-123456789")
    script = tmp_path / "printenv.py"
    script.write_text(
        "import json, os; print(json.dumps(dict(os.environ)))",
        encoding="utf-8",
    )
    result = _run_pi(
        (str(script),),
        executable=Path(sys.executable),
        timeout_seconds=2.0,
    )
    assert result.returncode == 0
    assert "ZAI_API_KEY" not in result.stdout
    assert "sk-zai-canary" not in result.stdout


def test_discover_pi_builds_runtime_specific_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_discovery as mod

    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    spec = PiProviderSpec(
        pao_provider_id="zai-coding-plan",
        pi_provider_id="zai",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
    )

    def fake_resolve(_explicit: Path | None) -> Path:
        return executable

    def fake_run(argv, **_kwargs):
        full = (str(executable), *argv)
        if argv == ("--version",):
            return SubprocessResult(full, 0, "0.85.1\n", "", False)
        if argv == (
            "auth",
            "check",
            "--provider",
            "zai",
            "--json",
            "--no-refresh",
        ):
            return SubprocessResult(
                full,
                0,
                '{"status":"ready","provider":"zai","authType":"api_key"}\n',
                "",
                False,
            )
        if argv == HARDENED_MODEL_ARGV:
            return SubprocessResult(
                full,
                0,
                "provider  model    context  max-out  thinking  images\n"
                "zai       glm-5.3  200K     128K     yes       no\n",
                "",
                False,
            )
        raise AssertionError(f"unexpected argv: {argv!r}")

    monkeypatch.setattr(mod, "_resolve_pi", fake_resolve)
    monkeypatch.setattr(mod, "_run_pi", fake_run)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    outcome = discover_pi(
        specs=(spec,),
        clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
    )

    assert outcome.error_code is None
    assert outcome.result is not None
    assert outcome.result.state is PiDiscoveryState.DISCOVERED
    assert outcome.result.source_method == PI_SOURCE_METHOD
    assert outcome.result.pi_version == "0.85.1"
    assert outcome.result.provider_count() == 1
    record = outcome.result.providers[0]
    assert record.provider_id == "zai-coding-plan"
    assert record.runtime_provider_id == "zai"
    assert record.auth_status is PiAuthStatus.READY
    assert record.model_skus == ("glm-5.3",)

    registry = build_pi_registry(outcome.result)
    assert "zai-coding-plan" in registry.providers
    assert "zai-coding-plan/glm-5.3" in registry.models
    target = registry.execution_targets["pi-zai-coding-plan-glm-5.3"]
    assert target.runtime_id == "pi"
    assert target.runtime_provider_id == "zai"
    assert target.model_sku_id == "zai-coding-plan/glm-5.3"
    assert target.execution_verified is False


def test_discover_pi_not_ready_does_not_call_model_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_discovery as mod

    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    spec = PiProviderSpec(
        pao_provider_id="zai-coding-plan",
        pi_provider_id="zai",
        display_name="GLM / Z.AI",
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(mod, "_resolve_pi", lambda _explicit: executable)

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        full = (str(executable), *argv)
        if argv == ("--version",):
            return SubprocessResult(full, 0, "0.85.1\n", "", False)
        return SubprocessResult(
            full,
            1,
            '{"status":"not_ready","provider":"zai","reason":"credentials_not_configured"}\n',
            "",
            False,
        )

    monkeypatch.setattr(mod, "_run_pi", fake_run)
    outcome = discover_pi(specs=(spec,))

    assert outcome.error_code is None
    assert outcome.result is not None
    assert outcome.result.state is PiDiscoveryState.EMPTY
    assert HARDENED_MODEL_ARGV not in calls


def test_ready_payload_with_nonzero_exit_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_discovery as mod

    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    spec = PiProviderSpec(
        pao_provider_id="zai-coding-plan",
        pi_provider_id="zai",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
    )
    monkeypatch.setenv("ZAI_API_KEY", "weak-evidence-only")
    monkeypatch.setattr(mod, "_resolve_pi", lambda _explicit: executable)

    def fake_run(argv, **_kwargs):
        full = (str(executable), *argv)
        if argv == ("--version",):
            return SubprocessResult(full, 0, "0.85.1\n", "", False)
        return SubprocessResult(
            full,
            9,
            '{"status":"ready","provider":"zai","authType":"api_key"}\n',
            "",
            False,
        )

    monkeypatch.setattr(mod, "_run_pi", fake_run)
    outcome = discover_pi(specs=(spec,))

    assert outcome.result is not None
    record = outcome.result.providers[0]
    assert record.auth_status is PiAuthStatus.UNKNOWN
    assert record.auth_reason == "auth_check_exit_mismatch"
    assert record.model_skus == ()


def test_discover_pi_surfaces_env_presence_only_as_weak_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import personal_ai_orchestrator.pi_provider_discovery as mod

    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    spec = PiProviderSpec(
        pao_provider_id="zai-coding-plan",
        pi_provider_id="zai",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
    )
    monkeypatch.setenv("ZAI_API_KEY", "canary-not-forwarded")
    monkeypatch.setattr(mod, "_resolve_pi", lambda _explicit: executable)

    def fake_run(argv, **_kwargs):
        full = (str(executable), *argv)
        if argv == ("--version",):
            return SubprocessResult(full, 0, "0.85.1\n", "", False)
        return SubprocessResult(
            full,
            1,
            '{"status":"not_ready","provider":"zai","reason":"credentials_not_configured"}\n',
            "",
            False,
        )

    monkeypatch.setattr(mod, "_run_pi", fake_run)
    outcome = discover_pi(specs=(spec,))

    assert outcome.result is not None
    assert outcome.result.state is PiDiscoveryState.DISCOVERED
    record = outcome.result.providers[0]
    assert record.auth_status is PiAuthStatus.NOT_READY
    assert record.env_variables_present == ("ZAI_API_KEY",)
    assert record.model_skus == ()
    serialized = json.dumps(outcome.result.to_dict())
    assert "canary-not-forwarded" not in serialized


def test_missing_pi_returns_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import personal_ai_orchestrator.pi_provider_discovery as mod

    monkeypatch.setattr(mod, "_resolve_pi", lambda _explicit: None)
    outcome = discover_pi()
    assert outcome.result is None
    assert outcome.error_code == "PI_CLI_NOT_FOUND"


def test_pi_discovery_payload_contains_no_credential_values() -> None:
    # Pure serialization regression: metadata names are allowed, secret values are not.
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    from personal_ai_orchestrator.pi_provider_discovery import (
        PiDiscoveryResult,
        PiProviderDiscovery,
    )

    result = PiDiscoveryResult(
        discovered_at=now,
        pi_path="/usr/local/bin/pi",
        pi_version="0.85.1",
        providers=(
            PiProviderDiscovery(
                provider_id="zai-coding-plan",
                runtime_provider_id="zai",
                display_name="GLM / Z.AI",
                auth_status=PiAuthStatus.READY,
                model_skus=("glm-5.3",),
                observed_at=now,
                env_variables_present=("ZAI_API_KEY",),
            ),
        ),
        state=PiDiscoveryState.DISCOVERED,
        configured_family_count=1,
    )
    payload = json.dumps(result.to_dict())
    assert "runtime_provider_id" in payload
    assert "zai-coding-plan" in payload
    assert "ZAI_API_KEY" in payload  # name is metadata
    assert "Bearer " not in payload
    assert "sk-" not in payload
