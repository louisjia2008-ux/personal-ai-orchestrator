"""Host-approved dependency preparation before any model or quota admission.

Each command runs in the task worktree under the normal bounded process-group
supervisor. Durable spawn intent precedes creation. An interrupted or uncertain
cleanup leaves the writer quarantined and prevents retry; exit(0) alone cannot
authorize the next command or a model launch.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore
from personal_ai_orchestrator.verifier import VerifierProfile


def preparation_report(store: SafetyKernelStore, task_id: str):
    store.get_task(task_id)
    row = store.connection.execute(
        "SELECT * FROM task_preparations WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return {
            "task_id": task_id,
            "status": "NOT_REQUIRED",
            "stages": [],
            "cleanup_state": None,
            "dispatch_id": None,
        }
    dispatch = store.connection.execute(
        "SELECT status FROM owner_dispatches WHERE dispatch_id=?", (row["dispatch_id"],)
    ).fetchone()
    status = row["status"]
    if status == "RUNNING" and (
        dispatch is None or dispatch["status"] not in {"RESERVED", "STARTED"}
    ):
        status = "UNKNOWN"
    return {
        "task_id": task_id,
        "dispatch_id": row["dispatch_id"],
        "status": status,
        "cleanup_state": row["cleanup_state"],
        "stages": json.loads(row["stages_json"]),
    }


async def run_task_preparation(
    store: SafetyKernelStore,
    *,
    dispatch_id: str,
    task_id: str,
    worktree: Path,
    profile: VerifierProfile,
    writer_token: str,
    expected_task_version: int,
    env: Callable[[], dict[str, str]],
) -> tuple[bool, bool]:
    """Return (passed, cleanup confirmed); no task/run success is inferred here."""
    if not profile.preparation_commands:
        return True, True
    digest = sha256(profile.model_dump_json().encode()).hexdigest()
    with store.connection:
        store.connection.execute(
            "INSERT INTO task_preparations VALUES(?,?,?,?,?,?,?,NULL)",
            (
                dispatch_id,
                task_id,
                digest,
                "RUNNING",
                "CONFIRMED",
                "[]",
                datetime.now(UTC).isoformat(),
            ),
        )
    stages = []

    def save(status: str, cleanup: str):
        with store.connection:
            store.connection.execute(
                "UPDATE task_preparations SET status=?,cleanup_state=?,stages_json=?,"
                "finished_at=? WHERE dispatch_id=?",
                (
                    status,
                    cleanup,
                    json.dumps(stages),
                    None if status == "RUNNING" else datetime.now(UTC).isoformat(),
                    dispatch_id,
                ),
            )

    for command in profile.preparation_commands:
        task = store.get_task(task_id)
        if (
            task.state_version != expected_task_version
            or store.get_workspace(task_id).writer_token != writer_token
        ):
            save("FAILED", "CONFIRMED")
            return False, True
        stage = {
            "name": command.name,
            "argv": list(command.argv),
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "cleanup": None,
        }
        stages.append(stage)
        # UNKNOWN is durable before crossing the OS boundary. A daemon crash
        # cannot turn an unobserved child into a retryable, clean workspace.
        save("RUNNING", "UNKNOWN")
        supervisor = ProcessSupervisor()
        ticket = supervisor.begin_spawn_observation()
        supervised = None
        failure = None
        try:
            supervised = await supervisor.start(command.argv, cwd=worktree, env=env())
            stage["pid"] = supervised.pid
            save("RUNNING", "UNKNOWN")
            result = await supervisor.collect(
                supervised,
                timeout_seconds=command.timeout_seconds,
                stdout_cap=12000,
                stderr_cap=12000,
                grace_seconds=2,
                cleanup_budget_seconds=10,
                eof_grace_seconds=2,
            )
        except (Exception, asyncio.CancelledError) as error:
            failure = type(error).__name__
            cleanup = await supervisor.abort_spawn(ticket, grace_seconds=2)
            result = supervised.collected_result if supervised else None
            if result is None:
                stage["error"] = failure
                stage["cleanup"] = cleanup.as_dict() if cleanup else None
                confirmed = (
                    bool(cleanup.confirmed)
                    if cleanup
                    else (ticket.creation_finished and ticket.raw_process is None)
                )
                save("FAILED" if confirmed else "UNKNOWN", "CONFIRMED" if confirmed else "UNKNOWN")
                if isinstance(error, asyncio.CancelledError):
                    raise
                return False, confirmed
        finally:
            supervisor.end_spawn_observation(ticket)
        stage.update(
            returncode=result.exit_code,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
            truncated=result.truncated,
            cleanup=result.cleanup.as_dict(),
        )
        if failure:
            stage["error"] = failure
        confirmed = result.cleanup.confirmed
        passed = confirmed and not failure and not result.timed_out and result.exit_code == 0
        if not passed:
            save("FAILED" if confirmed else "UNKNOWN", "CONFIRMED" if confirmed else "UNKNOWN")
            return False, confirmed
        save("RUNNING", "CONFIRMED")
    save("PASSED", "CONFIRMED")
    return True, True
