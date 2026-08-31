"""Immutable routing-policy snapshots for historical decision replay."""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.scheduler import RoutingPolicy


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    policy: RoutingPolicy

    @classmethod
    def from_policy(cls, policy: RoutingPolicy) -> PolicySnapshot:
        payload = policy.model_dump(mode="json")
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(id=f"policy-{digest[:24]}", policy=policy)


class PolicySnapshotJournal:
    def __init__(self, root: Path) -> None:
        self.directory = root / "policy-history"

    def path_for(self, snapshot_id: str) -> Path:
        if not snapshot_id or any(part in snapshot_id for part in ("/", "\\", "..")):
            raise ValueError("unsafe policy snapshot id")
        return self.directory / f"{snapshot_id}.json"

    def append(self, snapshot: PolicySnapshot) -> Path:
        target = self.path_for(snapshot.id)
        rendered = snapshot.model_dump_json(indent=2) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != rendered:
                raise ValueError("policy snapshot id already has different content")
            return target
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(self, snapshot_id: str) -> PolicySnapshot | None:
        path = self.path_for(snapshot_id)
        if not path.exists():
            return None
        return PolicySnapshot.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = ["PolicySnapshot", "PolicySnapshotJournal"]
