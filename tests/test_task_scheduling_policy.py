"""Task scheduling policy must survive the App, not live in it.

P4.2.6.1 §8/§9/§10. Codex shipped the New Task policy picker as SwiftUI state only,
so a chosen policy vanished the moment the App forgot it and never reached routing.
These tests pin the full path: request -> Control API -> SQLite -> policy resolution,
plus the precedence and MANUAL rules that make the choice meaningful.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.control_api import ControlPlaneError, ControlPlaneService
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.opencode_contract import RoutingMode, RoutingRequest
from personal_ai_orchestrator.routing_service import RoutingService
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.scheduler import (
    RoutingObjective,
    RoutingPolicy,
    SchedulingPolicyLevel,
)
from personal_ai_orchestrator.scheduling_settings import SchedulingSettings


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


@pytest.fixture()
def service(tmp_path: Path) -> ControlPlaneService:
    return ControlPlaneService(
        registry=ModelRegistry(),
        store=SafetyKernelStore(tmp_path / "state.db"),
        activation_gate=ActiveRoutingGate(),
        runtime_availability={},
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling-settings.json"),
    )


@pytest.fixture()
def project_id(service: ControlPlaneService, tmp_path: Path) -> str:
    repo = _make_repo(tmp_path / "project")
    return service.register_project({"path": str(repo)}).project_id


def _submit(service, project_id, **overrides):
    payload = {
        "task_id": overrides.pop("task_id", "task-1"),
        "request_id": overrides.pop("request_id", "req-1"),
        "project_id": project_id,
        "intent": "fix the bug",
    }
    payload.update(overrides)
    return service.submit_task(payload)


# --------------------------------------------------------------------------
# The policy reaches durable storage
# --------------------------------------------------------------------------


def test_task_policy_persists_through_the_api(service, project_id, tmp_path) -> None:
    submitted = _submit(service, project_id, scheduling_policy="QUOTA_SAVER")
    assert submitted.scheduling_policy == "QUOTA_SAVER"

    # Re-read through a fresh store: this is what "durable" has to mean.
    reopened = SafetyKernelStore(tmp_path / "state.db")
    try:
        record = reopened.get_task("task-1")
    finally:
        reopened.close()

    assert record.scheduling_policy == "QUOTA_SAVER"
    assert record.manual_execution_target_id is None


def test_manual_target_persists_with_the_task(service, project_id, tmp_path) -> None:
    submitted = _submit(
        service,
        project_id,
        scheduling_policy="MANUAL",
        manual_execution_target_id="zai-glm-53",
    )

    assert submitted.scheduling_policy == "MANUAL"
    assert submitted.manual_execution_target_id == "zai-glm-53"

    reopened = SafetyKernelStore(tmp_path / "state.db")
    try:
        assert reopened.get_task("task-1").manual_execution_target_id == "zai-glm-53"
    finally:
        reopened.close()


def test_omitted_policy_stays_none_rather_than_guessing(service, project_id) -> None:
    submitted = _submit(service, project_id)

    assert submitted.scheduling_policy is None
    assert submitted.manual_execution_target_id is None


# --------------------------------------------------------------------------
# M1 WP2: min_tier persists through the full path
# --------------------------------------------------------------------------


def test_task_min_tier_defaults_to_T1(service, project_id) -> None:
    """A submit that does not name ``min_tier`` lands as ``T1`` everywhere."""

    submitted = _submit(service, project_id)
    assert submitted.min_tier == "T1"


def test_task_min_tier_persists_through_the_api(service, project_id, tmp_path) -> None:
    """``min_tier`` survives the SQLite round-trip — what \"durable\" must mean."""

    submitted = _submit(service, project_id, min_tier="T0")
    assert submitted.min_tier == "T0"

    reopened = SafetyKernelStore(tmp_path / "state.db")
    try:
        record = reopened.get_task("task-1")
    finally:
        reopened.close()
    assert record.min_tier == "T0"


def test_task_min_tier_omitted_normalises_to_T1(service, project_id, tmp_path) -> None:
    """``None`` and the absent-field default both land as ``T1``."""

    submitted = _submit(service, project_id, min_tier=None)
    assert submitted.min_tier == "T1"

    reopened = SafetyKernelStore(tmp_path / "state.db")
    try:
        assert reopened.get_task("task-1").min_tier == "T1"
    finally:
        reopened.close()


@pytest.mark.parametrize("bad_value", ["T9", "flagship", "0", "t1", ""])
def test_task_min_tier_unknown_value_is_refused(service, project_id, bad_value) -> None:
    """A typo must land as a 400, not an opaque storage error."""

    with pytest.raises(ControlPlaneError) as error:
        _submit(service, project_id, min_tier=bad_value)

    assert error.value.code.startswith("invalid_min_tier")


# --------------------------------------------------------------------------
# MANUAL is never silently downgraded
# --------------------------------------------------------------------------


def test_manual_policy_without_target_is_refused(service, project_id) -> None:
    with pytest.raises(ControlPlaneError) as error:
        _submit(service, project_id, scheduling_policy="MANUAL")

    assert error.value.code == "manual_policy_requires_execution_target"


def test_manual_target_without_manual_policy_is_refused(service, project_id) -> None:
    with pytest.raises(ControlPlaneError) as error:
        _submit(
            service,
            project_id,
            scheduling_policy="BALANCED",
            manual_execution_target_id="zai-glm-53",
        )

    assert error.value.code == "manual_target_requires_manual_policy"


def test_unknown_policy_is_refused(service, project_id) -> None:
    with pytest.raises(ControlPlaneError) as error:
        _submit(service, project_id, scheduling_policy="CHEAPEST")

    assert error.value.code == "unsupported_scheduling_policy"


# --------------------------------------------------------------------------
# Global and project levels
# --------------------------------------------------------------------------


def test_global_default_is_host_owned_and_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "scheduling-settings.json"
    settings = SchedulingSettings(path)
    assert settings.default_policy == "BALANCED"

    settings.set_default_policy("QUALITY_FIRST")

    assert SchedulingSettings(path).default_policy == "QUALITY_FIRST"


def test_global_default_rejects_manual(tmp_path: Path) -> None:
    """MANUAL cannot name a target valid for every future task, so it is not global."""

    settings = SchedulingSettings(tmp_path / "scheduling-settings.json")

    with pytest.raises(ValueError):
        settings.set_default_policy("MANUAL")


# --------------------------------------------------------------------------
# M1 WP3 — BURN_DOWN joins the owner-facing selector list
# --------------------------------------------------------------------------


def test_settings_default_policy_can_be_set_to_BURN_DOWN(tmp_path: Path) -> None:
    """BURN_DOWN joins BALANCED/QUALITY_FIRST/QUOTA_SAVER/SPEED_FIRST.

    The pressure-first preset makes a STARVED target rank above an
    ON_TRACK one for the same provider. ``MANUAL`` stays excluded —
    a global default cannot name a target that is valid for every
    future task.
    """

    path = tmp_path / "scheduling-settings.json"
    settings = SchedulingSettings(path)
    settings.set_default_policy("BURN_DOWN")
    assert settings.default_policy == "BURN_DOWN"

    # Reload — the host-owned file persists across daemon restarts.
    assert SchedulingSettings(path).default_policy == "BURN_DOWN"


def test_settings_default_rejects_unknown_policy(tmp_path: Path) -> None:
    """The owner-facing selector list is closed.

    ``BURN_DOWN`` is the only WP3 addition; any other string is
    rejected by ``set_default_policy`` so a future typo never quietly
    defaults the whole system to an unsupported objective.
    """

    settings = SchedulingSettings(tmp_path / "scheduling-settings.json")
    with pytest.raises(ValueError, match="unsupported_global_scheduling_policy"):
        settings.set_default_policy("UNKNOWN_OBJECTIVE")


def test_corrupt_settings_fail_closed_to_balanced(tmp_path: Path) -> None:
    path = tmp_path / "scheduling-settings.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert SchedulingSettings(path).default_policy == "BALANCED"


def test_project_policy_persists(service, project_id, tmp_path) -> None:
    updated = service.set_project_scheduling_policy(
        project_id, {"scheduling_policy": "SPEED_FIRST"}
    )
    assert updated.scheduling_policy == "SPEED_FIRST"

    reopened = SafetyKernelStore(tmp_path / "state.db")
    try:
        assert reopened.get_project(project_id).scheduling_policy == "SPEED_FIRST"
    finally:
        reopened.close()


def test_project_policy_can_be_cleared_back_to_global_default(service, project_id) -> None:
    service.set_project_scheduling_policy(project_id, {"scheduling_policy": "SPEED_FIRST"})

    cleared = service.set_project_scheduling_policy(project_id, {"scheduling_policy": None})

    assert cleared.scheduling_policy is None


# --------------------------------------------------------------------------
# Precedence: task > project > global, resolved from durable state
# --------------------------------------------------------------------------


def _routing_request() -> RoutingRequest:
    return RoutingRequest(
        request_id="rr-1",
        session_id="session-1",
        task_id="task-1",
        mode=RoutingMode.SHADOW,
        requested_at=datetime.now(UTC),
    )


def _routing_service(service: ControlPlaneService, tmp_path: Path) -> RoutingService:
    return RoutingService(
        registry=ModelRegistry(),
        store=service.store,
        catalog_snapshot_id="catalog-1",
        policy=RoutingPolicy(),
        scheduling_settings=SchedulingSettings(tmp_path / "scheduling-settings.json"),
    )


def test_global_default_applies_when_nothing_overrides(service, project_id, tmp_path) -> None:
    _submit(service, project_id)
    service.scheduling_settings.set_default_policy("QUALITY_FIRST")
    routing = _routing_service(service, tmp_path)

    resolution = routing._resolved_policy(_routing_request())

    assert resolution.resolved_level is SchedulingPolicyLevel.GLOBAL_DEFAULT
    assert resolution.policy.objective is RoutingObjective.QUALITY_FIRST


def test_project_override_beats_global(service, project_id, tmp_path) -> None:
    _submit(service, project_id)
    service.scheduling_settings.set_default_policy("QUALITY_FIRST")
    service.set_project_scheduling_policy(project_id, {"scheduling_policy": "QUOTA_SAVER"})
    routing = _routing_service(service, tmp_path)

    resolution = routing._resolved_policy(_routing_request())

    assert resolution.resolved_level is SchedulingPolicyLevel.PROJECT_OVERRIDE
    assert resolution.policy.objective is RoutingObjective.QUOTA_SAVER


def test_task_override_beats_project_and_global(service, project_id, tmp_path) -> None:
    _submit(service, project_id, scheduling_policy="SPEED_FIRST")
    service.scheduling_settings.set_default_policy("QUALITY_FIRST")
    service.set_project_scheduling_policy(project_id, {"scheduling_policy": "QUOTA_SAVER"})
    routing = _routing_service(service, tmp_path)

    resolution = routing._resolved_policy(_routing_request())

    assert resolution.resolved_level is SchedulingPolicyLevel.TASK_OVERRIDE
    assert resolution.policy.objective is RoutingObjective.SPEED_FIRST


def test_manual_task_override_carries_its_target(service, project_id, tmp_path) -> None:
    _submit(
        service,
        project_id,
        scheduling_policy="MANUAL",
        manual_execution_target_id="zai-glm-53",
    )
    routing = _routing_service(service, tmp_path)

    resolution = routing._resolved_policy(_routing_request())

    assert resolution.policy.objective is RoutingObjective.MANUAL
    assert resolution.policy.manual_execution_target_id == "zai-glm-53"


def test_resolution_records_every_level_for_history(service, project_id, tmp_path) -> None:
    """A later Settings change must not be able to rewrite a past decision."""

    _submit(service, project_id, scheduling_policy="SPEED_FIRST")
    service.scheduling_settings.set_default_policy("QUALITY_FIRST")
    service.set_project_scheduling_policy(project_id, {"scheduling_policy": "QUOTA_SAVER"})
    routing = _routing_service(service, tmp_path)
    request = _routing_request()

    record = routing._policy_resolution_record(request, routing._resolved_policy(request))

    assert record == {
        "requested_task_policy": "SPEED_FIRST",
        "manual_execution_target_id": None,
        "project_policy": "QUOTA_SAVER",
        "global_default_policy": "QUALITY_FIRST",
        "resolved_policy": "SPEED_FIRST",
        "resolution_source": "TASK_OVERRIDE",
    }
