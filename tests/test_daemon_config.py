from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.daemon import build_service, load_runtime_config
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from personal_ai_orchestrator.scheduler import TaskProfile


def test_runtime_config_round_trip_and_service_build(tmp_path: Path) -> None:
    config = RuntimeConfig(
        catalog_snapshot_id="catalog-empty",
        registry=ModelRegistry(),
        task_profiles=(TaskProfile(task_id="task-1"),),
    )
    path = tmp_path / "runtime.json"
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    restored = load_runtime_config(path)
    assert restored == config

    service = build_service(
        config=restored,
        state_db=tmp_path / "state.sqlite3",
        runtime_state_root=tmp_path / "runtime-state",
    )
    try:
        assert service.activation_gate.authorized is False
        assert service.task_profiles["task-1"].task_id == "task-1"
        assert service.policy_journal is not None
    finally:
        service.store.close()


def test_daemon_build_reconciles_uncertain_execution_before_routing(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    seed = SafetyKernelStore(db)
    seed.submit_task(task_id="task-1", request_id="req-1", intent="implement")
    seed.transition_task("task-1", TaskState.READY)
    seed.transition_task("task-1", TaskState.RUNNING)
    seed.start_run(run_id="run-1", task_id="task-1", worker_id="worker", pid=12345)
    seed.close()

    config = RuntimeConfig(
        catalog_snapshot_id="catalog-empty",
        registry=ModelRegistry(),
        task_profiles=(TaskProfile(task_id="task-1"),),
    )
    service = build_service(
        config=config,
        state_db=db,
        runtime_state_root=tmp_path / "runtime-state",
    )
    try:
        assert service.store.get_task("task-1").state is TaskState.BLOCKED
        run = service.store.connection.execute(
            "SELECT status FROM runs WHERE run_id='run-1'"
        ).fetchone()
        assert run is not None
        assert run["status"] == "INTERRUPTED"
    finally:
        service.store.close()


def test_static_runtime_config_cannot_enable_production_active() -> None:
    payload = {
        "schema_version": 1,
        "catalog_snapshot_id": "catalog-empty",
        "registry": ModelRegistry().model_dump(mode="json"),
        "activation_gate": {"owner_approved": True},
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RuntimeConfig.model_validate(payload)


def test_runtime_config_rejects_unknown_target_refs() -> None:
    with pytest.raises(ValidationError, match="unknown targets"):
        RuntimeConfig(
            catalog_snapshot_id="catalog-empty",
            registry=ModelRegistry(),
            runtime_availability={"missing-target": True},
        )
