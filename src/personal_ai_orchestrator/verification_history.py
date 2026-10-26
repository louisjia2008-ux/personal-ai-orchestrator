"""Bounded, task-bound reads of immutable host verification history."""

from __future__ import annotations

import base64
import json
import re

from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.result_delivery import (
    _digest,
    _project_evidence,
    _read_evidence,
    result_delivery,
)

HISTORY_LIMIT = 50
_EVIDENCE = re.compile(r"verify-[a-f0-9]{24}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_PASS = "deterministic verification passed: "


def _error(code="historical_evidence_unavailable", status=409):
    return ControlPlaneError(status, code)


def _events(service, task_id, evidence_id=None):
    conditions = ""
    parameters = [task_id]
    if evidence_id is not None:
        conditions = (
            " AND (json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,"
            "'$.verification_evidence_id')=? OR json_extract(CASE WHEN json_valid(payload_json) "
            "THEN payload_json END,'$.reason')=?)"
        )
        parameters += [evidence_id, _PASS + evidence_id]
    return service.store.connection.execute(
        "SELECT audit_id,created_at,payload_json FROM audit_events WHERE task_id=? "
        "AND event_type='TASK_STATE_CHANGED' AND length(payload_json)<=65536 "
        "AND CASE WHEN json_valid(payload_json) THEN "
        "json_extract(payload_json,'$.from')='VERIFYING' AND "
        "json_extract(payload_json,'$.to') IN ('VERIFIED','BLOCKED') ELSE 0 END"
        + conditions
        + " ORDER BY audit_id DESC LIMIT ?",
        (*parameters, 1 if evidence_id is not None else HISTORY_LIMIT + 1),
    ).fetchall()


def _identity(row):
    payload = json.loads(row["payload_json"])
    evidence_id = payload.get("verification_evidence_id")
    if evidence_id is None and isinstance(payload.get("reason"), str):
        reason = payload["reason"]
        evidence_id = reason[len(_PASS) :] if reason.startswith(_PASS) else None
    digest = payload.get("verification_evidence_sha256")
    if (
        not isinstance(evidence_id, str)
        or not _EVIDENCE.fullmatch(evidence_id)
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise _error()
    return evidence_id, digest, payload["to"]


def historical_evidence(service, task_id, evidence_id):
    task = service.get_task(task_id)
    if not isinstance(evidence_id, str) or not _EVIDENCE.fullmatch(evidence_id):
        raise _error("invalid_evidence_id", 400)
    rows = _events(service, task_id, evidence_id)
    if not rows or service.verification_journal is None:
        raise _error()
    row = rows[0]
    _, attested, outcome = _identity(row)
    result, digest, error = _read_evidence(service.verification_journal, evidence_id)
    if error or result is None or digest != attested:
        raise _error()
    snapshot = result.patch_snapshot
    if snapshot is not None:
        run = service.store.connection.execute(
            "SELECT task_id,status FROM runs WHERE run_id=?", (snapshot.run_id,)
        ).fetchone()
        if (
            snapshot.task_id != task_id
            or snapshot.base_sha != task.base_sha
            or run is None
            or run["task_id"] != task_id
            or run["status"] != "FINISHED"
        ):
            raise _error()
    if outcome == "VERIFIED" and (
        not result.passed
        or not result.stages
        or result.unexpected_paths
        or not all(stage.passed for stage in result.stages)
    ):
        raise _error()
    return result, digest, row


def verification_history(service, task_id, evidence_id=None):
    task = service.get_task(task_id)
    if evidence_id is not None:
        evidence, digest, event = historical_evidence(service, task_id, evidence_id)
        report = result_delivery(
            service.store.connection,
            task={"task_id": task_id, "request_id": task.request_id, "state": task.state},
            store_id=service.store.store_id,
            journal=service.verification_journal,
        )
        snapshot = evidence.patch_snapshot
        report["binding"] = {
            "store_id": service.store.store_id,
            "task_id": task_id,
            "run_id": snapshot.run_id if snapshot else None,
            "base_commit": task.base_sha,
            "result_commit": None,
            "submission_event_id": None,
            "verification_event_id": event["audit_id"],
            "verification_evidence_sha256": digest,
        }
        report["limitations"] = ["HOST_REPORT_ONLY", "HISTORICAL_VERIFICATION_NOT_CURRENT"]
        report.pop("workspace_status", None)
        report["truncated"]["changed_files"] = report["truncated"]["tests"] = False
        _project_evidence(report, evidence, digest)
        accepted = _identity(event)[2] == "VERIFIED"
        report["verification"] = {
            "status": "HISTORICAL_VERIFIED" if accepted else "HISTORICAL_FAILED",
            "evidence_id": evidence_id,
        }
        if not accepted:
            report["completeness"] = "FAILED"
        if snapshot is not None and accepted:
            report["binding"].update(
                patch_sha256=snapshot.sha256, patch_byte_count=snapshot.byte_count
            )
            report["artifacts"].append(
                {
                    "kind": "VERIFIED_PATCH",
                    "reference": f"pao:patch:{snapshot.sha256}",
                    "sha256": snapshot.sha256,
                    "byte_count": snapshot.byte_count,
                }
            )
        report.pop("result_id", None)
        report["result_id"] = f"result-{_digest(report)}"
        return {"task_id": task_id, "result": report}
    rows = _events(service, task_id)
    current = service.verification_report(task_id)
    entries = []
    for row in rows[:HISTORY_LIMIT]:
        try:
            identifier, digest, outcome = _identity(row)
        except ControlPlaneError:
            continue
        entry = {
            "evidence_id": identifier,
            "evidence_sha256": digest,
            "verification_event_id": row["audit_id"],
            "recorded_at": row["created_at"],
            "verdict": "VERIFIED" if outcome == "VERIFIED" else "FAILED_VERIFICATION",
            "status": "HISTORICAL",
            "available": False,
            "patch_sha256": None,
            "patch_byte_count": None,
        }
        try:
            evidence, _, _ = historical_evidence(service, task_id, identifier)
            entry["available"] = True
            if (
                identifier == current.evidence_id
                and task.state == "VERIFIED"
                and current.status in {"VERIFIED", "VERIFICATION_STALE"}
            ):
                entry["status"] = "CURRENT" if current.status == "VERIFIED" else "STALE"
            if evidence.patch_snapshot is not None and outcome == "VERIFIED":
                entry["patch_sha256"] = evidence.patch_snapshot.sha256
                entry["patch_byte_count"] = evidence.patch_snapshot.byte_count
        except ControlPlaneError:
            pass
        entries.append(entry)
    return {"task_id": task_id, "entries": entries, "truncated": len(rows) > HISTORY_LIMIT}


def historical_patch(service, task_id, evidence_id):
    result, _, row = historical_evidence(service, task_id, evidence_id)
    snapshot = result.patch_snapshot
    if snapshot is None or _identity(row)[2] != "VERIFIED":
        raise _error("historical_patch_unavailable")
    raw = base64.b64decode(snapshot.patch_base64, validate=True)
    return {
        "task_id": task_id,
        "base_sha": snapshot.base_sha,
        "patch": raw.decode("utf-8", errors="replace"),
        "patch_base64": snapshot.patch_base64,
        "sha256": snapshot.sha256,
        "byte_count": snapshot.byte_count,
        "source": "VERIFIED_SNAPSHOT",
        "workspace_status": "HISTORICAL",
        "evidence_id": evidence_id,
    }
