"""Issue #72: a stable cancellation ID cannot turn an AUTO veto into a cancel."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from personal_ai_orchestrator.client_gateway import (
    ClientOperation,
    DeskPetClientAdapter,
    DeskPetToolRequest,
    ExternalClientGateway,
)
from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from tests.test_supervised_auto_step import _env, _plan


class DirectClient:
    def __init__(self, service):
        self.service = service

    def cancel(self, task_id, *, request_id=None):
        return self.service.cancel_task(task_id, {"request_id": request_id})


def _planned(tmp_path):
    env = _env(tmp_path, unattended=False)
    _plan(env)
    return env


def _veto_events(store):
    return [event for event in store.audit_events("task-1") if event["event_type"] == "AUTO_VETOED"]


def test_same_deskpet_cancel_is_stable_after_store_reopen(tmp_path):
    env = _planned(tmp_path)
    request = DeskPetToolRequest(
        request_id="cancel-auto-1", operation=ClientOperation.CANCEL, task_id="task-1"
    )
    adapter = DeskPetClientAdapter(
        ExternalClientGateway(DirectClient(env.service)), installation_id="fixture"
    )
    try:
        first = adapter.call(request)
        assert first.state == "READY"
        env.store.close()
        env.store = SafetyKernelStore(tmp_path / "safety.db")
        env.service.store = env.store
        second = adapter.call(request)
        assert second.payload == first.payload
        assert first.summary == second.summary
        assert "task remains ready" in second.summary
        assert len(_veto_events(env.store)) == 1
        assert env.shadow.load_pending_all() == ()
        assert env.store.get_task("task-1").scheduling_policy == "MANUAL"
        assert env.executor.unique_calls == []
    finally:
        env.store.close()


def test_new_cancel_id_can_cancel_after_veto_and_old_replay_never_resurrects(tmp_path):
    env = _planned(tmp_path)
    try:
        first = env.service.cancel_task("task-1", {"request_id": "first"})
        second = env.service.cancel_task("task-1", {"request_id": "new-operation"})
        assert first.task.state == "READY"
        assert second.task.state == "CANCELLED"
        replay = env.service.cancel_task("task-1", {"request_id": "first"})
        assert replay.task == second.task
        assert replay.cancelled_now is False
        assert len(_veto_events(env.store)) == 1
    finally:
        env.store.close()


def test_cancel_ids_do_not_alias_an_explicit_veto_operation(tmp_path):
    env = _planned(tmp_path)
    try:
        version = env.store.get_task("task-1").state_version
        env.service.auto_veto("task-1", {"request_id": "same-text", "task_state_version": version})
        cancelled = env.service.cancel_task("task-1", {"request_id": "same-text"})
        assert cancelled.task.state == "CANCELLED"
        assert cancelled.cancelled_now is True
    finally:
        env.store.close()


def test_cancel_replay_is_scoped_to_the_task(tmp_path):
    env = _planned(tmp_path)
    try:
        env.service.cancel_task("task-1", {"request_id": "same-text"})
        env.store.submit_task(task_id="task-other", request_id="submit-other", intent="other")
        cancelled = env.service.cancel_task("task-other", {"request_id": "same-text"})
        assert cancelled.task.state == "CANCELLED"
        assert env.store.get_task("task-1").state.value == "READY"
    finally:
        env.store.close()


def test_cancel_without_stable_id_preserves_new_operation_semantics(tmp_path):
    env = _planned(tmp_path)
    try:
        assert env.service.cancel_task("task-1", {}).task.state == "READY"
        assert env.service.cancel_task("task-1", {}).task.state == "CANCELLED"
    finally:
        env.store.close()


def test_lost_response_after_veto_commit_replays_without_a_second_transition(tmp_path, monkeypatch):
    env = _planned(tmp_path)
    from personal_ai_orchestrator import supervised_auto_step

    drain = supervised_auto_step.drain_auto_shadow_cleanup_outbox

    def lose_response(*args, **kwargs):
        raise RuntimeError("fixture interrupted after commit")

    try:
        monkeypatch.setattr(supervised_auto_step, "drain_auto_shadow_cleanup_outbox", lose_response)
        with pytest.raises(RuntimeError, match="interrupted after commit"):
            env.service.cancel_task("task-1", {"request_id": "lost-response"})
        committed = env.store.get_task("task-1")
        assert committed.state.value == "READY"
        assert len(env.store.pending_shadow_cleanups()) == 1
        monkeypatch.setattr(supervised_auto_step, "drain_auto_shadow_cleanup_outbox", drain)
        replay = env.service.cancel_task("task-1", {"request_id": "lost-response"})
        assert replay.task.state == "READY"
        assert replay.task.state_version == committed.state_version
        assert replay.cancelled_now is False
        assert len(_veto_events(env.store)) == 1
        drain(env.store, env.shadow)
        assert env.store.pending_shadow_cleanups() == ()
        assert env.shadow.load_pending_all() == ()
    finally:
        env.store.close()


@pytest.mark.parametrize("same_id", [True, False])
def test_concurrent_cancel_commit_race_only_replays_the_identical_request(tmp_path, same_id):
    env = _planned(tmp_path)
    barrier = Barrier(2, timeout=5)
    version = env.store.get_task("task-1").state_version

    def cancel(request_id):
        service = env.service.open_request()
        abort = service.store.abort_auto_lifecycle

        def simultaneous_abort(*args, **kwargs):
            barrier.wait()
            return abort(*args, **kwargs)

        service.store.abort_auto_lifecycle = simultaneous_abort
        try:
            return service.cancel_task("task-1", {"request_id": request_id})
        except ControlPlaneError as error:
            return error
        finally:
            service.store.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(cancel, "cancel-1"),
                pool.submit(cancel, "cancel-1" if same_id else "different-request"),
            ]
            results = [future.result(timeout=10) for future in futures]
        errors = [result for result in results if isinstance(result, ControlPlaneError)]
        successes = [result for result in results if not isinstance(result, ControlPlaneError)]
        assert len(errors) == (0 if same_id else 1)
        assert all(result.task.state == "READY" for result in successes)
        if same_id:
            assert sum(result.cancelled_now for result in successes) == 1
        else:
            assert errors[0].status == 409
        assert env.store.get_task("task-1").state_version == version + 1
        assert len(_veto_events(env.store)) == 1
        assert env.store.pending_shadow_cleanups() == ()
        assert env.executor.unique_calls == []
    finally:
        env.store.close()


def test_no_id_cancel_does_not_claim_a_later_explicit_fallback_shaped_id(tmp_path):
    env = _planned(tmp_path)
    try:
        first = env.service.cancel_task("task-1", {})
        assert first.task.state == "READY"
        event = _veto_events(env.store)[0]["payload"]
        assert event["request_id"] is None
        assert event["request_id_explicit"] is False
        second = env.service.cancel_task("task-1", {"request_id": "cancel-task-1"})
        assert second.task.state == "CANCELLED"
        assert second.cancelled_now is True
    finally:
        env.store.close()


def test_explicit_fallback_shaped_id_replays_when_provenance_is_known(tmp_path):
    env = _planned(tmp_path)
    try:
        first = env.service.cancel_task("task-1", {"request_id": "cancel-task-1"})
        assert _veto_events(env.store)[0]["payload"]["request_id_explicit"] is True
        second = env.service.cancel_task("task-1", {"request_id": "cancel-task-1"})
        assert first.task == second.task
        assert second.cancelled_now is False
    finally:
        env.store.close()


@pytest.mark.parametrize("legacy_id", ["cancel-task-1", "owner-supplied-legacy-id"])
def test_legacy_veto_identity_is_replayed_only_when_unambiguous(tmp_path, legacy_id):
    env = _planned(tmp_path)
    try:
        current = env.store.get_task("task-1")
        # Recreate an old-build audit row: the optional new provenance field is absent.
        first = env.store.abort_auto_lifecycle(
            "task-1",
            expected_version=current.state_version,
            reason="owner_cancel",
            event_type="AUTO_VETOED",
            request_id=legacy_id,
            force_manual=True,
        )
        assert "request_id_explicit" not in _veto_events(env.store)[0]["payload"]
        if legacy_id == "cancel-task-1":
            with pytest.raises(ControlPlaneError) as error:
                env.service.cancel_task("task-1", {"request_id": legacy_id})
            assert error.value.status == 409
            assert error.value.code == "cancel_request_identity_ambiguous"
        else:
            replay = env.service.cancel_task("task-1", {"request_id": legacy_id})
            assert replay.task.state == "READY"
            assert replay.cancelled_now is False
        assert env.store.get_task("task-1").state_version == first.state_version
        assert len(_veto_events(env.store)) == 1
    finally:
        env.store.close()
