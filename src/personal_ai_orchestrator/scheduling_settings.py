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

#: Policies the owner may pick as a *global default*. MANUAL is deliberately absent:
#: a global "manual" default cannot name a target that is valid for every future task.
SELECTABLE_GLOBAL_POLICIES = (
    "BALANCED",
    "QUALITY_FIRST",
    "QUOTA_SAVER",
    "SPEED_FIRST",
)


class SchedulingSettings:
    """Thread-safe, atomically persisted global default scheduling policy."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._policy = DEFAULT_POLICY
        self._load_from_disk()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def default_policy(self) -> str:
        with self._lock:
            return self._policy

    def set_default_policy(self, value: str) -> str:
        if value not in SELECTABLE_GLOBAL_POLICIES:
            raise ValueError("unsupported_global_scheduling_policy")
        with self._lock:
            self._policy = value
            if self._path is not None:
                self._persist(self._policy)
            return self._policy

    def _load_from_disk(self) -> str | None:
        if self._path is None or not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            return None
        policy = payload.get("default_scheduling_policy")
        if policy not in SELECTABLE_GLOBAL_POLICIES:
            return None
        self._policy = policy
        return policy

    def _persist(self, policy: str) -> None:
        from datetime import UTC, datetime

        payload = {
            "schema": SCHEMA,
            "default_scheduling_policy": policy,
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


__all__ = ["SCHEMA", "DEFAULT_POLICY", "SELECTABLE_GLOBAL_POLICIES", "SchedulingSettings"]
