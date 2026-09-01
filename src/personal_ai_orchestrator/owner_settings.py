"""Persisted Owner-Initiated Execution setting.

This setting is strictly separate from Production ACTIVE (which remains
``DISABLED_BY_DESIGN``). It means only: the local owner may explicitly
dispatch a task through the owner-dispatch endpoint. It never implies
autonomous task execution, scheduler switching, or permanent routing
approval.

The setting fails closed: absent, corrupt, or unknown-schema state is
always OFF. It is never migrated from legacy state and never fabricated.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

SCHEMA = "owner-execution-settings-v1"


class OwnerExecutionSettings:
    """Thread-safe, atomically persisted owner-execution toggle."""

    def __init__(self, path: Path | None = None, *, initial: bool = False) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._enabled = False
        if self._load_from_disk() is None and initial:
            self._enabled = True
            if self._path is not None:
                self._persist(self._enabled)

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, value: bool) -> None:
        with self._lock:
            self._enabled = bool(value)
            if self._path is not None:
                self._persist(self._enabled)

    def _load_from_disk(self) -> bool | None:
        if self._path is None or not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            return None
        enabled = payload.get("owner_initiated_execution_enabled")
        if not isinstance(enabled, bool):
            return None
        self._enabled = enabled
        return enabled

    def _persist(self, enabled: bool) -> None:
        from datetime import UTC, datetime

        payload = {
            "schema": SCHEMA,
            "owner_initiated_execution_enabled": enabled,
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


__all__ = ["OwnerExecutionSettings", "SCHEMA"]
