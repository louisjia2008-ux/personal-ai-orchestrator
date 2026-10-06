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

import json
import os
import tempfile
import threading
from pathlib import Path

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
        self._load_from_disk()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def default_policy(self) -> str:
        with self._lock:
            return self._policy

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_default_policy(self, value: str) -> str:
        if value not in SELECTABLE_GLOBAL_POLICIES:
            raise ValueError("unsupported_global_scheduling_policy")
        with self._lock:
            if self._path is not None:
                self._persist(policy=value, mode=self._mode)
            self._policy = value
            return self._policy

    def set_mode(self, value: str) -> str:
        """M1 WP5a-1: persist the orchestrator scheduling mode.

        Validates the value against ``SELECTABLE_MODES``. Activation
        gate enforcement (``mode == "ACTIVE"`` requires authorised
        activation authority) lives in the control-plane facade
        rather than here, so the persisted store stays free of any
        policy-side-effect logic.
        """

        if value not in SELECTABLE_MODES:
            raise ValueError("unsupported_scheduling_mode")
        with self._lock:
            if self._path is not None:
                self._persist(policy=self._policy, mode=value)
            self._mode = value
            return self._mode

    def _load_from_disk(self) -> str | None:
        if self._path is None or not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
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
        return policy

    def _persist(self, *, policy: str, mode: str) -> None:
        """Persist proposed values before publishing them to in-memory readers."""

        from datetime import UTC, datetime

        payload = {
            "schema": SCHEMA,
            "default_scheduling_policy": policy,
            "mode": mode,
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
]
