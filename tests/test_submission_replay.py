"""Client submit retries keep the original host-owned Git snapshot."""

from pathlib import Path

import pytest

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_dispatch_initiator import _build_service, _git, _make_repo


def _submission(tmp_path: Path):
    service = _build_service(tmp_path)
    repo = _make_repo(tmp_path / "project")
    project = service.register_project({"path": str(repo)})
    payload = {
        "task_id": "task-replay",
        "request_id": "request-replay",
        "project_id": project.project_id,
        "intent": "fix the button",
        "scheduling_policy": "MANUAL",
        "manual_execution_target_id": "model-a",
        "min_tier": "T1",
    }
    return service, repo, payload


def test_identical_replay_keeps_original_head_after_commit_and_restart(tmp_path):
    service, repo, payload = _submission(tmp_path)
    try:
        first = service.submit_task(payload)
        assert service.submit_task(payload) == first
        service.store.transition_task(
            first.task_id, TaskState.READY, expected_version=0, reason="fixture planning"
        )
        _git(repo, "commit", "--allow-empty", "-q", "-m", "later owner commit")
        assert _git(repo, "rev-parse", "HEAD") != first.base_sha
        service.store.close()
        service.store = SafetyKernelStore(tmp_path / "safety.db")
        replay = service.submit_task(payload)
        assert replay.task_id == first.task_id
        assert replay.base_sha == first.base_sha
        assert replay.state == "READY"
        assert replay.state_version == 1
        assert service.list_tasks().total == 1
        assert (
            len(
                [
                    event
                    for event in service.store.audit_events(first.task_id)
                    if event["event_type"] == "TASK_SUBMITTED"
                ]
            )
            == 1
        )
    finally:
        service.store.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"task_id": "different-task"},
        {"intent": "different intent"},
        {"manual_execution_target_id": "model-b"},
        {"scheduling_policy": "BALANCED", "manual_execution_target_id": None},
        {"min_tier": "T0"},
        {"project_id": "other-project"},
    ],
)
def test_head_advance_never_hides_conflicting_client_fields(tmp_path, changed):
    service, repo, payload = _submission(tmp_path)
    try:
        first = service.submit_task(payload)
        _git(repo, "commit", "--allow-empty", "-q", "-m", "later owner commit")
        if changed.get("project_id") == "other-project":
            other = _make_repo(tmp_path / "other")
            changed = {"project_id": service.register_project({"path": str(other)}).project_id}
        with pytest.raises(ControlPlaneError) as error:
            service.submit_task(payload | changed)
        assert error.value.status == 400
        assert error.value.code == "conflicting_request_id"
        assert service.list_tasks().total == 1
        assert service.get_task(first.task_id).base_sha == first.base_sha
    finally:
        service.store.close()


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_same_id_rechecks_frozen_head_without_weakening_conflicts(
    tmp_path, monkeypatch, conflicting
):
    service, repo, payload = _submission(tmp_path)
    old_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "later owner commit")
    original = service.store.submit_task
    calls = []

    def interleave(**submission):
        if not calls:
            concurrent = SafetyKernelStore(tmp_path / "safety.db")
            try:
                concurrent.submit_task(
                    **(
                        submission
                        | {
                            "base_sha": old_sha,
                            "intent": "other operation" if conflicting else payload["intent"],
                        }
                    )
                )
            finally:
                concurrent.close()
        calls.append(submission.copy())
        return original(**submission)

    monkeypatch.setattr(service.store, "submit_task", interleave)
    try:
        if conflicting:
            with pytest.raises(ControlPlaneError) as error:
                service.submit_task(payload)
            assert error.value.code == "conflicting_request_id"
        else:
            replay = service.submit_task(payload)
            assert replay.base_sha == old_sha
        assert calls[0]["base_sha"] != old_sha
        if not conflicting:
            assert len(calls) == 2
            assert calls[1]["base_sha"] == old_sha
        else:
            assert service.get_task(payload["task_id"]).intent == "other operation"
        assert service.list_tasks().total == 1
    finally:
        service.store.close()
