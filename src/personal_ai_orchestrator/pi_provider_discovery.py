"""Credential-safe provider discovery through the Pi harness.

PI-2 intentionally adds Pi as a *discovery source* without replacing the
existing OpenCode discovery path or changing scheduler/quota/verifier
semantics. The source is deliberately curated: a Pi provider is mapped to a
PAO provider surface only after that mapping has been verified. PI-1 proved
``zai-coding-plan`` -> Pi ``zai`` with a real ``zai/glm-5.3`` invocation.

Security contract
-----------------
- never read Pi credential files;
- never print or persist credential values;
- never invoke ``pi auth ... --credentials`` or a credential-print command;
- child processes inherit the existing discovery allowlist environment, so
  API-key values are not forwarded;
- auth readiness is inspected only through ``pi auth check --json
  --no-refresh``;
- model discovery uses Pi's offline catalog mode with project/resource
  discovery disabled and does not execute an LLM prompt or burn model quota;
- all subprocesses are bounded by the same no-shell wrapper used by the
  existing provider discovery implementation.

This module builds a normal :class:`ModelRegistry` whose execution targets are
explicitly ``runtime_id='pi'`` and retain Pi's canonical provider id in
``runtime_provider_id``. Product-level selection between OpenCode and Pi is
out of PI-2 scope and remains a later wiring phase.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from personal_ai_orchestrator.model_registry import (
    Account,
    ExecutionTarget,
    ModelCatalogSnapshot,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized
from personal_ai_orchestrator.provider_discovery import (
    DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES,
    SubprocessResult,
    _build_subprocess_env,
    _redact,
    _run_opencode,
)

PI_DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS = 15.0
PI_DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES = DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES
PI_SOURCE_METHOD = "pi_cli_inspection"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_SAFE_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")


class PiDiscoveryState(StrEnum):
    PENDING = "PENDING"
    DISCOVERED = "DISCOVERED"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class PiAuthStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PiProviderSpec:
    """Curated mapping between a PAO provider surface and Pi provider id.

    ``pao_provider_id`` carries commercial/quota identity inside PAO.
    ``pi_provider_id`` is only the runtime provider id passed to Pi. The two
    identities must not be collapsed: Pi's ``zai`` runtime surface is the PAO
    ``zai-coding-plan`` resource proven by PI-1.
    """

    pao_provider_id: str
    pi_provider_id: str
    display_name: str
    env_variables: tuple[str, ...] = ()
    auth_kind: str = "api_key"
    pool_kind: str = "windowed"


# Curated PAO commercial identities mapped to real Pi auth/catalog surfaces.
# PI-3 confirmed MiniMax China readiness and MiniMax-M3 catalog presence.
# Discovery never grants execution verification; each target needs its own probe.
PI_PROVIDER_SPECS: tuple[PiProviderSpec, ...] = (
    PiProviderSpec(
        pao_provider_id="zai-coding-plan",
        pi_provider_id="zai",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
        auth_kind="api_key",
        pool_kind="windowed",
    ),
    PiProviderSpec(
        pao_provider_id="minimax-cn-coding-plan",
        pi_provider_id="minimax-cn",
        display_name="MiniMax CN Coding Plan",
        env_variables=("MINIMAX_API_KEY",),
    ),
)


@dataclass(frozen=True)
class PiProviderDiscovery:
    provider_id: str
    runtime_provider_id: str
    display_name: str
    auth_status: PiAuthStatus
    model_skus: tuple[str, ...]
    observed_at: datetime
    evidence_source: str = "PI_CLI_INSPECTION"
    env_variables_present: tuple[str, ...] = ()
    auth_kind: str = "api_key"
    pool_kind: str = "windowed"
    execution_verified: bool = False
    auth_reason: str | None = None

    @property
    def catalog_discovered(self) -> bool:
        return bool(self.model_skus)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PiProviderDiscovery:
        assert_sanitized(payload)
        if payload.get("runtime_id") != "pi":
            raise ValueError("Pi provider record must declare runtime_id=pi")
        provider_id = payload.get("provider_id")
        runtime_provider_id = payload.get("runtime_provider_id")
        display_name = payload.get("display_name")
        auth_status_raw = payload.get("auth_status")
        model_skus_raw = payload.get("model_skus")
        observed_at_raw = payload.get("observed_at")
        if not all(
            isinstance(value, str)
            for value in (
                provider_id,
                runtime_provider_id,
                display_name,
                auth_status_raw,
                observed_at_raw,
            )
        ):
            raise ValueError("invalid Pi provider discovery identity")
        if not isinstance(model_skus_raw, list) or not all(
            isinstance(model, str) for model in model_skus_raw
        ):
            raise ValueError("invalid Pi provider model list")
        try:
            observed_at = datetime.fromisoformat(observed_at_raw)
            auth_status = PiAuthStatus(auth_status_raw)
        except ValueError as exc:
            raise ValueError("invalid Pi provider discovery timestamp/status") from exc
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        env_raw = payload.get("env_variables_present", [])
        auth_reason = payload.get("auth_reason")
        auth_type = payload.get("auth_kind", "api_key")
        pool_kind = payload.get("pool_kind", "windowed")
        if not isinstance(env_raw, list) or not all(
            isinstance(value, str) for value in env_raw
        ):
            raise ValueError("invalid Pi provider environment metadata")
        if not isinstance(auth_type, str) or not isinstance(pool_kind, str):
            raise ValueError("invalid Pi provider metadata")
        if auth_reason is not None and not isinstance(auth_reason, str):
            raise ValueError("invalid Pi provider auth reason")
        execution_verified = payload.get("execution_verified", False)
        if not isinstance(execution_verified, bool):
            raise ValueError("invalid Pi provider execution verification flag")
        return cls(
            provider_id=provider_id,
            runtime_provider_id=runtime_provider_id,
            display_name=display_name,
            auth_status=auth_status,
            model_skus=tuple(model_skus_raw),
            observed_at=observed_at,
            evidence_source=str(payload.get("evidence_source", "PI_CLI_INSPECTION")),
            env_variables_present=tuple(env_raw),
            auth_kind=auth_type,
            pool_kind=pool_kind,
            execution_verified=execution_verified,
            auth_reason=auth_reason,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider_id": self.provider_id,
            "runtime_id": "pi",
            "runtime_provider_id": self.runtime_provider_id,
            "display_name": self.display_name,
            "auth_status": self.auth_status.value,
            "model_skus": list(self.model_skus),
            "observed_at": self.observed_at.isoformat(),
            "evidence_source": self.evidence_source,
            "env_variables_present": list(self.env_variables_present),
            "auth_kind": self.auth_kind,
            "pool_kind": self.pool_kind,
            "execution_verified": self.execution_verified,
            "catalog_discovered": self.catalog_discovered,
            "auth_reason": self.auth_reason,
        }
        assert_sanitized(payload)
        return payload


@dataclass(frozen=True)
class PiDiscoveryResult:
    discovered_at: datetime
    pi_path: str
    pi_version: str
    providers: tuple[PiProviderDiscovery, ...]
    state: PiDiscoveryState
    source_method: str = PI_SOURCE_METHOD
    configured_family_count: int = 0
    last_error_code: str | None = None

    def provider_count(self) -> int:
        return len(self.providers)

    def execution_target_count(self) -> int:
        return sum(len(provider.model_skus) for provider in self.providers)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PiDiscoveryResult:
        assert_sanitized(payload)
        if payload.get("schema_version") != 1 or payload.get("runtime_id") != "pi":
            raise ValueError("unsupported Pi discovery snapshot")
        generated_at = payload.get("generated_at")
        pi_path = payload.get("runtime_path")
        pi_version = payload.get("runtime_version")
        source_method = payload.get("source_method", PI_SOURCE_METHOD)
        state_raw = payload.get("discovery_state")
        providers_raw = payload.get("providers")
        configured_count = payload.get("configured_family_count", 0)
        last_error_code = payload.get("last_error_code")
        if not all(
            isinstance(value, str)
            for value in (
                generated_at,
                pi_path,
                pi_version,
                source_method,
                state_raw,
            )
        ):
            raise ValueError("invalid Pi discovery snapshot identity")
        if not isinstance(providers_raw, list) or not all(
            isinstance(item, dict) for item in providers_raw
        ):
            raise ValueError("invalid Pi discovery provider list")
        if not isinstance(configured_count, int) or configured_count < 0:
            raise ValueError("invalid Pi configured family count")
        if last_error_code is not None and not isinstance(last_error_code, str):
            raise ValueError("invalid Pi discovery error code")
        try:
            discovered_at = datetime.fromisoformat(generated_at)
            state = PiDiscoveryState(state_raw)
        except ValueError as exc:
            raise ValueError("invalid Pi discovery timestamp/state") from exc
        if discovered_at.tzinfo is None:
            discovered_at = discovered_at.replace(tzinfo=UTC)
        providers = tuple(
            PiProviderDiscovery.from_dict(item) for item in providers_raw
        )
        if len(providers) != payload.get("provider_count"):
            raise ValueError("Pi provider_count does not match provider records")
        expected_targets = sum(len(provider.model_skus) for provider in providers)
        if expected_targets != payload.get("execution_target_count"):
            raise ValueError("Pi execution_target_count does not match model records")
        return cls(
            discovered_at=discovered_at,
            pi_path=pi_path,
            pi_version=pi_version,
            providers=providers,
            state=state,
            source_method=source_method,
            configured_family_count=configured_count,
            last_error_code=last_error_code,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "generated_at": self.discovered_at.isoformat(),
            "runtime_id": "pi",
            "runtime_path": self.pi_path,
            "runtime_version": self.pi_version,
            "source_method": self.source_method,
            "discovery_state": self.state.value,
            "configured_family_count": self.configured_family_count,
            "provider_count": self.provider_count(),
            "execution_target_count": self.execution_target_count(),
            "last_error_code": self.last_error_code,
            "providers": [provider.to_dict() for provider in self.providers],
        }
        assert_sanitized(payload)
        return payload


@dataclass(frozen=True)
class PiDiscoveryCycleOutcome:
    result: PiDiscoveryResult | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class PiAuthCheck:
    provider: str
    status: PiAuthStatus
    auth_type: str | None = None
    reason: str | None = None


def _resolve_pi(explicit: Path | None) -> Path | None:
    """Resolve Pi without assuming an fnm/npm installation path."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    which = shutil.which("pi")
    if which:
        candidates.append(Path(which))
    candidates.extend(
        (
            Path.home() / ".local/bin/pi",
            Path.home() / ".npm-global/bin/pi",
        )
    )
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_pi(
    argv: Sequence[str],
    *,
    executable: Path,
    timeout_seconds: float = PI_DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS,
    max_output_bytes: int = PI_DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> SubprocessResult:
    """Run Pi through PAO's existing bounded, no-shell discovery wrapper."""

    safe_env = _build_subprocess_env(os.environ if env is None else env)
    return _run_opencode(
        argv,
        executable=executable,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        env=safe_env,
    )


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def _parse_pi_auth_check(stdout: str, *, expected_provider: str) -> PiAuthCheck:
    """Parse the sanitized output of ``pi auth check --json --no-refresh``.

    Credential-bearing output is rejected. PI-2 never passes
    ``--credentials``; this check is a fail-closed guard against future CLI
    drift or accidental caller misuse.
    """

    cleaned = _redact(_strip_ansi(stdout)).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("PI_AUTH_CHECK_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("PI_AUTH_CHECK_INVALID_SHAPE")
    forbidden = {
        "credential",
        "credentials",
        "apiKey",
        "api_key",
        "token",
        "bearerToken",
    }
    if forbidden.intersection(payload):
        raise ValueError("PI_AUTH_CHECK_EXPOSED_CREDENTIAL_FIELD")
    # The status payload itself is metadata-only. Refuse any unexpected value
    # that still resembles a secret before selecting the fields we persist.
    assert_sanitized(payload)
    provider = payload.get("provider")
    if provider != expected_provider:
        raise ValueError("PI_AUTH_CHECK_PROVIDER_MISMATCH")
    raw_status = payload.get("status")
    if raw_status == "ready":
        status = PiAuthStatus.READY
    elif raw_status == "not_ready":
        status = PiAuthStatus.NOT_READY
    else:
        status = PiAuthStatus.UNKNOWN
    auth_type = payload.get("authType")
    reason = payload.get("reason")
    result = PiAuthCheck(
        provider=provider,
        status=status,
        auth_type=auth_type if isinstance(auth_type, str) else None,
        reason=reason if isinstance(reason, str) else None,
    )
    assert_sanitized(result.__dict__)
    return result


def _parse_pi_model_table(stdout: str, *, expected_provider: str) -> tuple[str, ...]:
    """Parse Pi 0.85.x ``--list-models`` table output.

    Upstream emits six whitespace-separated columns headed by ``provider`` and
    ``model``. We intentionally retain only those first two metadata fields
    and ignore context/max-output/thinking/image columns. A fallback accepts
    canonical ``provider/model`` tokens so the parser remains compatible with
    older or alternate Pi renderers without guessing provider identity.
    """

    if not _SAFE_PROVIDER_RE.fullmatch(expected_provider):
        raise ValueError("invalid expected Pi provider id")
    cleaned = _strip_ansi(stdout)
    models: set[str] = set()
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "provider" and parts[1] == "model":
            continue

        provider: str | None = None
        model: str | None = None
        if len(parts) >= 2 and _SAFE_PROVIDER_RE.fullmatch(parts[0]):
            provider = parts[0]
            model = parts[1]
        elif parts and "/" in parts[0]:
            provider, model = parts[0].split("/", 1)

        if provider != expected_provider or model is None:
            continue
        if not _SAFE_MODEL_RE.fullmatch(model):
            continue
        models.add(model)
    return tuple(sorted(models))


def discover_pi(
    *,
    specs: Sequence[PiProviderSpec] = PI_PROVIDER_SPECS,
    pi_path: Path | None = None,
    timeout_seconds: float = PI_DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> PiDiscoveryCycleOutcome:
    """Run one metadata-only Pi provider/model discovery cycle."""

    observed_at = clock() if clock is not None else datetime.now(tz=UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)

    executable = _resolve_pi(pi_path)
    if executable is None:
        return PiDiscoveryCycleOutcome(
            result=None,
            error_code="PI_CLI_NOT_FOUND",
            error_message="pi CLI not found on PATH or supported fallback paths",
        )

    version_result = _run_pi(
        ("--version",),
        executable=executable,
        timeout_seconds=timeout_seconds,
    )
    if version_result.returncode != 0 or version_result.truncated:
        return PiDiscoveryCycleOutcome(
            result=None,
            error_code="PI_VERSION_FAILED",
            error_message=f"pi --version exited with code {version_result.returncode}",
        )
    pi_version = (
        version_result.stdout.strip().splitlines()[0]
        if version_result.stdout
        else "unknown"
    )

    records: list[PiProviderDiscovery] = []
    for spec in specs:
        auth_result = _run_pi(
            (
                "auth",
                "check",
                "--provider",
                spec.pi_provider_id,
                "--json",
                "--no-refresh",
            ),
            executable=executable,
            timeout_seconds=timeout_seconds,
        )
        try:
            auth = _parse_pi_auth_check(
                auth_result.stdout,
                expected_provider=spec.pi_provider_id,
            )
            if auth.status is PiAuthStatus.READY and auth_result.returncode != 0:
                auth = PiAuthCheck(
                    provider=spec.pi_provider_id,
                    status=PiAuthStatus.UNKNOWN,
                    reason="auth_check_exit_mismatch",
                )
        except ValueError:
            auth = PiAuthCheck(
                provider=spec.pi_provider_id,
                status=PiAuthStatus.UNKNOWN,
                reason="auth_check_unparseable",
            )

        model_skus: tuple[str, ...] = ()
        if auth.status is PiAuthStatus.READY:
            # Do not let global/project extensions, skills, prompt templates,
            # context files, or project approval mutate a metadata-only catalog
            # read. `--offline` additionally disables startup network refreshes.
            models_result = _run_pi(
                (
                    "--offline",
                    "--no-approve",
                    "--no-extensions",
                    "--no-skills",
                    "--no-prompt-templates",
                    "--no-context-files",
                    "--list-models",
                    spec.pi_provider_id,
                ),
                executable=executable,
                timeout_seconds=timeout_seconds,
            )
            if models_result.returncode == 0 and not models_result.truncated:
                model_skus = _parse_pi_model_table(
                    models_result.stdout,
                    expected_provider=spec.pi_provider_id,
                )

        env_present = tuple(name for name in spec.env_variables if name in os.environ)
        # Keep a NOT_READY/UNKNOWN record only when some weak evidence exists;
        # READY is always surfaced even if the catalog is unexpectedly empty so
        # the operator can distinguish "authenticated, catalog missing" from
        # "provider absent".
        if auth.status is PiAuthStatus.READY or env_present:
            record = PiProviderDiscovery(
                provider_id=spec.pao_provider_id,
                runtime_provider_id=spec.pi_provider_id,
                display_name=spec.display_name,
                auth_status=auth.status,
                model_skus=model_skus,
                observed_at=observed_at,
                env_variables_present=env_present,
                auth_kind=spec.auth_kind,
                pool_kind=spec.pool_kind,
                execution_verified=False,
                auth_reason=auth.reason,
            )
            assert_sanitized(record.to_dict())
            records.append(record)

    state = PiDiscoveryState.DISCOVERED if records else PiDiscoveryState.EMPTY
    result = PiDiscoveryResult(
        discovered_at=observed_at,
        pi_path=str(executable),
        pi_version=pi_version,
        providers=tuple(records),
        state=state,
        configured_family_count=len(tuple(specs)),
        last_error_code=None,
    )
    try:
        result.to_dict()
    except ValueError as exc:
        return PiDiscoveryCycleOutcome(
            result=None,
            error_code="SANITIZATION_REJECTED",
            error_message=str(exc),
        )
    return PiDiscoveryCycleOutcome(result=result, error_code=None, error_message=None)


def build_pi_registry(result: PiDiscoveryResult) -> ModelRegistry:
    """Register Pi-discovered models without collapsing PAO/Runtime identity."""

    providers: dict[str, Provider] = {}
    accounts: dict[str, Account] = {}
    models: dict[str, ModelSKU] = {}
    execution_targets: dict[str, ExecutionTarget] = {}
    snapshot_id = f"pi-discovery:{result.discovered_at.isoformat()}"
    catalog_snapshot = ModelCatalogSnapshot(
        id=snapshot_id,
        source=result.source_method,
        as_of=result.discovered_at,
        fetched_at=result.discovered_at,
    )

    for record in result.providers:
        provider = Provider(id=record.provider_id, display_name=record.display_name)
        providers[provider.id] = provider
        account = Account(
            id=record.provider_id,
            provider_id=record.provider_id,
            label=record.display_name,
        )
        accounts[account.id] = account
        for sku in record.model_skus:
            model_id = f"{record.provider_id}/{sku}"
            models[model_id] = ModelSKU(
                id=model_id,
                provider_id=record.provider_id,
                display_name=sku,
                catalog_snapshot_id=snapshot_id,
            )
            target_id = f"pi-{record.provider_id}-{sku}"
            execution_targets[target_id] = ExecutionTarget(
                id=target_id,
                model_sku_id=model_id,
                account_id=account.id,
                runtime_id="pi",
                runtime_provider_id=record.runtime_provider_id,
                enabled=True,
                # Discovery is metadata-only; PI-1's real acceptance is not a
                # blanket execution proof for every future discovered model.
                execution_verified=record.execution_verified,
            )

    snapshot = {
        "schema_version": 1,
        "runtime_id": "pi",
        "registry": {
            "providers": {key: value.model_dump() for key, value in providers.items()},
            "accounts": {key: value.model_dump() for key, value in accounts.items()},
            "models": {key: value.model_dump() for key, value in models.items()},
            "execution_targets": {
                key: value.model_dump() for key, value in execution_targets.items()
            },
            "catalog_snapshots": {
                snapshot_id: catalog_snapshot.model_dump(mode="json")
            },
        },
    }
    assert_sanitized(snapshot)
    return ModelRegistry(
        providers=providers,
        accounts=accounts,
        models=models,
        execution_targets=execution_targets,
        catalog_snapshots={snapshot_id: catalog_snapshot},
    )


__all__ = [
    "PI_DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES",
    "PI_DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS",
    "PI_PROVIDER_SPECS",
    "PI_SOURCE_METHOD",
    "PiAuthCheck",
    "PiAuthStatus",
    "PiDiscoveryCycleOutcome",
    "PiDiscoveryResult",
    "PiDiscoveryState",
    "PiProviderDiscovery",
    "PiProviderSpec",
    "_parse_pi_auth_check",
    "_parse_pi_model_table",
    "_resolve_pi",
    "_run_pi",
    "build_pi_registry",
    "discover_pi",
]
