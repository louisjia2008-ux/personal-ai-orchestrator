"""Shared policy CAS, narrow bridge authority, and safe resource projections.

All mutations use real temporary SQLite/settings files. The HTTP harness runs the
real request handler without opening a socket or starting any provider process.
"""

from __future__ import annotations

import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator import client_cli, scheduling_settings
from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.local_client_v2 import CATALOG_LIMIT, handle_local_client_v2
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduling_settings import SCHEMA, SchedulingSettings
from tests.policy_helpers import preconditions
from tests.test_local_client_v2 import _http, counts, submit
from tests.test_local_client_v2 import service as service
from tests.test_owner_dispatch_recovery import _recoverable

PROJECT = "project-fixture"
PRIVATE = "/private/secret-provider-token-must-not-escape"


@pytest.fixture
def durable_service(service, tmp_path):
    service.scheduling_settings = SchedulingSettings(tmp_path / "scheduling.json")
    return service


def _read(service, operation="POLICY_GET"):
    return handle_local_client_v2(
        service, {"protocol_version": 2, "operation": operation, "request_id": "read-policy"}
    )


def _write(service, scope, policy="QUALITY_FIRST", **changes):
    view = (
        service.scheduling_settings_view()
        if scope == "global"
        else service.project_scheduling_policy_view(PROJECT)
    )
    payload = {
        "protocol_version": 2,
        "operation": "GLOBAL_POLICY_SET" if scope == "global" else "PROJECT_POLICY_SET",
        "request_id": f"write-{scope}",
        **preconditions(view),
    }
    if scope == "global":
        payload["default_scheduling_policy"] = policy
    else:
        payload.update(
            project_id=PROJECT, scheduling_policy=policy, manual_execution_target_id=None
        )
    return payload | changes


def _v1_request(request):
    return {
        key: value
        for key, value in request.items()
        if key not in {"protocol_version", "operation", "request_id", "project_id"}
    }


def _v1_path(scope):
    return "/v1/settings/scheduling" if scope == "global" else f"/v1/projects/{PROJECT}/scheduling"


def _state(service):
    return (
        service.scheduling_settings.versioned_snapshot(),
        service.store.get_project(PROJECT),
        counts(service),
        service.owner_initiated_execution_enabled,
    )


@pytest.mark.parametrize("scope", ["global", "project"])
def test_stale_editor_http_and_bridge_conflict_without_any_effect(durable_service, scope):
    service = durable_service
    stale = _write(service, scope, "QUOTA_SAVER")
    first = _write(service, scope, "QUALITY_FIRST")
    status, reply = _http(service, first)
    assert status == 200
    row = reply["payload"]["global" if scope == "global" else "projects"]
    revision = row["revision"] if scope == "global" else row[0]["revision"]
    assert revision != stale["expected_revision"]
    before = _state(service)
    assert _http(service, stale) == (409, {"error": "policy_revision_conflict"})
    assert _http(service, _v1_request(stale), path=_v1_path(scope), method="PUT") == (
        409,
        {"error": "policy_revision_conflict"},
    )
    assert _state(service) == before
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize("scope", ["global", "project"])
def test_concurrent_http_writers_have_exactly_one_winner(durable_service, scope):
    service = durable_service
    initial_audits = counts(service)[-1]
    seen_revisions = set()
    # Different objects/locks share the settings file; the OS file lock, rather
    # than a single Python object's mutex, must serialize global writes.
    for generation in range(4):
        candidates = [
            _write(service, scope, policy, request_id=f"writer-{generation}-{number}")
            for number, policy in enumerate(("SPEED_FIRST", "BURN_DOWN"))
        ]
        assert candidates[0]["expected_revision"] == candidates[1]["expected_revision"]
        barrier = threading.Barrier(2)

        def update(candidate, barrier=barrier):
            independent = replace(
                service, scheduling_settings=SchedulingSettings(service.scheduling_settings.path)
            )
            barrier.wait(timeout=10)
            return _http(independent, candidate)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(update, candidates))
        assert sorted(code for code, _ in outcomes) == [200, 409]
        loser = next(reply for code, reply in outcomes if code == 409)
        assert loser == {"error": "policy_revision_conflict"}
        winner_index = next(index for index, (code, _) in enumerate(outcomes) if code == 200)
        snapshot = _read(service)["payload"]
        row = snapshot["global"] if scope == "global" else snapshot["projects"][0]
        key = "default_scheduling_policy" if scope == "global" else "scheduling_policy"
        assert row[key] == candidates[winner_index][key]
        assert row["revision"] not in seen_revisions
        assert row["revision"] != candidates[0]["expected_revision"]
        seen_revisions.add(row["revision"])
    assert counts(service)[-1] - initial_audits == (4 if scope == "project" else 0)
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize("scope", ["global", "project"])
def test_policy_aba_edits_never_reuse_revision_and_survive_reopen(durable_service, scope):
    service = durable_service
    stale = _write(service, scope, "SPEED_FIRST")
    revisions = {stale["expected_revision"]}
    for policy in ("BURN_DOWN", "BALANCED", "BURN_DOWN", "BALANCED"):
        handle_local_client_v2(service, _write(service, scope, policy))
        current = _write(service, scope)
        assert current["expected_revision"] not in revisions
        revisions.add(current["expected_revision"])
    reopened = service.open_request()
    reopened.scheduling_settings = SchedulingSettings(service.scheduling_settings.path)
    try:
        assert _read(reopened) == _read(service)
        assert _http(reopened, stale) == (409, {"error": "policy_revision_conflict"})
    finally:
        reopened.store.close()


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("identity", ["expected_store_id", "expected_process_epoch"])
def test_policy_identity_conflicts_precede_mutation(durable_service, scope, identity, tmp_path):
    service = durable_service
    captured = _write(service, scope)
    if identity == "expected_process_epoch":
        changed = replace(service, process_epoch=uuid4().hex)
    else:
        other = SafetyKernelStore(tmp_path / "different-store.db")
        changed = replace(service, store=other)
    before = _state(service)
    try:
        code = (
            "store_identity_mismatch"
            if identity == "expected_store_id"
            else "process_epoch_mismatch"
        )
        assert _http(changed, captured) == (409, {"error": code})
        assert _http(changed, _v1_request(captured), path=_v1_path(scope), method="PUT") == (
            409,
            {"error": code},
        )
        assert _state(service) == before
        if identity == "expected_store_id":
            assert (
                changed.store.connection.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
            )
    finally:
        if identity == "expected_store_id":
            changed.store.close()


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize(
    "missing", ["all", "expected_revision", "expected_store_id", "expected_process_epoch"]
)
def test_legacy_http_writes_require_all_preconditions(durable_service, scope, missing):
    service = durable_service
    payload = _v1_request(_write(service, scope))
    for field in tuple(payload):
        if field == missing or (missing == "all" and field.startswith("expected_")):
            del payload[field]
    before = _state(service)
    assert _http(service, payload, path=_v1_path(scope), method="PUT") == (
        428,
        {"error": "policy_revision_required"},
    )
    assert _state(service) == before


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_global_revision_is_stable_until_first_durable_write(tmp_path, legacy):
    path = tmp_path / "settings.json"
    if legacy:
        path.write_text(json.dumps({"schema": SCHEMA, "default_scheduling_policy": "QUOTA_SAVER"}))
    before = path.read_bytes() if path.exists() else None
    first, second = SchedulingSettings(path), SchedulingSettings(path)
    snapshot = first.versioned_snapshot()
    assert second.versioned_snapshot() == snapshot
    assert (path.read_bytes() if path.exists() else None) == before
    first.update(default_policy="SPEED_FIRST", expected_revision=snapshot[2])
    committed = first.versioned_snapshot()
    assert committed[2] != snapshot[2]
    assert json.loads(path.read_text())["revision"] == committed[2]
    assert SchedulingSettings(path).versioned_snapshot() == committed
    with pytest.raises(scheduling_settings.PolicyRevisionConflict):
        second.update(default_policy="BURN_DOWN", expected_revision=snapshot[2])
    assert second.versioned_snapshot() == committed


def test_global_atomic_replace_failure_rolls_back_memory_and_disk(durable_service, monkeypatch):
    service = durable_service
    handle_local_client_v2(service, _write(service, "global", "QUOTA_SAVER"))
    path = service.scheduling_settings.path
    before = _state(service), path.read_bytes()
    captured = _write(service, "global", "BURN_DOWN")

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(scheduling_settings.os, "replace", fail_replace)
        with pytest.raises(OSError, match="atomic replace failure"):
            handle_local_client_v2(service, captured)
    assert (_state(service), path.read_bytes()) == before
    assert SchedulingSettings(path).versioned_snapshot() == before[0][0]
    assert list(path.parent.glob(f".{path.name}.*")) == []
    # A failed write did not consume the caller's generation.
    handle_local_client_v2(service, captured)
    assert service.scheduling_settings.default_policy == "BURN_DOWN"


def test_project_audit_failure_rolls_back_value_revision_and_audit(durable_service, monkeypatch):
    service = durable_service
    captured = _write(service, "project")
    before = _state(service)

    def fail_audit(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated audit persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "_audit", fail_audit)
        with pytest.raises(sqlite3.OperationalError, match="audit persistence failure"):
            handle_local_client_v2(service, captured)
    assert _state(service) == before
    assert not service.store.connection.in_transaction
    handle_local_client_v2(service, captured)
    assert service.store.get_project(PROJECT).scheduling_policy == "QUALITY_FIRST"


@pytest.mark.parametrize("owner_enabled", [False, True])
@pytest.mark.parametrize("mode", ["MANUAL", "SUPERVISED_AUTO"])
def test_policy_changes_do_not_launch_rewrite_task_or_grant_authority(
    durable_service, owner_enabled, mode
):
    service = durable_service
    service.owner_execution.set_enabled(owner_enabled)
    service.scheduling_settings.update(mode=mode)
    submit(service)
    task_before = service.store.get_task("task-1")
    authority_before = service.store.get_project(PROJECT)
    counts_before = counts(service)
    handle_local_client_v2(service, _write(service, "global", "BURN_DOWN"))
    handle_local_client_v2(service, _write(service, "project", "SPEED_FIRST"))
    handle_local_client_v2(service, _write(service, "project", None))
    assert service.store.get_task("task-1") == task_before
    assert task_before.scheduling_policy == "MANUAL"
    assert task_before.manual_execution_target_id == "m3-sub"
    assert counts(service)[:4] == counts_before[:4]
    assert service.dispatch_executor.calls == []
    assert service.owner_initiated_execution_enabled is owner_enabled
    assert service.scheduling_settings.mode == mode
    after = service.store.get_project(PROJECT)
    assert (after.supervised_auto_allowed, after.unattended_allowed, after.grace_seconds) == (
        authority_before.supervised_auto_allowed,
        authority_before.unattended_allowed,
        authority_before.grace_seconds,
    )


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize(
    "injection",
    [
        {"mode": "ACTIVE"},
        {"mode": "SUPERVISED_AUTO"},
        {"authority": "SUPERVISED_AUTO"},
        {"approved": True},
        {"owner_initiated_execution_enabled": True},
        {"supervised_auto_allowed": True},
        {"unattended_allowed": True},
        {"argv": ["sh", "-c", "echo bad"]},
        {"retry": True},
        {"task_state_version": 0},
    ],
)
def test_policy_bridge_rejects_unrelated_authority_fields(durable_service, scope, injection):
    service = durable_service
    before = _state(service)
    assert _http(service, _write(service, scope) | injection) == (
        400,
        {"error": "invalid_json_schema"},
    )
    assert _state(service) == before


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize(
    "field", ["expected_revision", "expected_store_id", "expected_process_epoch"]
)
def test_policy_bridge_rejects_missing_or_null_preconditions(durable_service, scope, field):
    request = _write(durable_service, scope)
    missing = dict(request)
    del missing[field]
    for invalid in (missing, request | {field: None}, request | {field: 7}):
        with pytest.raises(ValidationError):
            handle_local_client_v2(durable_service, invalid)


@pytest.mark.parametrize(
    "scope,changes,error",
    [
        ("global", {"default_scheduling_policy": "MANUAL"}, "unsupported_global_scheduling_policy"),
        (
            "global",
            {"default_scheduling_policy": "AUTONOMOUS"},
            "unsupported_global_scheduling_policy",
        ),
        ("project", {"scheduling_policy": "AUTONOMOUS"}, "unsupported_scheduling_policy"),
        ("project", {"scheduling_policy": "MANUAL"}, "manual_policy_requires_execution_target"),
        (
            "project",
            {"scheduling_policy": None, "manual_execution_target_id": "m3-sub"},
            "manual_target_requires_manual_policy",
        ),
    ],
)
def test_invalid_policy_values_do_not_consume_revision(durable_service, scope, changes, error):
    before = _state(durable_service)
    assert _http(durable_service, _write(durable_service, scope) | changes) == (
        400,
        {"error": error},
    )
    assert _state(durable_service) == before


def test_explicit_null_inheritance_survives_typed_client_and_cli(
    durable_service, monkeypatch, capsys
):
    service = durable_service
    handle_local_client_v2(service, _write(service, "global", "QUOTA_SAVER"))
    handle_local_client_v2(
        service, _write(service, "project", "MANUAL", manual_execution_target_id="m3-sub")
    )
    inherited = _write(service, "project", None)
    calls = []

    def downstream(_client, method, path, *, payload=None):
        calls.append((method, path, payload))
        status, response = _http(service, payload, method=method, path=path)
        if status >= 400:
            raise ControlPlaneError(status, response["error"])
        return response

    monkeypatch.setattr(ControlPlaneClient, "_request", downstream)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(inherited)))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert calls == [("POST", "/v2/local-client", inherited)]
    row = response["payload"]["projects"][0]
    assert row["scheduling_policy"] is None
    assert row["manual_execution_target_id"] is None
    assert row["effective_scheduling_policy"] == "QUOTA_SAVER"
    assert row["effective_manual_execution_target_id"] is None
    assert row["policy_source"] == "GLOBAL"
    assert row["global_revision"] == response["payload"]["global"]["revision"]
    before = _state(service)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(inherited)))
    assert client_cli.main(["deskpet", "--installation-id", "fixture"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "policy_revision_conflict",
        "status": 409,
    }
    assert len(calls) == 2  # one attempt per invocation, never an automatic retry
    assert _state(service) == before


@pytest.mark.parametrize(
    "field", ["scheduling_policy", "manual_execution_target_id", "scheduling_revision"]
)
def test_policy_snapshot_fails_closed_on_corrupt_private_project_fields(durable_service, field):
    service = durable_service
    service.store.connection.execute(f"UPDATE projects SET {field}=?", (PRIVATE * 2000,))
    code, reply = _http(
        service, {"protocol_version": 2, "operation": "POLICY_GET", "request_id": "safe-read"}
    )
    assert code == 503
    assert reply == {"error": "local_client_view_unavailable"}
    assert PRIVATE not in json.dumps(reply)


def test_policy_snapshot_is_bounded_and_readonly(durable_service):
    service = durable_service
    for index in range(CATALOG_LIMIT + 5):
        service.store.register_project(
            project_id=f"project-{index:03}",
            display_name=PRIVATE,
            canonical_repo_root=f"{PRIVATE}/{index}",
            git_root=f"{PRIVATE}/{index}",
            default_branch="main",
            last_known_head="abc",
        )
    before = _state(service)
    snapshot = _read(service)["payload"]
    assert len(snapshot["projects"]) == CATALOG_LIMIT
    assert snapshot["truncated"] == {"projects": True}
    assert PRIVATE not in json.dumps(snapshot)
    assert _state(service) == before


def _resource_views(*, provider_count=1, target_count=1, pool_count=1, window_count=1):
    """Host projections include private fields that the bridge must not export."""
    health, cards = [], []
    for index in range(provider_count):
        provider_id = f"provider-{index:03}"
        targets = tuple(
            SimpleNamespace(
                execution_target_id=f"target-{index}-{number}",
                enabled=True,
                runtime_available=True,
                launch_authorized=False,
                execution_verified_stale=True,
                credential_ref=PRIVATE,
                runtime_command=PRIVATE,
            )
            for number in range(target_count)
        )
        pools = tuple(
            SimpleNamespace(
                quota_pool_id=f"pool-{index}-{number}",
                state="AVAILABLE",
                confidence="EXACT",
                raw_evidence=PRIVATE,
                windows=tuple(
                    SimpleNamespace(
                        window_id=f"window-{index}-{number}-{window}",
                        window_kind="FIVE_HOUR",
                        state="AVAILABLE",
                        confidence="EXACT",
                        remaining_fraction=0.42,
                        reset_at="2026-10-16T01:00:00+00:00",
                        raw_response=PRIVATE,
                    )
                    for window in range(window_count)
                ),
            )
            for number in range(pool_count)
        )
        common = dict(
            provider_id=provider_id,
            connection_state="CONNECTED",
            auth_state="AUTHENTICATED",
            pool_kind="windowed",
            display_name=PRIVATE,
            auth_file_path=PRIVATE,
        )
        health.append(
            SimpleNamespace(
                **common,
                execution_targets=targets,
                runtime_state="AVAILABLE",
                last_checked="2026-10-16T00:00:00+00:00",
            )
        )
        cards.append(
            SimpleNamespace(
                **common,
                quota_pools=pools,
                quota_state="OBSERVED",
                confidence="EXACT",
                observed_at="2026-10-16T00:00:00+00:00",
                collection_failure_streak=0,
            )
        )
    return health, cards


def _replace_resource_views(service, monkeypatch, health, cards):
    monkeypatch.setattr(service, "providers", lambda: SimpleNamespace(providers=health))
    monkeypatch.setattr(service, "quota", lambda **kwargs: SimpleNamespace(providers=cards))


def test_resources_are_bounded_across_all_providers_and_do_not_expose_private_fields(
    durable_service, monkeypatch
):
    service = durable_service
    health, cards = _resource_views(
        provider_count=CATALOG_LIMIT + 3, target_count=3, pool_count=3, window_count=3
    )
    _replace_resource_views(service, monkeypatch, health, cards)
    before = _state(service)
    snapshot = _read(service, "RESOURCES")["payload"]
    providers = snapshot["providers"]
    pools = [pool for provider in providers for pool in provider["quota"]["pools"]]
    windows = [window for pool in pools for window in pool["windows"]]
    assert len(providers) == CATALOG_LIMIT
    assert sum(len(provider["execution_targets"]) for provider in providers) == CATALOG_LIMIT
    assert len(pools) == len(windows) == CATALOG_LIMIT
    assert all(snapshot["truncated"].values())
    rendered = json.dumps(snapshot, allow_nan=False)
    assert PRIVATE not in rendered
    for field in (
        "credential_ref",
        "auth_file_path",
        "raw_evidence",
        "raw_response",
        "runtime_command",
    ):
        assert field not in rendered
    assert len(rendered) < 120_000
    assert _state(service) == before
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize("fraction", [None, -0.1, 1.1, float("inf"), float("nan")])
def test_resource_unknown_or_invalid_quota_is_never_zero(durable_service, monkeypatch, fraction):
    health, cards = _resource_views()
    cards[0].quota_pools[0].windows[0].remaining_fraction = fraction
    _replace_resource_views(durable_service, monkeypatch, health, cards)
    snapshot = _read(durable_service, "RESOURCES")["payload"]
    window = snapshot["providers"][0]["quota"]["pools"][0]["windows"][0]
    assert window["remaining_fraction"] is None
    json.dumps(snapshot, allow_nan=False)
    _replace_resource_views(durable_service, monkeypatch, health, [])
    missing = _read(durable_service, "RESOURCES")["payload"]["providers"][0]
    assert missing["quota"]["quota_state"] == "UNKNOWN"
    assert missing["quota"]["confidence"] == "UNKNOWN"
    assert missing["quota"]["pools"] == []


@pytest.mark.parametrize("unknown_field", ["window_confidence", "window_state", "card_quota_state"])
def test_resource_unknown_evidence_suppresses_even_numeric_balance(
    durable_service, monkeypatch, unknown_field
):
    health, cards = _resource_views()
    window = cards[0].quota_pools[0].windows[0]
    window.remaining_fraction = 0.8
    if unknown_field == "window_confidence":
        window.confidence = "UNKNOWN"
    elif unknown_field == "window_state":
        window.state = "UNKNOWN"
    else:
        cards[0].quota_state = "UNKNOWN"
    _replace_resource_views(durable_service, monkeypatch, health, cards)
    snapshot = _read(durable_service, "RESOURCES")["payload"]
    returned = snapshot["providers"][0]["quota"]["pools"][0]["windows"][0]
    assert returned["remaining_fraction"] is None


@pytest.mark.parametrize("confidence", ["EXACT", "ESTIMATED"])
def test_resource_observed_balance_preserves_value_and_confidence(
    durable_service, monkeypatch, confidence
):
    health, cards = _resource_views()
    cards[0].quota_pools[0].windows[0].confidence = confidence
    _replace_resource_views(durable_service, monkeypatch, health, cards)
    snapshot = _read(durable_service, "RESOURCES")["payload"]
    window = snapshot["providers"][0]["quota"]["pools"][0]["windows"][0]
    assert window["remaining_fraction"] == 0.42
    assert window["confidence"] == confidence


def test_resource_read_reuses_host_projections_without_refresh(durable_service, monkeypatch):
    service = durable_service
    before = _state(service)

    class ReadOnlyQuota:
        def cached_observations(self):
            return ()

        def refresh(self, *_args, **_kwargs):
            pytest.fail("resource read must not collect quota or start providers")

    service.quota_refresh_service = ReadOnlyQuota()
    reply = _read(service, "RESOURCES")
    assert (
        reply["payload"]["providers"][0]["execution_targets"][0]["execution_target_id"] == "m3-sub"
    )
    assert "credential-ref-must-never-appear" not in json.dumps(reply)
    assert str(service.store.path) not in json.dumps(reply)
    assert _state(service) == before
    assert service.dispatch_executor.calls == []


@pytest.mark.parametrize("invalid_field", ["provider_id", "last_checked"])
def test_resources_fail_closed_on_invalid_identifiers_or_stamps(
    durable_service, monkeypatch, invalid_field
):
    health, cards = _resource_views()
    setattr(health[0], invalid_field, PRIVATE)
    if invalid_field == "provider_id":
        cards[0].provider_id = PRIVATE
    _replace_resource_views(durable_service, monkeypatch, health, cards)
    with pytest.raises(ControlPlaneError) as error:
        _read(durable_service, "RESOURCES")
    assert error.value.status == 503
    assert error.value.code == "local_client_view_unavailable"
    assert PRIVATE not in str(error.value)


def test_recovery_candidate_requires_durable_provenance_and_current_generation(service, tmp_path):
    service.store.close()
    root = tmp_path / "recoverable"
    root.mkdir()
    service.store = _recoverable(root)
    task = service.store.get_task("task")
    request = {
        "protocol_version": 2,
        "operation": "DETAIL",
        "request_id": "detail",
        "task_id": "task",
    }
    before = counts(service)
    candidate = handle_local_client_v2(service, request)["payload"]["recovery_candidate"]
    assert candidate == {
        "task_id": "task",
        "task_state_version": task.state_version,
        "prior_dispatch_id": "dispatch",
    }
    assert counts(service) == before
    reopened = service.open_request()
    try:
        assert (
            handle_local_client_v2(reopened, request)["payload"]["recovery_candidate"] == candidate
        )
    finally:
        reopened.store.close()
    recover = {
        "protocol_version": 2,
        "operation": "RECOVER",
        "request_id": "recover",
        **candidate,
        "expected_store_id": service.store.store_id,
        "expected_process_epoch": service.process_epoch,
    }
    assert handle_local_client_v2(service, recover)["payload"]["launch_performed"] is False
    assert handle_local_client_v2(service, request)["payload"]["recovery_candidate"] is None
    assert service.dispatch_executor.calls == []


def test_blocked_manual_task_without_interruption_has_no_recovery_candidate(service):
    submit(service)
    service.store.transition_task("task-1", TaskState.BLOCKED)
    reply = handle_local_client_v2(
        service,
        {"protocol_version": 2, "operation": "DETAIL", "request_id": "detail", "task_id": "task-1"},
    )
    assert reply["payload"]["recovery_candidate"] is None
    assert service.dispatch_executor.calls == []


def test_older_interruption_never_becomes_a_current_recovery_candidate(service, tmp_path):
    service.store.close()
    root = tmp_path / "stale-interruption"
    root.mkdir()
    service.store = _recoverable(root)
    service.store.transition_task("task", TaskState.READY)
    service.store.transition_task("task", TaskState.BLOCKED)
    reply = handle_local_client_v2(
        service,
        {"protocol_version": 2, "operation": "DETAIL", "request_id": "detail", "task_id": "task"},
    )
    assert reply["payload"]["recovery_candidate"] is None
    assert service.dispatch_executor.calls == []


def _canonical_contract_exchanges(service):
    """Recorded HTTP pairs with only nondeterministic identities/time normalized."""
    exchanges = []

    def record(name, request):
        status, response = _http(service, request)
        exchanges.append(
            {"name": name, "request": request, "http_status": status, "response": response}
        )

    for operation in ("CAPABILITIES", "POLICY_GET", "RESOURCES"):
        record(
            operation.lower(),
            {
                "protocol_version": 2,
                "operation": operation,
                "request_id": f"fixture-{operation.lower()}",
            },
        )
    # Include an observed pool/window shape as well as the real host's unknown
    # card. Only input projections are synthetic; the HTTP serialization is real.
    health, cards = _resource_views()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(service), "providers", lambda _self: SimpleNamespace(providers=health))
        patch.setattr(
            type(service), "quota", lambda _self, **kwargs: SimpleNamespace(providers=cards)
        )
        record(
            "resources_observed",
            {
                "protocol_version": 2,
                "operation": "RESOURCES",
                "request_id": "fixture-resources-observed",
            },
        )
    stale = _write(service, "global", "SPEED_FIRST", request_id="fixture-stale")
    record("set_global", _write(service, "global", "QUOTA_SAVER", request_id="fixture-global"))
    record(
        "set_project_manual",
        _write(
            service,
            "project",
            "MANUAL",
            manual_execution_target_id="m3-sub",
            request_id="fixture-project",
        ),
    )
    record("inherit_global", _write(service, "project", None, request_id="fixture-inherit"))
    record("stale_global_conflict", stale)
    identities = {service.store.store_id: "1" * 32, service.process_epoch: "2" * 32}
    for exchange in exchanges:
        payload = exchange["response"].get("payload", {})
        if "global" in payload:
            for row in [payload["global"], *payload["projects"]]:
                revision = row["revision"]
                if revision not in identities:
                    identities[revision] = format(len(identities) + 1, "032x")

    def canonicalize(value, key=None):
        if isinstance(value, dict):
            return {field: canonicalize(item, field) for field, item in value.items()}
        if isinstance(value, list):
            return [canonicalize(item) for item in value]
        if isinstance(value, str):
            if value in identities:
                return identities[value]
            if key == "observed_at":
                return "2026-10-16T00:00:00+00:00"
        return value

    return {"fixture_version": 1, "exchanges": canonicalize(exchanges)}


def test_canonical_shared_policy_fixture_matches_real_http_contract(durable_service):
    expected = json.loads((Path(__file__).parent / "fixtures/shared_policy_v1.json").read_text())
    assert _canonical_contract_exchanges(durable_service) == expected


def test_resources_never_resolve_credentials_or_construct_collectors(durable_service, tmp_path):
    from personal_ai_orchestrator.quota_refresh import QuotaRefreshService

    class ForbiddenCredentials:
        def resolve(self, provider_id):
            raise AssertionError("RESOURCES must not access Keychain or credential stores")

    durable_service.quota_refresh_service = QuotaRefreshService(
        runtime_state_root=tmp_path,
        connected_provider_ids=lambda: ("zai-coding-plan",),
        credential_resolver=ForbiddenCredentials(),
        environ={},
        auth_store_paths=(),
    )
    before = _state(durable_service)
    assert _read(durable_service, "RESOURCES")["payload"]["schema_version"] == 1
    assert _state(durable_service) == before


def test_independent_live_routing_reader_observes_bridge_policy_update(durable_service):
    from personal_ai_orchestrator.model_registry import ModelRegistry
    from personal_ai_orchestrator.routing_service import RoutingService
    from personal_ai_orchestrator.scheduler import RoutingObjective, RoutingPolicy

    independent = SchedulingSettings(durable_service.scheduling_settings.path)
    routing = RoutingService(
        registry=ModelRegistry(),
        store=durable_service.store,
        catalog_snapshot_id="test-shared-policy",
        policy=RoutingPolicy(),
        scheduling_settings=independent,
    )
    assert routing._global_policy().objective is RoutingObjective.BALANCED
    handle_local_client_v2(durable_service, _write(durable_service, "global", "QUALITY_FIRST"))
    assert routing._global_policy().objective is RoutingObjective.QUALITY_FIRST
    assert independent.snapshot() == ("QUALITY_FIRST", "MANUAL")
    assert independent.mode == "MANUAL"
