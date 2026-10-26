"""Host-owned patch bytes and observational worktree freshness checks."""

from __future__ import annotations

import base64
import binascii
import re
import sqlite3
import stat
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

PATCH_BYTE_LIMIT = 1024 * 1024

# Per-command config overrides keep the canonical patch representation independent of the
# user's diff display preferences: no custom file ordering, default path
# quoting, standard blank-context lines, and the default Myers diff algorithm
# (a repository/global histogram or other setting would produce different
# patch bytes for the same content and break verification). ``git apply``
# accepts the result without needing any of these settings.
STABLE_GIT_CONFIG = (
    "-c",
    "diff.orderFile=/dev/null",
    "-c",
    "core.quotePath=true",
    "-c",
    "diff.suppressBlankEmpty=false",
    "-c",
    "diff.algorithm=myers",
)

# A hunk header is "@@ -a,b +c,d @@" optionally followed by a space and a
# section-heading text derived from the diff driver's xfuncname/funcname
# pattern. That text is display-only: changing the driver changes the heading
# but never the line numbers or the patch body.
_HUNK_HEADER = re.compile(rb"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _normalize_hunk_headers(raw: bytes) -> bytes:
    """Strip only the optional section heading after the second ``@@``.

    The same source tree must produce identical patch bytes regardless of any
    custom ``diff.<driver>.xfuncname``/``funcname`` configuration, so the
    display-only heading text is removed from each hunk header while line
    number ranges, patch bodies, index/path/mode lines and binary payloads are
    preserved byte for byte. Patch body lines always start with a space, ``+``,
    ``-`` or ``\\``, so a source line that merely looks like ``@@ -1 +1 @@``
    never matches; splitting and rejoining on ``\n`` alone keeps CR/VT/FF and
    non-UTF-8 bytes intact. Binary patch payloads are arbitrary base85 lines, so
    regions from ``GIT binary patch`` to the terminating blank line are skipped.
    """
    if b"\n@@" not in raw and not raw.startswith(b"@@"):
        return raw
    lines = raw.split(b"\n")
    in_binary = False
    for index, line in enumerate(lines):
        if in_binary:
            if not line:
                in_binary = False
            continue
        if line.startswith(b"GIT binary patch"):
            in_binary = True
        elif line.startswith(b"@@"):
            header = _HUNK_HEADER.match(line)
            if header is not None:
                lines[index] = line[: header.end()]
    return b"\n".join(lines)


class PatchSnapshotError(ValueError):
    """A safe reason code; never include source content or host paths."""


class PatchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    base_sha: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0, le=PATCH_BYTE_LIMIT)
    patch_base64: str = Field(max_length=4 * ((PATCH_BYTE_LIMIT + 2) // 3))

    @model_validator(mode="after")
    def exact_bytes(self):
        try:
            raw = base64.b64decode(self.patch_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid snapshot encoding") from None
        if len(raw) != self.byte_count or sha256(raw).hexdigest() != self.sha256:
            raise ValueError("snapshot bytes do not match attestation")
        return self

    @classmethod
    def freeze(cls, raw: bytes, *, task_id: str, run_id: str, base_sha: str):
        return cls(
            task_id=task_id,
            run_id=run_id,
            base_sha=base_sha,
            sha256=sha256(raw).hexdigest(),
            byte_count=len(raw),
            patch_base64=base64.b64encode(raw).decode("ascii"),
        )


def capture_patch(root: Path, base_sha: str) -> bytes:
    """Capture the same deterministic binary diff before/after checks and at read time.

    Git binary patches and non-UTF-8 text stay bytes. Never enable external diff
    commands or textconv, and never follow an untracked symlink or hardlink.
    """
    options = [
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--no-renames",
        "--full-index",
        "-U3",
        "--inter-hunk-context=0",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    ]
    with tempfile.TemporaryFile() as output:
        subprocess.run(
            ["git", "-C", str(root), *STABLE_GIT_CONFIG, "diff", *options, base_sha, "--"],
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
        if output.tell() > PATCH_BYTE_LIMIT:
            raise PatchSnapshotError("result_patch_too_large")
        others = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout.split(b"\0")
        for raw_path in sorted(filter(None, others)):
            path = Path(raw_path.decode("utf-8", errors="strict"))
            if path.is_absolute() or ".." in path.parts:
                raise PatchSnapshotError("result_contains_unsupported_file")
            current = root
            for part in path.parts:
                current /= part
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise PatchSnapshotError("result_contains_unsupported_file")
            info = current.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > PATCH_BYTE_LIMIT
            ):
                raise PatchSnapshotError("result_contains_unsupported_file")
            completed = subprocess.run(
                [
                    "git",
                    "--literal-pathspecs",
                    "-C",
                    str(root),
                    *STABLE_GIT_CONFIG,
                    "diff",
                    "--no-index",
                    *options,
                    "--",
                    "/dev/null",
                    str(path),
                ],
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if completed.returncode not in {0, 1}:
                raise PatchSnapshotError("result_patch_unavailable")
            if output.tell() > PATCH_BYTE_LIMIT:
                raise PatchSnapshotError("result_patch_too_large")
        output.seek(0)
        raw = output.read(PATCH_BYTE_LIMIT + 1)
    if len(raw) > PATCH_BYTE_LIMIT:
        raise PatchSnapshotError("result_patch_too_large")
    # The size limit applies to the raw Git output; normalization only removes
    # display-only hunk heading bytes afterwards.
    return _normalize_hunk_headers(raw)


def snapshot_status(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    run_id: str | None,
    base_sha: str | None,
    snapshot: PatchSnapshot,
) -> str:
    """Observe only; a read must never transition tasks or regenerate accepted bytes."""
    if (snapshot.task_id, snapshot.run_id, snapshot.base_sha) != (task_id, run_id, base_sha):
        return "BINDING_MISMATCH"
    row = connection.execute("SELECT * FROM workspaces WHERE task_id=?", (task_id,)).fetchone()
    if row is None or row["writer_token"] or row["base_sha"] != base_sha:
        return "UNAVAILABLE"
    try:
        from personal_ai_orchestrator.worktree_manager import WorktreeManager

        root = Path(row["worktree_path"])
        WorktreeManager(root.parent).adopt(
            repo_path=Path(row["repo_path"]),
            task_id=task_id,
            worktree_path=root,
            branch=row["branch"],
            base_sha=base_sha,
        )
        # Two reads reject an unstable observation rather than showing a pass.
        current = capture_patch(root, base_sha)
        if current != capture_patch(root, base_sha):
            return "UNAVAILABLE"
        if sha256(current).hexdigest() == snapshot.sha256:
            return "CURRENT"
        # Legacy snapshots may still carry hunk heading text from a custom
        # diff driver. Compare with that display-only field removed on both
        # sides without ever rewriting the snapshot's attested bytes.
        stored = base64.b64decode(snapshot.patch_base64, validate=True)
        return "CURRENT" if current == _normalize_hunk_headers(stored) else "STALE"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "UNAVAILABLE"
