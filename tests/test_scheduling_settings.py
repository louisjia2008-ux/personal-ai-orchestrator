"""M1 WP5a-1 commit 2 — scheduling settings (mode) round-trip + ACTIVE gate.

The tests pin:

- The default ``mode`` is ``"MANUAL"`` (pre-WP5a-1 behavior).
- ``PUT /v1/settings/scheduling`` accepts ``mode="SUPERVISED_AUTO"``
  and persists it across a fresh daemon/service instantiation.
- ``PUT /v1/settings/scheduling`` with ``mode="ACTIVE"`` and no
  activation authority returns 409 ``production_active_not_authorized``.
- A pre-WP5a-1 settings file (no ``mode`` key) loads cleanly and
  defaults to ``mode="MANUAL"``.
- Selecting an unknown mode returns 400 ``unsupported_scheduling_mode``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneServer,
    ControlPlaneService,
)
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.scheduling_settings import (
    DEFAULT_MODE,
    SELECTABLE_MODES,
    SchedulingSettings,
)


def _free_socket_path() -> Path:
    """Build a Unix-domain socket path under 104 bytes for macOS."""

    socket_dir = Path(tempfile.mkdtemp(prefix="pao-sched-"))
    return socket_dir / "control.sock"


def _make_service(tmp_path: Path, *, activation_authorised: bool = False):
    """Build a ``ControlPlaneService`` with the project's
    scheduling-settings file in ``tmp_path``.

    When ``activation_authorised=True`` the activation gate is set to
    authorised (the ``ACTIVE`` mode then becomes reachable on the wire);
    otherwise the gate denies ``ACTIVE`` with 409 (the default).
    """

    from personal_ai_orchestrator.owner_settings import OwnerExecutionSettings
    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityJournal,
    )
    from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
    from personal_ai_orchestrator.verification_evidence import (
        VerificationEvidenceJournal,
    )
    from tests.test_dispatch_initiator import _registry as _shared_registry

    store = SafetyKernelStore(tmp_path / "safety.db")
    availability = QuotaAvailabilityJournal(tmp_path)
    gate = ActiveRoutingGate(
        p0_safety_kernel_authoritative=activation_authorised,
        p1_verifier_authoritative=activation_authorised,
        adapter_fail_closed_validated=activation_authorised,
        shadow_evidence_accepted=activation_authorised,
        safe_bypass_validated=activation_authorised,
        owner_approved=activation_authorised,
    )
    service = ControlPlaneService(
        registry=_shared_registry(),
        store=store,
        runtime_availability={"m3-sub": True},
        verification_journal=VerificationEvidenceJournal(tmp_path),
        quota_availability_journal=availability,
        owner_execution=OwnerExecutionSettings(
            tmp_path / "owner-execution.json",
            initial=False,
        ),
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling.json"),
        activation_gate=gate,
    )
    return service, store


def _http_pair(service: ControlPlaneService):
    socket_path = _free_socket_path()
    server = ControlPlaneServer(service, socket_path)
    server.start_background()
    client = ControlPlaneClient(socket_path)
    return server, client


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_default_mode_is_manual(tmp_path: Path) -> None:
    """The default ``mode`` matches ``DEFAULT_MODE = "MANUAL"`` so an
    upgrade from a pre-WP5a-1 settings file preserves the prior
    behavior.
    """

    settings = SchedulingSettings(tmp_path / "scheduling.json")
    assert settings.mode == DEFAULT_MODE == "MANUAL"


def test_selectable_modes_includes_supervised_auto_and_active() -> None:
    """The wire surface advertises all three selectable modes —
    ``MANUAL``, ``SUPERVISED_AUTO``, ``ACTIVE`` — so the client
    renders the full picker.
    """

    assert "MANUAL" in SELECTABLE_MODES
    assert "SUPERVISED_AUTO" in SELECTABLE_MODES
    assert "ACTIVE" in SELECTABLE_MODES


def test_settings_view_default_round_trip(tmp_path: Path) -> None:
    """``GET /v1/settings/scheduling`` returns the default mode
    (``MANUAL``) plus the policy and selectable lists.
    """

    service, store = _make_service(tmp_path)
    try:
        view = service.scheduling_settings_view()
        assert view.mode == "MANUAL"
        assert view.default_scheduling_policy == "BALANCED"
        assert "MANUAL" in view.selectable_modes
        assert "SUPERVISED_AUTO" in view.selectable_modes
        assert "ACTIVE" in view.selectable_modes
    finally:
        store.close()


def test_update_scheduling_settings_supervised_auto_round_trip(
    tmp_path: Path,
) -> None:
    """Setting ``mode="SUPERVISED_AUTO"`` succeeds (no ACTIVE gate
    check) and persists across a fresh daemon/service instantiation.
    """

    service, store = _make_service(tmp_path)
    try:
        result = service.update_scheduling_settings(
            {
                "default_scheduling_policy": "BALANCED",
                "mode": "SUPERVISED_AUTO",
            }
        )
        assert result.mode == "SUPERVISED_AUTO"

        # Persisted: a fresh ``SchedulingSettings`` from the same
        # file path sees the new mode.
        reloaded = SchedulingSettings(tmp_path / "scheduling.json")
        assert reloaded.mode == "SUPERVISED_AUTO"
    finally:
        store.close()


def test_update_scheduling_settings_active_without_authority_409(
    tmp_path: Path,
) -> None:
    """``mode="ACTIVE"`` with no activation authority is fail-closed:
    the facade returns 409 ``production_active_not_authorized`` and
    the persisted mode is unchanged.
    """

    service, store = _make_service(tmp_path, activation_authorised=False)
    try:
        with pytest.raises(ControlPlaneError) as error:
            service.update_scheduling_settings(
                {
                    "default_scheduling_policy": "BALANCED",
                    "mode": "ACTIVE",
                }
            )
        assert error.value.status == 409
        assert error.value.code == "production_active_not_authorized"

        # Persisted mode is unchanged — the failed activation did
        # not flip the host-side state.
        reloaded = SchedulingSettings(tmp_path / "scheduling.json")
        assert reloaded.mode == "MANUAL"
    finally:
        store.close()


def test_update_scheduling_settings_active_with_authority_persists(
    tmp_path: Path,
) -> None:
    """When the activation gate is authorised, ``mode="ACTIVE"``
    succeeds and persists.
    """

    service, store = _make_service(tmp_path, activation_authorised=True)
    try:
        result = service.update_scheduling_settings(
            {
                "default_scheduling_policy": "BALANCED",
                "mode": "ACTIVE",
            }
        )
        assert result.mode == "ACTIVE"

        reloaded = SchedulingSettings(tmp_path / "scheduling.json")
        assert reloaded.mode == "ACTIVE"
    finally:
        store.close()


def test_update_scheduling_settings_unknown_mode_400(tmp_path: Path) -> None:
    """An unsupported mode string is rejected with 400
    ``unsupported_scheduling_mode`` and leaves the persisted mode
    unchanged.
    """

    service, store = _make_service(tmp_path)
    try:
        with pytest.raises(ControlPlaneError) as error:
            service.update_scheduling_settings(
                {
                    "default_scheduling_policy": "BALANCED",
                    "mode": "AUTOPILOT",
                }
            )
        assert error.value.status == 400
        assert error.value.code == "unsupported_scheduling_mode"

        reloaded = SchedulingSettings(tmp_path / "scheduling.json")
        assert reloaded.mode == "MANUAL"
    finally:
        store.close()


def test_update_scheduling_settings_legacy_payload_without_mode(
    tmp_path: Path,
) -> None:
    """A payload that omits ``mode`` leaves the persisted mode
    unchanged — the existing ``default_scheduling_policy`` update
    path keeps working for callers that haven't migrated yet.
    """

    service, store = _make_service(tmp_path)
    try:
        # First flip to SUPERVISED_AUTO so we can prove the legacy
        # payload does not reset it back to MANUAL.
        service.update_scheduling_settings(
            {
                "default_scheduling_policy": "BALANCED",
                "mode": "SUPERVISED_AUTO",
            }
        )

        # Legacy caller: only sends the policy field.
        result = service.update_scheduling_settings({"default_scheduling_policy": "QUALITY_FIRST"})
        assert result.mode == "SUPERVISED_AUTO"  # unchanged
        assert result.default_scheduling_policy == "QUALITY_FIRST"
    finally:
        store.close()


def test_legacy_settings_file_without_mode_key_loads_as_manual(
    tmp_path: Path,
) -> None:
    """A pre-WP5a-1 ``scheduling.json`` (no ``mode`` key) loads
    cleanly with ``mode="MANUAL"`` so an upgrade does not require
    a one-time migration.
    """

    settings_path = tmp_path / "scheduling.json"
    settings_path.write_text(
        json.dumps(
            {
                "schema": "scheduling-settings-v1",
                "default_scheduling_policy": "QUALITY_FIRST",
                "updated_at": "2026-09-07T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    settings = SchedulingSettings(settings_path)
    assert settings.default_policy == "QUALITY_FIRST"
    assert settings.mode == "MANUAL"


def test_persisted_payload_round_trip_via_http(tmp_path: Path) -> None:
    """End-to-end round-trip: write via HTTP, read back, restart
    the daemon, read again.
    """

    service, store = _make_service(tmp_path)
    try:
        server, client = _http_pair(service)
        try:
            client.update_scheduling_settings(
                default_scheduling_policy="BALANCED",
                mode="SUPERVISED_AUTO",
            )
            view = client.scheduling_settings()
            assert view.mode == "SUPERVISED_AUTO"
        finally:
            server.stop()

        # Reopen the same scheduling file in a fresh SchedulingSettings
        # to prove durability.
        reloaded = SchedulingSettings(tmp_path / "scheduling.json")
        assert reloaded.mode == "SUPERVISED_AUTO"
    finally:
        store.close()
