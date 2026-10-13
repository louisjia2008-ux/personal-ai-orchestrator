"""Bounded host-report delivery and safe, manual-only continuation exports.

The existing audit transition and verification journal remain authoritative.
This module is an observational projection: it never reads worker result_json,
provider output, project files, or external conversation/session storage.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import unicodedata
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult

RESULT_VERSION = 1
FILE_LIMIT = 100
TEST_LIMIT = 50
GOAL_LIMIT = 1024
EVIDENCE_BYTE_LIMIT = 1024 * 1024
AUDIT_CHARACTER_LIMIT = 65536
AUDIT_EVENT_LIMIT = 200
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_EVIDENCE = re.compile(r"verify-[a-f0-9]{24}\Z")
_PASS_PREFIX = "deterministic verification passed: "
_SENSITIVE = re.compile(
    r"(?i)(?:\b(?:password|passwd|secret|token|api[ _-]?key|authorization|"
    r"credential|cookie|bearer|private[ _-]?key|session)\b|"
    r"(?:sk|ghp|github_pat|xox[baprs])[-_]|AKIA[A-Z0-9]|"
    r"[A-Za-z0-9_+=-]{24,}|[\\/]|\b[A-Za-z]:|\b\S+@\S+|[<>`])"
)


def _digest(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _goal(value: str) -> tuple[str, bool, bool]:
    """Conservatively withhold suspicious lines; never export a partial secret."""
    truncated = len(value) > GOAL_LIMIT
    if truncated:
        return "Original goal exceeds the safe export limit; review it in PAO.", True, True
    redacted = False
    lines = []
    for line in value.splitlines():
        # This also strips bidi controls and zero-width formatting characters.
        if _SENSITIVE.search(line) or any(unicodedata.category(c).startswith("C") for c in line):
            lines.append("[Goal detail withheld for safe export]")
            redacted = True
        else:
            lines.append(" ".join(line.split()))
    return " ".join(lines).strip() or "Review the original goal in PAO.", redacted, False


def _safe_changed_file(value: str) -> bool:
    if len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*", value):
        return False
    parts = value.split("/")
    return not any(
        part in {".", "..", ".codex", ".ssh", ".aws", ".env"} or _SENSITIVE.search(part)
        for part in parts
    )


def _safe_test_name(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _().:+-]{0,79}", value)
        and not _SENSITIVE.search(value)
    )


def _audit(connection: sqlite3.Connection, task_id: str, event_type: str) -> sqlite3.Row | None:
    # Project only small host-owned fields, never select payload_json itself.
    # Invalid JSON becomes NULL and cannot authorize delivery.
    return connection.execute(
        """WITH bounded AS (
        SELECT audit_id, CASE WHEN length(payload_json)<=? THEN payload_json END AS payload_json
        FROM audit_events WHERE task_id=? AND event_type=? ORDER BY audit_id DESC LIMIT 1
        ) SELECT audit_id,
        substr(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,
            '$.run_id'),1,129) AS run_id,
        substr(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,
            '$.status'),1,81) AS status
        FROM bounded""",
        (AUDIT_CHARACTER_LIMIT, task_id, event_type),
    ).fetchone()


def _verification_transition(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    # Match REPORT's authoritative transition, but do not load the entire audit
    # history or expose free-form failure reasons. The latest verification
    # outcome is required, so an older pass cannot hide a later failed attempt.
    return connection.execute(
        """WITH bounded AS (
        SELECT audit_id, CASE WHEN length(payload_json)<=? THEN payload_json END AS payload_json
        FROM audit_events WHERE task_id=? AND event_type='TASK_STATE_CHANGED'
        ORDER BY audit_id DESC LIMIT ?
        ) SELECT audit_id,
        substr(json_extract(payload_json,'$.to'),1,81) AS to_state,
        substr(json_extract(payload_json,'$.reason'),1,161) AS reason,
        substr(json_extract(payload_json,'$.verification_evidence_sha256'),1,65) AS evidence_sha256
        FROM bounded WHERE
        CASE WHEN json_valid(payload_json) THEN
            json_extract(payload_json,'$.from')='VERIFYING' ELSE 0 END
        ORDER BY audit_id DESC LIMIT 1""",
        (AUDIT_CHARACTER_LIMIT, task_id, AUDIT_EVENT_LIMIT),
    ).fetchone()


def _submission(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """WITH bounded AS (
        SELECT audit_id, CASE WHEN length(payload_json)<=? THEN payload_json END AS payload_json
        FROM audit_events WHERE task_id=? AND event_type='TASK_SUBMITTED'
        ORDER BY audit_id LIMIT 1
        ) SELECT audit_id,
        substr(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,
            '$.base_sha'),1,65) AS base_sha,
        substr(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,
            '$.request_id'),1,129) AS request_id
        FROM bounded""",
        (AUDIT_CHARACTER_LIMIT, task_id),
    ).fetchone()


def _read_evidence(
    journal: VerificationEvidenceJournal, evidence_id: str
) -> tuple[VerificationResult | None, str | None, str | None]:
    """Read one bounded regular journal file, refusing symlinks and special files."""
    directory_fd = None
    fd = None
    try:
        directory_fd = os.open(journal.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(
            f"{evidence_id}.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, None, "EVIDENCE_UNAVAILABLE"
        if before.st_size > EVIDENCE_BYTE_LIMIT:
            return None, None, "EVIDENCE_TOO_LARGE"
        with os.fdopen(fd, "rb") as handle:
            fd = None
            raw = handle.read(EVIDENCE_BYTE_LIMIT + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > EVIDENCE_BYTE_LIMIT:
            return None, None, "EVIDENCE_TOO_LARGE"
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None, None, "EVIDENCE_CHANGED_DURING_READ"
        result = VerificationResult.model_validate_json(raw)
        if result.evidence_id != evidence_id:
            return None, None, "EVIDENCE_ID_MISMATCH"
        return result, sha256(raw).hexdigest(), None
    except (OSError, ValueError, ValidationError):
        return None, None, "EVIDENCE_UNAVAILABLE"
    finally:
        if fd is not None:
            os.close(fd)
        if directory_fd is not None:
            os.close(directory_fd)


def result_delivery(
    connection: sqlite3.Connection,
    *,
    task: dict[str, Any],
    store_id: str,
    journal: VerificationEvidenceJournal | None,
) -> dict[str, Any]:
    """Return a content-addressed safe report inside the caller's DB snapshot.

    COMPLETE means the host verification report is fully delivered, not that
    arbitrary user intent, publication, or a native/provider acceptance passed.
    """
    task_id = task["task_id"]
    row = connection.execute(
        "SELECT substr(intent,1,?) AS intent, substr(base_sha,1,65) AS base_sha "
        "FROM tasks WHERE task_id=?",
        (GOAL_LIMIT + 1, task_id),
    ).fetchone()
    goal, redacted, goal_truncated = _goal(row["intent"])
    base = row["base_sha"]
    base = base if isinstance(base, str) and _COMMIT.fullmatch(base) else None
    run = connection.execute(
        "SELECT substr(run_id,1,129) AS run_id, substr(status,1,81) AS status "
        "FROM runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    run_id = run["run_id"] if run and _ID.fullmatch(run["run_id"]) else None
    report: dict[str, Any] = {
        "schema_version": RESULT_VERSION,
        "kind": "HOST_VERIFICATION_REPORT",
        "binding": {
            "store_id": store_id,
            "task_id": task_id,
            "run_id": run_id,
            "base_commit": base,
            "result_commit": None,
            "submission_event_id": None,
            "verification_event_id": None,
            "verification_evidence_sha256": None,
        },
        "task_state": task["state"],
        "goal": goal,
        "goal_redacted": redacted,
        "completeness": "MISSING",
        "verification": {"status": "NOT_OBSERVED", "evidence_id": None},
        "changed_files": [],
        "tests": [],
        "artifacts": [],
        "limitations": ["RESULT_COMMIT_NOT_RECORDED", "HOST_REPORT_ONLY"],
        "truncated": {"goal": goal_truncated, "changed_files": False, "tests": False},
    }
    state = task["state"]
    transition = _verification_transition(connection, task_id)
    if state in {"BLOCKED", "FAILED", "CANCELLED"}:
        report["completeness"] = "FAILED"
        report["verification"]["status"] = (
            "FAILED_VERIFICATION"
            if transition and transition["to_state"] == "BLOCKED"
            else "NOT_OBSERVED"
        )
        report["limitations"].append("TASK_REQUIRES_OWNER_REVIEW")
    elif state == "VERIFYING":
        report["verification"]["status"] = "IN_PROGRESS"
    elif state in {"VERIFIED", "COMPLETED"}:
        report["verification"]["status"] = "VERIFIED_EVIDENCE_UNAVAILABLE"
        reason = transition["reason"] if transition else None
        evidence_id = (
            reason[len(_PASS_PREFIX) :]
            if isinstance(reason, str) and reason.startswith(_PASS_PREFIX)
            else ""
        )
        started = _audit(connection, task_id, "RUN_STARTED")
        finished = _audit(connection, task_id, "RUN_FINISHED")
        submitted = _submission(connection, task_id)
        bound = bool(
            base
            and run_id
            and run["status"] == "FINISHED"
            and transition
            and transition["to_state"] == "VERIFIED"
            and _EVIDENCE.fullmatch(evidence_id)
            and submitted
            and submitted["request_id"] == task["request_id"]
            and submitted["base_sha"] == base
            and started
            and finished
            and started["run_id"] == finished["run_id"] == run_id
            and finished["status"] == "FINISHED"
            and submitted["audit_id"]
            < started["audit_id"]
            < finished["audit_id"]
            < transition["audit_id"]
        )
        if not bound:
            report["limitations"].append("IMMUTABLE_BINDING_UNAVAILABLE")
        elif not (
            isinstance(transition["evidence_sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", transition["evidence_sha256"])
        ):
            report["limitations"].append("EVIDENCE_DIGEST_UNATTESTED")
        elif journal is None:
            report["limitations"].append("EVIDENCE_UNAVAILABLE")
        else:
            report["binding"]["submission_event_id"] = submitted["audit_id"]
            report["binding"]["verification_event_id"] = transition["audit_id"]
            report["binding"]["verification_evidence_sha256"] = transition["evidence_sha256"]
            report["verification"]["evidence_id"] = evidence_id
            evidence, digest, error = _read_evidence(journal, evidence_id)
            if error:
                report["limitations"].append(error)
                if error == "EVIDENCE_TOO_LARGE":
                    report["completeness"] = "TRUNCATED"
            elif digest != transition["evidence_sha256"]:
                report["limitations"].append("EVIDENCE_DIGEST_MISMATCH")
            elif evidence is not None:
                _project_evidence(report, evidence, digest)
    report["result_id"] = f"result-{_digest(report)}"
    return report


def _project_evidence(report: dict[str, Any], evidence: VerificationResult, digest: str) -> None:
    # Expose names/outcomes only. argv, stdout, stderr and failure_reason are
    # deliberately absent even when they look like a useful final answer.
    report["artifacts"] = [
        {
            "kind": "VERIFICATION_EVIDENCE",
            "reference": f"pao:verification:{evidence.evidence_id}",
            "sha256": digest,
        }
    ]
    safe_files = [path for path in evidence.changed_paths[:FILE_LIMIT] if _safe_changed_file(path)]
    tests = [
        {"name": stage.name, "status": "PASSED" if stage.passed else "FAILED"}
        for stage in evidence.stages[:TEST_LIMIT]
        if _safe_test_name(stage.name)
    ]
    report["changed_files"] = safe_files
    report["tests"] = tests
    report["truncated"]["changed_files"] = len(safe_files) != len(evidence.changed_paths)
    report["truncated"]["tests"] = len(tests) != len(evidence.stages)
    passed = bool(
        evidence.passed
        and evidence.stages
        and not evidence.unexpected_paths
        and all(stage.passed for stage in evidence.stages)
    )
    if not passed:
        report["completeness"] = "FAILED"
        report["verification"]["status"] = "EVIDENCE_CONFLICT"
        report["limitations"].append("EVIDENCE_CONFLICT")
    else:
        report["verification"]["status"] = "VERIFIED"
        report["completeness"] = (
            "TRUNCATED"
            if (any(report["truncated"].values()) or report["goal_redacted"])
            else "COMPLETE"
        )
    if report["completeness"] == "TRUNCATED":
        report["limitations"].append("SAFE_EXPORT_OMISSIONS")


def continuation_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Copyable data, never an automatic retry/resume command or provider handoff."""
    verified = report["verification"]["status"] == "VERIFIED"
    completed = ["Host deterministic verification passed."] if verified else []
    pending = []
    if report["completeness"] != "COMPLETE":
        pending.append(f"Resolve result delivery state: {report['completeness']}.")
    pending.append(
        "Review the requested outcome and any acceptance checks beyond the host verifier."
    )
    next_step = (
        "Review the delivered evidence and changed files in PAO before deciding the next action."
        if verified
        else "Review this task's current state and missing or failed evidence in PAO; "
        "decide explicitly whether any further execution is appropriate."
    )
    summary = {
        "schema_version": RESULT_VERSION,
        "result_id": report["result_id"],
        "binding": report["binding"],
        "goal": report["goal"],
        "completed": completed,
        "pending": pending,
        "evidence": report["artifacts"],
        "next_step": next_step,
        "manual_only": True,
    }
    evidence = (
        ", ".join(f"{item['reference']} (sha256:{item['sha256']})" for item in report["artifacts"])
        or "Not available"
    )
    summary["text"] = "\n".join(
        [
            "PAO manual continuation summary v1",
            f"Store: {report['binding']['store_id']}",
            f"Task: {report['binding']['task_id']}",
            f"Run: {report['binding']['run_id'] or 'Not recorded'}",
            f"Base commit: {report['binding']['base_commit'] or 'Not recorded'}",
            f"Result: {report['result_id']}",
            f"Report delivery: {report['completeness']}",
            f"Goal: {report['goal']}",
            f"Completed: {' '.join(completed) or 'No verified completion recorded.'}",
            f"Pending: {' '.join(pending)}",
            f"Evidence: {evidence}",
            f"Next step: {next_step}",
        ]
    )
    summary["summary_id"] = f"continuation-{_digest(summary)}"
    return summary
