"""Persisted global default scheduling policy.

The owner-facing default lives host-side, not in the App. A macOS ``AppStorage``
value is a client preference; scheduling truth must survive App reinstall and be
identical for every local client, so the daemon owns it.

Reads fail closed to ``BALANCED``: absent, corrupt, or unknown-schema state never
invents an aggressive policy. Changing the default affects only future unresolved
routing decisions — historical decisions record their own resolution and are never
rewritten.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

SCHEMA = "scheduling-settings-v1"
DEFAULT_POLICY = "BALANCED"
#: M1 WP5a-1: orchestrator scheduling mode default. ``MANUAL`` keeps
#: the pre-WP5a-1 owner-explicit-dispatch behavior.
DEFAULT_MODE = "MANUAL"
#: M1 WP5a-1: orchestrator modes selectable on ``PUT
#: /v1/settings/scheduling``. ``ACTIVE`` is reachable on the wire
#: but the facade rejects it with 409 when activation authority is
#: not authorised — it stays production-disabled.
SELECTABLE_MODES = (
    "MANUAL",
    "SUPERVISED_AUTO",
    "ACTIVE",
)

#: Policies the owner may pick as a *global default*. MANUAL is deliberately absent:
#: a global "manual" default cannot name a target that is valid for every future task.
#: BURN_DOWN is added in M1 WP3 — it is the pressure-first preset that
#: makes ``STARVED`` targets rank above ``ON_TRACK`` for the same provider.
SELECTABLE_GLOBAL_POLICIES = (
    "BALANCED",
    "QUALITY_FIRST",
    "QUOTA_SAVER",
    "SPEED_FIRST",
    "BURN_DOWN",
)


class PolicyRevisionConflict(ValueError):
    """The owner edited a settings generation that has since changed."""


class SchedulingSettings:
    """Thread-safe, atomically persisted global default scheduling policy.

    M1 WP5a-1 adds ``mode`` — the orchestrator scheduling mode that
    gates whether the host-owned ``SUPERVISED_AUTO`` tick path is
    enabled. ``mode`` is persisted alongside ``default_scheduling_policy``
    on the same JSON file so an upgrade is a single atomic write.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._policy = DEFAULT_POLICY
        self._mode = DEFAULT_MODE
        self._revision = self._legacy_revision()
        self._load_from_disk()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def default_policy(self) -> str:
        return self.versioned_snapshot()[0]

    @property
    def mode(self) -> str:
        return self.versioned_snapshot()[1]

    def snapshot(self) -> tuple[str, str]:
        """Read the policy and mode from one committed settings generation."""

        policy, mode, _ = self.versioned_snapshot()
        return policy, mode

    @staticmethod
    def _legacy_revision(raw: bytes = b"") -> str:
        # Old files have no revision. Derive a stable migration token without
        # modifying owner configuration on read; the first edit persists a UUID.
        return hashlib.sha256(b"scheduling-settings-legacy:" + raw).hexdigest()[:32]

    @contextmanager
    def _committed_generation(self):
        with self._lock:
            if self._path is None:
                yield
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.with_name(self._path.name + ".lock")
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._load_from_disk()
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def versioned_snapshot(self) -> tuple[str, str, str]:
        """Read a committed generation, including other daemon writers."""
        with self._committed_generation():
            return self._policy, self._mode, self._revision

    def update(
        self,
        *,
        default_policy: str | None = None,
        mode: str | None = None,
        expected_revision: str | None = None,
    ) -> tuple[str, str]:
        """Validate and commit one settings generation, returning the previous pair.

        Omitted fields retain their value under the same lock as the write. All
        values are checked before persistence; a failed write publishes neither
        field in memory. Activation authority remains the facade's responsibility.
        """

        if default_policy is not None and default_policy not in SELECTABLE_GLOBAL_POLICIES:
            raise ValueError("unsupported_global_scheduling_policy")
        if mode is not None and mode not in SELECTABLE_MODES:
            raise ValueError("unsupported_scheduling_mode")
        with self._committed_generation():
            if expected_revision is not None and expected_revision != self._revision:
                raise PolicyRevisionConflict("policy_revision_conflict")
            previous = (self._policy, self._mode)
            proposed_policy = self._policy if default_policy is None else default_policy
            proposed_mode = self._mode if mode is None else mode
            revision = uuid4().hex
            if self._path is not None:
                self._persist(policy=proposed_policy, mode=proposed_mode, revision=revision)
            self._policy, self._mode, self._revision = proposed_policy, proposed_mode, revision
            return previous

    def set_default_policy(self, value: str) -> str:
        self.update(default_policy=value)
        return value

    def set_mode(self, value: str) -> str:
        """M1 WP5a-1: persist the orchestrator scheduling mode.

        Validates the value against ``SELECTABLE_MODES``. Activation
        gate enforcement (``mode == "ACTIVE"`` requires authorised
        activation authority) lives in the control-plane facade
        rather than here, so the persisted store stays free of any
        policy-side-effect logic.
        """

        self.update(mode=value)
        return value

    def _load_from_disk(self) -> str | None:
        self._policy, self._mode = DEFAULT_POLICY, DEFAULT_MODE
        self._revision = self._legacy_revision()
        if self._path is None or not self._path.exists():
            return None
        try:
            raw = self._path.read_bytes()
            self._revision = self._legacy_revision(raw)
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            return None
        policy = payload.get("default_scheduling_policy")
        if policy not in SELECTABLE_GLOBAL_POLICIES:
            return None
        self._policy = policy
        # M1 WP5a-1: mode field is optional in the persisted payload
        # so pre-WP5a-1 settings files load cleanly. Absent mode falls
        # back to ``DEFAULT_MODE`` — the pre-WP5a-1 manual-only behavior.
        mode = payload.get("mode", DEFAULT_MODE)
        if mode not in SELECTABLE_MODES:
            mode = DEFAULT_MODE
        self._mode = mode
        revision = payload.get("revision")
        if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{32}", revision):
            self._revision = revision
        return policy

    def _persist(self, *, policy: str, mode: str, revision: str) -> None:
        """Persist proposed values before publishing them to in-memory readers."""

        from datetime import UTC, datetime

        payload = {
            "schema": SCHEMA,
            "default_scheduling_policy": policy,
            "mode": mode,
            "revision": revision,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self._path.name}.", dir=self._path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


__all__ = [
    "DEFAULT_MODE",
    "DEFAULT_POLICY",
    "SCHEMA",
    "SELECTABLE_GLOBAL_POLICIES",
    "SELECTABLE_MODES",
    "SchedulingSettings",
    "PolicyRevisionConflict",
]
