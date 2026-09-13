from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from personal_ai_orchestrator.daemon import build_control_service
from personal_ai_orchestrator.delegation_campaign import (
    DelegationCalibrationCampaignStore,
    DelegationCampaignState,
)
from personal_ai_orchestrator.delegation_campaign_control import (
    DelegationCampaignControlPlaneServer,
)
from personal_ai_orchestrator.delegation_campaign_runtime import (
    CampaignAwareDelegationChildPort,
)
from personal_ai_orchestrator.delegation_evidence import (
    DelegationOutcomeJournal,
    DelegationOutcomePhase,
)
from personal_ai_orchestrator.delegation_quota_calibration import (
    DelegationQuotaCalibrationJournal,
)
from personal_ai_orchestrator.delegation_shadow import DelegationShadowJournal
from personal_ai_orchestrator.runtime_config import RuntimeConfig
from tests.test_delegation_quota_calibration import _FakeRefresh, _pair
from tests.test_pi5_child_execution import _setup
from tests.test_pi_dispatch_executor import _registry


def _request(socket_path: Path, method: str, path: str, payload=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost",
        "Connection: close",
        f"Content-Length: {len(body)}",
    ]
    if payload is not None:
        headers.append("Content-Type: application/json")
    wire = ("\r\n".join(headers) + "\r\n\r\n").encode() + body
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(str(socket_path))
    client.sendall(wire)
    chunks = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    client.close()
    response = b"".join(chunks)
    head, _, raw_body = response.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    decoded = json.loads(raw_body) if raw_body else None
    return status, decoded


def _campaign_port(base_port, *, campaign, root, refresh):
    return CampaignAwareDelegationChildPort(
        state_db=base_port.state_db,
        executor=base_port.executor,
        recommendation_factory=base_port.recommendation_factory,
        registry_provider=base_port.registry_provider,
        runtime_available_provider=base_port.runtime_available_provider,
        provider_registry_manager=base_port.provider_registry_manager,
        execution_evidence_journal=base_port.execution_evidence_journal,
        delegation_shadow_journal=DelegationShadowJournal(root),
        delegation_outcome_journal=DelegationOutcomeJournal(root),
        task_profile_provider=base_port.task_profile_provider,
        policy_resolution_provider=base_port.policy_resolution_provider,
        delegation_campaign=campaign,
        quota_refresh_service=refresh,
        quota_calibration_journal=DelegationQuotaCalibrationJournal(root),
        timeout_seconds=base_port.timeout_seconds,
        poll_seconds=base_port.poll_seconds,
    )


def test_campaign_missing_corrupt_start_stop_and_restart_are_fail_closed(tmp_path):
    path = tmp_path / "campaign.json"
    campaign = DelegationCalibrationCampaignStore(path)
    assert campaign.snapshot().state is DelegationCampaignState.OFF
    assert not path.exists()

    with pytest.raises(ValueError, match="max_observations_out_of_range"):
        campaign.start(max_observations=0)

    started = campaign.start(max_observations=2, project_ids=("project-b", "project-a"))
    assert started.state is DelegationCampaignState.ACTIVE
    assert started.project_ids == ("project-a", "project-b")
    assert started.remaining_observations == 2
    assert path.stat().st_mode & 0o777 == 0o600

    assert not campaign.claim(observation_id="outside", project_id="other-project")
    assert campaign.claim(observation_id="obs-1", project_id="project-a")
    assert campaign.snapshot().consumed_observations == 1

    restarted = DelegationCalibrationCampaignStore(path)
    assert restarted.claim(observation_id="obs-1", project_id="project-a")
    assert restarted.snapshot().consumed_observations == 1
    assert restarted.claim(observation_id="obs-2", project_id="project-b")
    exhausted = restarted.snapshot()
    assert exhausted.state is DelegationCampaignState.EXHAUSTED
    assert exhausted.remaining_observations == 0
    assert not restarted.claim(observation_id="obs-3", project_id="project-b")
    assert restarted.is_admitted(observation_id="obs-1", project_id="project-a")

    stopped = restarted.stop()
    assert stopped.state is DelegationCampaignState.STOPPED
    assert not restarted.claim(observation_id="new", project_id="project-a")
    assert restarted.is_admitted(observation_id="obs-2", project_id="project-b")

    path.write_text("{not-json", encoding="utf-8")
    invalid = DelegationCalibrationCampaignStore(path).snapshot()
    assert invalid.state is DelegationCampaignState.OFF
    assert invalid.reason_code == "CAMPAIGN_STATE_INVALID"


@pytest.mark.asyncio
async def test_campaign_off_or_out_of_scope_adds_zero_refresh_and_preserves_verified_child(
    tmp_path,
    monkeypatch,
):
    store, _, base_port, _, plan, _ = _setup(tmp_path, monkeypatch)
    before, after = _pair()
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "evidence"
    campaign = DelegationCalibrationCampaignStore(tmp_path / "campaign.json")
    port = _campaign_port(base_port, campaign=campaign, root=root, refresh=refresh)
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert refresh.refresh_count == 0
    finally:
        store.close()

    # Fresh fixture: active campaign, but project is outside the allowlist.
    store, _, base_port, _, plan, _ = _setup(tmp_path / "other", monkeypatch)
    before, after = _pair()
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "other-evidence"
    campaign = DelegationCalibrationCampaignStore(tmp_path / "other-campaign.json")
    campaign.start(max_observations=1, project_ids=("different-project",))
    port = _campaign_port(base_port, campaign=campaign, root=root, refresh=refresh)
    try:
        result = await port.execute_child(plan)
        assert result.verified
        assert refresh.refresh_count == 0
        assert campaign.snapshot().consumed_observations == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_admitted_campaign_consumes_one_slot_and_refreshes_once(tmp_path, monkeypatch):
    store, _, base_port, _, plan, _ = _setup(tmp_path, monkeypatch)
    before, after = _pair()
    refresh = _FakeRefresh(before, after)
    root = tmp_path / "evidence"
    campaign = DelegationCalibrationCampaignStore(tmp_path / "campaign.json")
    campaign.start(max_observations=1, project_ids=(plan.project_id,))
    port = _campaign_port(base_port, campaign=campaign, root=root, refresh=refresh)
    try:
        result = await port.execute_child(plan)
        assert result.verified
        snap = campaign.snapshot()
        assert snap.state is DelegationCampaignState.EXHAUSTED
        assert snap.consumed_observations == 1
        assert refresh.refresh_count == 1

        observation_id = port.delegation_shadow_journal.observation_id(
            parent_run_id=plan.parent_run_id,
            child_task_id=plan.child_task_id,
        )
        child = port.delegation_outcome_journal.load(
            observation_id=observation_id,
            phase=DelegationOutcomePhase.CHILD_FINAL,
        )
        assert child is not None
        assert child.quota_before_snapshot_id == "quota-before"
        assert child.quota_after_snapshot_id == "quota-after"

        # Existing observation remains admitted under EXHAUSTED but neither the
        # budget nor read-only refresh count changes on replay/re-entry.
        assert (await port.execute_child(plan)).verified
        assert campaign.snapshot().consumed_observations == 1
        assert refresh.refresh_count == 1
    finally:
        store.close()


def test_typed_campaign_control_surface_launches_nothing(tmp_path):
    runtime_root = tmp_path / "runtime"
    service = build_control_service(
        config=RuntimeConfig(catalog_snapshot_id="campaign-test", registry=_registry()),
        state_db=tmp_path / "state.db",
        runtime_state_root=runtime_root,
    )
    campaign = getattr(service, "_delegation_campaign_store")
    server = DelegationCampaignControlPlaneServer(
        service,
        tmp_path / "control.sock",
        campaign=campaign,
    )
    server.start_background()
    try:
        assert service.dispatch_executor is None
        status, payload = _request(
            server.socket_path,
            "GET",
            "/v1/settings/delegation-calibration-campaign",
        )
        assert status == 200 and payload["state"] == "OFF"

        status, payload = _request(
            server.socket_path,
            "POST",
            "/v1/settings/delegation-calibration-campaign/start",
            {"max_observations": 2},
        )
        assert status == 200
        assert payload["state"] == "ACTIVE"
        assert payload["remaining_observations"] == 2
        assert service.store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert service.store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

        status, payload = _request(
            server.socket_path,
            "POST",
            "/v1/settings/delegation-calibration-campaign/stop",
            {},
        )
        assert status == 200 and payload["state"] == "STOPPED"
        assert service.store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert service.store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        server.stop()
        service.store.close()
