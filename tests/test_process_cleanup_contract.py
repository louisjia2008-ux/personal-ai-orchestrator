"""Offline exact-process tests; every long-lived fixture has a bounded teardown."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError

import pytest

from personal_ai_orchestrator.process_supervisor import (
    CleanupIncomplete,
    CleanupStatus,
    OutputCollectionError,
    ProcessSpawnError,
    ProcessSupervisor,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")


async def _until(predicate, *, seconds: float = 2.0) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture
def owned_supervisor():
    supervisor = ProcessSupervisor()
    yield supervisor
    # Last-resort fixture teardown is exact-child only, and remains under the
    # production sole-reaper lock. No process-name or stale-PID cleanup exists.
    for child in supervisor._retained:
        child._identity_error = None
        with child._lock:
            if not child._reaped:
                try:
                    pid, status = os.waitpid(child.pid, os.WNOHANG)
                    if pid:
                        supervisor._mark_reaped_locked(child, status)
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                except ChildProcessError:
                    continue
        deadline = time.monotonic() + 1
        while not child._reaped and time.monotonic() < deadline:
            supervisor._final_reap(child)
            time.sleep(0.005)
        for state in child._pipes:
            if state.transport is not None:
                state.transport.close()


@pytest.fixture
def unrelated_process():
    child = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield child
    finally:
        child.kill()
        child.wait(timeout=2)


@pytest.fixture
def adopted_descendants():
    """Reap only registered fixture descendants even under a non-reaping PID 1."""
    if sys.platform != "linux":
        pytest.skip("Linux child-subreaper fixture")
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        pytest.skip("child-subreaper fixture unsupported")
    descendants: set[int] = set()
    try:
        yield descendants
    finally:
        for pid in descendants:
            try:
                observed, _ = os.waitpid(pid, os.WNOHANG)
                if not observed:
                    os.kill(pid, signal.SIGKILL)
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        if os.waitpid(pid, os.WNOHANG)[0]:
                            break
                        time.sleep(0.005)
            except ChildProcessError:
                pass
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


async def _reap_adopted(pid: int, descendants: set[int]) -> None:
    async with asyncio.timeout(3):
        while True:
            try:
                observed, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                observed = 0
            if observed:
                descendants.discard(pid)
                return
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_normal_collection_capped_and_receipt_immutable(tmp_path, owned_supervisor):
    child = await owned_supervisor.start(
        (sys.executable, "-c", "import os; os.write(1,b'x'*50000); os.write(2,b'y'*50000)"),
        cwd=tmp_path,
    )
    async with asyncio.timeout(3):
        result = await owned_supervisor.collect(child, stdout_cap=17, stderr_cap=23)
    assert result.stdout == b"x" * 17
    assert result.stderr == b"y" * 23
    assert result.truncated and result.exit_code == 0
    receipt = result.cleanup
    assert receipt.confirmed and receipt.child_reaped and receipt.group_empty
    assert receipt.scope == "CREATED_PROCESS_GROUP"
    assert receipt.stdout.eof and receipt.stderr.eof
    assert receipt.signals == ()
    assert owned_supervisor.owned_pids() == ()
    with pytest.raises(FrozenInstanceError):
        receipt.group_empty = False


@pytest.mark.asyncio
async def test_timeout_exit_zero_drains_full_term_output(
    tmp_path, owned_supervisor, unrelated_process, monkeypatch
):
    ready = tmp_path / "ready"
    finished = tmp_path / "finished"
    worker = (
        "import os,signal,time,pathlib\n"
        "def stop(*args):\n"
        " os.write(1,b'a'*300000)\n"
        " os.write(2,b'b'*300000)\n"
        f" pathlib.Path({str(finished)!r}).write_text('done')\n"
        " raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "while True: time.sleep(.1)\n"
    )
    child = await owned_supervisor.start((sys.executable, "-c", worker), cwd=tmp_path)
    await _until(ready.exists)
    original = os.killpg

    def checked_signal(pid, value):
        assert pid == child.pid and pid != unrelated_process.pid
        assert not value or (child._pinned and not child._reaped)
        return original(pid, value)

    monkeypatch.setattr(os, "killpg", checked_signal)
    async with asyncio.timeout(3):
        with pytest.raises(TimeoutError):
            await owned_supervisor.collect(
                child,
                timeout_seconds=0.03,
                stdout_cap=101,
                stderr_cap=203,
                grace_seconds=0.5,
                cleanup_budget_seconds=1,
            )
    result = child.collected_result
    assert result is not None and result.timed_out and result.exit_code == 0
    assert result.cleanup.confirmed and result.cleanup.stdout.eof and result.cleanup.stderr.eof
    assert result.stdout == b"a" * 101 and result.stderr == b"b" * 203
    assert finished.read_text() == "done"
    assert result.truncated
    assert unrelated_process.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("held_fd", [1, 2])
async def test_leader_first_descendant_is_killed_while_leader_pins_group(
    tmp_path, owned_supervisor, adopted_descendants, unrelated_process, monkeypatch, held_fd
):
    pidfile = tmp_path / "descendant"
    script = (
        "import os,signal,time,pathlib\n"
        "pid=os.fork()\n"
        "if pid:\n"
        f" pathlib.Path({str(pidfile)!r}).write_text(str(pid))\n"
        " os._exit(0)\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"os.close({3 - held_fd})\n"
        "time.sleep(30)\n"
    )
    child = await owned_supervisor.start((sys.executable, "-c", script), cwd=tmp_path)
    await _until(pidfile.exists)
    descendant = int(pidfile.read_text())
    adopted_descendants.add(descendant)
    async with asyncio.timeout(2):
        assert await child.process.wait() == 0
    assert child._pinned and not child._reaped
    real_signal = os.killpg
    observed_signals = []

    def checked_signal(pid, requested):
        assert pid == child.pid
        if requested:
            assert child._pinned and not child._reaped
            # The exact leader remains an observable, unreaped child even though
            # its return code is zero and its descendant still owns a pipe.
            assert os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        observed_signals.append(requested)
        return real_signal(pid, requested)

    monkeypatch.setattr(os, "killpg", checked_signal)
    reaper = asyncio.create_task(_reap_adopted(descendant, adopted_descendants))
    try:
        async with asyncio.timeout(3):
            with pytest.raises(OutputCollectionError):
                await owned_supervisor.collect(
                    child,
                    eof_grace_seconds=0.03,
                    grace_seconds=0.03,
                    cleanup_budget_seconds=1,
                )
            await reaper
        receipt = child.cleanup_result
        assert receipt is not None and receipt.confirmed and receipt.exit_code == 0
        assert signal.SIGTERM in observed_signals and signal.SIGKILL in observed_signals
        assert receipt.stdout.eof and receipt.stderr.eof
        assert unrelated_process.poll() is None
    finally:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper


@pytest.mark.asyncio
async def test_stalled_reader_is_bounded_and_forced_close_is_not_eof(tmp_path, owned_supervisor):
    child = await owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
    never = asyncio.Event()

    async def stuck_read(size):
        await never.wait()
        return b""

    child.process.stdout.read = stuck_read
    started = asyncio.get_running_loop().time()
    async with asyncio.timeout(2):
        with pytest.raises(CleanupIncomplete) as caught:
            await owned_supervisor.collect(
                child, eof_grace_seconds=0.02, grace_seconds=0.02, cleanup_budget_seconds=0.12
            )
    receipt = caught.value.receipt
    assert receipt.status is CleanupStatus.UNKNOWN
    assert receipt.stdout.forced_closed and not receipt.stdout.eof
    assert receipt.stdout.collector_done
    assert receipt.child_reaped and receipt.group_empty
    assert asyncio.get_running_loop().time() - started < 0.8
    assert child.pid in owned_supervisor.owned_pids()


@pytest.mark.asyncio
async def test_reader_error_never_confirms_cleanup(tmp_path, owned_supervisor):
    child = await owned_supervisor.start(
        (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
    )

    async def broken_read(size):
        raise OSError("injected reader failure")

    child.process.stderr.read = broken_read
    async with asyncio.timeout(2):
        with pytest.raises(CleanupIncomplete) as caught:
            await owned_supervisor.collect(child, grace_seconds=0.02, cleanup_budget_seconds=0.2)
    assert caught.value.receipt.stderr.error == "OSError"
    assert not caught.value.receipt.stderr.eof
    assert caught.value.receipt.child_reaped


@pytest.mark.asyncio
async def test_repeated_cancellation_joins_one_fixed_deadline(tmp_path, owned_supervisor):
    ready = tmp_path / "ready"
    child = await owned_supervisor.start(
        (
            sys.executable,
            "-c",
            "import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"pathlib.Path({str(ready)!r}).touch();time.sleep(30)",
        ),
        cwd=tmp_path,
    )
    await _until(ready.exists)
    caller = asyncio.create_task(
        owned_supervisor.collect(child, grace_seconds=0.1, cleanup_budget_seconds=0.4)
    )
    await _until(lambda: child._operation is not None)
    caller.cancel()
    await _until(lambda: child._cleanup_deadline is not None)
    deadline, operation = child._cleanup_deadline, child._operation
    for _ in range(5):
        caller.cancel()
        await asyncio.sleep(0.005)
        assert child._cleanup_deadline == deadline and child._operation is operation
    async with asyncio.timeout(2):
        with pytest.raises(asyncio.CancelledError):
            await caller
    assert child.cleanup_result is not None and child.cleanup_result.confirmed
    assert child.cleanup_result.deadline_monotonic == deadline
    assert child.cleanup_result.signals == ("SIGTERM", "SIGKILL")
    assert (await owned_supervisor.cleanup(child)).deadline_monotonic == deadline


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "waitid"), reason="waitid uncertainty injection")
async def test_uncertain_identity_signals_neither_child_nor_negative_control(
    tmp_path, owned_supervisor, unrelated_process, monkeypatch
):
    child = await owned_supervisor.start(
        (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
    )
    original = os.waitid

    def lost_identity(*args):
        if args[1] == child.pid:
            raise ChildProcessError("injected competing reaper")
        return original(*args)

    def forbidden_signal(*args):
        raise AssertionError("no signal is authorized when identity is uncertain")

    with monkeypatch.context() as patch:
        patch.setattr(os, "waitid", lost_identity)
        patch.setattr(os, "killpg", forbidden_signal)
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.cleanup(child, cleanup_budget_seconds=0.2)
    assert not receipt.confirmed and not receipt.child_reaped
    assert receipt.signals == () and "IDENTITY_ChildProcessError" in receipt.reasons
    assert unrelated_process.poll() is None


@pytest.mark.asyncio
async def test_waitpid_fallback_normal_completion_and_live_cancel(
    tmp_path, owned_supervisor, monkeypatch
):
    monkeypatch.delattr(os, "WNOWAIT", raising=False)
    child = await owned_supervisor.start((sys.executable, "-c", "print('ok')"), cwd=tmp_path)
    async with asyncio.timeout(2):
        result = await owned_supervisor.collect(child)
    assert result.cleanup.confirmed and result.stdout == b"ok\n"
    assert result.cleanup.capability == "WAITPID_REAP_ON_OBSERVE"
    live = await owned_supervisor.start(
        (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
    )
    async with asyncio.timeout(2):
        receipt = await owned_supervisor.cleanup(
            live, grace_seconds=0.05, cleanup_budget_seconds=0.4
        )
    assert receipt.confirmed and receipt.signals == ("SIGTERM",)


@pytest.mark.asyncio
async def test_waitpid_fallback_reaped_leader_never_authorizes_descendant_signal(
    tmp_path, owned_supervisor, adopted_descendants, monkeypatch
):
    monkeypatch.delattr(os, "WNOWAIT", raising=False)
    pidfile = tmp_path / "descendant"
    child = await owned_supervisor.start(
        (
            sys.executable,
            "-c",
            "import os,time,pathlib\npid=os.fork()\nif pid:\n"
            f" pathlib.Path({str(pidfile)!r}).write_text(str(pid))\n os._exit(0)\n"
            "time.sleep(30)\n",
        ),
        cwd=tmp_path,
    )
    await _until(pidfile.exists)
    descendant = int(pidfile.read_text())
    adopted_descendants.add(descendant)
    assert await child.process.wait() == 0
    assert child._reaped and not child._pinned
    original = os.killpg

    def no_nonzero_signal(pid, value):
        assert value == 0
        return original(pid, value)

    monkeypatch.setattr(os, "killpg", no_nonzero_signal)
    async with asyncio.timeout(2):
        receipt = await owned_supervisor.cleanup(
            child, grace_seconds=0.02, cleanup_budget_seconds=0.1
        )
    assert not receipt.confirmed and not receipt.group_empty
    assert receipt.signals == ()
    assert os.waitpid(descendant, os.WNOHANG) == (0, 0)


@pytest.mark.asyncio
async def test_cross_task_ticket_captures_cancelled_adapter_child(tmp_path, owned_supervisor):
    ticket = owned_supervisor.begin_spawn_observation()
    registered = asyncio.Event()
    pause = asyncio.Event()

    async def adapter():
        await owned_supervisor.start(
            (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
        )
        registered.set()
        await pause.wait()

    caller = asyncio.create_task(adapter())
    try:
        await asyncio.wait_for(registered.wait(), timeout=2)
        child = owned_supervisor.observed_process(ticket)
        assert child is not None and owned_supervisor.observed_process() is child
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(
                ticket, grace_seconds=0, cleanup_budget_seconds=0.5
            )
        assert receipt is not None and receipt.confirmed
        assert ticket.abort_requested and ticket.creation_finished
        assert child.cleanup_result is receipt
    finally:
        caller.cancel()
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_ticket_fence_prevents_late_start(tmp_path, owned_supervisor):
    ticket = owned_supervisor.begin_spawn_observation()
    try:
        assert await owned_supervisor.abort_spawn(ticket, cleanup_budget_seconds=0.01) is None
        with pytest.raises(RuntimeError, match="fenced"):
            await owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
        assert not ticket.creation_started and ticket.supervised is None
    finally:
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_closed_pipes_do_not_prove_created_group_empty(
    tmp_path, owned_supervisor, adopted_descendants, monkeypatch
):
    pidfile = tmp_path / "descendant"
    child = await owned_supervisor.start(
        (
            sys.executable,
            "-c",
            "import os,time,pathlib\npid=os.fork()\nif pid:\n"
            f" pathlib.Path({str(pidfile)!r}).write_text(str(pid))\n os._exit(0)\n"
            "os.close(1);os.close(2);time.sleep(30)\n",
        ),
        cwd=tmp_path,
    )
    await _until(pidfile.exists)
    descendant = int(pidfile.read_text())
    adopted_descendants.add(descendant)
    real_signal = os.killpg

    def observation_only(pid, value):
        assert value == 0
        assert child._reaped
        return real_signal(pid, value)

    monkeypatch.setattr(os, "killpg", observation_only)
    async with asyncio.timeout(2):
        with pytest.raises(CleanupIncomplete) as caught:
            await owned_supervisor.collect(child)
    assert caught.value.receipt.child_reaped
    assert caught.value.receipt.stdout.eof and caught.value.receipt.stderr.eof
    assert not caught.value.receipt.group_empty
    assert "GROUP_NOT_OBSERVED_EMPTY" in caught.value.receipt.reasons
    assert os.waitpid(descendant, os.WNOHANG) == (0, 0)


@pytest.mark.asyncio
async def test_scope_explicitly_excludes_descendants_that_escape_the_group(
    tmp_path, owned_supervisor, adopted_descendants
):
    pidfile = tmp_path / "escaped"
    child = await owned_supervisor.start(
        (
            sys.executable,
            "-c",
            "import os,time,pathlib\npid=os.fork()\nif pid:\n"
            " os._exit(0)\nos.setsid();os.close(1);os.close(2)\n"
            f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n",
        ),
        cwd=tmp_path,
    )
    await _until(pidfile.exists)
    escaped = int(pidfile.read_text())
    adopted_descendants.add(escaped)
    async with asyncio.timeout(2):
        result = await owned_supervisor.collect(child)
    assert result.cleanup.confirmed
    assert result.cleanup.scope == "CREATED_PROCESS_GROUP"
    assert os.getpgid(escaped) != child.pid
    assert os.waitpid(escaped, os.WNOHANG) == (0, 0)


@pytest.mark.asyncio
async def test_cancel_during_pipe_attach_retains_creation_and_uses_same_cleanup(
    tmp_path, owned_supervisor, monkeypatch
):
    ticket = owned_supervisor.begin_spawn_observation()
    attaching = asyncio.Event()
    release = asyncio.Event()
    loop = asyncio.get_running_loop()
    connect = loop.connect_read_pipe

    async def paused_connect(*args, **kwargs):
        attaching.set()
        await release.wait()
        return await connect(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", paused_connect)
    caller = asyncio.create_task(
        owned_supervisor.start((sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path)
    )
    try:
        await asyncio.wait_for(attaching.wait(), 2)
        assert ticket.supervised is not None and not ticket.creation_finished
        caller.cancel()
        await _until(lambda: ticket.deadline_monotonic is not None)
        assert ticket.creation_task is not None and not ticket.creation_task.cancelled()
        deadline = ticket.deadline_monotonic
        operation = ticket.supervised._operation
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(
                ticket, grace_seconds=0, cleanup_budget_seconds=0.1
            )
        assert receipt is not None and receipt.confirmed
        assert receipt.deadline_monotonic == deadline
        assert ticket.supervised._operation is operation
        assert ticket.creation_finished
    finally:
        release.set()
        caller.cancel()
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_pending_creation_returns_unknown_and_fences_late_create(
    tmp_path, owned_supervisor, monkeypatch
):
    ticket = owned_supervisor.begin_spawn_observation()
    release = asyncio.Event()
    create = owned_supervisor._create

    async def pending_create(*args, **kwargs):
        await release.wait()
        return await create(*args, **kwargs)

    monkeypatch.setattr(owned_supervisor, "_create", pending_create)
    caller = asyncio.create_task(
        owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
    )
    try:
        await _until(lambda: ticket.creation_task is not None)
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(ticket, cleanup_budget_seconds=0.02)
        assert receipt is not None and not receipt.confirmed
        assert receipt.reasons == ("SPAWN_OUTCOME_PENDING",)
        assert not ticket.creation_finished and ticket.supervised is None
        release.set()
        with pytest.raises(RuntimeError):
            await caller
        assert ticket.creation_finished and not ticket.creation_started
        assert owned_supervisor.owned_pids() == ()
    finally:
        release.set()
        caller.cancel()
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_creation_guard_covers_exact_capture_and_no_async_pipe_setup(
    tmp_path, owned_supervisor, monkeypatch
):
    ticket = owned_supervisor.begin_spawn_observation()
    guard_active = False
    reported = []

    @contextlib.contextmanager
    def guard():
        nonlocal guard_active
        guard_active = True

        def report(pid):
            assert ticket.supervised is not None
            assert ticket.supervised.pid == pid
            assert ticket.supervised in owned_supervisor._retained
            reported.append(pid)

        try:
            yield report
        finally:
            guard_active = False

    ticket.creation_guard = guard
    loop = asyncio.get_running_loop()
    connect = loop.connect_read_pipe

    async def checked_connect(*args, **kwargs):
        assert not guard_active
        assert reported
        return await connect(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", checked_connect)
    try:
        child = await owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
        async with asyncio.timeout(2):
            result = await owned_supervisor.collect(child)
        assert result.cleanup.confirmed and reported == [child.pid]
        assert result.cleanup.deadline_monotonic > loop.time()
    finally:
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_stale_cleanup_cannot_remove_replacement_registry_owner(
    tmp_path, owned_supervisor, monkeypatch
):
    child = await owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
    replacement = object()
    reap = owned_supervisor._final_reap

    def reap_then_replace(supervised):
        reap(supervised)
        if supervised._reaped:
            owned_supervisor._children[supervised.pid] = replacement

    monkeypatch.setattr(owned_supervisor, "_final_reap", reap_then_replace)
    async with asyncio.timeout(2):
        result = await owned_supervisor.collect(child)
    assert result.cleanup.confirmed
    assert owned_supervisor._children[child.pid] is replacement


@pytest.mark.asyncio
async def test_direct_shutdown_cancellation_keeps_cleanup_operation_alive(
    tmp_path, owned_supervisor
):
    ready = tmp_path / "ready"
    child = await owned_supervisor.start(
        (
            sys.executable,
            "-c",
            "import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"pathlib.Path({str(ready)!r}).touch();time.sleep(30)",
        ),
        cwd=tmp_path,
    )
    await _until(ready.exists)
    caller = asyncio.create_task(
        owned_supervisor.collect(child, grace_seconds=0.03, cleanup_budget_seconds=0.2)
    )
    await _until(lambda: child._operation is not None)
    operation = child._operation
    assert operation is not None
    # Mimic asyncio.run's cancellation of all tasks, not just the caller. Pipe
    # cancellation is conservatively incomplete, but may never abandon the child.
    await _until(lambda: all(state.task is not None for state in child._pipes))
    for state in child._pipes:
        state.task.cancel()
    operation.cancel()
    await asyncio.sleep(0)
    deadline = child._cleanup_deadline
    operation.cancel()
    async with asyncio.timeout(2):
        with pytest.raises(CleanupIncomplete) as caught:
            await caller
    assert caught.value.receipt.child_reaped and caught.value.receipt.group_empty
    assert not caught.value.receipt.confirmed
    assert child._operation is operation and child._cleanup_deadline == deadline
    assert "SUPERVISOR_CANCELLED" in caught.value.receipt.reasons


@pytest.mark.asyncio
async def test_late_pipe_transport_is_closed_without_registry_resurrection(
    tmp_path, owned_supervisor, monkeypatch
):
    ticket = owned_supervisor.begin_spawn_observation()
    attaching = asyncio.Event()
    release = asyncio.Event()
    late_transports = []

    class LateTransport:
        def __init__(self, pipe):
            self.pipe = pipe
            self.closed = False

        def close(self):
            self.closed = True
            self.pipe.close()

    async def delayed_connect(protocol_factory, pipe):
        attaching.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                # A faulty attachment ignores cancellation and returns after
                # cleanup's deadline. The returned handle must still be fenced.
                continue
        transport = LateTransport(pipe)
        late_transports.append(transport)
        return transport, protocol_factory()

    monkeypatch.setattr(asyncio.get_running_loop(), "connect_read_pipe", delayed_connect)
    caller = asyncio.create_task(
        owned_supervisor.start((sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path)
    )
    try:
        await asyncio.wait_for(attaching.wait(), 2)
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(
                ticket, grace_seconds=0, cleanup_budget_seconds=0.05
            )
        child = ticket.supervised
        assert child is not None
        assert receipt is not None and not receipt.confirmed
        assert receipt.child_reaped and not receipt.stdout.eof
        assert child.pid not in owned_supervisor._children
        release.set()
        async with asyncio.timeout(2):
            assert await caller is child
        assert ticket.creation_finished
        assert len(late_transports) == 1 and late_transports[0].closed
        assert child.pid not in owned_supervisor._children
        assert child.cleanup_result is receipt
    finally:
        release.set()
        caller.cancel()
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_start_cancellation_uses_ticket_budget_before_executor_abort(
    tmp_path, owned_supervisor, monkeypatch
):
    ticket = owned_supervisor.begin_spawn_observation(cleanup_budget_seconds=0.05, grace_seconds=0)
    release = asyncio.Event()
    create = owned_supervisor._create

    async def pending_create(*args, **kwargs):
        await release.wait()
        return await create(*args, **kwargs)

    monkeypatch.setattr(owned_supervisor, "_create", pending_create)
    caller = asyncio.create_task(
        owned_supervisor.start((sys.executable, "-c", "pass"), cwd=tmp_path)
    )
    try:
        await _until(lambda: ticket.creation_task is not None)
        started = asyncio.get_running_loop().time()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        deadline = ticket.deadline_monotonic
        assert deadline is not None and deadline - started <= 0.06
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(ticket, cleanup_budget_seconds=1)
        assert receipt is not None and not receipt.confirmed
        assert receipt.deadline_monotonic == deadline
        assert asyncio.get_running_loop().time() - started < 0.3
        release.set()
        await _until(lambda: ticket.creation_finished)
        assert ticket.supervised is None
    finally:
        release.set()
        caller.cancel()
        owned_supervisor.end_spawn_observation(ticket)


def test_asyncio_run_shutdown_reaps_child_created_before_pipe_attachment(tmp_path):
    script = f"""
import asyncio, os, sys
from pathlib import Path
from personal_ai_orchestrator.process_supervisor import ProcessSupervisor
supervisor = ProcessSupervisor()
ticket = None
async def main():
    global ticket
    ticket = supervisor.begin_spawn_observation(cleanup_budget_seconds=.2, grace_seconds=0)
    attaching = asyncio.Event()
    async def paused(*args, **kwargs):
        attaching.set()
        await asyncio.Event().wait()
    asyncio.get_running_loop().connect_read_pipe = paused
    asyncio.create_task(supervisor.start(
        (sys.executable, '-c', 'import time;time.sleep(30)'), cwd=Path({str(tmp_path)!r})
    ))
    await attaching.wait()
asyncio.run(main())
assert ticket.supervised is not None
receipt = ticket.supervised.cleanup_result
assert receipt is not None and receipt.child_reaped, receipt
assert not receipt.confirmed
try:
    os.kill(ticket.supervised.pid, 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError('shutdown left exact child alive')
"""
    completed = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, text=True, timeout=3
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_wrapper_allocation_failure_happens_before_os_child_creation(
    tmp_path, owned_supervisor, monkeypatch
):
    from personal_ai_orchestrator import process_supervisor as module

    ticket = owned_supervisor.begin_spawn_observation()
    launches = []
    popen = subprocess.Popen

    def record_launch(*args, **kwargs):
        launches.append(True)
        return popen(*args, **kwargs)

    def fail_wrapper(*args, **kwargs):
        raise RuntimeError("injected wrapper allocation failure")

    monkeypatch.setattr(subprocess, "Popen", record_launch)
    monkeypatch.setattr(module, "_OwnedProcess", fail_wrapper)
    try:
        with pytest.raises(ProcessSpawnError):
            await owned_supervisor.start(
                (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
            )
        assert launches == []
        assert ticket.raw_process is None and ticket.supervised is None
        assert await owned_supervisor.abort_spawn(ticket) is None
    finally:
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_post_create_guard_failure_keeps_raw_capture_and_requires_cleanup(
    tmp_path, owned_supervisor
):
    ticket = owned_supervisor.begin_spawn_observation(cleanup_budget_seconds=0.2, grace_seconds=0)

    @contextlib.contextmanager
    def guard():
        def report(pid):
            assert ticket.raw_process is not None and ticket.raw_process.pid == pid
            assert ticket.supervised is not None and ticket.supervised.pid == pid
            raise RuntimeError("injected registration commit failure")

        yield report

    ticket.creation_guard = guard
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            await owned_supervisor.start(
                (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
            )
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.abort_spawn(
                ticket, cleanup_budget_seconds=0.2, grace_seconds=0
            )
        assert receipt is not None and receipt.child_reaped
        assert not receipt.confirmed
        assert ticket.raw_process is not None
        assert ticket.raw_process.returncode == -signal.SIGKILL
    finally:
        owned_supervisor.end_spawn_observation(ticket)


@pytest.mark.asyncio
async def test_immediate_operation_cancellation_cannot_bypass_cleanup_finalizer(
    tmp_path, owned_supervisor
):
    child = await owned_supervisor.start(
        (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
    )
    caller = asyncio.create_task(
        owned_supervisor.cleanup(child, grace_seconds=0, cleanup_budget_seconds=0.2)
    )
    await asyncio.sleep(0)
    operation = child._operation
    assert operation is not None
    deadline = child._cleanup_deadline
    operation.cancel()
    async with asyncio.timeout(2):
        receipt = await caller
    assert receipt.child_reaped and receipt.group_empty
    assert child._operation is operation and child._cleanup_deadline == deadline


@pytest.mark.parametrize("policy", ["ignored", "custom"])
def test_nondefault_sigchld_policy_rejected_before_spawn_in_isolated_interpreter(tmp_path, policy):
    # Changing SIGCHLD in pytest itself would undermine every other fixture's
    # sole-reaper assumptions. The isolated interpreter creates no worker at all.
    script = f"""
import asyncio, signal, subprocess, sys
from pathlib import Path
from personal_ai_orchestrator.process_supervisor import ProcessSpawnError, ProcessSupervisor
policy = signal.SIG_IGN if {policy!r} == 'ignored' else lambda *args: None
signal.signal(signal.SIGCHLD, policy)
def forbidden_spawn(*args, **kwargs):
    raise AssertionError('unsafe SIGCHLD policy reached Popen')
subprocess.Popen = forbidden_spawn
async def main():
    supervisor = ProcessSupervisor()
    ticket = supervisor.begin_spawn_observation()
    try:
        try:
            await supervisor.start((sys.executable, '-c', 'pass'), cwd=Path({str(tmp_path)!r}))
        except ProcessSpawnError as error:
            assert not error.diagnostics.child_created
            assert error.diagnostics.process_group_setup_stage == 'UNSUPPORTED_SIGCHLD_POLICY'
        else:
            raise AssertionError('nondefault SIGCHLD policy was accepted')
        assert ticket.raw_process is None and ticket.supervised is None
        assert await supervisor.abort_spawn(ticket) is None
        assert signal.getsignal(signal.SIGCHLD) == policy
    finally:
        supervisor.end_spawn_observation(ticket)
asyncio.run(main())
"""
    completed = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, text=True, timeout=3
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [signal.SIG_IGN, lambda *args: None], ids=["ignored", "custom"])
async def test_changed_sigchld_policy_withholds_signals_and_preserves_unknown(
    tmp_path, owned_supervisor, unrelated_process, monkeypatch, policy
):
    child = await owned_supervisor.start(
        (sys.executable, "-c", "import time;time.sleep(30)"), cwd=tmp_path
    )
    real_getsignal = signal.getsignal

    def changed_policy(requested):
        return policy if requested == signal.SIGCHLD else real_getsignal(requested)

    def forbidden_signal(*args):
        raise AssertionError("changed SIGCHLD policy must invalidate all signal authority")

    # Mock only the observation; do not introduce a real competing reaper in
    # the pytest interpreter. Both exact child and negative control remain alive.
    with monkeypatch.context() as patch:
        patch.setattr(signal, "getsignal", changed_policy)
        patch.setattr(os, "killpg", forbidden_signal)
        async with asyncio.timeout(2):
            receipt = await owned_supervisor.cleanup(child, cleanup_budget_seconds=0.1)
    assert receipt.status is CleanupStatus.UNKNOWN
    assert "UNSUPPORTED_SIGCHLD_POLICY" in receipt.reasons
    assert not receipt.child_reaped and receipt.signals == ()
    assert not child._pinned
    assert unrelated_process.poll() is None
    assert os.waitpid(child.pid, os.WNOHANG) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_name", ["execution_probe", "pi_execution_probe"])
@pytest.mark.parametrize("failed_pipe", [1, 2])
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_probe_pipe_setup_error_joins_exact_cleanup_before_propagating(
    tmp_path,
    owned_supervisor,
    unrelated_process,
    monkeypatch,
    probe_name,
    failed_pipe,
    repeat_cancel,
):
    from personal_ai_orchestrator import execution_probe, pi_execution_probe

    module = execution_probe if probe_name == "execution_probe" else pi_execution_probe
    worker = tmp_path / "offline-probe"
    worker.write_text(f"#!{sys.executable}\nimport signal,time\nsignal.alarm(8)\ntime.sleep(30)\n")
    worker.chmod(0o700)
    monkeypatch.setattr(module, "ProcessSupervisor", lambda: owned_supervisor)
    ticket = owned_supervisor.begin_spawn_observation(cleanup_budget_seconds=0.3, grace_seconds=0)
    loop = asyncio.get_running_loop()
    connect = loop.connect_read_pipe
    calls = 0
    failure = OSError("offline pipe attachment failure")

    async def fail_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failed_pipe:
            raise failure
        return await connect(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", fail_connect)
    entered, release = asyncio.Event(), asyncio.Event()
    terminate = owned_supervisor._terminate

    async def paused_terminate(supervised):
        entered.set()
        await release.wait()
        await terminate(supervised)

    monkeypatch.setattr(owned_supervisor, "_terminate", paused_terminate)
    kwargs = {"cwd": tmp_path, "timeout_seconds": 0.2}
    if module is execution_probe:
        kwargs.update(opencode_bin=str(worker), model_sku_id="offline/model")
    else:
        kwargs.update(
            pi_bin=str(worker), model_ref="offline/model", guard_path=tmp_path / "guard.ts"
        )
    caller = asyncio.create_task(module._probe(**kwargs))
    started = loop.time()
    try:
        await _until(lambda: entered.is_set() or caller.done())
        child = ticket.supervised
        assert child is not None
        operation, deadline = child._operation, child._cleanup_deadline
        if repeat_cancel and entered.is_set():
            for _ in range(4):
                caller.cancel()
                await asyncio.sleep(0)
                assert not caller.done()
                assert child._operation is operation and child._cleanup_deadline == deadline
        release.set()
        async with asyncio.timeout(2):
            with pytest.raises(OSError) as caught:
                await caller
        assert caught.value is failure
        receipt = child.cleanup_result
        assert receipt is not None, "post-create failure must finish bounded cleanup before return"
        assert entered.is_set() and operation is not None and operation.done()
        assert receipt.child_reaped and receipt.group_empty
        assert receipt.status is CleanupStatus.UNKNOWN
        failed = child._pipes[failed_pipe - 1]
        assert failed.error == "PIPE_SETUP_FAILED" and not failed.eof
        assert receipt.deadline_monotonic == deadline == ticket.deadline_monotonic
        assert child._operation is operation
        assert ticket.abort_requested and ticket.creation_finished
        assert child.pid in owned_supervisor.owned_pids()
        assert len(owned_supervisor._retained) == 1 and calls == failed_pipe
        assert (await owned_supervisor.cleanup(child)) is receipt
        assert loop.time() - started < 1
        assert unrelated_process.poll() is None
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
            await asyncio.wait({caller}, timeout=1)
        if ticket.supervised is not None and ticket.supervised.cleanup_result is None:
            # Keep red-baseline teardown inside the live loop as well.
            await owned_supervisor.abort_spawn(ticket, grace_seconds=0, cleanup_budget_seconds=0.3)
        owned_supervisor.end_spawn_observation(ticket)
