from __future__ import annotations

import asyncio
import errno
import os
import stat
from pathlib import Path

import pytest

from personal_ai_orchestrator.process_supervisor import (
    SPAWN_DIAGNOSTICS_VERSION,
    ProcessSpawnError,
    ProcessSupervisor,
)


def test_spawn_diagnostics_version_is_frozen() -> None:
    assert SPAWN_DIAGNOSTICS_VERSION == "pao-spawn-diagnostics-v1"


@pytest.mark.asyncio
async def test_process_create_happy_path_is_sanitized(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    secret_value = "must-not-appear"
    process = await supervisor.start(
        ("/usr/bin/true", "private-prompt-value"),
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "TOKEN_NAME_ONLY": secret_value},
    )
    exit_code, stdout, stderr = await supervisor.wait(process)

    detail = process.spawn_diagnostics.as_dict()
    rendered = str(detail)
    assert exit_code == 0
    assert stdout == stderr == b""
    assert detail["spawn_stage"] == "PROCESS_CREATED"
    assert detail["spawn_diagnostics_version"] == "pao-spawn-diagnostics-v1"
    assert detail["child_created"] is True
    assert detail["child_pid_observed"] is True
    assert detail["stdout_pipe_created"] is True
    assert detail["stderr_pipe_created"] is True
    assert detail["process_group_setup_stage"] == "NEW_SESSION_CREATED"
    assert detail["env_key_names"] == ["PATH", "TOKEN_NAME_ONLY"]
    assert secret_value not in rendered
    assert "private-prompt-value" not in rendered
    assert str(tmp_path) not in rendered
    assert supervisor.owned_pids() == ()


def test_argv_shape_does_not_retain_option_or_prompt_values(tmp_path: Path) -> None:
    secret = "secret-value-must-not-appear"
    diagnostics = ProcessSupervisor.inspect_contract(
        (
            "/usr/bin/true",
            "--model=provider/secret-model",
            "--token",
            secret,
            "--",
            f"--prompt={secret}",
        ),
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    rendered = str(diagnostics.as_dict())
    assert "option:--model" in diagnostics.argv_shape
    assert "option:--token" in diagnostics.argv_shape
    assert secret not in rendered
    assert "secret-model" not in rendered


@pytest.mark.asyncio
async def test_missing_executable_has_precise_precreate_failure(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    with pytest.raises(ProcessSpawnError) as caught:
        await supervisor.start((str(tmp_path / "missing-worker"),), cwd=tmp_path, env={})

    detail = caught.value.diagnostics.as_dict()
    assert detail["spawn_stage"] == "PROCESS_CREATE_STARTED"
    assert detail["exception_class"] == "FileNotFoundError"
    assert detail["safe_errno"] == errno.ENOENT
    assert detail["executable_exists"] is False
    assert detail["child_created"] is False
    assert supervisor.owned_pids() == ()


@pytest.mark.asyncio
async def test_non_executable_has_precise_precreate_failure(tmp_path: Path) -> None:
    worker = tmp_path / "worker"
    worker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    worker.chmod(worker.stat().st_mode & ~stat.S_IXUSR)
    supervisor = ProcessSupervisor()

    with pytest.raises(ProcessSpawnError) as caught:
        await supervisor.start((str(worker),), cwd=tmp_path, env={})

    detail = caught.value.diagnostics.as_dict()
    assert detail["exception_class"] == "PermissionError"
    assert detail["safe_errno"] == errno.EACCES
    assert detail["executable_exists"] is True
    assert detail["executable_executable"] is False
    assert detail["child_created"] is False


@pytest.mark.asyncio
async def test_invalid_cwd_has_precise_precreate_failure(tmp_path: Path) -> None:
    missing_cwd = tmp_path / "missing-cwd"
    supervisor = ProcessSupervisor()

    with pytest.raises(ProcessSpawnError) as caught:
        await supervisor.start(("/usr/bin/true",), cwd=missing_cwd, env={})

    detail = caught.value.diagnostics.as_dict()
    assert detail["exception_class"] == "FileNotFoundError"
    assert detail["cwd_exists"] is False
    assert detail["executable_exists"] is True
    assert detail["child_created"] is False


@pytest.mark.asyncio
async def test_stdio_setup_failure_is_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_create(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EMFILE, "too many files")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_create)
    supervisor = ProcessSupervisor()
    with pytest.raises(ProcessSpawnError) as caught:
        await supervisor.start(("/usr/bin/true",), cwd=tmp_path, env={})

    detail = caught.value.diagnostics.as_dict()
    assert detail["exception_class"] == "OSError"
    assert detail["safe_errno"] == errno.EMFILE
    assert detail["stdout_pipe_created"] is False
    assert detail["stderr_pipe_created"] is False


@pytest.mark.asyncio
async def test_immediate_exit_is_created_owned_and_reaped(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    process = await supervisor.start(("/usr/bin/true",), cwd=tmp_path, env={})
    assert process.spawn_diagnostics.child_created is True
    assert process.pid in supervisor.owned_pids()
    exit_code, _, _ = await supervisor.wait(process)
    assert exit_code == 0
    assert supervisor.owned_pids() == ()


@pytest.mark.asyncio
async def test_abort_unowned_reaps_already_exited_process_without_signalling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = ProcessSupervisor()
    process = await supervisor.start(("/usr/bin/true",), cwd=tmp_path, env={})
    assert await process.process.wait() == 0

    def forbidden_killpg(pid: int, requested_signal: int) -> None:
        raise AssertionError((pid, requested_signal))

    monkeypatch.setattr(os, "killpg", forbidden_killpg)
    assert await supervisor.abort_unowned(process) == 0
    assert supervisor.owned_pids() == ()


@pytest.mark.asyncio
async def test_post_create_adapter_failure_can_abort_exact_unowned_child(
    tmp_path: Path,
) -> None:
    class BrokenAccountingSupervisor(ProcessSupervisor):
        async def start(self, argv, *, cwd, env=None):
            process = await super().start(argv, cwd=cwd, env=env)
            # This is Campaign C's exact failure shape: stale role accounting
            # reaches a missing child-only scope record after process creation.
            next(item for item in [] if item)
            return process

    supervisor = BrokenAccountingSupervisor()
    token = supervisor.begin_spawn_observation()
    try:
        with pytest.raises(RuntimeError, match="coroutine raised StopIteration"):
            await supervisor.start(("/bin/sleep", "30"), cwd=tmp_path, env={})
        observed = supervisor.observed_process()
        assert observed is not None
        pid = observed.pid
        assert pid in supervisor.owned_pids()
        assert await supervisor.abort_unowned(observed) == -9
        assert supervisor.owned_pids() == ()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        supervisor.end_spawn_observation(token)


@pytest.mark.asyncio
async def test_local_ownership_index_failure_still_exposes_exact_child(
    tmp_path: Path,
) -> None:
    class BrokenIndex(dict[int, object]):
        def __setitem__(self, key: int, value: object) -> None:
            raise RuntimeError("injected ownership index failure")

    supervisor = ProcessSupervisor()
    supervisor._children = BrokenIndex()  # type: ignore[assignment]
    token = supervisor.begin_spawn_observation()
    try:
        with pytest.raises(RuntimeError, match="ownership index"):
            await supervisor.start(("/bin/sleep", "30"), cwd=tmp_path, env={})
        observed = supervisor.observed_process()
        assert observed is not None
        pid = observed.pid
        assert await supervisor.abort_unowned(observed) == -9
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        supervisor.end_spawn_observation(token)
