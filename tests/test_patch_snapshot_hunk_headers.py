"""Hunk-header section text from custom diff drivers never changes patch identity.

``diff.<driver>.xfuncname``/``funcname`` only alters the human-readable heading
after the second ``@@`` of each hunk header. These real-Git regressions cover:
custom drivers, verification-time driver changes, plain ``git apply`` byte
exactness, lookalike source lines and control bytes, legacy snapshots saved
with heading text, and rejection of real source changes.
"""

import base64
import os
import re
import sqlite3
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from personal_ai_orchestrator.patch_snapshot import (
    PatchSnapshot,
    _normalize_hunk_headers,
    capture_patch,
    snapshot_status,
)
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from personal_ai_orchestrator.worktree_manager import WorktreeManager
from tests.test_dispatch_executor import _git, make_main_repo

# The exact option list capture_patch uses, for raw (unnormalized) comparisons.
_RAW_OPTIONS = (
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
)

DRIVER_SETTINGS = [
    ("diff.pydriver.xfuncname", "^def .*"),
    ("diff.pydriver.funcname", "^def .*"),
]

_HEADER_ONLY = re.compile(rb"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")

CODE_BASE = (
    b"def beta():\n"
    b"    one = 1\n"
    b"    two = 2\n"
    b"    three = 3\n"
    b"    return 2\n" + b"# filler\n" * 10 + b"def gamma():\n"
    b"    one = 1\n"
    b"    two = 2\n"
    b"    three = 3\n"
    b"    return 3\n"
)

CODE_CHANGED = CODE_BASE.replace(b"    return 2\n", b"    return 20\n").replace(
    b"    return 3\n", b"    return 30\n"
)

# Exact raw-Git hunk headers (heading text included) for code.py changes.
BETA_HEADER = b"@@ -2,7 +2,7 @@ def beta():"
GAMMA_HEADER = b"@@ -17,4 +17,4 @@ def gamma():"

# Lines that look like hunk headers plus CR/VT/FF control bytes in real content.
LOOKALIKE_BASE = (
    b"intro line\n"
    b"@@ -1 +1 @@\r\n"
    b"@@ -2 +2 @@\x0bstatic void f(){}\n"
    b"@@ -3 +3 @@\x0cbody\n"
    b"trailing line\n"
)
LOOKALIKE_CHANGED = LOOKALIKE_BASE + b"appended line\n"


def _repo_with_changes(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    root = make_main_repo(tmp_path / "repo")
    (root / ".gitattributes").write_bytes(b"*.py diff=pydriver\n")
    (root / "code.py").write_bytes(CODE_BASE)
    (root / "lookalike.txt").write_bytes(LOOKALIKE_BASE)
    (root / "legacy.txt").write_bytes(b"old \xff\n")
    (root / "tracked.bin").write_bytes(b"before\x00\xff")
    (root / "script.sh").write_bytes(b"#!/bin/sh\necho old\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "driver regression baseline")
    base = _git(root, "rev-parse", "HEAD")
    expected = {
        "code.py": CODE_CHANGED,
        "lookalike.txt": LOOKALIKE_CHANGED,
        "legacy.txt": b"new \xfe\n",
        "tracked.bin": b"after\x00\xfe",
    }
    for name, content in expected.items():
        (root / name).write_bytes(content)
    # Real mode change (header lines must survive normalization byte for byte).
    (root / "script.sh").chmod(0o755)
    return root, base, expected


def _raw_diff(root: Path, base: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), "diff", *_RAW_OPTIONS, base, "--"],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def _clean_consumer(tmp_path: Path, root: Path) -> Path:
    clone = tmp_path / "consumer"
    environment = dict(os.environ)
    environment.update({"HOME": str(tmp_path / "home"), "GIT_CONFIG_NOSYSTEM": "1"})
    (tmp_path / "home").mkdir(exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(root), str(clone)],
        check=True,
        env=environment,
        timeout=30,
    )
    return clone


@pytest.mark.parametrize("key,value", DRIVER_SETTINGS)
def test_driver_heading_text_is_stripped_but_raw_diff_carries_it(tmp_path, key, value):
    root, base, _ = _repo_with_changes(tmp_path)
    baseline_root, baseline_base, _ = _repo_with_changes(tmp_path / "baseline")
    baseline = capture_patch(baseline_root, baseline_base)
    _git(root, "config", key, value)
    raw = _raw_diff(root, base)
    # The unnormalized Git output really carries the driver heading text...
    assert BETA_HEADER in raw
    assert GAMMA_HEADER in raw
    patch = capture_patch(root, base)
    # ...while the captured bytes are identical to the driver-free baseline.
    assert patch == baseline
    assert b"def beta():" not in patch
    assert b"def gamma():" not in patch
    for line in patch.split(b"\n"):
        if line.startswith(b"@@"):
            assert _HEADER_ONLY.fullmatch(line)
    assert _normalize_hunk_headers(raw) == patch


def test_normalization_only_touches_real_hunk_headers():
    # Heading text removed, line ranges and body kept.
    assert (
        _normalize_hunk_headers(b"@@ -1,2 +1,2 @@ def foo():\n+new\n") == b"@@ -1,2 +1,2 @@\n+new\n"
    )
    # Bare headers stay untouched (idempotent).
    bare = b"@@ -1 +1 @@\n+new\n"
    assert _normalize_hunk_headers(bare) == bare
    # A lookalike after CR is mid-line, not a header line start.
    cr = b"old\r@@ -1 +1 @@ looks real\n"
    assert _normalize_hunk_headers(cr) == cr
    # Context lines keep their leading space (and all trailing bytes).
    context = b"@@ -1 +1 @@\n @@ -1 +1 @@ source line\r\n+edit\n"
    assert _normalize_hunk_headers(context) == context
    # Binary payloads may contain arbitrary base85 lines starting with @@.
    binary = (
        b"diff --git a/b.bin b/b.bin\n"
        b"index 1234567..89abcde 100644\n"
        b"GIT binary patch\n"
        b"@@ -1 +1 @@ fake header data\n"
        b"literal 4\n"
        b"LcmZQz@\n"
        b"\n"
        b"@@ -1 x 1 @@ not a real header outside the payload\n"
    )
    assert _normalize_hunk_headers(binary) == binary


def test_exact_bytes_apply_plainly_with_driver_active(tmp_path):
    root, base, expected = _repo_with_changes(tmp_path)
    _git(root, "config", "diff.pydriver.xfuncname", "^def .*")
    patch = capture_patch(root, base)
    clone = _clean_consumer(tmp_path, root)
    for args in (("apply", "--check", "--binary", "-"), ("apply", "--binary", "-")):
        subprocess.run(
            ["git", "-C", str(clone), *args], input=patch, check=True, capture_output=True
        )
    for name, content in expected.items():
        assert (clone / name).read_bytes() == content, name
    assert ((clone / "script.sh").stat().st_mode & 0o111) == 0o111


@pytest.mark.parametrize("key,value", DRIVER_SETTINGS)
def test_driver_change_during_verification_is_accepted(tmp_path, key, value):
    root, base, _ = _repo_with_changes(tmp_path)
    frozen = capture_patch(root, base)
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        profile=VerifierProfile(
            name="driver-only",
            commands=(VerifierCommand(name="set driver", argv=("git", "config", key, value)),),
        ),
        task_id="driver-task",
        run_id="driver-run",
    )
    assert result.passed
    assert result.patch_snapshot is not None
    raw = base64.b64decode(result.patch_snapshot.patch_base64, validate=True)
    assert raw == frozen
    assert sha256(raw).hexdigest() == result.patch_snapshot.sha256


@pytest.fixture
def legacy_workspace(tmp_path):
    root, base, expected = _repo_with_changes(tmp_path)
    # The host's stored snapshot predates heading normalization: raw Git bytes
    # captured while the custom driver was active.
    _git(root, "config", "diff.pydriver.xfuncname", "^def .*")
    legacy_raw = _raw_diff(root, base)
    assert BETA_HEADER in legacy_raw
    manager = WorktreeManager(tmp_path / "managed")
    worktree = manager.create(repo_path=root, task_id="task-1", base_sha=base)
    for name, content in expected.items():
        (worktree.worktree_path / name).write_bytes(content)
    (worktree.worktree_path / "script.sh").chmod(0o755)
    # The worktree shares the repository config, so its raw diff carries TEXT too.
    legacy_raw = _raw_diff(worktree.worktree_path, base)
    assert b"def beta():" in legacy_raw
    snapshot = PatchSnapshot.freeze(legacy_raw, task_id="task-1", run_id="run-1", base_sha=base)
    connection = sqlite3.connect(tmp_path / "state.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE workspaces ("
        "task_id TEXT PRIMARY KEY, writer_token TEXT, base_sha TEXT,"
        " worktree_path TEXT, repo_path TEXT, branch TEXT)"
    )
    connection.execute(
        "INSERT INTO workspaces VALUES (?, NULL, ?, ?, ?, ?)",
        ("task-1", base, str(worktree.worktree_path), str(root), worktree.branch),
    )
    connection.commit()
    try:
        yield connection, snapshot, worktree.worktree_path, base, expected
    finally:
        connection.close()


def test_legacy_text_snapshot_is_current_without_rewriting_attestation(tmp_path, legacy_workspace):
    connection, snapshot, worktree, base, _ = legacy_workspace
    current = capture_patch(worktree, base)
    assert current != base64.b64decode(snapshot.patch_base64, validate=True)
    assert (
        snapshot_status(
            connection,
            task_id="task-1",
            run_id="run-1",
            base_sha=base,
            snapshot=snapshot,
        )
        == "CURRENT"
    )
    # The old snapshot's raw bytes, base64, byte_count and SHA are untouched.
    stored = base64.b64decode(snapshot.patch_base64, validate=True)
    assert len(stored) == snapshot.byte_count
    assert sha256(stored).hexdigest() == snapshot.sha256
    assert b"def beta():" in stored


def test_legacy_identity_and_lock_checks_are_unchanged(tmp_path, legacy_workspace):
    connection, snapshot, worktree, base, _ = legacy_workspace
    kwargs = {"run_id": "run-1", "base_sha": base, "snapshot": snapshot}
    assert snapshot_status(connection, task_id="other", **kwargs) == "BINDING_MISMATCH"
    connection.execute("UPDATE workspaces SET writer_token='held'")
    connection.commit()
    assert snapshot_status(connection, task_id="task-1", **kwargs) == "UNAVAILABLE"
    connection.execute("UPDATE workspaces SET writer_token=NULL, branch='wrong'")
    connection.commit()
    assert snapshot_status(connection, task_id="task-1", **kwargs) == "UNAVAILABLE"


def test_legacy_snapshot_rejects_real_source_change(tmp_path, legacy_workspace):
    connection, snapshot, worktree, base, expected = legacy_workspace
    (worktree / "legacy.txt").write_bytes(b"really changed \xff\n")
    assert (
        snapshot_status(
            connection,
            task_id="task-1",
            run_id="run-1",
            base_sha=base,
            snapshot=snapshot,
        )
        == "STALE"
    )
    # Restoring the bytes returns to CURRENT (heading-only difference forgiven).
    (worktree / "legacy.txt").write_bytes(expected["legacy.txt"])
    assert (
        snapshot_status(
            connection,
            task_id="task-1",
            run_id="run-1",
            base_sha=base,
            snapshot=snapshot,
        )
        == "CURRENT"
    )
