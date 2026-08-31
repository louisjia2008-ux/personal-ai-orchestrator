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
    """Create and recover task worktrees without giving workers authority over the main checkout."""

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

    def _target_for(self, task_id: str) -> Path:
        safe_id = self._safe_task_id(task_id)
        target = (self.managed_root / safe_id).resolve()
        if target.parent != self.managed_root:
            raise ValueError("worktree target escaped managed root")
        return target

    def create(self, *, repo_path: Path, task_id: str, base_sha: str) -> ManagedWorktree:
        repo = repo_path.expanduser().resolve()
        if not (repo / ".git").exists():
            raise ValueError("repo_path must be a Git checkout")
        if task_id in self._owned:
            existing = self._owned[task_id]
            if existing.base_sha != base_sha or existing.repo_path != repo:
                raise ValueError("task_id already owns a different managed worktree")
            return existing

        target = self._target_for(task_id)
        if target == repo or repo in target.parents:
            raise ValueError("managed worktree must not overlap the source checkout")
        if target.exists():
            raise FileExistsError(target)

        before = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "cat-file", "-e", f"{base_sha}^{{commit}}")
        safe_id = self._safe_task_id(task_id)
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
            # The source checkout changed concurrently. Do not adopt an isolation boundary
            # whose creation raced with external mutation; leave the task fail-closed.
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
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

    def adopt(
        self,
        *,
        repo_path: Path,
        task_id: str,
        worktree_path: Path,
        branch: str,
        base_sha: str,
    ) -> ManagedWorktree:
        """Recover a previously host-created worktree after orchestrator restart.

        Adoption is verification-only: the path must remain under the managed root, be
        registered by Git as a linked worktree of ``repo_path``, be on the expected branch,
        and contain ``base_sha`` in its current history. Nothing is created or mutated here.
        """

        repo = repo_path.expanduser().resolve()
        target = worktree_path.expanduser().resolve()
        expected_target = self._target_for(task_id)
        if target != expected_target:
            raise ValueError("persisted worktree path does not match managed task path")
        if not target.exists():
            raise FileNotFoundError(target)
        if task_id in self._owned:
            existing = self._owned[task_id]
            expected = (repo, target, branch, base_sha)
            actual = (
                existing.repo_path,
                existing.worktree_path,
                existing.branch,
                existing.base_sha,
            )
            if actual != expected:
                raise ValueError("task_id already owns a different managed worktree")
            return existing

        self._git(repo, "cat-file", "-e", f"{base_sha}^{{commit}}")
        top = Path(self._git(target, "rev-parse", "--show-toplevel")).resolve()
        if top != target:
            raise ValueError("persisted path is not the expected worktree root")
        actual_branch = self._git(target, "branch", "--show-current")
        if actual_branch != branch:
            raise ValueError("persisted worktree branch does not match recorded branch")
        ancestry = subprocess.run(
            ["git", "-C", str(target), "merge-base", "--is-ancestor", base_sha, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if ancestry.returncode != 0:
            raise ValueError("recorded base_sha is not an ancestor of worktree HEAD")

        registered_paths: set[Path] = set()
        for line in self._git(repo, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                registered_paths.add(Path(line.removeprefix("worktree ")).resolve())
        if target not in registered_paths:
            raise ValueError("persisted path is not registered as a Git worktree")

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
        if managed.worktree_path.parent != self.managed_root:
            raise RuntimeError("refusing to remove a worktree outside managed root")
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
