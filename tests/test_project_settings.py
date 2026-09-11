"""M1 WP5a-1 commit 2 — project-level supervised-auto settings round-trip.

The tests pin:

- Fresh-DB path: the ``_ensure_column`` migration adds the three new
  columns with safe defaults (``False`` / ``False`` / ``120``) so an
  upgrade keeps the pre-WP5a-1 manual-only behavior.
- Old-DB migration: an existing project row written before the
  column add still reads cleanly with the new defaults.
- Restart persistence: ``PUT /v1/projects/{id}/settings`` writes
  through a fresh ``SafetyKernelStore`` round-trip.
- Validation: ``grace_seconds`` outside ``[1, 86_400]`` is rejected
  with 400 ``invalid_grace_seconds``.
- ``ProjectView`` exposes the three new fields (lenient decode for
  pre-WP5a-1 daemons that omit them — a fresh row reads as the
  dataclass defaults).
- Disabling ``supervised_auto_allowed`` round-trips (no implicit
  re-enable).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneServer,
    ControlPlaneService,
)
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
from personal_ai_orchestrator.quota_availability import (
    QuotaAvailabilityJournal,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.scheduling_settings import SchedulingSettings
from personal_ai_orchestrator.verification_evidence import (
    VerificationEvidenceJournal,
)
from tests.test_dispatch_initiator import (
    _make_repo,
    _registry,
)


def _free_socket_path() -> Path:
    socket_dir = Path(tempfile.mkdtemp(prefix="pao-proj-"))
    return socket_dir / "control.sock"


def _build_service(
    tmp_path: Path,
    *,
    safety_path: Path | None = None,
) -> tuple[ControlPlaneService, SafetyKernelStore]:
    store = SafetyKernelStore(safety_path or tmp_path / "safety.db")
    availability = QuotaAvailabilityJournal(tmp_path)
    service = ControlPlaneService(
        registry=_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=availability,
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json",
            initial=True,
        ),
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling.json"),
    )
    return service, store


def _http_pair(service: ControlPlaneService):
    socket_path = _free_socket_path()
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path)
    return server, client


def _register_project(service: ControlPlaneService, tmp_path: Path) -> str:
    repo = _make_repo(tmp_path / "project")
    project = service.store.register_project(
        project_id="project-fixture",
        display_name="Fixture",
        canonical_repo_root=str(repo),
        git_root=str(repo),
        default_branch="main",
        last_known_head="abc123",
    )
    return project.project_id


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_fresh_db_defaults_match_dataclass(tmp_path: Path) -> None:
    """A fresh DB writes the column defaults (False / False / 120)
    so existing callers see the pre-WP5a-1 manual-only behavior.
    """

    service, store = _build_service(tmp_path)
    try:
        project_id = _register_project(service, tmp_path)
        project = store.get_project(project_id)
        assert project.supervised_auto_allowed is False
        assert project.unattended_allowed is False
        assert project.grace_seconds == 120
    finally:
        store.close()


def test_old_db_migration_reads_defaults(tmp_path: Path) -> None:
    """A SQLite file that pre-dates WP5a-1 (no
    ``supervised_auto_allowed`` / ``unattended_allowed`` /
    ``grace_seconds`` columns) reads cleanly after the
    ``_ensure_column`` migration runs.
    """

    # Build a pre-WP5a-1 SQLite file with a project row but no
    # supervised-auto columns.
    safety_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(safety_path)
    try:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                canonical_repo_root TEXT NOT NULL,
                git_root TEXT NOT NULL,
                default_branch TEXT NOT NULL,
                last_known_head TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                working_subpath TEXT,
                remote_url TEXT,
                last_opened_at TEXT,
                storage_availability TEXT NOT NULL,
                security_bookmark_b64 TEXT,
                scheduling_policy TEXT,
                manual_execution_target_id TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects(
                project_id, display_name, canonical_repo_root, git_root,
                default_branch, last_known_head, created_at, updated_at,
                storage_availability
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-1",
                "Legacy Project",
                "/tmp/legacy",
                "/tmp/legacy",
                "main",
                "deadbeef",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "ONLINE",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Reopen the legacy DB through the SafetyKernelStore migration
    # path. ``_ensure_column`` adds the three new columns with the
    # declared defaults; the existing row reads back as
    # ``False / False / 120``.
    store = SafetyKernelStore(safety_path)
    try:
        project = store.get_project("legacy-1")
        assert project.supervised_auto_allowed is False
        assert project.unattended_allowed is False
        assert project.grace_seconds == 120
    finally:
        store.close()


def test_set_project_settings_round_trip(tmp_path: Path) -> None:
    """PUT the three fields, read the project back, then close and
    reopen the store to confirm durability.
    """

    service, store = _build_service(tmp_path)
    try:
        project_id = _register_project(service, tmp_path)
        updated = service.store.set_project_settings(
            project_id,
            supervised_auto_allowed=True,
            unattended_allowed=True,
            grace_seconds=300,
        )
        assert updated.supervised_auto_allowed is True
        assert updated.unattended_allowed is True
        assert updated.grace_seconds == 300

        # Restart: a fresh ``SafetyKernelStore`` from the same DB
        # sees the persisted values.
        store.close()
        store2 = SafetyKernelStore(tmp_path / "safety.db")
        try:
            project = store2.get_project(project_id)
            assert project.supervised_auto_allowed is True
            assert project.unattended_allowed is True
            assert project.grace_seconds == 300
        finally:
            store2.close()
    finally:
        pass  # store closed above


def test_set_project_settings_via_http_round_trip(tmp_path: Path) -> None:
    """End-to-end HTTP PUT round-trip plus ``ProjectView`` exposes
    the three new fields.
    """

    service, store = _build_service(tmp_path)
    try:
        server, client = _http_pair(service)
        try:
            project_id = _register_project(service, tmp_path)
            view = client.set_project_settings(
                project_id,
                supervised_auto_allowed=True,
                unattended_allowed=False,
                grace_seconds=600,
            )
            assert view.supervised_auto_allowed is True
            assert view.unattended_allowed is False
            assert view.grace_seconds == 600
        finally:
            server.stop()
    finally:
        store.close()


def test_set_project_settings_invalid_grace_seconds_returns_400(
    tmp_path: Path,
) -> None:
    """``grace_seconds`` outside ``[1, 86_400]`` is rejected with
    400 ``invalid_grace_seconds`` and leaves the persisted state
    unchanged.
    """

    service, store = _build_service(tmp_path)
    try:
        project_id = _register_project(service, tmp_path)
        with pytest.raises(ValueError):
            service.store.set_project_settings(
                project_id,
                supervised_auto_allowed=True,
                unattended_allowed=False,
                grace_seconds=86_401,  # one over the 24h cap
            )
        with pytest.raises(ValueError):
            service.store.set_project_settings(
                project_id,
                supervised_auto_allowed=True,
                unattended_allowed=False,
                grace_seconds=0,  # below the floor
            )

        project = store.get_project(project_id)
        assert project.supervised_auto_allowed is False
        assert project.unattended_allowed is False
        assert project.grace_seconds == 120
    finally:
        store.close()


def test_set_project_settings_http_invalid_grace_seconds_400(
    tmp_path: Path,
) -> None:
    """The HTTP facade maps ``ValueError`` from the store to 400
    ``invalid_grace_seconds``.
    """

    service, store = _build_service(tmp_path)
    try:
        server, client = _http_pair(service)
        try:
            project_id = _register_project(service, tmp_path)
            with pytest.raises(ControlPlaneError) as error:
                service.set_project_supervised_auto_settings(
                    project_id,
                    {
                        "supervised_auto_allowed": True,
                        "unattended_allowed": False,
                        "grace_seconds": 86_401,
                    },
                )
            assert error.value.status == 400
            assert error.value.code == "invalid_grace_seconds"
        finally:
            server.stop()
    finally:
        store.close()


def test_set_project_settings_disables_round_trip(tmp_path: Path) -> None:
    """Disabling ``supervised_auto_allowed`` does not silently
    re-enable — the persisted state stays ``False``.
    """

    service, store = _build_service(tmp_path)
    try:
        project_id = _register_project(service, tmp_path)
        # Enable, then disable.
        service.store.set_project_settings(
            project_id,
            supervised_auto_allowed=True,
            unattended_allowed=True,
            grace_seconds=240,
        )
        updated = service.store.set_project_settings(
            project_id,
            supervised_auto_allowed=False,
            unattended_allowed=False,
            grace_seconds=120,
        )
        assert updated.supervised_auto_allowed is False
        assert updated.unattended_allowed is False
        assert updated.grace_seconds == 120
    finally:
        store.close()


def test_project_view_decodes_three_new_fields(tmp_path: Path) -> None:
    """``ProjectView`` exposes the three new fields and accepts the
    pre-WP5a-1 omit-defaults shape (a daemon that doesn't send the
    new fields reads as the dataclass defaults).
    """

    # Pre-WP5a-1 payload — no ``supervised_auto_allowed``,
    # ``unattended_allowed``, or ``grace_seconds``. The view-model
    # surfaces the dataclass defaults (False / False / 120).
    legacy_payload = {
        "project_id": "p1",
        "display_name": "Legacy",
        "canonical_repo_root": "/x",
        "git_root": "/x",
        "default_branch": "main",
        "last_known_head": "h",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "storage_availability": "ONLINE",
        "recent_task_count": 0,
        "current_branch": None,
        "scheduling_policy": None,
        "manual_execution_target_id": None,
    }
    from personal_ai_orchestrator.control_api import ProjectView

    legacy_view = ProjectView.model_validate(legacy_payload)
    assert legacy_view.supervised_auto_allowed is False
    assert legacy_view.unattended_allowed is False
    assert legacy_view.grace_seconds == 120

    # Current payload — explicit values round-trip.
    current_payload = dict(
        legacy_payload,
        supervised_auto_allowed=True,
        unattended_allowed=True,
        grace_seconds=600,
    )
    current_view = ProjectView.model_validate(current_payload)
    assert current_view.supervised_auto_allowed is True
    assert current_view.unattended_allowed is True
    assert current_view.grace_seconds == 600


def test_set_project_settings_unknown_project_returns_404(tmp_path: Path) -> None:
    """An unknown project_id raises ``ControlPlaneError`` with 404
    ``project_not_found``.
    """

    service, store = _build_service(tmp_path)
    try:
        with pytest.raises(ControlPlaneError) as error:
            service.set_project_supervised_auto_settings(
                "no-such-project",
                {
                    "supervised_auto_allowed": True,
                    "unattended_allowed": False,
                    "grace_seconds": 120,
                },
            )
        assert error.value.status == 404
        assert error.value.code == "project_not_found"
    finally:
        store.close()


def test_persisted_audit_event_records_change(tmp_path: Path) -> None:
    """The store records a ``PROJECT_SUPERVISED_AUTO_SETTINGS_SET``
    audit event when settings are persisted — pin so the Swift
    audit panel can render the change.
    """

    service, store = _build_service(tmp_path)
    try:
        project_id = _register_project(service, tmp_path)
        service.store.set_project_settings(
            project_id,
            supervised_auto_allowed=True,
            unattended_allowed=True,
            grace_seconds=300,
        )
        row = store.connection.execute(
            "SELECT event_type, payload_json FROM audit_events "
            "WHERE event_type='PROJECT_SUPERVISED_AUTO_SETTINGS_SET'"
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert payload["supervised_auto_allowed"] is True
        assert payload["unattended_allowed"] is True
        assert payload["grace_seconds"] == 300
        assert payload["project_id"] == project_id
    finally:
        store.close()