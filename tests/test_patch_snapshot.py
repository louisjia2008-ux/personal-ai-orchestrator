"""Accepted artifacts survive edits/restarts and reject corrupt or foreign bindings."""

import base64
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.local_client_v2 import handle_local_client_v2
from personal_ai_orchestrator.patch_snapshot import (
    PatchSnapshot,
    capture_patch,
    snapshot_status,
)
from personal_ai_orchestrator.project_workflow import task_patch
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import (
    DeterministicVerifier,
    VerifierCommand,
    VerifierProfile,
)
from tests.test_dispatch_executor import _git, make_main_repo
from tests.test_project_workflow import _harness


def _report(service):
    return handle_local_client_v2(
        service,
        {
            "protocol_version": 2,
            "operation": "DETAIL",
            "request_id": "snapshot-read",
            "task_id": "task-1",
        },
    )["payload"]["result"]


def test_stale_observation_is_readonly_and_frozen_export_survives_restart(tmp_path):
    harness, service, request = _harness(tmp_path)
    try:
        harness.run(request)
        original = task_patch(service, "task-1")
        task = service.store.get_task("task-1")
        audit = service.store.audit_events(task.task_id)
        root = Path(service.store.get_workspace(task.task_id).worktree_path)
        original_content = (root / "hello.txt").read_bytes()
        (root / "hello.txt").write_bytes(b"changed after acceptance\n")
        service.verification_journal = VerificationEvidenceJournal(harness.runtime_root)
        result = _report(service)
        assert result["verification"]["status"] == "VERIFICATION_STALE"
        assert result["completeness"] == "COMPLETE"
        assert service.get_task(task.task_id).current_verification_status == "VERIFICATION_STALE"
        assert service.list_tasks().tasks[0].current_verification_status == "VERIFICATION_STALE"
        patch = task_patch(service, task.task_id)
        assert patch["patch_base64"] == original["patch_base64"]
        assert service.store.get_task(task.task_id) == task
        assert service.store.audit_events(task.task_id) == audit
        (root / "hello.txt").write_bytes(original_content)
        assert _report(service)["verification"]["status"] == "VERIFIED"
    finally:
        service.store.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("patch_base64", "invalid%%%"),
        ("byte_count", 1),
        ("sha256", "0" * 64),
    ],
)
def test_snapshot_rejects_corrupt_encoding_count_and_hash(field, value):
    snapshot = PatchSnapshot.freeze(b"\xff\x00\xfe", task_id="t", run_id="r", base_sha="a" * 40)
    payload = snapshot.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        PatchSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field,value", [("task_id", "other"), ("run_id", "other"), ("base_sha", "b" * 40)]
)
def test_snapshot_cannot_bind_another_task_run_or_base(tmp_path, field, value):
    snapshot = PatchSnapshot.freeze(b"", task_id="t", run_id="r", base_sha="a" * 40)
    expected = {"task_id": "t", "run_id": "r", "base_sha": "a" * 40}
    expected[field] = value
    # Wrong identity rejects before consulting any filesystem or DB workspace.
    assert snapshot_status(None, snapshot=snapshot, **expected) == "BINDING_MISMATCH"


def test_checks_that_change_the_code_cannot_create_accepted_snapshot(tmp_path):
    root = make_main_repo(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    command = VerifierCommand(
        name="mutating check",
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('README.md').write_text('changed')",
        ),
    )
    result = DeterministicVerifier().verify(
        root,
        base_sha=base,
        profile=VerifierProfile(name="p", commands=(command,)),
        task_id="t",
        run_id="r",
    )
    assert not result.passed
    assert result.failure_reason == "worktree changed during verification"
    assert result.patch_snapshot is None


def test_corrupt_attested_journal_cannot_export_or_claim_current_verification(tmp_path):
    harness, service, request = _harness(tmp_path)
    try:
        harness.run(request)
        report = _report(service)
        path = service.verification_journal.path_for(report["verification"]["evidence_id"])
        payload = json.loads(path.read_text())
        payload["patch_snapshot"]["task_id"] = "other-task"
        path.write_text(json.dumps(payload))
        assert _report(service)["verification"]["status"] != "VERIFIED"
        with pytest.raises(ControlPlaneError):
            task_patch(service, "task-1")
    finally:
        service.store.close()


def test_binary_and_non_utf8_patch_applies_byte_for_byte(tmp_path):
    root = make_main_repo(tmp_path / "repo")
    (root / "legacy.txt").write_bytes(b"old \xff\n")
    (root / "binary.bin").write_bytes(b"before\x00\xff")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "legacy inputs")
    base = _git(root, "rev-parse", "HEAD")
    expected = {
        "legacy.txt": b"new \xfe\n",
        "binary.bin": b"after\x00\xfe",
        "中文 新文件.txt": b"new \xff\n",
    }
    for name, content in expected.items():
        (root / name).write_bytes(content)
    patch = capture_patch(root, base)
    snapshot = PatchSnapshot.freeze(patch, task_id="t", run_id="r", base_sha=base)
    assert sha256(base64.b64decode(snapshot.patch_base64)).hexdigest() == snapshot.sha256
    clone = tmp_path / "consumer"
    subprocess.run(["git", "clone", "-q", str(root), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "apply", "--binary", "-"], input=patch, check=True)
    assert all((clone / name).read_bytes() == content for name, content in expected.items())
