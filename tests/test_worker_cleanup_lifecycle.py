"""Offline cross-adapter cleanup boundaries; no provider or broker is started.

Each executable has its own alarm. Every asynchronous fixture operation has an
outer watchdog, and fixture teardown targets only its captured exact children.
Low-level process-group and PID-reuse tests live in test_process_cleanup_contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import stat
import sys
import threading
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

import personal_ai_orchestrator.dispatch_executor as opencode_module
import personal_ai_orchestrator.pi_dispatch_executor as pi_module
from personal_ai_orchestrator.dispatch_executor import OwnerDispatchExecutor
from personal_ai_orchestrator.execution_controller import reconcile_workspace_truth
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor, SupervisedProcess
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_dispatch_executor import HELLO_CONTENT, ExecutorHarness, _git
from tests.test_pi_dispatch_executor import HELLO, _executor, _reserve

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX executable/alarm fixtures")
WATCHDOG_SECONDS = 4.0
CLEANUP_BUDGET = 0.6


async def _bounded(operation: Awaitable[Any], *, seconds: float = WATCHDOG_SECONDS) -> Any:
    """Do not let wait_for extend its deadline waiting for cancellation cleanup."""
    task = asyncio.ensure_future(operation)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        pytest.fail("isolated executor fixture exceeded its outer watchdog")
    return task.result()


@dataclass
class LifecycleFixture:
    adapter: str
    executor: OwnerDispatchExecutor
    repo: Path
    state_db: Path
    task_id: str
    request_id: str
    script: Path
    ready: Path
    content: str
    head: str
    observed: list[SupervisedProcess] = field(default_factory=list)
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    barriers: list[asyncio.Event] = field(default_factory=list)

    def write_worker(self, body: str = "") -> None:
        # Valid Pi completion also makes the OpenCode fixture harmless. Failure
        # assertions cannot accidentally pass because the happy path is invalid.
        events = [
            {"type": "session", "version": 3, "id": "offline", "cwd": "fixture"},
            {"type": "agent_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "provider": "zai",
                    "model": "glm-5.3",
                    "stopReason": "stop",
                    "content": [],
                },
            },
            {"type": "agent_end", "messages": []},
        ]
        output = "".join(json.dumps(event) + "\n" for event in events)
        self.script.write_text(
            f"#!{sys.executable}\n"
            "import os, signal, sys, time\n"
            "from pathlib import Path\n"
            "signal.alarm(8)\n"
            f"Path('hello.txt').write_text({self.content!r})\n"
            f"os.write(1, {output.encode()!r})\n" + body,
            encoding="utf-8",
        )
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def write_waiting_worker(self, term_body: str = "    sys.exit(0)\n") -> None:
        self.write_worker(
            "def terminate(*_):\n"
            + term_body
            + "signal.signal(signal.SIGTERM, terminate)\n"
            + f"Path({str(self.ready)!r}).touch()\n"
            + "while True: time.sleep(0.01)\n"
        )

    def new_executor(self) -> OwnerDispatchExecutor:
        original = self.executor
        kwargs = {
            "state_db": self.state_db,
            "config": original.config,
            "registry_provider": original._registry_provider,
            "verification_journal": original._verification_journal,
            "execution_evidence_journal": original._execution_evidence_journal,
            "quota_availability_journal": original._quota_availability_journal,
            "quota_collectors": {},
        }
        if self.adapter == "pi":
            kwargs["pi_runtime"] = original.pi_runtime
        return type(original)(**kwargs)

    def start(self) -> asyncio.Task[Any]:
        task = asyncio.create_task(self.executor.execute_async(self.request_id))
        self.tasks.append(task)
        return task

    def barrier(self) -> asyncio.Event:
        event = asyncio.Event()
        self.barriers.append(event)
        return event

    async def await_ready(self) -> None:
        async def poll() -> None:
            while not self.ready.exists():
                await asyncio.sleep(0.005)

        await _bounded(poll())

    def assert_outcome(
        self, *, cleanup: str, cancelled: bool = False, no_run: bool = False
    ) -> dict:
        store = SafetyKernelStore(self.state_db)
        try:
            task = store.get_task(self.task_id)
            assert task.state is (TaskState.CANCELLED if cancelled else TaskState.BLOCKED)
            dispatch = store.get_owner_dispatch_by_request_id(self.request_id)
            attempt = store.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
            assert attempt is not None
            assert attempt.task_id == self.task_id
            assert attempt.cleanup_state == cleanup
            rows = store.connection.execute(
                "SELECT status,result_json FROM runs WHERE task_id=?", (self.task_id,)
            ).fetchall()
            assert len(rows) == (0 if no_run else 1)
            if rows:
                expected_status = (
                    "CANCELLED"
                    if cancelled
                    else ("CLEANUP_UNKNOWN" if cleanup == "UNKNOWN" else "FAILED")
                )
                assert rows[0]["status"] == expected_status
            assert store.get_workspace(self.task_id).writer_token == (
                None if cleanup == "CONFIRMED" else attempt.writer_token
            )
            events = store.audit_events(self.task_id)
            assert not any(
                event["event_type"] == "TASK_STATE_CHANGED"
                and event["payload"]["to"] in {"VERIFYING", "VERIFIED", "WORKER_FINISHED"}
                for event in events
            )
            receipts = store.connection.execute(
                "SELECT payload_json FROM worker_cleanup_receipts WHERE attempt_id=?",
                (attempt.attempt_id,),
            ).fetchall()
            assert len(receipts) == 1
            payload = json.loads(receipts[0]["payload_json"])
            assert payload["attempt_id"] == attempt.attempt_id
            assert payload["writer_token"] == attempt.writer_token
            assert payload["state"] == cleanup
            store.assert_running_invariant(self.task_id)
        finally:
            store.close()
        assert self.executor.execution_supervisor.owned_task_ids() == ()
        if self.adapter == "pi":
            assert self.executor._delegation_brokers == {}
        assert _git(self.repo, "rev-parse", "HEAD") == self.head
        assert _git(self.repo, "status", "--porcelain") == ""
        return payload

    def assert_quarantine_survives_reconcile(self) -> None:
        # Reopen independently of the executor's connection and replay both
        # recovery paths, including a second boot. A terminal task isn't proof.
        for _ in range(2):
            store = SafetyKernelStore(self.state_db)
            try:
                token = store.get_workspace(self.task_id).writer_token
                assert token is not None
                store.reconcile_startup()
                reconcile_workspace_truth(store)
                assert store.has_cleanup_quarantine(self.task_id)
                assert store.get_workspace(self.task_id).writer_token == token
                with pytest.raises(RuntimeError, match="cleanup|quarantin"):
                    store.release_writer(self.task_id, token)
                with pytest.raises(RuntimeError, match="cleanup|quarantin"):
                    store.acquire_writer(self.task_id, "unrelated-second-writer")
            finally:
                store.close()


@pytest.fixture(params=["opencode", "pi"])
async def lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    if request.param == "pi":
        executor, repo, state_db = _executor(tmp_path, complete=True)
        executor.pi_runtime = replace(executor.pi_runtime, delegation_enabled=False)
        _reserve(repo, state_db)
        task_id, request_id, content = "task-pi", "dispatch-pi", HELLO
    else:
        harness = ExecutorHarness(tmp_path)
        executor, repo, state_db = harness.executor, harness.main_repo, harness.state_db
        task_id, request_id, content = "task-1", harness.reserve(), HELLO_CONTENT
    executor.config = replace(
        executor.config,
        worker_timeout_seconds=1.5,
        worker_grace_seconds=0.2,
        worker_cleanup_budget_seconds=CLEANUP_BUDGET,
        worker_eof_grace_seconds=0.05,
    )
    fixture = LifecycleFixture(
        adapter=request.param,
        executor=executor,
        repo=repo,
        state_db=state_db,
        task_id=task_id,
        request_id=request_id,
        script=Path(executor.config.opencode_bin),
        ready=tmp_path / "ready",
        content=content,
        head=_git(repo, "rev-parse", "HEAD"),
    )
    original_create = executor._supervisor._create

    async def capture_child(*args, **kwargs):
        supervised = await original_create(*args, **kwargs)
        fixture.observed.append(supervised)
        return supervised

    monkeypatch.setattr(executor._supervisor, "_create", capture_child)
    fixture.write_worker()
    try:
        yield fixture
    finally:
        for barrier in fixture.barriers:
            barrier.set()
        for task in fixture.tasks:
            if not task.done():
                task.cancel()
        # No raw PID/group signalling or unrelated waitpid. This sole-reaper
        # fixture supervisor retains the exact child through its cleanup lock.
        for supervised in fixture.observed:
            if supervised.cleanup_result is None:
                await _bounded(
                    ProcessSupervisor.cleanup(
                        executor._supervisor,
                        supervised,
                        grace_seconds=0,
                        cleanup_budget_seconds=CLEANUP_BUDGET,
                    ),
                    seconds=2,
                )
            if not supervised._reaped:
                executor._supervisor._signal(supervised, signal.SIGKILL)
                deadline = time.monotonic() + 1
                while not supervised._reaped and time.monotonic() < deadline:
                    executor._supervisor._observe(supervised)
                    executor._supervisor._final_reap(supervised)
                    await asyncio.sleep(0.005)
                assert supervised._reaped, "fixture must reap its exact captured child"
        for task in fixture.tasks:
            done, _ = await asyncio.wait({task}, timeout=2)
            assert done, "fixture operation must settle after exact-child cleanup"
            if not task.cancelled():
                task.exception()


async def test_offline_happy_path_is_valid_for_both_adapters(lifecycle):
    await _bounded(lifecycle.start())
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.VERIFIED
        assert store.get_workspace(lifecycle.task_id).writer_token is None
        dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
        attempt = store.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
        assert attempt.cleanup_state == "CONFIRMED"
    finally:
        store.close()
    assert lifecycle.executor._supervisor.owned_pids() == ()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_never_eof_is_bounded_and_unknown_survives_reconcile(
    lifecycle, monkeypatch: pytest.MonkeyPatch, stream: str
):
    original_spawn = lifecycle.executor._spawn_worker
    never_eof = lifecycle.barrier()

    async def spawn_with_missing_eof(*args):
        supervised = await original_spawn(*args)
        reader = getattr(supervised.process, stream)
        original_read = reader.read

        async def read_without_eof(size):
            chunk = await original_read(size)
            if not chunk:
                await never_eof.wait()
            return chunk

        monkeypatch.setattr(reader, "read", read_without_eof)
        return supervised

    monkeypatch.setattr(lifecycle.executor, "_spawn_worker", spawn_with_missing_eof)
    started = time.monotonic()
    await _bounded(lifecycle.start())
    assert time.monotonic() - started < 2
    receipt = lifecycle.observed[0].cleanup_result
    assert receipt is not None and receipt.status == "UNKNOWN"
    assert receipt.child_exited and receipt.child_reaped
    assert receipt.exit_code == 0
    pipe = getattr(receipt, stream)
    assert not pipe.eof and pipe.forced_closed
    payload = lifecycle.assert_outcome(cleanup="UNKNOWN")
    assert payload[stream]["eof"] is False
    assert payload[stream]["forced_closed"] is True
    lifecycle.assert_quarantine_survives_reconcile()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert len(lifecycle.observed) == 1, "quarantined same-request replay must never respawn"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_reader_exception_cannot_verify_valid_worker_output(
    lifecycle, monkeypatch: pytest.MonkeyPatch, stream: str
):
    original_spawn = lifecycle.executor._spawn_worker

    async def spawn_with_reader_failure(*args):
        supervised = await original_spawn(*args)
        reader = getattr(supervised.process, stream)

        async def broken_read(_size):
            raise ConnectionError("offline collector failure")

        monkeypatch.setattr(reader, "read", broken_read)
        return supervised

    monkeypatch.setattr(lifecycle.executor, "_spawn_worker", spawn_with_reader_failure)
    await _bounded(lifecycle.start())
    receipt = lifecycle.observed[0].cleanup_result
    assert getattr(receipt, stream).error == "ConnectionError"
    payload = lifecycle.assert_outcome(cleanup="UNKNOWN")
    assert payload[stream]["error"] == "ConnectionError"
    lifecycle.assert_quarantine_survives_reconcile()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_truncated_output_never_authorizes_verified(
    lifecycle, monkeypatch: pytest.MonkeyPatch, stream: str
):
    for module in (opencode_module, pi_module):
        constant = "MAX_WORKER_STDERR_BYTES" if stream == "stderr" else "MAX_WORKER_STDOUT_BYTES"
        if module is pi_module and stream == "stdout":
            constant = "PI_MAX_STDOUT_BYTES"
        monkeypatch.setattr(module, constant, 512)
    lifecycle.write_worker(f"os.write({1 if stream == 'stdout' else 2}, b'x' * 32768)\n")
    await _bounded(lifecycle.start())
    collected = lifecycle.observed[0].collected_result
    assert collected.exit_code == 0 and collected.truncated
    assert len(getattr(collected, stream)) == 512
    lifecycle.assert_outcome(cleanup="CONFIRMED")
    assert lifecycle.executor._supervisor.owned_pids() == ()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_term_handler_full_pipe_drains_and_timeout_zero_exit_stays_failed(
    lifecycle, stream: str
):
    completed = lifecycle.ready.with_name("term-output-completed")
    lifecycle.write_waiting_worker(
        f"    for _ in range(128): os.write({1 if stream == 'stdout' else 2}, b'x' * 8192)\n"
        f"    Path({str(completed)!r}).touch()\n"
        "    sys.exit(0)\n"
    )
    lifecycle.executor.config = replace(lifecycle.executor.config, worker_timeout_seconds=0.2)
    await _bounded(lifecycle.start())
    assert completed.exists(), "drainers must stay alive while TERM writes more than pipe capacity"
    receipt = lifecycle.observed[0].cleanup_result
    assert receipt.exit_code == 0
    assert "SIGTERM" in receipt.signals
    assert receipt.stdout.eof and receipt.stderr.eof
    lifecycle.assert_outcome(cleanup="CONFIRMED")
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        row = store.connection.execute(
            "SELECT result_json FROM runs WHERE task_id=?", (lifecycle.task_id,)
        ).fetchone()
        assert json.loads(row["result_json"])["exit_code"] == 124
        events = store.audit_events(lifecycle.task_id)
        assert len([event for event in events if event["event_type"] == "WORKER_TIMED_OUT"]) == 1
    finally:
        store.close()


async def test_cancelled_error_after_create_before_adapter_return_cleans_exact_child(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    lifecycle.write_waiting_worker()
    original_spawn = lifecycle.executor._spawn_worker

    async def create_then_cancel(*args):
        supervised = await original_spawn(*args)
        assert supervised.pid > 0
        raise asyncio.CancelledError

    monkeypatch.setattr(lifecycle.executor, "_spawn_worker", create_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await _bounded(lifecycle.start())
    assert len(lifecycle.observed) == 1
    supervised = lifecycle.observed[0]
    assert supervised.process.returncode is not None
    assert supervised.cleanup_result.child_reaped
    lifecycle.assert_outcome(cleanup="CONFIRMED", no_run=True)
    assert lifecycle.executor._supervisor.owned_pids() == ()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert len(lifecycle.observed) == 1


async def test_repeated_caller_cancellation_joins_one_cleanup_and_fixed_deadline(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    lifecycle.write_waiting_worker()
    entered, release = lifecycle.barrier(), lifecycle.barrier()
    original_terminate = lifecycle.executor._supervisor._terminate
    operations = []

    async def delayed_terminate(supervised):
        operations.append((supervised._operation, supervised._cleanup_deadline))
        entered.set()
        await release.wait()
        await original_terminate(supervised)

    monkeypatch.setattr(lifecycle.executor._supervisor, "_terminate", delayed_terminate)
    task = lifecycle.start()
    await lifecycle.await_ready()
    task.cancel()
    await _bounded(entered.wait())
    supervised = lifecycle.observed[0]
    original_operation, original_deadline = operations[0]
    for _ in range(4):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert supervised._operation is original_operation
        assert supervised._cleanup_deadline == original_deadline
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await _bounded(task)
    assert len(operations) == 1
    assert supervised.cleanup_result.deadline_monotonic == original_deadline
    lifecycle.assert_outcome(cleanup="CONFIRMED")
    assert lifecycle.executor._supervisor.owned_pids() == ()


async def test_timeout_cancel_overlap_has_one_cleanup_and_authoritative_cancel(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    lifecycle.write_waiting_worker()
    lifecycle.executor.config = replace(lifecycle.executor.config, worker_timeout_seconds=0.2)
    entered, release = lifecycle.barrier(), lifecycle.barrier()
    original_terminate = lifecycle.executor._supervisor._terminate
    operations = []

    async def delayed_terminate(supervised):
        operations.append((supervised._operation, supervised._cleanup_deadline))
        entered.set()
        await release.wait()
        await original_terminate(supervised)

    monkeypatch.setattr(lifecycle.executor._supervisor, "_terminate", delayed_terminate)
    task = lifecycle.start()
    await lifecycle.await_ready()
    await _bounded(entered.wait())
    active = lifecycle.executor.execution_supervisor.get(lifecycle.task_id)
    assert active is not None
    cancel_task = asyncio.create_task(lifecycle.executor._cancel_on_loop(active))
    lifecycle.tasks.append(cancel_task)
    await _bounded(active.cancel_requested.wait())
    release.set()
    assert await _bounded(cancel_task)
    await _bounded(task)
    assert len(operations) == 1
    supervised = lifecycle.observed[0]
    assert supervised.cleanup_result.deadline_monotonic == operations[0][1]
    assert supervised.cleanup_result.exit_code == 0
    lifecycle.assert_outcome(cleanup="CONFIRMED", cancelled=True)
    assert lifecycle.executor._supervisor.owned_pids() == ()


async def test_receipt_persistence_failure_keeps_writer_quarantined(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    def fail_receipt_commit(self, **kwargs):
        raise sqlite3.OperationalError("offline receipt transaction failure")

    monkeypatch.setattr(SafetyKernelStore, "complete_worker_attempt", fail_receipt_commit)
    await _bounded(lifecycle.start())
    supervised = lifecycle.observed[0]
    assert supervised.cleanup_result.status == "CONFIRMED"
    assert supervised.cleanup_result.exit_code == 0
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.BLOCKED
        dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
        attempt = store.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
        assert attempt.cleanup_state == "UNRESOLVED"
        run = store.connection.execute(
            "SELECT status,result_json FROM runs WHERE task_id=?", (lifecycle.task_id,)
        ).fetchone()
        assert run["status"] == "CLEANUP_UNKNOWN"
        assert json.loads(run["result_json"])["cleanup_state"] == "UNKNOWN"
        assert store.get_workspace(lifecycle.task_id).writer_token == attempt.writer_token
        assert (
            store.connection.execute(
                "SELECT count(*) FROM worker_cleanup_receipts WHERE attempt_id=?",
                (attempt.attempt_id,),
            ).fetchone()[0]
            == 0
        )
        events = store.audit_events(lifecycle.task_id)
        assert not any(
            event["event_type"] == "TASK_STATE_CHANGED"
            and event["payload"]["to"] in {"VERIFYING", "VERIFIED", "WORKER_FINISHED"}
            for event in events
        )
        store.assert_running_invariant(lifecycle.task_id)
    finally:
        store.close()
    lifecycle.assert_quarantine_survives_reconcile()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert len(lifecycle.observed) == 1
    assert lifecycle.executor.execution_supervisor.owned_task_ids() == ()
    assert lifecycle.executor._supervisor.owned_pids() == ()
    assert _git(lifecycle.repo, "rev-parse", "HEAD") == lifecycle.head
    assert _git(lifecycle.repo, "status", "--porcelain") == ""


async def test_startup_fences_adapter_paused_before_os_creation(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    entered, release = lifecycle.barrier(), lifecycle.barrier()
    original_start = lifecycle.executor._supervisor.start

    async def paused_before_start(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_start(*args, **kwargs)

    monkeypatch.setattr(lifecycle.executor._supervisor, "start", paused_before_start)
    task = lifecycle.start()
    await _bounded(entered.wait())
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
        attempt = store.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
        assert attempt.spawn_state == "SPAWN_ENTERED"
        assert not attempt.fenced
        token = attempt.writer_token
        store.reconcile_startup()
        assert store.get_worker_attempt(attempt.attempt_id).fenced
        assert store.get_workspace(lifecycle.task_id).writer_token == token
    finally:
        store.close()

    # Install after initial worktree Git subprocesses, so this observes only
    # attempts to cross the paused worker's OS creation boundary.
    popen_calls = []

    def forbidden_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        raise AssertionError("a startup-fenced executor must not create a worker")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "personal_ai_orchestrator.process_supervisor.subprocess.Popen", forbidden_popen
        )
        release.set()
        await _bounded(task)
    assert popen_calls == []
    assert lifecycle.observed == []
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.BLOCKED
        dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
        assert dispatch.status.value == "BLOCKED"
        attempt = store.get_worker_attempt(attempt.attempt_id)
        assert attempt.fenced
        assert not attempt.spawn_creation_claimed
        assert attempt.pid is None and attempt.spawn_ticket is None
        assert attempt.cleanup_state == "CONFIRMED"
        assert store.get_workspace(lifecycle.task_id).writer_token is None
        assert (
            store.connection.execute(
                "SELECT count(*) FROM runs WHERE task_id=?", (lifecycle.task_id,)
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()
    payload = lifecycle.assert_outcome(cleanup="CONFIRMED", no_run=True)
    assert payload["spawn_state"] == "SPAWN_FAILED_NO_CHILD"
    assert payload["child_created"] is False
    assert payload["process_create_failed"] is True
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        store.reconcile_startup()
        reconcile_workspace_truth(store)
        assert store.get_worker_attempt(attempt.attempt_id).fenced
        assert store.get_workspace(lifecycle.task_id).writer_token is None
    finally:
        store.close()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert lifecycle.observed == []


async def test_duplicate_request_while_adapter_holds_child_does_not_mutate_original(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    entered, release = lifecycle.barrier(), lifecycle.barrier()
    original_spawn = lifecycle.executor._spawn_worker

    async def paused_after_create(*args):
        supervised = await original_spawn(*args)
        entered.set()
        await release.wait()
        return supervised

    monkeypatch.setattr(lifecycle.executor, "_spawn_worker", paused_after_create)
    disable_requests = []
    original_disable = lifecycle.executor._disable_worker_session

    def observe_disable(request_id):
        disable_requests.append(request_id)
        original_disable(request_id)

    monkeypatch.setattr(lifecycle.executor, "_disable_worker_session", observe_disable)
    original_task = lifecycle.start()
    await _bounded(entered.wait())
    assert len(lifecycle.observed) == 1
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        before_task = store.get_task(lifecycle.task_id)
        before_dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
        before_attempt = store.get_worker_attempt_for_dispatch(before_dispatch.dispatch_id)
        before_events = store.audit_events(lifecycle.task_id)
        before_workspace = store.get_workspace(lifecycle.task_id)
    finally:
        store.close()
    duplicates = [lifecycle.start(), lifecycle.start()]
    for duplicate in duplicates:
        await _bounded(duplicate)
    assert not original_task.done()
    assert disable_requests == [], "duplicate invocation must not revoke the original session"
    assert len(lifecycle.observed) == 1
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id) == before_task
        assert store.get_owner_dispatch_by_request_id(lifecycle.request_id) == before_dispatch
        assert store.get_worker_attempt_for_dispatch(before_dispatch.dispatch_id) == before_attempt
        assert store.get_workspace(lifecycle.task_id) == before_workspace
        assert store.audit_events(lifecycle.task_id) == before_events
        assert store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    finally:
        store.close()
    release.set()
    await _bounded(original_task)
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.VERIFIED
        assert (
            store.get_owner_dispatch_by_request_id(lifecycle.request_id).status.value == "FINISHED"
        )
        after_attempt = store.get_worker_attempt_for_dispatch(before_dispatch.dispatch_id)
        assert after_attempt.attempt_id == before_attempt.attempt_id
        assert after_attempt.cleanup_state == "CONFIRMED"
        assert store.get_workspace(lifecycle.task_id).writer_token is None
        assert store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    finally:
        store.close()
    assert lifecycle.executor._supervisor.owned_pids() == ()
    assert len(lifecycle.observed) == 1


async def test_receipt_committed_before_terminal_failure_stays_fenced_on_restart(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    committed_receipts = []

    def fail_terminal_commit(store, **kwargs):
        # Observe from a different connection: an uncommitted receipt on the
        # executor connection cannot justify the subsequent terminal transition.
        independent = SafetyKernelStore(lifecycle.state_db)
        try:
            dispatch = independent.get_owner_dispatch_by_request_id(lifecycle.request_id)
            attempt = independent.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
            assert attempt.cleanup_state == "CONFIRMED"
            assert independent.get_task(lifecycle.task_id).state is TaskState.RUNNING
            row = independent.connection.execute(
                "SELECT payload_json FROM worker_cleanup_receipts WHERE attempt_id=?",
                (attempt.attempt_id,),
            ).fetchone()
            committed_receipts.append((attempt.attempt_id, row["payload_json"]))
        finally:
            independent.close()
        raise sqlite3.OperationalError("offline terminal commit failure after cleanup receipt")

    monkeypatch.setattr(opencode_module, "record_worker_exit", fail_terminal_commit)
    await _bounded(lifecycle.start())
    assert len(committed_receipts) == 1
    lifecycle.assert_outcome(cleanup="CONFIRMED")
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        store.reconcile_startup()
        reconcile_workspace_truth(store)
        attempt_id, payload = committed_receipts[0]
        attempt = store.get_worker_attempt(attempt_id)
        assert attempt.fenced and attempt.cleanup_state == "CONFIRMED"
        assert (
            store.connection.execute(
                "SELECT payload_json FROM worker_cleanup_receipts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()["payload_json"]
            == payload
        )
        assert store.get_workspace(lifecycle.task_id).writer_token is None
    finally:
        store.close()

    original = lifecycle.executor
    restarted = lifecycle.new_executor()
    assert restarted._executor_id != original._executor_id
    spawn_calls = []

    async def forbidden_spawn(*args):
        spawn_calls.append(args)
        raise AssertionError("same-request replay after restart cannot retry the worker")

    monkeypatch.setattr(restarted, "_spawn_worker", forbidden_spawn)
    await _bounded(restarted.execute_async(lifecycle.request_id))
    assert spawn_calls == []
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.BLOCKED
        assert store.get_worker_attempt(attempt_id).fenced
        assert store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    finally:
        store.close()


async def test_cancelled_pending_creation_remains_unknown_after_late_creator_settles(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    entered, release, finished = (lifecycle.barrier() for _ in range(3))
    original_create = lifecycle.executor._supervisor._create
    tickets = []

    async def delayed_creation(*args, **kwargs):
        tickets.append(kwargs["ticket"])
        entered.set()
        try:
            await release.wait()
            return await original_create(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(lifecycle.executor._supervisor, "_create", delayed_creation)
    execution = lifecycle.start()
    await _bounded(entered.wait())
    cancelled_at = time.monotonic()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await _bounded(execution)
    assert time.monotonic() - cancelled_at < 2
    ticket = tickets[0]
    assert ticket.deadline_monotonic <= cancelled_at + CLEANUP_BUDGET + 0.1
    assert ticket.abort_requested
    assert ticket.supervised is None
    assert not ticket.creation_finished
    assert not ticket.creation_task.done()
    assert lifecycle.observed == []
    payload = lifecycle.assert_outcome(cleanup="UNKNOWN", no_run=True)
    assert payload["capability"] == "CREATION_PENDING"
    assert "SPAWN_OUTCOME_PENDING" in payload["reasons"]
    lifecycle.assert_quarantine_survives_reconcile()

    # Let the retained creation operation wake after cleanup's budget expired.
    # Its closed ticket must reject the late OS launch, while the original
    # durable UNKNOWN receipt remains conservative and immutable.
    release.set()
    await _bounded(finished.wait())
    await asyncio.sleep(0)
    assert ticket.creation_task.done()
    assert ticket.creation_finished
    assert ticket.supervised is None
    assert lifecycle.observed == []
    lifecycle.assert_outcome(cleanup="UNKNOWN", no_run=True)
    lifecycle.assert_quarantine_survives_reconcile()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert lifecycle.observed == []


@pytest.mark.parametrize("reject_receipt", [False, True], ids=["fence-only", "all-cleanup-writes"])
async def test_spawn_fence_write_failure_cannot_skip_exact_child_cleanup(
    lifecycle, monkeypatch: pytest.MonkeyPatch, reject_receipt: bool
):
    lifecycle.write_waiting_worker()
    original_spawn = lifecycle.executor._spawn_worker
    fence_calls = []

    async def create_then_cancel(*args):
        await original_spawn(*args)
        raise asyncio.CancelledError

    def fail_fence_write(self, **kwargs):
        supervised = lifecycle.observed[0]
        fence_calls.append((kwargs["attempt_id"], supervised.cleanup_result))
        raise sqlite3.OperationalError("offline durable fence write failure")

    def fail_receipt_write(self, **kwargs):
        raise sqlite3.OperationalError("offline durable receipt write failure")

    monkeypatch.setattr(lifecycle.executor, "_spawn_worker", create_then_cancel)
    monkeypatch.setattr(SafetyKernelStore, "fence_worker_attempt", fail_fence_write)
    if reject_receipt:
        monkeypatch.setattr(SafetyKernelStore, "complete_worker_attempt", fail_receipt_write)
    with pytest.raises(asyncio.CancelledError):
        await _bounded(lifecycle.start())
    assert len(fence_calls) == 1
    receipt_at_fence = fence_calls[0][1]
    assert receipt_at_fence is not None and receipt_at_fence.child_reaped
    supervised = lifecycle.observed[0]
    assert supervised.cleanup_result.status == "CONFIRMED"
    assert supervised.cleanup_result.child_reaped
    assert supervised.process.returncode is not None
    assert lifecycle.executor._supervisor.owned_pids() == ()
    assert lifecycle.executor.execution_supervisor.owned_task_ids() == ()
    if not reject_receipt:
        lifecycle.assert_outcome(cleanup="CONFIRMED", no_run=True)
    else:
        store = SafetyKernelStore(lifecycle.state_db)
        try:
            assert store.get_task(lifecycle.task_id).state is TaskState.BLOCKED
            dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
            assert dispatch.status.value == "BLOCKED"
            attempt = store.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
            assert attempt.cleanup_state == "UNRESOLVED"
            assert store.get_workspace(lifecycle.task_id).writer_token == attempt.writer_token
            assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
            assert (
                store.connection.execute("SELECT count(*) FROM worker_cleanup_receipts").fetchone()[
                    0
                ]
                == 0
            )
        finally:
            store.close()
        lifecycle.assert_quarantine_survives_reconcile()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert len(lifecycle.observed) == 1


async def test_second_executor_before_attempt_claim_is_observational(
    lifecycle, monkeypatch: pytest.MonkeyPatch
):
    entered, release = threading.Event(), threading.Event()
    original_admit = lifecycle.executor._admit_quota

    def pause_after_writer(store, dispatch):
        entered.set()
        if not release.wait(WATCHDOG_SECONDS):
            raise TimeoutError("isolated quota-admission barrier watchdog")
        return original_admit(store, dispatch)

    monkeypatch.setattr(lifecycle.executor, "_admit_quota", pause_after_writer)
    # Admission is synchronous. The fixture executor owns its own loop in this
    # thread; the duplicate runs independently, as a restarted host could.
    original_task = asyncio.create_task(
        asyncio.to_thread(lifecycle.executor.execute, lifecycle.request_id)
    )
    lifecycle.tasks.append(original_task)
    try:
        assert await _bounded(asyncio.to_thread(entered.wait, WATCHDOG_SECONDS))
        store = SafetyKernelStore(lifecycle.state_db)
        try:
            before_task = store.get_task(lifecycle.task_id)
            before_dispatch = store.get_owner_dispatch_by_request_id(lifecycle.request_id)
            before_workspace = store.get_workspace(lifecycle.task_id)
            before_events = store.audit_events(lifecycle.task_id)
            assert before_workspace.writer_token is not None
            assert store.get_worker_attempt_for_dispatch(before_dispatch.dispatch_id) is None
        finally:
            store.close()
        duplicate = lifecycle.new_executor()
        assert duplicate._executor_id != lifecycle.executor._executor_id
        duplicate_spawn_calls = []

        async def forbidden_spawn(*args):
            duplicate_spawn_calls.append(args)
            raise AssertionError("the second executor does not own the first writer")

        monkeypatch.setattr(duplicate, "_spawn_worker", forbidden_spawn)
        await _bounded(duplicate.execute_async(lifecycle.request_id))
        assert duplicate_spawn_calls == []
        assert not original_task.done()
        store = SafetyKernelStore(lifecycle.state_db)
        try:
            assert store.get_task(lifecycle.task_id) == before_task
            assert store.get_owner_dispatch_by_request_id(lifecycle.request_id) == before_dispatch
            assert store.get_workspace(lifecycle.task_id) == before_workspace
            assert store.audit_events(lifecycle.task_id) == before_events
            assert (
                store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0] == 0
            )
            assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        finally:
            store.close()
    finally:
        release.set()
        await _bounded(original_task)
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.VERIFIED
        assert (
            store.get_owner_dispatch_by_request_id(lifecycle.request_id).status.value == "FINISHED"
        )
        assert store.get_workspace(lifecycle.task_id).writer_token is None
        assert store.connection.execute("SELECT count(*) FROM worker_attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    finally:
        store.close()
    assert len(lifecycle.observed) == 1
    assert lifecycle.executor._supervisor.owned_pids() == ()


async def test_sqlite_contention_cannot_delay_reap_or_release_unpersisted_cleanup(lifecycle):
    lifecycle.write_waiting_worker()
    execution = lifecycle.start()
    await lifecycle.await_ready()
    active = lifecycle.executor.execution_supervisor.get(lifecycle.task_id)
    assert active is not None
    locker = SafetyKernelStore(lifecycle.state_db)
    locker.connection.execute("BEGIN IMMEDIATE")
    try:
        token = locker.get_workspace(lifecycle.task_id).writer_token
        started = time.monotonic()
        cancellation = asyncio.create_task(lifecycle.executor._cancel_on_loop(active))
        lifecycle.tasks.append(cancellation)
        assert await _bounded(cancellation) is False
        await _bounded(execution)
        assert time.monotonic() - started < 2
        supervised = active.supervised
        assert supervised.cleanup_result.status == "CONFIRMED"
        assert supervised.cleanup_result.child_reaped
        assert supervised.process.returncode == 0
        assert lifecycle.executor._supervisor.owned_pids() == ()
        # The independent write transaction is still held. In-memory cleanup
        # cannot fabricate a persisted receipt or clear this durable owner.
        assert locker.get_task(lifecycle.task_id).state is TaskState.RUNNING
        assert locker.get_workspace(lifecycle.task_id).writer_token == token
        dispatch = locker.get_owner_dispatch_by_request_id(lifecycle.request_id)
        attempt = locker.get_worker_attempt_for_dispatch(dispatch.dispatch_id)
        assert attempt.cleanup_state == "UNRESOLVED"
        assert locker.has_cleanup_quarantine(lifecycle.task_id)
        assert (
            locker.connection.execute(
                "SELECT count(*) FROM worker_cleanup_receipts WHERE attempt_id=?",
                (attempt.attempt_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        locker.connection.execute("ROLLBACK")
        locker.close()
    lifecycle.assert_quarantine_survives_reconcile()
    store = SafetyKernelStore(lifecycle.state_db)
    try:
        assert store.get_task(lifecycle.task_id).state is TaskState.BLOCKED
        assert store.get_workspace(lifecycle.task_id).writer_token == token
        assert store.get_worker_attempt(attempt.attempt_id).fenced
    finally:
        store.close()
    await _bounded(lifecycle.executor.execute_async(lifecycle.request_id))
    assert len(lifecycle.observed) == 1


async def test_stale_completion_cannot_unregister_newer_same_task_identity(lifecycle):
    lifecycle.write_waiting_worker()
    execution = lifecycle.start()
    await lifecycle.await_ready()
    registry = lifecycle.executor.execution_supervisor
    original = registry.get(lifecycle.task_id)
    assert original is not None
    # A distinct registration sentinel models the next generation without
    # starting a second real writer. Reusing the PID is deliberately irrelevant:
    # unregister must compare the exact supervised identity, not task or PID.
    newer = replace(
        original,
        run_id="fixture-newer-generation",
        supervised=replace(original.supervised),
    )
    assert newer.supervised is not original.supervised
    assert newer.supervised.pid == original.supervised.pid
    registry.register(newer)
    try:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(execution)
        assert registry.get(lifecycle.task_id) is newer
        registry.unregister(lifecycle.task_id, supervised=original.supervised)
        assert registry.get(lifecycle.task_id) is newer
    finally:
        registry.unregister(lifecycle.task_id, supervised=newer.supervised)
    assert registry.get(lifecycle.task_id) is None
    lifecycle.assert_outcome(cleanup="CONFIRMED")
