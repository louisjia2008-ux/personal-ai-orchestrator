"""Explicit task baselines without changing the owner's HEAD, index, or files."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

BaselineMode = Literal["COMMITTED_HEAD", "WORKING_TREE_SNAPSHOT"]
MAX_FILES = 10000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024


class BaselineError(ValueError):
    """Only a safe reason code may cross the local API."""


class BaselineReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: BaselineMode
    source_head: str
    source_branch: str
    source_fingerprint: str | None
    base_sha: str
    snapshot_ref: str | None = None


class BaselinePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_head: str
    source_branch: str
    fingerprint: str
    changed_file_count: int
    untracked_file_count: int
    snapshot_available: bool
    snapshot_error: str | None = None


def _git(
    root: Path,
    *args: str,
    data: bytes | None = None,
    index: str | None = None,
    commit_time: str | None = None,
) -> bytes:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if index is not None:
        env["GIT_INDEX_FILE"] = index
    if commit_time is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{commit_time} +0000"
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "PAO baseline snapshot"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "baseline@local.invalid"
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            input=data,
            capture_output=True,
            check=True,
            timeout=30,
            env=env,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        raise BaselineError("baseline_git_unavailable") from None


def _sensitive(path: bytes) -> bool:
    parts = [os.fsdecode(p).lower() for p in path.split(b"/")]
    return any(
        p in {".git", ".ssh", ".aws", "auth.json", "credentials.json", "credentials"}
        or p == ".env"
        or (p.startswith(".env.") and p not in {".env.example", ".env.sample", ".env.template"})
        or p.endswith((".pem", ".p12", ".pfx", ".key"))
        for p in parts
    )


def _read(root: Path, path: bytes) -> tuple[bytes, bytes] | None:
    """Read a regular file through directory FDs; never traverse links."""
    parts = path.split(b"/")
    if any(p in {b"", b".", b"..", b".git"} for p in parts):
        raise BaselineError("baseline_unsupported_path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
            try:
                os.stat(b".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BaselineError("baseline_nested_repository")
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise BaselineError("baseline_unsupported_file")
            if before.st_size > MAX_FILE_BYTES:
                raise BaselineError("baseline_file_too_large")
            chunks = []
            size = 0
            while chunk := os.read(file_fd, min(65536, MAX_FILE_BYTES + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise BaselineError("baseline_file_too_large")
            after = os.fstat(file_fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise BaselineError("baseline_changed")
            return (b"100755" if before.st_mode & 0o111 else b"100644", b"".join(chunks))
        finally:
            os.close(file_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise BaselineError("baseline_unsupported_file") from None
    finally:
        os.close(fd)


@dataclass
class _Capture:
    preview: BaselinePreview
    entries: dict[bytes, tuple[bytes, bytes]]
    blobs: dict[bytes, bytes]


def _capture(root: Path) -> _Capture:
    root = root.resolve(strict=True)
    head = _git(root, "rev-parse", "--verify", "HEAD").strip().decode("ascii")
    branch = os.fsdecode(_git(root, "branch", "--show-current").strip())
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    index = _git(root, "ls-files", "--stage", "-z")
    others = set(
        filter(None, _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    )
    original = {}
    for entry in filter(None, _git(root, "ls-tree", "-r", "-z", "HEAD").split(b"\0")):
        meta, name = entry.split(b"\t", 1)
        mode, _, oid = meta.split()
        original[name] = (mode, oid)
    names = set(original) | others
    index_entries = {}
    for entry in filter(None, index.split(b"\0")):
        meta, name = entry.split(b"\t", 1)
        mode, oid, stage = meta.split()
        index_entries[name] = (mode, oid)
        names.add(name)
        if stage != b"0":
            raise BaselineError("baseline_unmerged_index")
    if len(names) > MAX_FILES:
        raise BaselineError("baseline_too_many_files")
    algorithm = _git(root, "rev-parse", "--show-object-format").strip().decode("ascii")
    entries = {}
    blobs = {}
    total = 0
    for name in sorted(names):
        if (
            original.get(name, (None,))[0] == b"160000"
            or index_entries.get(name, (None,))[0] == b"160000"
        ):
            raise BaselineError("baseline_submodules_unsupported")
        current = _read(root, name)
        if current is None:
            continue
        mode, raw = current
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise BaselineError("baseline_too_large")
        oid = (
            hashlib.new(algorithm, b"blob " + str(len(raw)).encode() + b"\0" + raw)
            .hexdigest()
            .encode()
        )
        entries[name] = (mode, oid)
        if entries[name] != original.get(name):
            if _sensitive(name):
                raise BaselineError("baseline_sensitive_path")
            blobs[oid] = raw
    # Checkout filters could change the bytes represented by this snapshot.
    # This bounded first version rejects them instead of promising a false copy.
    config = _git(root, "config", "--list", "--null")
    if any(
        p.lower() in {b"core.autocrlf\ntrue", b"core.autocrlf\ninput"} for p in config.split(b"\0")
    ):
        raise BaselineError("baseline_checkout_conversion")
    attrs = _git(
        root,
        "check-attr",
        "-z",
        "--stdin",
        "filter",
        "text",
        "eol",
        "working-tree-encoding",
        data=b"\0".join(sorted(names)) + b"\0",
    ).split(b"\0")
    if any(value not in {b"unspecified", b"unset"} for value in attrs[2::3] if value):
        raise BaselineError("baseline_checkout_conversion")
    changed = {
        n
        for n in names
        if entries.get(n) != original.get(n) or index_entries.get(n) != original.get(n)
    }
    identity = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    manifest = b"\0".join(
        n + b"\0" + mode + b" " + oid for n, (mode, oid) in sorted(entries.items())
    )
    token = hashlib.sha256(
        b"\0".join(
            [
                os.fsencode(root),
                identity,
                head.encode(),
                os.fsencode(branch),
                status,
                index,
                manifest,
            ]
        )
    ).hexdigest()
    if head.encode() != _git(root, "rev-parse", "HEAD").strip():
        raise BaselineError("baseline_changed")
    return _Capture(
        BaselinePreview(
            source_head=head,
            source_branch=branch,
            fingerprint=token,
            changed_file_count=len(changed),
            untracked_file_count=len(others),
            snapshot_available=True,
        ),
        entries,
        blobs,
    )


def preview_baseline(root: Path) -> BaselinePreview:
    try:
        return _capture(root).preview
    except BaselineError as error:
        head = _git(root, "rev-parse", "HEAD").strip().decode("ascii")
        branch = os.fsdecode(_git(root, "branch", "--show-current").strip())
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
        return BaselinePreview(
            source_head=head,
            source_branch=branch,
            fingerprint=hashlib.sha256(head.encode() + status + str(error).encode()).hexdigest(),
            changed_file_count=sum(1 for item in status.split(b"\0") if item[:2].strip()),
            untracked_file_count=sum(1 for item in status.split(b"\0") if item.startswith(b"?? ")),
            snapshot_available=False,
            snapshot_error=str(error),
        )


def select_baseline(
    root: Path, *, mode: BaselineMode, expected_fingerprint: str | None, request_id: str
) -> BaselineReceipt:
    preview = preview_baseline(root)
    if expected_fingerprint is not None and preview.fingerprint != expected_fingerprint:
        raise BaselineError("baseline_changed")
    if mode == "COMMITTED_HEAD":
        return BaselineReceipt(
            mode=mode,
            source_head=preview.source_head,
            source_branch=preview.source_branch,
            source_fingerprint=expected_fingerprint,
            base_sha=preview.source_head,
        )
    if expected_fingerprint is None:
        raise BaselineError("baseline_preview_required")
    if not preview.snapshot_available:
        raise BaselineError(preview.snapshot_error or "baseline_snapshot_unavailable")
    captured = _capture(root)
    if captured.preview.fingerprint != expected_fingerprint:
        raise BaselineError("baseline_changed")
    with tempfile.TemporaryDirectory(prefix="pao-baseline-") as temp:
        index = str(Path(temp) / "index")
        _git(root, "read-tree", "--empty", index=index)
        for oid, raw in captured.blobs.items():
            if _git(root, "hash-object", "-w", "--no-filters", "--stdin", data=raw).strip() != oid:
                raise BaselineError("baseline_object_mismatch")
        payload = b"".join(
            mode + b" " + oid + b"\t" + name + b"\0"
            for name, (mode, oid) in sorted(captured.entries.items())
        )
        _git(root, "update-index", "-z", "--index-info", data=payload, index=index)
        tree = _git(root, "write-tree", index=index).strip().decode("ascii")
    # commit-tree bypasses hooks and signing; identity contains no owner details.
    key = hashlib.sha256(request_id.encode()).hexdigest()
    stamp = _git(root, "show", "-s", "--format=%ct", preview.source_head).strip().decode("ascii")
    commit = (
        _git(
            root,
            "-c",
            "user.name=PAO baseline snapshot",
            "-c",
            "user.email=baseline@local.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit-tree",
            tree,
            "-p",
            preview.source_head,
            data=f"PAO working tree baseline {key}\n{expected_fingerprint}\n".encode(),
            commit_time=stamp,
        )
        .strip()
        .decode("ascii")
    )
    # Keep local objects reachable, independently of task branch creation/retries.
    if _capture(root).preview.fingerprint != expected_fingerprint:
        raise BaselineError("baseline_changed")
    reference = f"refs/pao/task-baselines/{key}"
    # Creation is serialized by Git. Replays normally resolve from the task audit.
    try:
        _git(root, "update-ref", reference, commit, "0" * len(commit))
    except BaselineError:
        existing = _git(root, "rev-parse", "--verify", reference).strip().decode("ascii")
        if existing != commit:
            raise BaselineError("baseline_request_conflict") from None
        commit = existing
    return BaselineReceipt(
        mode=mode,
        source_head=preview.source_head,
        source_branch=preview.source_branch,
        source_fingerprint=expected_fingerprint,
        base_sha=commit,
        snapshot_ref=reference,
    )
