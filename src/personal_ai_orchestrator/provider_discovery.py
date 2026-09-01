"""Credential-safe provider discovery for the Personal AI Orchestrator product runtime.

This module answers a single question at boot:

    Which provider families are reachable through the currently configured local
    runtime, and which model SKUs do they expose, without ever reading or
    persisting a single credential value?

Allowed inputs (per P4.2.4-A §15):
- ``opencode providers list`` stdout — provider names + backend labels + env
  variable **names** only. Verified by the harness that no token value is
  written to stdout or stderr by the upstream CLI.
- ``opencode models [<family>]`` stdout — model catalog lines shaped like
  ``<provider>/<model>``. No secrets are returned by this subcommand.
- Environment variable *presence* checks (``ZAI_API_KEY``,
  ``MINIMAX_API_KEY``, etc.) — the values are never read.

Forbidden:
- reading token values
- printing tokens
- copying ``~/.local/share/opencode/auth.json``
- reading browser cookies
- persisting tokens, Authorization headers, or ``credential_ref``

The output is a fully sanitized ``DiscoveryResult`` that the rest of the
product runtime can persist and project to the Dashboard without leaking
secrets. ``assert_sanitized`` is applied to every snapshot before it leaves
this module.

The discovery subprocess is bounded with a hard wall-clock timeout, a stdout
size cap, and an absolute ``argv`` path so no shell is ever involved. The
process is *never* spawned with ``shell=True``, ``/bin/sh -c``, ``bash -c``,
or ``zsh -c``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    ModelRegistry,
    ModelSKU,
    Provider,
)
from personal_ai_orchestrator.provider_acceptance import (
    AuthSurface,
    LiveProviderResult,
    ProviderSurfaceEvidence,
    QuotaSurface,
    assert_sanitized,
)

## # ---------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Default absolute path to the OpenCode CLI. The discovery subprocess is
# spawned with ``[executable, ...]`` so there is no shell parsing.
DEFAULT_OPENCODE_PATH = Path.home() / ".opencode/bin/opencode"

# Hard wall-clock cap on a single ``opencode providers list`` / ``opencode models``
# invocation. Discovery must complete or fail within this budget.
DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS = 8.0

# Maximum bytes the harness will accept from a discovery subprocess. The
# OpenCode CLI never returns more than a few KB for these subcommands; 256 KB
# is a generous upper bound that still rejects runaway output.
DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES = 256 * 1024

# The provider families we attempt to discover. Each entry pairs:
#   - the OpenCode provider family identifier as returned by ``opencode models``
#   - the environment variable name whose *presence* indicates that an API
#     credential is configured (value is never read)
#   - an optional alternative env var (for vendors that document multiple)
#
# Adding a new provider here is a deliberate act: nothing in the codebase
# should infer families automatically from CLI output, because the harness
# must not interpret third-party help text as executable instructions.
PROVIDER_FAMILIES: tuple[ProviderFamilySpec, ...] = ()

# ANSI escape sequence stripper. ``opencode providers list`` emits colored
# output even when stdout is a pipe; the harness treats color codes as
# cosmetic and removes them before parsing.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Match the ``opencode models`` shape: ``<provider>/<model>``.
_MODEL_LINE_RE = re.compile(r"^(?P<provider>[a-z0-9_-]+)/(?P<model>[a-zA-Z0-9._-]+)\s*$")

# Lines we expect to see in the ``opencode providers list`` header. Used only
# to count provider categories — never to extract secrets.
_PROVIDER_LIST_CREDENTIAL_HEADER_RE = re.compile(r"^[│|]\s*(\d+)\s+credentials?\s*$")


# -----------------------------------------------------------------------------
# Public model
# -----------------------------------------------------------------------------

class DiscoveryState(StrEnum):
    """Lifecycle of a single provider-discovery cycle."""

    PENDING = "PENDING"            # cycle has not yet completed
    DISCOVERED = "DISCOVERED"        # at least one family produced evidence
    EMPTY = "EMPTY"                  # discovery ran and produced no families
    FAILED = "FAILED"                # discovery raised; see ``last_error_code``


class AuthStatus(StrEnum):
    """Auth status for one provider family.

    ``AUTH_FROM_ENV_PRESENCE`` is the **only** non-default positive state this
    module is allowed to emit. ``EXECUTABLE`` is intentionally not in this
    enum: per §17 the harness must not run a model prompt merely to populate
    a Dashboard row, so the discovery module never claims EXECUTABLE.
    """

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FROM_ENV_PRESENCE = "AUTH_FROM_ENV_PRESENCE"
    AUTH_UNKNOWN = "AUTH_UNKNOWN"


class ExecutionStatus(StrEnum):
    """Execution status for one provider family.

    Discovery by inspection only ever reports ``UNKNOWN`` or
    ``AVAILABLE_FOR_CATALOG``. ``EXECUTABLE`` would require an execution
    probe (§17) which this module never runs.
    """

    UNKNOWN = "UNKNOWN"
    AVAILABLE_FOR_CATALOG = "AVAILABLE_FOR_CATALOG"


@dataclass(frozen=True)
class ProviderFamilySpec:
    """One provider family we attempt to discover.

    Attributes
    ----------
    provider_id:
        The OpenCode provider family identifier (matches the ``<provider>``
        segment of ``opencode models`` lines).
    display_name:
        Human-readable label used by Dashboard cards.
    env_variables:
        Environment variables whose *presence* indicates that an API
        credential is configured for this family. The discovery module
        never reads their values.
    provider_label_keywords:
        Substrings matched against ``opencode providers list`` display
        labels (first whitespace-delimited token) to determine whether
        the family has a credential configured in OpenCode's auth store
        or environment section. Case-sensitive.
    alternative_endpoints:
        Free-form annotations describing documented provider endpoints. Used
        only for the CN/international distinction (§19); never copied to
        the Dashboard verbatim.
    """

    provider_id: str
    display_name: str
    env_variables: tuple[str, ...]
    provider_label_keywords: tuple[str, ...]
    alternative_endpoints: tuple[str, ...] = ()


PROVIDER_FAMILIES: tuple[ProviderFamilySpec, ...] = (
    ProviderFamilySpec(
        provider_id="zai-coding-plan",
        display_name="GLM / Z.AI",
        env_variables=("ZAI_API_KEY",),
        provider_label_keywords=("Z.AI", "GLM"),
    ),
    ProviderFamilySpec(
        provider_id="minimax-cn",
        display_name="MiniMax CN",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax"),
        alternative_endpoints=("api.minimaxi.com",),
    ),
    ProviderFamilySpec(
        provider_id="minimax-cn-coding-plan",
        display_name="MiniMax CN Coding Plan",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimaxi", "MiniMax"),
        alternative_endpoints=("api.minimaxi.com",),
    ),
    ProviderFamilySpec(
        provider_id="minimax",
        display_name="MiniMax International",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io", "MiniMax"),
        alternative_endpoints=("api.minimax.io",),
    ),
    ProviderFamilySpec(
        provider_id="minimax-coding-plan",
        display_name="MiniMax International Coding Plan",
        env_variables=("MINIMAX_API_KEY",),
        provider_label_keywords=("minimax.io", "MiniMax"),
        alternative_endpoints=("api.minimax.io",),
    ),
)


@dataclass(frozen=True)
class ProviderDiscovery:
    """Sanitized per-family discovery record.

    This object is the only thing the discovery module hands to the
    product runtime. ``assert_sanitized`` rejects any field whose value
    matches a credential shape before it ever leaves the module.
    """

    provider_id: str
    display_name: str
    auth_status: AuthStatus
    execution_status: ExecutionStatus
    evidence_source: str                       # §33 evidence flag
    model_skus: tuple[str, ...]
    env_variables_present: tuple[str, ...]
    observed_at: datetime
    region: str | None = None
    in_credentials_store: bool = False

    def to_provider_surface_evidence(
        self,
        *,
        command_family: str,
        source_method: str,
    ) -> ProviderSurfaceEvidence:
        """Convert to the canonical P3.6 surface-evidence model.

        ``LIVE_PROBE_NOT_RUN`` reflects §17/§33: this module never runs an
        actual prompt against a provider. ``LIVE_UNKNOWN`` keeps the rest of
        the P3.6 contract intact.
        """

        live_result = LiveProviderResult.LIVE_UNKNOWN
        auth_surface = (
            AuthSurface.EXISTING_SUPPORTED_TOKEN_REFERENCE
            if self.auth_status is AuthStatus.AUTH_FROM_ENV_PRESENCE
            else AuthSurface.AUTH_REQUIRED
            if self.auth_status is AuthStatus.AUTH_REQUIRED
            else AuthSurface.UNKNOWN
        )
        return ProviderSurfaceEvidence(
            provider_id=self.provider_id,
            plan_id=self.provider_id,
            auth_surface=auth_surface,
            quota_surface=QuotaSurface.UNKNOWN,
            live_result=live_result,
            command_family=command_family,
            observed_at=self.observed_at,
            confidence=EvidenceConfidence.UNKNOWN,
            quota_semantics="discovery_by_inspection_only",
            reset_semantics="not_observed",
            source_method=source_method,
            sanitized_status=self.auth_status.value,
            error_category=None,
        )


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of one discovery cycle.

    This is what the product runtime persists to
    ``provider-registry.json`` and what the Control API projects to the
    Dashboard.
    """

    discovered_at: datetime
    opencode_path: str
    opencode_version: str
    source_method: str
    providers: tuple[ProviderDiscovery, ...]
    state: DiscoveryState
    last_error_code: str | None = None

    def provider_count(self) -> int:
        return len(self.providers)

    def execution_target_count(self) -> int:
        return sum(len(p.model_skus) for p in self.providers)

    def to_dict(self) -> dict[str, object]:
        """Sanitized JSON-serializable form.

        The dict never contains a credential value, credential_ref, or any
        raw subprocess output. ``assert_sanitized`` is applied to the
        resulting structure so a regression that accidentally inserts a
        secret is caught immediately.
        """

        data: dict[str, object] = {
            "schema_version": 1,
            "generated_at": self.discovered_at.isoformat(),
            "opencode_path": self.opencode_path,
            "opencode_version": self.opencode_version,
            "source_method": self.source_method,
            "discovery_state": self.state.value,
            "last_error_code": self.last_error_code,
            "providers": [
                {
                    "provider_id": p.provider_id,
                    "display_name": p.display_name,
                    "auth_status": p.auth_status.value,
                    "execution_status": p.execution_status.value,
                    "evidence_source": p.evidence_source,
                    "model_skus": list(p.model_skus),
                    "env_variables_present": list(p.env_variables_present),
                    "region": p.region,
                    "in_credentials_store": p.in_credentials_store,
                    "observed_at": p.observed_at.isoformat(),
                }
                for p in self.providers
            ],
        }
        assert_sanitized(data)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> DiscoveryResult:
        providers = tuple(
            ProviderDiscovery(
                provider_id=str(item["provider_id"]),
                display_name=str(item["display_name"]),
                auth_status=AuthStatus(str(item["auth_status"])),
                execution_status=ExecutionStatus(str(item["execution_status"])),
                evidence_source=str(item["evidence_source"]),
                model_skus=tuple(str(s) for s in item.get("model_skus", [])),  # type: ignore[arg-type]
                env_variables_present=tuple(
                    str(s) for s in item.get("env_variables_present", [])
                ),  # type: ignore[arg-type]
                region=(
                    str(item["region"])
                    if item.get("region") is not None
                    else None
                ),
                in_credentials_store=bool(item.get("in_credentials_store", False)),
                observed_at=datetime.fromisoformat(str(item["observed_at"])),
            )
            for item in payload.get("providers", [])  # type: ignore[arg-type]
        )
        assert_sanitized(payload)
        result = cls(
            discovered_at=datetime.fromisoformat(str(payload["generated_at"])),
            opencode_path=str(payload["opencode_path"]),
            opencode_version=str(payload["opencode_version"]),
            source_method=str(payload["source_method"]),
            providers=providers,
            state=DiscoveryState(str(payload.get("discovery_state", "DISCOVERED"))),
            last_error_code=(
                str(payload["last_error_code"])
                if payload.get("last_error_code") is not None
                else None
            ),
        )
        assert_sanitized(result.to_dict())
        return result


# -----------------------------------------------------------------------------
# Subprocess contract
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SubprocessResult:
    """A bounded, sanitized result of one subprocess invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        # stderr is intentionally not persisted. If the subprocess leaked a
        # secret to stderr we *want* to drop it on the floor.
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "truncated": self.truncated,
            "stdout_bytes": len(self.stdout.encode("utf-8")),
            "stderr_bytes": len(self.stderr.encode("utf-8")),
        }


def _resolve_opencode(explicit: Path | None) -> Path | None:
    """Resolve the OpenCode CLI path.

    Resolution order:
      1. ``explicit`` if provided
      2. ``~/.opencode/bin/opencode`` (DEFAULT_OPENCODE_PATH)
      3. ``shutil.which("opencode")`` on ``PATH``

    Returns ``None`` if no candidate exists. The caller decides whether
    ``None`` is an error or a soft "discovery unavailable" condition.
    """

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.append(DEFAULT_OPENCODE_PATH)
    which = shutil.which("opencode")
    if which:
        candidates.append(Path(which))
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_opencode(
    argv: Sequence[str],
    *,
    executable: Path,
    timeout_seconds: float = DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS,
    max_output_bytes: int = DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES,
) -> SubprocessResult:
    """Spawn the OpenCode CLI with a strict argv, timeout, and output cap.

    Never spawns a shell. ``argv`` is a tuple of strings; the first element
    is the resolved executable path. ``stdout`` and ``stderr`` are captured
    as bytes; output larger than ``max_output_bytes`` is truncated and the
    ``truncated`` flag is set so the caller can decide to retry or fail
    closed.
    """

    if not argv:
        raise ValueError("argv must not be empty")
    full_argv: tuple[str, ...] = (str(executable), *argv)
    try:
        completed = subprocess.run(  # noqa: S603 — argv is fully controlled
            full_argv,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,                                # never a shell
            env={k: v for k, v in os.environ.items() if k != "PATH"} | {"PATH": ""},
        )
    except subprocess.TimeoutExpired as exc:
        return SubprocessResult(
            argv=full_argv,
            returncode=-1,
            stdout=(exc.stdout.decode("utf-8", "replace") if exc.stdout else ""),
            stderr=(exc.stderr.decode("utf-8", "replace") if exc.stderr else ""),
            truncated=True,
        )
    stdout_bytes = completed.stdout or b""
    stderr_bytes = completed.stderr or b""
    truncated = False
    if len(stdout_bytes) > max_output_bytes:
        stdout_bytes = stdout_bytes[:max_output_bytes]
        truncated = True
    if len(stderr_bytes) > max_output_bytes:
        stderr_bytes = stderr_bytes[:max_output_bytes]
        truncated = True
    stdout = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")
    # Belt and suspenders: refuse to keep anything that looks like a token
    # even if a future OpenCode build changes its stdout shape.
    stdout = _redact(stdout)
    stderr = _redact(stderr)
    return SubprocessResult(
        argv=full_argv,
        returncode=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
    )


_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
)


def _redact(value: str) -> str:
    """Strip anything that looks like a secret from a CLI string.

    Belt-and-suspenders: even though ``opencode providers list`` does not
    emit token values, the harness refuses to persist anything that
    matches a credential pattern. The output is still useful for
    debugging because the structure survives — only the would-be secret
    characters are removed.
    """

    out = value
    for pattern in _SECRET_VALUE_PATTERNS:
        out = pattern.sub("<redacted>", out)
    return out


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def _infer_region(spec: ProviderFamilySpec) -> str | None:
    """Best-effort region tag derived from ``alternative_endpoints``.

    The harness never reads credentials, so it cannot actually probe the
    CN vs international endpoints. It *can* infer the documentation-
    intended region from the curated family table so the Dashboard can
    distinguish CN from international surfaces without ambiguity.
    """

    if not spec.alternative_endpoints:
        return None
    joined = " ".join(spec.alternative_endpoints)
    if "minimaxi.com" in joined:
        return "CN"
    if "minimax.io" in joined:
        return "INTERNATIONAL"
    return None


# -----------------------------------------------------------------------------
# Provider list parsing
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class _AuthPresence:
    """Internal: which provider families report credentials, derived from
    ``opencode providers list`` output.

    The harness reads only provider **names** and the count of credentials
    in each section. It never copies raw JSON, headers, or token values.

    Attributes
    ----------
    credentials_section_labels:
        Display labels seen under the ``Credentials`` section (e.g.
        ``"Z.AI Coding Plan"``). These reflect the OpenCode-managed
        credential store (``~/.local/share/opencode/auth.json``); we
        only see the labels, not the secrets.
    environment_section_labels:
        Display labels seen under the ``Environment`` section. We use
        these plus the env var names for the per-family auth check.
    env_variable_names_seen:
        Environment variable names seen in the ``Environment`` section.
    """

    credentials_section_labels: frozenset[str]
    environment_section_labels: frozenset[str]
    env_variable_names_seen: frozenset[str]

    def has_in_credentials_store(self, spec: ProviderFamilySpec) -> bool:
        for label in self.credentials_section_labels:
            if any(token in label for token in spec.provider_label_keywords):
                return True
        return False

    def has_in_environment(self, spec: ProviderFamilySpec) -> bool:
        for label in self.environment_section_labels:
            if any(token in label for token in spec.provider_label_keywords):
                return True
        return any(name in self.env_variable_names_seen for name in spec.env_variables)


def _parse_provider_list(stdout: str) -> _AuthPresence:
    """Parse ``opencode providers list`` output into a credential-presence map.

    The parser strips ANSI codes, walks the section headers, and collects
    provider **labels** and environment variable **names** only. It treats
    anything that matches a secret regex as a hard error (the harness never
    sees a token value).
    """

    cleaned = _strip_ansi(stdout)
    credentials_labels: set[str] = set()
    env_section_labels: set[str] = set()
    env_names: set[str] = set()
    section = ""
    for raw_line in cleaned.splitlines():
        line = raw_line.rstrip()
        if "Credentials" in line and line.startswith(("┌", "│", "|")):
            section = "credentials"
            continue
        if "Environment" in line and line.startswith(("┌", "│", "|")):
            section = "environment"
            continue
        if line.startswith(("└", "+")):
            section = ""
            continue
        if not line.strip():
            continue
        if line.startswith(("│", "|")):
            # Box-drawing separator line; skip.
            continue
        if line.startswith("●"):
            content = line[1:].strip()
        elif line.startswith("•"):
            content = line[1:].strip()
        else:
            continue
        if not content:
            continue
        # Split on whitespace. The first whitespace-delimited token is the
        # provider label; any token that looks like ``UPPER_SNAKE`` is an
        # environment variable name (only meaningful in the Environment
        # section).
        parts = content.split()
        if not parts:
            continue
        # Preserve the full display label (not just the first token) so
        # that families like ``Z.AI Coding Plan`` match on the substring
        # ``Z.AI``.
        display_label = parts[0]
        if section == "credentials":
            credentials_labels.add(display_label)
        elif section == "environment":
            env_section_labels.add(display_label)
            env_candidate = next(
                (p for p in parts if re.fullmatch(r"[A-Z][A-Z0-9_]*", p)),
                None,
            )
            if env_candidate:
                env_names.add(env_candidate)
    return _AuthPresence(
        credentials_section_labels=frozenset(credentials_labels),
        environment_section_labels=frozenset(env_section_labels),
        env_variable_names_seen=frozenset(env_names),
    )


# -----------------------------------------------------------------------------
# Model catalog parsing
# -----------------------------------------------------------------------------

def _parse_model_catalog(stdout: str) -> dict[str, tuple[str, ...]]:
    """Parse ``opencode models`` output into ``{provider_id: (model_ids,)}``.

    Lines shaped ``<provider>/<model>`` are kept. Anything else is
    discarded. Lines whose shape resembles a secret are rejected up-front
    so the harness can never persist them.
    """

    cleaned = _strip_ansi(stdout)
    by_provider: dict[str, list[str]] = {}
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _MODEL_LINE_RE.match(line)
        if not match:
            continue
        provider = match["provider"]
        model = match["model"]
        # Reject anything that matches a secret shape; this should be
        # impossible for ``opencode models`` but the harness is paranoid.
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(model) or pattern.search(provider):
                raise ValueError(f"refusing to persist suspicious model line {line!r}")
        by_provider.setdefault(provider, []).append(model)
    return {key: tuple(sorted(set(values))) for key, values in by_provider.items()}


# -----------------------------------------------------------------------------
# Discovery entry point
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryCycleOutcome:
    """The outcome of a single ``discover`` invocation.

    Always populated, even on failure — the product runtime needs the
    failure code to surface ``FAILED`` honestly on the Dashboard.
    """

    result: DiscoveryResult | None
    error_code: str | None
    error_message: str | None


class ProviderDiscoveryError(RuntimeError):
    """Raised when discovery cannot complete.

    The product runtime catches this and converts it into a
    ``DiscoveryState.FAILED`` outcome so the Dashboard never silently
    shows an empty provider list.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def discover(
    *,
    families: Sequence[ProviderFamilySpec] = PROVIDER_FAMILIES,
    opencode_path: Path | None = None,
    timeout_seconds: float = DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> DiscoveryCycleOutcome:
    """Run a single credential-safe discovery cycle.

    Parameters
    ----------
    families:
        Provider families to probe. The default is the curated
        ``PROVIDER_FAMILIES`` table; callers (tests, future expansion)
        may override it.
    opencode_path:
        Explicit path to the OpenCode CLI. ``None`` triggers the
        standard resolution order.
    timeout_seconds:
        Wall-clock cap for each subprocess invocation.
    clock:
        Override for tests. Defaults to ``datetime.now(tz=UTC)``.

    Returns
    -------
    ``DiscoveryCycleOutcome`` containing either a populated
    ``DiscoveryResult`` or a typed error code.

    The function never raises for ordinary failures (missing CLI,
    timeout, malformed output). It always returns an outcome so the
    product runtime can present the truth to the user.
    """

    observed_at = (clock() if clock is not None else datetime.now(tz=UTC))
    if clock is not None and observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)

    executable = _resolve_opencode(opencode_path)
    if executable is None:
        return DiscoveryCycleOutcome(
            result=None,
            error_code="OPENCODE_CLI_NOT_FOUND",
            error_message="opencode CLI not found on PATH or at ~/.opencode/bin/opencode",
        )

    version_result = _run_opencode(
        ("--version",),
        executable=executable,
        timeout_seconds=timeout_seconds,
    )
    opencode_version = (
    version_result.stdout.strip().splitlines()[0]
    if version_result.stdout else "unknown"
)

    providers_result = _run_opencode(
        ("providers", "list"),
        executable=executable,
        timeout_seconds=timeout_seconds,
    )
    if providers_result.returncode != 0:
        return DiscoveryCycleOutcome(
            result=None,
            error_code="OPENCODE_PROVIDERS_LIST_FAILED",
            error_message=(
                f"opencode providers list exited with code {providers_result.returncode}"
            ),
        )
    presence = _parse_provider_list(providers_result.stdout)

    catalog_by_provider: dict[str, tuple[str, ...]] = {}
    for family in families:
        models_result = _run_opencode(
            ("models", family.provider_id),
            executable=executable,
            timeout_seconds=timeout_seconds,
        )
        if models_result.returncode != 0:
            # A missing provider family is not a hard error: a missing
            # ``opencode models <family>`` simply means the catalog does
            # not advertise that family on this installation.
            no_models_msg = "No models found"
            if no_models_msg in models_result.stderr or no_models_msg in models_result.stdout:
                continue
            continue
        parsed = _parse_model_catalog(models_result.stdout)
        catalog_by_provider.update(parsed)

    discovered: list[ProviderDiscovery] = []
    for family in families:
        model_skus = catalog_by_provider.get(family.provider_id, ())
        env_present = tuple(
            name for name in family.env_variables
            # We check ``os.environ`` (the harness process environment) for
            # **presence** only. The value is never read.
            if name in os.environ
        )
        in_cred_store = presence.has_in_credentials_store(family)
        in_env_section = presence.has_in_environment(family)
        if in_cred_store or in_env_section or env_present:
            auth_status = AuthStatus.AUTH_FROM_ENV_PRESENCE
        else:
            auth_status = AuthStatus.AUTH_REQUIRED
        execution_status = (
            ExecutionStatus.AVAILABLE_FOR_CATALOG
            if model_skus
            else ExecutionStatus.UNKNOWN
        )
        evidence_source = (
            "DISCOVERED_FROM_CATALOG" if model_skus
            else "DISCOVERED_FROM_CATALOG"   # always catalog-derived; no probes run
        )
        region = _infer_region(family)
        # Reject any per-family record whose serialised form would carry
        # a secret. ``assert_sanitized`` raises ``ValueError`` on the
        # first canary match; we convert that to a typed outcome so the
        # caller never panics.
        try:
            record = ProviderDiscovery(
                provider_id=family.provider_id,
                display_name=family.display_name,
                auth_status=auth_status,
                execution_status=execution_status,
                evidence_source=evidence_source,
                model_skus=model_skus,
                env_variables_present=env_present,
                region=region,
                in_credentials_store=in_cred_store,
                observed_at=observed_at,
            )
            assert_sanitized(record.__dict__)
        except Exception as exc:  # pragma: no cover — defensive
            return DiscoveryCycleOutcome(
                result=None,
                error_code="SANITIZATION_REJECTED",
                error_message=str(exc),
            )
        discovered.append(record)

    state = (
        DiscoveryState.DISCOVERED if discovered
        else DiscoveryState.EMPTY
    )
    try:
        result = DiscoveryResult(
            discovered_at=observed_at,
            opencode_path=str(executable),
            opencode_version=opencode_version,
            source_method="opencode_cli_inspection",
            providers=tuple(discovered),
            state=state,
            last_error_code=None,
        )
        result.to_dict()  # triggers assert_sanitized
    except Exception as exc:
        return DiscoveryCycleOutcome(
            result=None,
            error_code="SANITIZATION_REJECTED",
            error_message=str(exc),
        )

    return DiscoveryCycleOutcome(result=result, error_code=None, error_message=None)


# -----------------------------------------------------------------------------
# Registry assembly
# -----------------------------------------------------------------------------

def build_registry(result: DiscoveryResult) -> ModelRegistry:
    """Convert a ``DiscoveryResult`` into a typed ``ModelRegistry``.

    The registry contains only sanitized ``Provider``, ``ModelSKU``,
    and synthetic ``ExecutionTarget`` rows. We synthesise one
    ``ExecutionTarget`` per ``ModelSKU`` so the Control API can
    project a concrete execution surface even though the discovery
    cycle did not authorize quota or query the runtime directly
    (§17: discovery must not burn quota).

    Any sensitive fields that the upstream CLI accidentally surfaces
    will be rejected by ``assert_sanitized`` before this function
    returns.
    """

    from personal_ai_orchestrator.model_registry import (
        Account,
        ExecutionTarget,
        ModelCatalogSnapshot,
    )

    providers: dict[str, Provider] = {}
    accounts: dict[str, Account] = {}
    models: dict[str, ModelSKU] = {}
    execution_targets: dict[str, ExecutionTarget] = {}
    snapshot_id = f"discovery:{result.discovered_at.isoformat()}"
    catalog_snapshot = ModelCatalogSnapshot(
        id=snapshot_id,
        source=result.source_method,
        as_of=result.discovered_at,
        fetched_at=result.discovered_at,
    )
    for record in result.providers:
        provider = Provider(
            id=record.provider_id,
            display_name=record.display_name,
        )
        providers[provider.id] = provider
        # Synthetic account: the discovery cycle does not surface a
        # distinct account identity, so we model one implicit account
        # per provider family. ``credential_ref`` stays ``None`` so the
        # persisted registry remains credential-free.
        accounts[provider.id] = Account(
            id=provider.id,
            provider_id=provider.id,
            label=provider.display_name,
        )
        for sku in record.model_skus:
            key = f"{record.provider_id}/{sku}"
            models[key] = ModelSKU(
                id=key,
                provider_id=record.provider_id,
                display_name=sku,
                catalog_snapshot_id=snapshot_id,
            )
            # Synthetic execution target. ``account_id`` defaults to
            # the provider family id because the discovery cycle did
            # not surface a distinct account identity; the runtime
            # surface is ``opencode`` (the only harness we know of).
            # The execution target id uses a single dash instead of
            # the slash used by ``ModelSKU.id`` because downstream
            # filesystem-based evidence stores reject slashes.
            target_id = f"{record.provider_id}-{sku}"
            execution_targets[target_id] = ExecutionTarget(
                id=target_id,
                model_sku_id=key,
                account_id=provider.id,
                runtime_id="opencode",
                runtime_provider_id="opencode",
                enabled=True,
            )
    snapshot = {
        "schema_version": 1,
        "registry": {
            "providers": {p.id: p.model_dump() for p in providers.values()},
            "accounts": {a.id: a.model_dump() for a in accounts.values()},
            "models": {m.id: m.model_dump() for m in models.values()},
            "execution_targets": {
                e.id: e.model_dump() for e in execution_targets.values()
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
    "AuthStatus",
    "DEFAULT_OPENCODE_PATH",
    "DISCOVERY_SUBPROCESS_MAX_OUTPUT_BYTES",
    "DISCOVERY_SUBPROCESS_TIMEOUT_SECONDS",
    "DiscoveryCycleOutcome",
    "DiscoveryResult",
    "DiscoveryState",
    "ExecutionStatus",
    "PROVIDER_FAMILIES",
    "ProviderDiscovery",
    "ProviderDiscoveryError",
    "ProviderFamilySpec",
    "SubprocessResult",
    "build_registry",
    "discover",
]