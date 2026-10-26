"""Real-process regression tests for verifier ``_run`` subprocess-tree cleanup.

Cleanup owns only the newly created process group. Detached sessions have
separate ownership; no system process discovery is permitted.

Every long-lived fixture performs a readiness handshake through a file and is
verified dead after cleanup; assertions never rely on fixed sleeps alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personal_ai_orchestrator.verifier import _run

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _until(predicate, *, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within budget")


def _gone(pid: int) -> bool:
    observation = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        timeout=2,
    )
    # A zombie has stopped executing and cannot append to the worktree.
    return observation.returncode != 0 or observation.stdout.strip().startswith("Z")


@pytest.fixture
def unrelated_process():
    child = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield child
    finally:
        child.kill()
        child.wait(timeout=2)


def test_command_runs_in_own_session(tmp_path: Path) -> None:
    stage = _run(
        _python(
            "import os; print(int(os.getpgrp()==os.getpid())); "
            "print(int(os.getsid(0)==os.getpid()))"
        ),
        cwd=tmp_path,
        timeout=10,
    )
    assert stage.returncode == 0
    assert stage.stdout.split() == ["1", "1"]


def test_normal_output_and_exit_code(tmp_path: Path) -> None:
    stage = _run(
        _python("import sys; print('out'); sys.stderr.write('err'); sys.exit(7)"),
        cwd=tmp_path,
        timeout=10,
    )
    assert stage.returncode == 7
    assert not stage.passed
    assert stage.stdout == "out\n"
    assert stage.stderr == "err"
    assert stage.name == stage.argv[0]
    assert stage.argv == _python("import sys; print('out'); sys.stderr.write('err'); sys.exit(7)")


def test_timeout_stops_grandchild_writes(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    pidfile = tmp_path / "grand.pid"
    grand = (
        "import time\n"
        f"log = {str(log)!r}\n"
        "while True:\n"
        "    with open(log, 'a') as handle: handle.write('tick\\n')\n"
        "    time.sleep(0.1)\n"
    )
    parent = (
        "import pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {grand!r}])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "while True: time.sleep(0.1)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_python(parent), cwd=tmp_path, timeout=3.0)
    _until(pidfile.exists)
    grand_pid = int(pidfile.read_text())
    size = log.stat().st_size
    assert size > 0
    # After cleanup the grandchild must no longer append output.
    time.sleep(1.0)
    assert log.stat().st_size == size
    _until(lambda: _gone(grand_pid))


def test_timeout_kills_sigterm_ignoring_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    ready = tmp_path / "ready"
    code = (
        "import os, pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(0.1)\n"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_python(code), cwd=tmp_path, timeout=3.0)
    _until(lambda: pidfile.exists() and ready.exists())
    pid = int(pidfile.read_text())
    # Escalation to SIGKILL must end the tree within a bounded budget.
    _until(lambda: _gone(pid), seconds=8)
    assert time.monotonic() - started < 10


def test_parent_exits_descendant_holds_pipe(tmp_path: Path) -> None:
    pidfile = tmp_path / "grand.pid"
    grand = "import time\nwhile True: time.sleep(0.1)\n"
    parent = (
        "import pathlib, subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', {grand!r}])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "sys.exit(0)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_python(parent), cwd=tmp_path, timeout=3.0)
    _until(pidfile.exists)
    grand_pid = int(pidfile.read_text())
    _until(lambda: _gone(grand_pid))


def test_timeout_captures_output_and_metadata(tmp_path: Path) -> None:
    code = (
        "import sys, time\n"
        "sys.stdout.write('partial-out\\n'); sys.stdout.flush()\n"
        "sys.stderr.write('partial-err\\n'); sys.stderr.flush()\n"
        "while True: time.sleep(0.1)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(_python(code), cwd=tmp_path, timeout=3.0)
    assert caught.value.timeout == 3.0
    assert caught.value.cmd is not None and sys.executable in str(caught.value.cmd)
    output = caught.value.output or b""
    stderr = caught.value.stderr or b""
    assert b"partial-out" in output
    assert b"partial-err" in stderr


def test_timeout_cleans_same_group_nested_descendants(tmp_path: Path) -> None:
    pidfile = tmp_path / "leaf.pid"
    ready = tmp_path / "leaf.ready"
    leaf = (
        "import os,pathlib,time\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(0.1)\n"
    )
    middle = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {leaf!r}])\n"
        "while True: time.sleep(0.1)\n"
    )
    parent = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {middle!r}])\n"
        "while True: time.sleep(0.1)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_python(parent), cwd=tmp_path, timeout=3.0)
    _until(lambda: pidfile.exists() and ready.exists())
    _until(lambda: _gone(int(pidfile.read_text())))


def test_keyboardinterrupt_cleans_up_before_propagating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "pid"
    ready = tmp_path / "ready"
    code = (
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(0.1)\n"
    )

    original_communicate = subprocess.Popen.communicate
    first_call = True

    def interrupted(self, *args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            _until(lambda: pidfile.exists() and ready.exists())
            raise KeyboardInterrupt
        return original_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run(_python(code), cwd=tmp_path, timeout=30)
    _until(lambda: pidfile.exists() and ready.exists())
    pid = int(pidfile.read_text())
    _until(lambda: _gone(pid))


def test_unrelated_process_survives_cleanup(tmp_path: Path, unrelated_process) -> None:
    assert unrelated_process.poll() is None
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_python("import time\nwhile True: time.sleep(0.1)"), cwd=tmp_path, timeout=3.0)
    assert unrelated_process.poll() is None
    unrelated_process.kill()
    assert unrelated_process.wait(timeout=2) is not None


def test_cleanup_does_not_discover_processes_outside_the_owned_group(tmp_path, monkeypatch):
    attempted_discovery = []
    original_run = subprocess.run

    def observe_discovery(argv, *args, **kwargs):
        if argv and str(argv[0]).rsplit("/", 1)[-1] == "ps":
            attempted_discovery.append(tuple(argv))
            raise AssertionError("the new session PID already identifies the owned group")
        return original_run(argv, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", observe_discovery)
        with pytest.raises(subprocess.TimeoutExpired):
            _run(_python("import time; time.sleep(20)"), cwd=tmp_path, timeout=1)
    assert not attempted_discovery
