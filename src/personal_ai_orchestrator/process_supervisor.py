"""Exact child-process supervision for worker commands.

Only process groups created by this supervisor are signalled. There is no killall/pkill path.
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SupervisedProcess:
    process: asyncio.subprocess.Process
    argv: tuple[str, ...]
    cwd: Path

    @property
    def pid(self) -> int:
        if self.process.pid is None:
            raise RuntimeError("child process has no pid")
        return self.process.pid


class ProcessSupervisor:
    def __init__(self) -> None:
        self._children: dict[int, SupervisedProcess] = {}

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> SupervisedProcess:
        if not argv:
            raise ValueError("argv must not be empty")
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        supervised = SupervisedProcess(process=process, argv=argv, cwd=cwd)
        self._children[supervised.pid] = supervised
        return supervised

    async def wait(self, supervised: SupervisedProcess) -> tuple[int, bytes, bytes]:
        stdout, stderr = await supervised.process.communicate()
        self._children.pop(supervised.pid, None)
        return supervised.process.returncode or 0, stdout, stderr

    async def cancel(self, supervised: SupervisedProcess, *, grace_seconds: float = 2.0) -> int:
        pid = supervised.pid
        if self._children.get(pid) is not supervised:
            raise RuntimeError("process is not owned by this supervisor")
        if supervised.process.returncode is not None:
            self._children.pop(pid, None)
            return supervised.process.returncode

        os.killpg(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(supervised.process.wait(), timeout=grace_seconds)
        except TimeoutError:
            os.killpg(pid, signal.SIGKILL)
            await supervised.process.wait()
        self._children.pop(pid, None)
        return supervised.process.returncode or 0

    def emergency_kill(self, supervised: SupervisedProcess) -> None:
        """Best-effort synchronous cleanup for executor teardown paths."""

        pid = supervised.pid
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            try:
                supervised.process.kill()
            except Exception:
                pass
        self._children.pop(pid, None)

    def owned_pids(self) -> tuple[int, ...]:
        return tuple(sorted(self._children))


__all__ = ["ProcessSupervisor", "SupervisedProcess"]
