"""Host-owned Git worktree lifecycle with conservative path and branch rules."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


_SAFE_TASK = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ManagedWorktree:
    task_id: str
    repo_path: Path
    worktree_path: Path
    branch: str
    base_sha: str


class WorktreeManager:
    """Create task worktrees without giving workers authority over the main checkout."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.expanduser().resolve()
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self._owned: dict[str, ManagedWorktree] = {}

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return completed.stdout.strip()

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        safe = _SAFE_TASK.sub("-", task_id).strip(".-")
        if not safe:
            raise ValueError("task_id cannot produce a safe worktree name")
        return safe

    def create(self, *, repo_path: Path, task_id: str, base_sha: str) -> ManagedWorktree:
        repo = repo_path.expanduser().resolve()
        if not (repo / ".git").exists():
            # A linked worktree has a .git file, but the source repo for creation must be a checkout.
            raise ValueError("repo_path must be a Git checkout")
        if task_id in self._owned:
            existing = self._owned[task_id]
            if existing.base_sha != base_sha or existing.repo_path != repo:
                raise ValueError("task_id already owns a different managed worktree")
            return existing

        safe_id = self._safe_task_id(task_id)
        target = (self.managed_root / safe_id).resolve()
        if target.parent != self.managed_root:
            raise ValueError("worktree target escaped managed root")
        if target == repo or repo in target.parents:
            raise ValueError("managed worktree must not overlap the source checkout")
        if target.exists():
            raise FileExistsError(target)

        before = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "cat-file", "-e", f"{base_sha}^{{commit}}")
        branch = f"task/{safe_id}"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(target), base_sha],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        after = self._git(repo, "rev-parse", "HEAD")
        if after != before:
            raise RuntimeError("source checkout HEAD changed during worktree creation")

        managed = ManagedWorktree(
            task_id=task_id,
            repo_path=repo,
            worktree_path=target,
            branch=branch,
            base_sha=base_sha,
        )
        self._owned[task_id] = managed
        return managed

    def remove(self, task_id: str) -> None:
        managed = self._owned.get(task_id)
        if managed is None:
            raise KeyError(task_id)
        subprocess.run(
            [
                "git",
                "-C",
                str(managed.repo_path),
                "worktree",
                "remove",
                str(managed.worktree_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        del self._owned[task_id]


__all__ = ["ManagedWorktree", "WorktreeManager"]
