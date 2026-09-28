"""Credential resolution for the host-owned, read-only quota collector.

Three credential contracts, deliberately kept apart
---------------------------------------------------
``DISCOVERY``
    Answers "does a credential appear to be configured". Credential *values*
    are stripped from the subprocess environment entirely
    (:mod:`provider_discovery` blocklist). Presence only.

``QUOTA`` — this module
    Needs an actual value to call a documented read-only quota endpoint, and
    nothing else. It resolves one credential for one provider surface, holds it
    in memory for the duration of the request, and never writes it anywhere.

``EXECUTION``
    Provider execution auth, owned by the execution path.

Fixing quota by handing every provider credential to every child process would
erase that separation, so resolution here is narrow: one explicitly permitted
environment variable, explicit owner-managed PAO Keychain account(s), and one
explicitly permitted entry in the OpenCode auth store, per provider surface.
The Keychain path is opt-in and never inspects or copies Pi credential storage.

Why the auth store is read at all
---------------------------------
The daemon is normally launched by the GUI app, which inherits LaunchServices'
environment rather than a login shell's. ``ZAI_API_KEY`` is therefore absent in
exactly the situation the owner cares about, even though OpenCode holds a
working credential for the same account. Reporting ``CREDENTIAL_NOT_AVAILABLE``
there is technically true of the environment and misleading about the product:
the owner *is* signed in. Reading the store the owner already authorized
OpenCode to keep is the honest resolution, and it stays read-only —
this module never writes, copies, or reshapes the store.

Exposure rules enforced below
-----------------------------
:class:`SecretValue` redacts under ``repr``/``str``/``format`` and refuses to
serialize, so a credential cannot reach a log line, an exception message, an
evidence file, or a JSON response through the ordinary paths that produce them.
Only :meth:`SecretValue.reveal` returns the value, and its sole caller is the
collector building an ``Authorization`` header.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REDACTED = "***REDACTED***"
OWNER_QUOTA_KEYCHAIN_SERVICE = "personal-ai-orchestrator.quota"

#: Field names OpenCode uses for the credential inside an auth-store entry.
_AUTH_STORE_KEY_FIELDS: tuple[str, ...] = ("key", "apiKey", "api_key", "token", "accessToken")

#: Auth-store entry types we accept. ``api`` is a bearer-style API key; other
#: types (OAuth refresh flows) need a token exchange this module does not do,
#: and silently treating one as an API key would send a useless string to the
#: provider and report the resulting 401 as the owner's fault.
_SUPPORTED_AUTH_TYPES: frozenset[str] = frozenset({"api"})


class CredentialScope(StrEnum):
    """Which of the three credential contracts a request belongs to."""

    DISCOVERY = "DISCOVERY"
    QUOTA = "QUOTA"
    EXECUTION = "EXECUTION"


class CredentialSource(StrEnum):
    """Where a resolved quota credential came from. Sanitized; owner-facing."""

    ENVIRONMENT = "ENVIRONMENT"
    OWNER_KEYCHAIN = "OWNER_KEYCHAIN"
    OPENCODE_AUTH_STORE = "OPENCODE_AUTH_STORE"
    NONE = "NONE"


class SecretValue:
    """A string that refuses to render itself.

    ``__repr__``/``__str__``/``__format__`` return :data:`REDACTED`, so f-strings,
    ``print``, ``logging``, ``json.dumps`` on a containing dataclass, and
    exception formatting all produce the placeholder rather than the secret.
    Equality is constant-time-ish and only against another ``SecretValue``, so
    a credential is never compared against attacker-influenced text.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw value. The only legitimate caller builds a request header."""

        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial, but security-relevant
        return REDACTED

    def __str__(self) -> str:  # pragma: no cover - trivial, but security-relevant
        return REDACTED

    def __format__(self, _spec: str) -> str:  # pragma: no cover - trivial
        return REDACTED

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(("SecretValue", self._value))

    def __reduce__(self):  # pragma: no cover - exercised by the security tests
        raise TypeError("SecretValue must never be pickled or serialized")


@dataclass(frozen=True)
class QuotaCredentialSpec:
    """The credential references one provider surface is permitted to use.

    Both fields are explicit allowlists. A surface can only read the variable
    and the store entry named here; nothing is discovered by pattern matching,
    so adding a provider is a deliberate edit rather than an emergent behaviour.
    """

    provider_id: str
    env_var: str | None = None
    #: Explicit owner-managed macOS Keychain accounts this quota surface may
    #: read. This is intentionally separate from Pi execution authentication:
    #: nothing here discovers, copies, or parses Pi credential storage.
    owner_keychain_accounts: tuple[str, ...] = ()
    opencode_auth_provider_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedQuotaCredential:
    """A resolved credential plus its sanitized provenance.

    The dataclass is safe to log or embed in evidence: ``secret`` renders as
    :data:`REDACTED` through every standard formatting path.
    """

    provider_id: str
    source: CredentialSource
    secret: SecretValue | None

    @property
    def present(self) -> bool:
        """Boolean presence — the only credential fact any diagnostic may report."""

        return self.secret is not None and bool(self.secret)


def default_opencode_auth_paths(home: Path | None = None) -> tuple[Path, ...]:
    """Candidate OpenCode auth-store locations, most specific first.

    ``XDG_DATA_HOME`` is honoured before the default so an owner who relocated
    their data directory is not told they are signed out.
    """

    base = home or Path.home()
    candidates: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "opencode" / "auth.json")
    candidates.append(base / ".local" / "share" / "opencode" / "auth.json")
    return tuple(candidates)


def _read_auth_store(path: Path) -> dict[str, object]:
    """Parse one auth store, or return empty. Never raises, never logs content."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_secret(entry: object) -> SecretValue | None:
    """Pull the credential out of one auth-store entry, or ``None``.

    Entries whose ``type`` we do not support are skipped rather than guessed
    at: sending an OAuth refresh token as an API key produces a 401 the owner
    would reasonably read as "my subscription is broken".
    """

    if isinstance(entry, str):
        return SecretValue(entry) if entry else None
    if not isinstance(entry, dict):
        return None
    entry_type = entry.get("type")
    if isinstance(entry_type, str) and entry_type not in _SUPPORTED_AUTH_TYPES:
        return None
    for field in _AUTH_STORE_KEY_FIELDS:
        value = entry.get(field)
        if isinstance(value, str) and value:
            return SecretValue(value)
    return None


def _read_owner_keychain_secret(account: str) -> SecretValue | None:
    """Read one explicitly owner-managed macOS Keychain quota credential.

    The secret is requested by stable service/account identity and captured
    only in memory. It never appears in argv, logs, persisted PAO state, or
    provider discovery. Non-macOS hosts and lookup failures simply return
    ``None`` so test/Linux paths stay fail-closed.
    """

    if sys.platform != "darwin":
        return None
    security = Path("/usr/bin/security")
    if not security.is_file():
        return None
    try:
        completed = subprocess.run(
            [
                str(security),
                "find-generic-password",
                "-s",
                OWNER_QUOTA_KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.rstrip("\r\n")
    return SecretValue(value) if value else None


class QuotaCredentialResolver:
    """Resolves the single credential one quota collector is allowed to use.

    Resolution order is environment first, then the owner-managed PAO
    Keychain, then the OpenCode auth store. The environment remains the
    explicit per-run override; the Keychain is the owner's explicit quota-only
    authorization; OpenCode is the existing standing provider credential.
    """

    def __init__(
        self,
        *,
        specs: Mapping[str, QuotaCredentialSpec],
        environ: Mapping[str, str] | None = None,
        auth_store_paths: tuple[Path, ...] | None = None,
        owner_keychain_lookup: Callable[[str], SecretValue | None] | None = None,
    ) -> None:
        self._specs = dict(specs)
        self._environ = environ if environ is not None else os.environ
        self._auth_store_paths = (
            auth_store_paths if auth_store_paths is not None else default_opencode_auth_paths()
        )
        self._owner_keychain_lookup = owner_keychain_lookup or _read_owner_keychain_secret

    def resolve(self, provider_id: str) -> ResolvedQuotaCredential:
        spec = self._specs.get(provider_id)
        if spec is None:
            return ResolvedQuotaCredential(
                provider_id=provider_id,
                source=CredentialSource.NONE,
                secret=None,
            )

        if spec.env_var:
            value = self._environ.get(spec.env_var)
            if value:
                return ResolvedQuotaCredential(
                    provider_id=provider_id,
                    source=CredentialSource.ENVIRONMENT,
                    secret=SecretValue(value),
                )

        for account in spec.owner_keychain_accounts:
            try:
                secret = self._owner_keychain_lookup(account)
            except Exception:
                secret = None
            if secret is not None:
                return ResolvedQuotaCredential(
                    provider_id=provider_id,
                    source=CredentialSource.OWNER_KEYCHAIN,
                    secret=secret,
                )

        for path in self._auth_store_paths:
            store = _read_auth_store(path)
            if not store:
                continue
            for auth_provider_id in spec.opencode_auth_provider_ids:
                secret = _extract_secret(store.get(auth_provider_id))
                if secret is not None:
                    return ResolvedQuotaCredential(
                        provider_id=provider_id,
                        source=CredentialSource.OPENCODE_AUTH_STORE,
                        secret=secret,
                    )

        return ResolvedQuotaCredential(
            provider_id=provider_id,
            source=CredentialSource.NONE,
            secret=None,
        )

    def presence(self, provider_id: str) -> bool:
        """Boolean-only presence probe for diagnostics and the Dashboard."""

        return self.resolve(provider_id).present


__all__ = [
    "REDACTED",
    "OWNER_QUOTA_KEYCHAIN_SERVICE",
    "CredentialScope",
    "CredentialSource",
    "QuotaCredentialResolver",
    "QuotaCredentialSpec",
    "ResolvedQuotaCredential",
    "SecretValue",
    "default_opencode_auth_paths",
]
