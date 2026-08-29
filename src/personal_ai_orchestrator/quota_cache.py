"""Credential-free atomic last-known-good cache for normalized quota snapshots."""

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
        raise ValueError("quota_pool_id cannot produce an empty cache filename")
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


class QuotaSnapshotCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, quota_pool_id: str) -> Path:
        return self.root / f"quota-{_safe_name(quota_pool_id)}.json"

    def write(self, snapshot: QuotaSnapshot) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        record = CachedQuotaSnapshot(snapshot=snapshot)
        payload = record.model_dump(mode="json")
        _assert_credential_free(payload)
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        target = self.path_for(snapshot.quota_pool_id)

        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.root)
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


def refresh_with_last_known_good(
    collector: QuotaCollector,
    cache: QuotaSnapshotCache,
    *,
    quota_pool_id: str,
) -> QuotaCollectionResult:
    result = collector.collect()
    if result.status is QuotaCollectionStatus.SUCCESS and result.snapshot is not None:
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
