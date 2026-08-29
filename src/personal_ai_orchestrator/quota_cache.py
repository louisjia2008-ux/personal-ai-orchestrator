"""Credential-free quota cache and append-only replay journal."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
    QuotaCollector,
)
from personal_ai_orchestrator.quota_observability import QuotaSnapshot

CACHE_SCHEMA_VERSION = 1
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "token",
}


class CachedQuotaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=CACHE_SCHEMA_VERSION, ge=1)
    snapshot: QuotaSnapshot


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not cleaned:
        raise ValueError("value cannot produce an empty cache filename")
    return cleaned


def _assert_credential_free(value: Any, *, key: str | None = None) -> None:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        raise ValueError(f"credential-like field {key!r} is forbidden in quota cache")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _assert_credential_free(child_value, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _assert_credential_free(child)


def _render_record(snapshot: QuotaSnapshot) -> str:
    record = CachedQuotaSnapshot(snapshot=snapshot)
    payload = record.model_dump(mode="json")
    _assert_credential_free(payload)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _atomic_write(target: Path, rendered: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
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


class QuotaSnapshotCache:
    """Mutable last-known-good pointer used for current runtime state."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, quota_pool_id: str) -> Path:
        return self.root / f"quota-{_safe_name(quota_pool_id)}.json"

    def write(self, snapshot: QuotaSnapshot) -> Path:
        if snapshot.quota_pool_id is None:
            raise ValueError("quota snapshot must name quota_pool_id before caching")
        return _atomic_write(self.path_for(snapshot.quota_pool_id), _render_record(snapshot))

    def load(self, quota_pool_id: str) -> QuotaSnapshot | None:
        path = self.path_for(quota_pool_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        record = CachedQuotaSnapshot.model_validate_json(raw)
        if record.schema_version != CACHE_SCHEMA_VERSION:
            return None
        return record.snapshot


class QuotaSnapshotJournal:
    """Append-only immutable history addressed by ``QuotaSnapshot.id``.

    Routing decisions can persist snapshot IDs and later resolve the exact observation that
    was known at decision time. Re-appending the same ID is idempotent only when the bytes
    represent the same normalized snapshot; conflicting reuse fails closed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, snapshot_id: str) -> Path:
        return self.root / "quota-history" / f"{_safe_name(snapshot_id)}.json"

    def append(self, snapshot: QuotaSnapshot) -> Path:
        target = self.path_for(snapshot.id)
        rendered = _render_record(snapshot)
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing != rendered:
                raise ValueError(f"quota snapshot id {snapshot.id!r} already has different content")
            return target
        return _atomic_write(target, rendered)

    def load(self, snapshot_id: str) -> QuotaSnapshot | None:
        path = self.path_for(snapshot_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        record = CachedQuotaSnapshot.model_validate_json(raw)
        if record.schema_version != CACHE_SCHEMA_VERSION:
            return None
        return record.snapshot


def refresh_with_last_known_good(
    collector: QuotaCollector,
    cache: QuotaSnapshotCache,
    *,
    quota_pool_id: str,
    journal: QuotaSnapshotJournal | None = None,
) -> QuotaCollectionResult:
    result = collector.collect()
    if result.status is QuotaCollectionStatus.SUCCESS and result.snapshot is not None:
        if journal is not None:
            journal.append(result.snapshot)
        cache.write(result.snapshot)
        return result

    cached = cache.load(quota_pool_id)
    if cached is not None:
        return QuotaCollectionResult(
            status=QuotaCollectionStatus.STALE,
            snapshot=None,
            last_known_good=cached,
            error_category=result.error_category,
        )
    return result
