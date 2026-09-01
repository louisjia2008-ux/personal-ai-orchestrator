"""Legacy state migration tests: idempotent, non-destructive, safe."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from personal_ai_orchestrator.legacy_migration import (
    MIGRATION_SCHEMA,
    EntryOutcome,
    MigrationOutcome,
    migrate_legacy_state,
)


def _make_legacy(source: Path) -> None:
    (source / "runtime-state" / "verification-evidence").mkdir(parents=True, exist_ok=True)
    (source / "runtime-state" / "verification-evidence" / "verify-1.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )
    (source / "runtime-state" / "provider-registry.json").write_text(
        json.dumps({"schema": "provider-registry-snapshot-v1", "providers": []}),
        encoding="utf-8",
    )
    conn = sqlite3.connect(source / "state.sqlite3")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()


def test_migration_copies_safe_state_with_hashes(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _make_legacy(source)
    destination = tmp_path / "app-support"

    record = migrate_legacy_state(source, destination)

    assert record.migration_schema == MIGRATION_SCHEMA
    assert record.source_path == str(source)
    assert record.outcome is MigrationOutcome.COMPLETED
    copied = [e for e in record.entries if e.outcome is EntryOutcome.COPIED]
    assert len(copied) == 3
    assert all(e.sha256 and len(e.sha256) == 64 for e in copied)

    migrated_db = destination / "state.sqlite3"
    assert migrated_db.exists()
    rows = sqlite3.connect(migrated_db).execute("SELECT x FROM t").fetchall()
    assert rows == [(42,)]
    evidence = destination / "runtime-state" / "verification-evidence" / "verify-1.json"
    assert evidence.read_text(encoding="utf-8") == json.dumps({"passed": True})


def test_migration_is_idempotent_and_never_reruns(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _make_legacy(source)
    destination = tmp_path / "app-support"

    first = migrate_legacy_state(source, destination)
    second = migrate_legacy_state(source, destination)
    assert second == first

    # Even new files in the legacy tree are not picked up afterwards.
    (source / "runtime-state" / "new-file.json").write_text("{}", encoding="utf-8")
    third = migrate_legacy_state(source, destination)
    assert third == first


def test_migration_never_deletes_or_mutates_source(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _make_legacy(source)
    before = sorted(p.relative_to(source).as_posix() for p in source.rglob("*"))
    (source / "runtime-state" / "marker.json").write_text("keep", encoding="utf-8")

    migrate_legacy_state(source, tmp_path / "app-support")

    after = sorted(p.relative_to(source).as_posix() for p in source.rglob("*"))
    assert "runtime-state/marker.json" in after
    assert set(before).issubset(set(after))
    assert (source / "runtime-state" / "marker.json").read_text(encoding="utf-8") == "keep"


def test_migration_refuses_credentials(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    (source / "runtime-state").mkdir(parents=True, exist_ok=True)
    (source / "auth.json").write_text("{}", encoding="utf-8")
    (source / "runtime-state" / "leaked.json").write_text(
        json.dumps({"api_key": "sk-secret-value"}), encoding="utf-8"
    )
    (source / "runtime-state" / "safe.json").write_text("{}", encoding="utf-8")

    record = migrate_legacy_state(source, tmp_path / "app-support")

    outcomes = {e.relative_path: e.outcome for e in record.entries}
    assert outcomes["auth.json"] is EntryOutcome.SKIPPED_CREDENTIAL_SUSPECT
    assert outcomes["runtime-state/leaked.json"] is EntryOutcome.SKIPPED_CREDENTIAL_SUSPECT
    assert outcomes["runtime-state/safe.json"] is EntryOutcome.COPIED
    assert record.outcome is MigrationOutcome.COMPLETED_WITH_SKIPS
    assert not (tmp_path / "app-support" / "auth.json").exists()
    assert not (tmp_path / "app-support" / "runtime-state" / "leaked.json").exists()


def test_migration_never_migrates_authority_surfaces(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    (source / "runtime-state").mkdir(parents=True, exist_ok=True)
    (source / "runtime-state" / "owner-execution.json").write_text(
        json.dumps({"owner_initiated_execution_enabled": True}), encoding="utf-8"
    )
    (source / "runtime-state" / "activation.json").write_text("{}", encoding="utf-8")

    record = migrate_legacy_state(source, tmp_path / "app-support")

    outcomes = {e.relative_path: e.outcome for e in record.entries}
    assert outcomes["runtime-state/owner-execution.json"] is (
        EntryOutcome.SKIPPED_AUTHORITY_SURFACE
    )
    assert outcomes["runtime-state/activation.json"] is EntryOutcome.SKIPPED_AUTHORITY_SURFACE
    assert not (tmp_path / "app-support" / "runtime-state" / "owner-execution.json").exists()
    assert not (tmp_path / "app-support" / "runtime-state" / "activation.json").exists()


def test_migration_skips_runtime_surfaces(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir(parents=True, exist_ok=True)
    (source / "daemon-start.lock").write_text("", encoding="utf-8")
    (source / "runtime-state").mkdir(exist_ok=True)
    (source / "runtime-state" / "snapshot.json").write_text("{}", encoding="utf-8")

    record = migrate_legacy_state(source, tmp_path / "app-support")

    outcomes = {e.relative_path: e.outcome for e in record.entries}
    assert outcomes["daemon-start.lock"] is EntryOutcome.SKIPPED_UNSUPPORTED
    assert outcomes["runtime-state/snapshot.json"] is EntryOutcome.COPIED


def test_migration_without_legacy_source_is_observable_skip(tmp_path: Path) -> None:
    record = migrate_legacy_state(
        tmp_path / "missing-legacy", tmp_path / "app-support"
    )
    assert record.outcome is MigrationOutcome.SKIPPED
    record_path = (
        tmp_path / "app-support" / "runtime-state" / "migration" / "legacy-migration-v1.json"
    )
    assert record_path.exists()


def test_migration_never_overwrites_conflicting_destination(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    (source / "runtime-state").mkdir(parents=True, exist_ok=True)
    (source / "runtime-state" / "snapshot.json").write_text('{"v": 1}', encoding="utf-8")
    destination = tmp_path / "app-support"
    (destination / "runtime-state").mkdir(parents=True, exist_ok=True)
    (destination / "runtime-state" / "snapshot.json").write_text('{"v": 2}', encoding="utf-8")

    record = migrate_legacy_state(source, destination)

    entry = next(
        e for e in record.entries if e.relative_path == "runtime-state/snapshot.json"
    )
    assert entry.outcome is EntryOutcome.SKIPPED_DEST_CONFLICT
    assert (destination / "runtime-state" / "snapshot.json").read_text(encoding="utf-8") == (
        '{"v": 2}'
    )


def test_corrupt_migration_record_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _make_legacy(source)
    destination = tmp_path / "app-support"
    record_path = (
        destination / "runtime-state" / "migration" / "legacy-migration-v1.json"
    )
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("corrupt-not-json", encoding="utf-8")

    record = migrate_legacy_state(source, destination)

    assert record.outcome is MigrationOutcome.FAILED
    assert record.failure_code == "MIGRATION_RECORD_CORRUPT"
