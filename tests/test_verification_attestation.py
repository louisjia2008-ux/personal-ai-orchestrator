from hashlib import sha256

import pytest

from personal_ai_orchestrator.execution_controller import apply_verification_result
from personal_ai_orchestrator.safety_kernel import TaskState
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult
from tests.test_execution_controller import _finish_worker, _running_store


def test_verified_transition_attests_exact_journal_bytes(tmp_path):
    store = _running_store(tmp_path)
    try:
        _finish_worker(store)
        journal = VerificationEvidenceJournal(tmp_path / "evidence")
        result = VerificationResult(
            profile="fixture",
            passed=True,
            changed_paths=(),
            unexpected_paths=(),
            stages=(),
            evidence_id="verify-attested",
        )
        path = journal.append(result)
        expected = sha256(path.read_bytes()).hexdigest()
        apply_verification_result(store, task_id="t1", result=result, evidence_journal=journal)
        event = store.audit_events("t1")[-1]
        assert event["event_type"] == "TASK_STATE_CHANGED"
        assert event["payload"]["to"] == "VERIFIED"
        assert event["payload"]["verification_evidence_sha256"] == expected
        # Later bytes cannot rewrite the originally accepted digest.
        path.write_text(
            result.model_copy(update={"changed_paths": ("other.py",)}).model_dump_json()
        )
        assert sha256(path.read_bytes()).hexdigest() != expected
        assert store.audit_events("t1")[-1]["payload"]["verification_evidence_sha256"] == expected
    finally:
        store.close()


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "A" * 64, "z" * 64])
def test_invalid_attestation_rejects_before_state_or_audit_change(tmp_path, digest):
    store = _running_store(tmp_path)
    try:
        _finish_worker(store)
        task = store.get_task("t1")
        events = store.audit_events("t1")
        with pytest.raises(ValueError, match="invalid verification evidence digest"):
            store.apply_verification_outcome(
                "t1",
                expected_version=task.state_version,
                target=TaskState.VERIFIED,
                reason="fixture",
                verification_evidence_sha256=digest,
            )
        assert store.get_task("t1") == task
        assert store.audit_events("t1") == events
    finally:
        store.close()


def test_missing_evidence_never_attests_unpersisted_result(tmp_path):
    store = _running_store(tmp_path)
    try:
        _finish_worker(store)
        result = VerificationResult(
            profile="fixture",
            passed=True,
            changed_paths=(),
            unexpected_paths=(),
            stages=(),
            evidence_id="verify-not-present",
        )
        apply_verification_result(
            store,
            task_id="t1",
            result=result,
            evidence_journal=VerificationEvidenceJournal(tmp_path / "evidence"),
        )
        event = store.audit_events("t1")[-1]
        assert event["payload"]["to"] == "BLOCKED"
        assert "verification_evidence_sha256" not in event["payload"]
    finally:
        store.close()
