"""Runtime build identity for the daemon.

The daemon must be able to state which commit produced it, so a current UI can
never silently talk to a stale daemon. Identity is resolved at *build* time and
baked into the frozen binary; it is never hard-coded in source.

Resolution order (first hit wins):

1. ``PAO_BUILD_COMMIT`` in the environment (development runs, CI overrides).
2. A ``_build_stamp.json`` written next to this package by the packaging
   script and carried into the PyInstaller bundle.
3. ``git rev-parse HEAD`` when running from a real checkout.
4. Unknown — reported explicitly, never guessed.

Nothing here reads credentials or repository content; only commit identity and
build configuration are exposed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

UNKNOWN = "unknown"
STAMP_FILENAME = "_build_stamp.json"

_FULL_SHA_LENGTH = 40
_SHORT_SHA_LENGTH = 7


def _is_sha(value: str) -> bool:
    """A commit identifier is a 40-character lowercase hex string."""
    if len(value) != _FULL_SHA_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _normalize_sha(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower()
    return candidate if _is_sha(candidate) else None


def short_sha(commit_sha: str) -> str:
    """Owner-facing abbreviation; ``unknown`` stays ``unknown``."""
    if not _is_sha(commit_sha):
        return UNKNOWN
    return commit_sha[:_SHORT_SHA_LENGTH]


def _stamp_paths() -> list[Path]:
    paths = [Path(__file__).resolve().parent / STAMP_FILENAME]
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        base = Path(bundle_dir)
        paths.append(base / "personal_ai_orchestrator" / STAMP_FILENAME)
        paths.append(base / STAMP_FILENAME)
    return paths


def _from_stamp() -> dict[str, str] | None:
    for path in _stamp_paths():
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return {str(key): str(value) for key, value in payload.items()}
    return None


def _from_git() -> str | None:
    """Commit of the checkout this source lives in, when there is one.

    Frozen binaries have no checkout; this simply returns ``None`` there.
    """
    if getattr(sys, "frozen", False):
        return None
    repo_dir = Path(__file__).resolve().parent
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return _normalize_sha(completed.stdout)


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    """Sanitized build identity for the running daemon."""

    commit_sha: str
    configuration: str
    built_at: str

    @property
    def short_sha(self) -> str:
        return short_sha(self.commit_sha)

    @property
    def is_known(self) -> bool:
        return _is_sha(self.commit_sha)

    def as_dict(self) -> dict[str, str]:
        return {
            "commit_sha": self.commit_sha,
            "short_sha": self.short_sha,
            "configuration": self.configuration,
            "built_at": self.built_at,
        }


def resolve_build_identity() -> BuildIdentity:
    """Resolve identity without ever inventing a commit."""
    environment_sha = _normalize_sha(os.environ.get("PAO_BUILD_COMMIT"))
    if environment_sha is not None:
        return BuildIdentity(
            commit_sha=environment_sha,
            configuration=os.environ.get("PAO_BUILD_CONFIGURATION", "development"),
            built_at=os.environ.get("PAO_BUILD_TIMESTAMP", UNKNOWN),
        )

    stamp = _from_stamp()
    if stamp is not None:
        stamped_sha = _normalize_sha(stamp.get("commit_sha"))
        if stamped_sha is not None:
            return BuildIdentity(
                commit_sha=stamped_sha,
                configuration=stamp.get("configuration", UNKNOWN),
                built_at=stamp.get("built_at", UNKNOWN),
            )

    git_sha = _from_git()
    if git_sha is not None:
        return BuildIdentity(
            commit_sha=git_sha,
            configuration="source",
            built_at=UNKNOWN,
        )

    return BuildIdentity(commit_sha=UNKNOWN, configuration=UNKNOWN, built_at=UNKNOWN)
