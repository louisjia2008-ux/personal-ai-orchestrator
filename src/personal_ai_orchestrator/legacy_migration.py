"""Idempotent, non-destructive migration of legacy runtime state.

Source: ``~/.personal-ai-orchestrator`` (pre-P4.2 location).
Destination: the macOS Application Support layout.

Guarantees:

- schema/version-stamped migration record with source-path attribution
  and per-entry content hashes;
- idempotent: an existing record short-circuits a re-run;
- no deletion or mutation of the legacy tree;
- credentials are never copied (name- and content-level guards);
- authority surfaces (owner-execution settings, activation/approval
  state) are never migrated, so no owner approval, Production ACTIVE or
  execution verification can be fabricated by migration;
- every skip/failure is observable in the record; nothing silently
  disappears.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

MIGRATION_SCHEMA = "legacy-state-migration-v1"
MIGRATION_RECORD_NAME = "legacy-migration-v1.json"

_CREDENTIAL_NAME_PATTERN = re.compile(
    r"(auth\.json|credential|api[-_]?key|secret|token|\.key$)", re.IGNORECASE
)
_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(api[_-]?key|credential[_-]?ref|access[_-]?token|refresh[_-]?token|secret|bearer)",
    re.IGNORECASE,
)
_AUTHORITY_SURFACE_NAMES = {"owner-execution.json", "activation.json", "approvals.json"}
_UNSUPPORTED_NAMES = {"control.sock", "daemon-start.lock", ".ds_store"}
_SKIP_WAL_SIDECARS = (".sqlite3-shm", ".sqlite3-wal")


class EntryOutcome(StrEnum):
    COPIED = "COPIED"
    SKIPPED_ALREADY_MIGRATED = "SKIPPED_ALREADY_MIGRATED"
    SKIPPED_CREDENTIAL_SUSPECT = "SKIPPED_CREDENTIAL_SUSPECT"
    SKIPPED_AUTHORITY_SURFACE = "SKIPPED_AUTHORITY_SURFACE"
    SKIPPED_UNSUPPORTED = "SKIPPED_UNSUPPORTED"
    SKIPPED_DEST_CONFLICT = "SKIPPED_DEST_CONFLICT"
    FAILED = "FAILED"


class MigrationOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_SKIPS = "COMPLETED_WITH_SKIPS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class MigrationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    outcome: EntryOutcome
    sha256: str | None = None
    bytes: int | None = None
    source_path: str
    detail: str | None = None


class MigrationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    migration_schema: str
    migration_id: str
    source_path: str
    destination_path: str
    migrated_at: datetime
    outcome: MigrationOutcome
    entries: tuple[MigrationEntry, ...] = ()
    failure_code: str | None = None

    @property
    def copied_count(self) -> int:
        return sum(1 for e in self.entries if e.outcome is EntryOutcome.COPIED)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_has_credential_material(payload: object) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str) and value and _CREDENTIAL_KEY_PATTERN.search(key):
                return True
            if _json_has_credential_material(value):
                return True
    elif isinstance(payload, list):
        for item in payload:
            if _json_has_credential_material(item):
                return True
    return False


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle, source.open("rb") as src:
            shutil.copyfileobj(src, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()
    os.chmod(destination, 0o600)


def migrate_legacy_state(
    source: Path,
    destination_root: Path,
    *,
    migration_id: str | None = None,
    now: datetime | None = None,
) -> MigrationRecord:
    """Migrate the legacy tree into ``destination_root`` exactly once."""

    record_path = destination_root / "runtime-state" / "migration" / MIGRATION_RECORD_NAME
    if record_path.exists():
        try:
            existing = MigrationRecord.model_validate_json(
                record_path.read_text(encoding="utf-8")
            )
            if existing.migration_schema == MIGRATION_SCHEMA:
                return existing
        except Exception:
            # A corrupt record is an observable failure; never re-migrate
            # on top of ambiguous state.
            return MigrationRecord(
                migration_schema=MIGRATION_SCHEMA,
                migration_id=migration_id or "unknown",
                source_path=str(source),
                destination_path=str(destination_root),
                migrated_at=now or datetime.now(UTC),
                outcome=MigrationOutcome.FAILED,
                failure_code="MIGRATION_RECORD_CORRUPT",
            )

    source = source.expanduser()
    if not source.exists():
        record = MigrationRecord(
            migration_schema=MIGRATION_SCHEMA,
            migration_id=migration_id or f"legacy-{(now or datetime.now(UTC)).isoformat()}",
            source_path=str(source),
            destination_path=str(destination_root),
            migrated_at=now or datetime.now(UTC),
            outcome=MigrationOutcome.SKIPPED,
            failure_code=None,
        )
        _write_record(record_path, record)
        return record

    entries: list[MigrationEntry] = []
    hard_failure: str | None = None
    try:
        candidates = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and not path.name.endswith(_SKIP_WAL_SIDECARS)
        )
        for path in candidates:
            relative = path.relative_to(source).as_posix()
            destination = destination_root / relative
            entry = _migrate_one(path, relative, destination)
            entries.append(entry)
    except OSError as error:
        hard_failure = f"SOURCE_UNREADABLE:{type(error).__name__}"

    if all(entry.outcome is EntryOutcome.COPIED for entry in entries) and not hard_failure:
        outcome = MigrationOutcome.COMPLETED
    elif hard_failure:
        outcome = MigrationOutcome.FAILED
    else:
        outcome = MigrationOutcome.COMPLETED_WITH_SKIPS

    record = MigrationRecord(
        migration_schema=MIGRATION_SCHEMA,
        migration_id=migration_id or f"legacy-{(now or datetime.now(UTC)).isoformat()}",
        source_path=str(source),
        destination_path=str(destination_root),
        migrated_at=now or datetime.now(UTC),
        outcome=outcome,
        entries=tuple(entries),
        failure_code=hard_failure,
    )
    _write_record(record_path, record)
    return record


def _migrate_one(path: Path, relative: str, destination: Path) -> MigrationEntry:
    name = path.name.lower()
    if name in _UNSUPPORTED_NAMES or name.endswith(".sock") or name.endswith(".lock"):
        return MigrationEntry(
            relative_path=relative,
            outcome=EntryOutcome.SKIPPED_UNSUPPORTED,
            source_path=str(path),
            detail="runtime surface (socket/lock) is not state",
        )
    if _CREDENTIAL_NAME_PATTERN.search(name):
        return MigrationEntry(
            relative_path=relative,
            outcome=EntryOutcome.SKIPPED_CREDENTIAL_SUSPECT,
            source_path=str(path),
            detail="credential-suspect file name; never copied",
        )
    if name in _AUTHORITY_SURFACE_NAMES:
        return MigrationEntry(
            relative_path=relative,
            outcome=EntryOutcome.SKIPPED_AUTHORITY_SURFACE,
            source_path=str(path),
            detail="authority surfaces are never migrated (no fabricated approval/active)",
        )

    if destination.exists():
        try:
            if _sha256(destination) == _sha256(path):
                return MigrationEntry(
                    relative_path=relative,
                    outcome=EntryOutcome.SKIPPED_ALREADY_MIGRATED,
                    source_path=str(path),
                    detail="identical content already present",
                )
        except OSError:
            pass
        return MigrationEntry(
            relative_path=relative,
            outcome=EntryOutcome.SKIPPED_DEST_CONFLICT,
            source_path=str(path),
            detail="destination differs; never overwritten",
        )

    try:
        if name.endswith(".json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if _json_has_credential_material(payload):
                return MigrationEntry(
                    relative_path=relative,
                    outcome=EntryOutcome.SKIPPED_CREDENTIAL_SUSPECT,
                    source_path=str(path),
                    detail="credential-suspect JSON keys; never copied",
                )
            _atomic_copy(path, destination)
        elif name.endswith(".sqlite3"):
            _sqlite_backup(path, destination)
        else:
            _atomic_copy(path, destination)
    except Exception as error:
        return MigrationEntry(
            relative_path=relative,
            outcome=EntryOutcome.FAILED,
            source_path=str(path),
            detail=f"{type(error).__name__}",
        )

    try:
        digest = _sha256(destination)
        size = destination.stat().st_size
    except OSError:
        digest, size = None, None
    return MigrationEntry(
        relative_path=relative,
        outcome=EntryOutcome.COPIED,
        sha256=digest,
        bytes=size,
        source_path=str(path),
    )


def _write_record(record_path: Path, record: MigrationRecord) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{record_path.name}.", dir=record_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(record.model_dump_json(indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, record_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "MIGRATION_SCHEMA",
    "EntryOutcome",
    "MigrationEntry",
    "MigrationOutcome",
    "MigrationRecord",
    "migrate_legacy_state",
]
