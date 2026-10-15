"""Offline regressions for failed worker cleanup and retry idempotency."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

from personal_ai_orchestrator.pi_runtime import PI_PROTOCOL_ERROR_EXIT, summarize_pi_json_stream
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_dispatch_executor import HELLO_CONTENT, ExecutorHarness, write_worker_script
from tests.test_pi_dispatch_executor import _executor, _reserve, _run_row


@pytest.mark.parametrize(
    "json_value",
    ["1" * 5000, "[" * 10000 + "0" + "]" * 10000],
    ids=["oversized-integer", "excessive-nesting"],
)
def test_pi_parser_rejects_decoder_resource_limits(json_value: str) -> None:
    summary = summarize_pi_json_stream(('{"type":"session","value":' + json_value + "}\n").encode())
    assert not summary.completed
    assert summary.parse_error == "invalid_jsonl"


@pytest.mark.parametrize(
    "json_value",
    ["1" * 5000, "[" * 10000 + "0" + "]" * 10000],
    ids=["oversized-integer", "excessive-nesting"],
)
def test_pi_decoder_failure_finishes_task_without_reexecution(
    tmp_path: Path, json_value: str
) -> None:
    executor, repo, state_db = _executor(tmp_path, complete=True)
    worker = Path(executor.config.opencode_bin)
    worker.write_text(
        "#!/bin/sh\nprintf '%s\\n' '" + '{"type":"session","value":' + json_value + "}'\n",
        encoding="utf-8",
    )
    _reserve(repo, state_db)
    executor.execute("dispatch-pi")
    executor.execute("dispatch-pi")

    store = SafetyKernelStore(state_db)
    try:
        assert store.get_task("task-pi").state is TaskState.BLOCKED
        run = _run_row(store)
        assert run is not None and run["status"] == "FAILED"
        assert json.loads(run["result_json"])["exit_code"] == PI_PROTOCOL_ERROR_EXIT
        assert store.get_workspace("task-pi").writer_token is None
        assert store.get_owner_dispatch_by_request_id("dispatch-pi").status.value == "FINISHED"
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        store.assert_running_invariant("task-pi")
    finally:
        store.close()
    assert executor._supervisor.owned_pids() == ()
    assert executor.execution_supervisor.owned_task_ids() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, ConnectionError])
async def test_unexpected_worker_error_repairs_live_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    worker = write_worker_script(tmp_path / "bin", sleep_seconds=30)
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()
    observed = []

    async def fail_wait(supervised):
        observed.append(supervised)
        raise error_type("isolated worker transport failure")

    monkeypatch.setattr(harness.executor, "_wait_for_worker", fail_wait)
    try:
        await harness.executor.execute_async(request_id)
        snapshot = harness.snapshot()
        try:
            assert snapshot["task"].state is TaskState.BLOCKED
            assert snapshot["run_status"] == "FAILED"
            assert snapshot["writer_token"] is None
            assert snapshot["dispatch_failure_code"] == "EXECUTOR_INTERNAL_ERROR"
            snapshot["store"].assert_running_invariant("task-1")
        finally:
            snapshot["store"].close()
        assert observed[0].process.returncode is not None
        assert harness.executor._supervisor.owned_pids() == ()
        assert harness.executor.execution_supervisor.owned_task_ids() == ()
        await harness.executor.execute_async(request_id)
        assert len(observed) == 1
        assert harness.main_unchanged()
    finally:
        for supervised in observed:
            if supervised.process.returncode is None:
                await harness.executor._supervisor.abort_unowned(supervised)


@pytest.mark.asyncio
async def test_timeout_with_zero_exit_never_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "worker-ready"
    worker = tmp_path / "timeout-worker"
    worker.write_text(
        f"#!{sys.executable}\n"
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        f"Path('hello.txt').write_text({HELLO_CONTENT!r})\n"
        f"Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(0.01)\n",
        encoding="utf-8",
    )
    worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()
    observed = []

    async def expire_after_ready(supervised):
        observed.append(supervised)
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        raise TimeoutError

    monkeypatch.setattr(harness.executor, "_wait_for_worker", expire_after_ready)
    try:
        await asyncio.wait_for(harness.executor.execute_async(request_id), timeout=10)
        snapshot = harness.snapshot()
        try:
            assert observed[0].process.returncode == 0
            assert snapshot["task"].state is TaskState.BLOCKED
            assert snapshot["run_status"] == "FAILED"
            assert snapshot["writer_token"] is None
            assert snapshot["dispatch_failure_code"] == "TASK_BLOCKED"
            events = snapshot["store"].audit_events("task-1")
            assert not any(
                event["event_type"] == "TASK_STATE_CHANGED"
                and event["payload"]["to"] == "VERIFYING"
                for event in events
            )
            timeout_events = [
                event for event in events if event["event_type"] == "WORKER_TIMED_OUT"
            ]
            assert len(timeout_events) == 1
            assert timeout_events[0]["payload"]["safe_exit_code"] == 0
            run = snapshot["store"].connection.execute("SELECT result_json FROM runs").fetchone()
            assert json.loads(run["result_json"])["exit_code"] == 124
        finally:
            snapshot["store"].close()
        assert harness.executor._supervisor.owned_pids() == ()
        assert harness.executor.execution_supervisor.owned_task_ids() == ()
        assert harness.main_unchanged()
    finally:
        for supervised in observed:
            if supervised.process.returncode is None:
                await harness.executor._supervisor.abort_unowned(supervised)


@pytest.mark.asyncio
async def test_requested_cancel_wins_over_graceful_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "worker-ready"
    worker = tmp_path / "cancel-worker"
    worker.write_text(
        f"#!{sys.executable}\n"
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        f"Path('hello.txt').write_text({HELLO_CONTENT!r})\n"
        f"Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(0.01)\n",
        encoding="utf-8",
    )
    worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
    harness = ExecutorHarness(tmp_path, worker_bin=worker)
    request_id = harness.reserve()
    release_cancel = asyncio.Event()
    worker_collected = asyncio.Event()
    original_cancel = harness.executor._supervisor.cancel
    original_wait = harness.executor._wait_for_worker

    async def delayed_cancel(supervised, **kwargs):
        code = await original_cancel(supervised, **kwargs)
        await release_cancel.wait()
        return code

    async def collect_worker(supervised):
        result = await original_wait(supervised)
        worker_collected.set()
        return result

    monkeypatch.setattr(harness.executor._supervisor, "cancel", delayed_cancel)
    monkeypatch.setattr(harness.executor, "_wait_for_worker", collect_worker)
    execution_task = asyncio.create_task(harness.executor.execute_async(request_id))
    cancel_task = None
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        active = harness.executor.execution_supervisor.get("task-1")
        assert active is not None
        cancel_task = asyncio.create_task(harness.executor._cancel_on_loop(active))
        await asyncio.wait_for(worker_collected.wait(), timeout=5)
        assert not execution_task.done(), "worker exit must wait for the cancellation transaction"
        release_cancel.set()
        assert await asyncio.wait_for(cancel_task, timeout=5)
        await asyncio.wait_for(execution_task, timeout=5)
        snapshot = harness.snapshot()
        try:
            assert active.supervised.process.returncode == 0
            assert snapshot["task"].state is TaskState.CANCELLED
            assert snapshot["run_status"] == "CANCELLED"
            assert snapshot["dispatch_status"] == "CANCELLED"
            assert snapshot["writer_token"] is None
        finally:
            snapshot["store"].close()
        assert harness.executor._supervisor.owned_pids() == ()
        assert harness.executor.execution_supervisor.owned_task_ids() == ()
        assert harness.main_unchanged()
    finally:
        release_cancel.set()
        pending = [execution_task] + ([cancel_task] if cancel_task is not None else [])
        await asyncio.gather(*pending, return_exceptions=True)
