import sqlite3
from pathlib import Path

import pytest

from personal_ai_orchestrator.opencode_contract import ModelRef, RoutingDecision, RoutingMode
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.switch_lease import SwitchLeaseAuthority, SwitchLeaseStatus


def _store_with_active_decision(tmp_path: Path) -> tuple[SafetyKernelStore, RoutingDecision, int]:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    ready = store.transition_task("t1", TaskState.READY)
    decision = RoutingDecision(
        decision_id="decision-1",
        request_id="route-request",
        mode=RoutingMode.ACTIVE,
        selected_model=ModelRef(provider_id="minimax", model_id="m3"),
        selected_execution_target_id="target-m3",
        switch_requested=True,
        task_state_version=ready.state_version,
    )
    store.record_routing_decision(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        payload=decision.model_dump(mode="json"),
    )
    return store, decision, ready.state_version


def test_authorized_switch_lease_freezes_task_state_until_completed(tmp_path: Path) -> None:
    store, decision, version = _store_with_active_decision(tmp_path)
    authority = SwitchLeaseAuthority(store)
    lease = authority.authorize(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        task_state_version=version,
        session_id="session-1",
    )
    assert lease.status is SwitchLeaseStatus.AUTHORIZED

    with pytest.raises(sqlite3.IntegrityError, match="active model switch lease"):
        store.transition_task("t1", TaskState.RUNNING, expected_version=version)
    assert store.get_task("t1").state is TaskState.READY

    completed = authority.resolve(
        lease_id=lease.lease_id,
        decision_id=decision.decision_id,
        session_id="session-1",
        completed=True,
    )
    assert completed.status is SwitchLeaseStatus.COMPLETED
    transitioned = store.transition_task("t1", TaskState.RUNNING, expected_version=version)
    assert transitioned.state is TaskState.RUNNING


def test_switch_authorization_revalidates_decision_state_version(tmp_path: Path) -> None:
    store, decision, version = _store_with_active_decision(tmp_path)
    store.transition_task("t1", TaskState.RUNNING, expected_version=version)
    authority = SwitchLeaseAuthority(store)
    with pytest.raises(RuntimeError, match="task state changed"):
        authority.authorize(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            task_id="t1",
            task_state_version=version,
            session_id="session-1",
        )


def test_switch_lease_is_bound_to_session_and_decision(tmp_path: Path) -> None:
    store, decision, version = _store_with_active_decision(tmp_path)
    authority = SwitchLeaseAuthority(store)
    lease = authority.authorize(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        task_state_version=version,
        session_id="session-1",
    )
    with pytest.raises(ValueError, match="does not match"):
        authority.resolve(
            lease_id=lease.lease_id,
            decision_id=decision.decision_id,
            session_id="session-other",
            completed=True,
        )
    assert authority.get(lease.lease_id).status is SwitchLeaseStatus.AUTHORIZED


def test_host_abort_releases_switch_freeze_for_cancellation(tmp_path: Path) -> None:
    store, decision, version = _store_with_active_decision(tmp_path)
    authority = SwitchLeaseAuthority(store)
    lease = authority.authorize(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        task_state_version=version,
        session_id="session-1",
    )
    assert authority.abort_for_task("t1", reason="host cancellation") == (lease.lease_id,)
    assert authority.get(lease.lease_id).status is SwitchLeaseStatus.ABORTED
    cancelled = store.transition_task("t1", TaskState.CANCELLED, expected_version=version)
    assert cancelled.state is TaskState.CANCELLED


def test_non_switch_decision_cannot_receive_lease(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    ready = store.transition_task("t1", TaskState.READY)
    decision = RoutingDecision(
        decision_id="decision-shadow",
        request_id="route-shadow",
        mode=RoutingMode.SHADOW,
        selected_model=ModelRef(provider_id="minimax", model_id="m3"),
        switch_requested=False,
        task_state_version=ready.state_version,
    )
    store.record_routing_decision(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id="t1",
        payload=decision.model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="does not authorize"):
        SwitchLeaseAuthority(store).authorize(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            task_id="t1",
            task_state_version=ready.state_version,
            session_id="session-1",
        )


# ---------------------------------------------------------------------------
# M1 WP5a-2 — SwitchLeaseAuthority.has_active_lease
# ---------------------------------------------------------------------------


def _authorized_lease(
    store: SafetyKernelStore,
    *,
    task_id: str = "t1",
    decision_id: str = "decision-active",
    request_id: str = "route-active",
    ttl_seconds: int = 10,
):
    ready = store.transition_task(task_id, TaskState.READY)
    authority = SwitchLeaseAuthority(store)
    decision = RoutingDecision(
        decision_id=decision_id,
        request_id=request_id,
        mode=RoutingMode.ACTIVE,
        switch_requested=True,
        selected_model=ModelRef(provider_id="minimax", model_id="m3"),
        task_state_version=ready.state_version,
    )
    store.record_routing_decision(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id=task_id,
        payload=decision.model_dump(mode="json"),
    )
    return authority.authorize(
        decision_id=decision.decision_id,
        request_id=decision.request_id,
        task_id=task_id,
        task_state_version=ready.state_version,
        session_id="session-1",
        ttl_seconds=ttl_seconds,
    )


def test_has_active_lease_true_for_matching_unexpired_authorized(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    _authorized_lease(store)
    authority = SwitchLeaseAuthority(store)
    assert authority.has_active_lease("t1") is True


def test_has_active_lease_false_for_expired_authorized(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    _authorized_lease(store, ttl_seconds=5)
    authority = SwitchLeaseAuthority(store)
    future = datetime.now(UTC) + timedelta(seconds=60)
    assert authority.has_active_lease("t1", now=future) is False


def test_has_active_lease_false_for_wrong_task(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    store.submit_task(task_id="t2", request_id="task-request-2", intent="implement")
    _authorized_lease(store, task_id="t1")
    authority = SwitchLeaseAuthority(store)
    assert authority.has_active_lease("t2") is False


def test_has_active_lease_false_for_completed(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    lease = _authorized_lease(store)
    authority = SwitchLeaseAuthority(store)
    authority.resolve(
        lease_id=lease.lease_id,
        decision_id=lease.decision_id,
        session_id="session-1",
        completed=True,
    )
    assert authority.has_active_lease("t1") is False


def test_has_active_lease_false_for_aborted(tmp_path: Path) -> None:
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    store.submit_task(task_id="t1", request_id="task-request", intent="implement")
    _authorized_lease(store)
    authority = SwitchLeaseAuthority(store)
    authority.abort_for_task("t1", reason="owner cancellation")
    assert authority.has_active_lease("t1") is False
