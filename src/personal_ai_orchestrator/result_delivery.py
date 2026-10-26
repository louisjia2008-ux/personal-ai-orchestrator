"""Bounded host-report delivery and safe, manual-only continuation exports.

The existing audit transition and verification journal remain authoritative.
This module never reads worker result_json, provider output, or external
conversation/session storage. Snapshot freshness reads bounded Git diffs through
the host observer; source bytes never enter the safe cross-client projection.
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

from personal_ai_orchestrator.patch_snapshot import snapshot_status
from personal_ai_orchestrator.verification_evidence import VerificationEvidenceJournal
from personal_ai_orchestrator.verifier import VerificationResult

RESULT_VERSION = 1
FILE_LIMIT = 100
TEST_LIMIT = 50
GOAL_LIMIT = 1024
# One 1 MiB binary patch encoded as base64, plus bounded verifier stage output.
EVIDENCE_BYTE_LIMIT = 4 * 1024 * 1024
AUDIT_CHARACTER_LIMIT = 65536
AUDIT_EVENT_LIMIT = 200
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_EVIDENCE = re.compile(r"verify-[a-f0-9]{24}\Z")
_PASS_PREFIX = "deterministic verification passed: "


def _digest(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _credential_like(value: str, *, identifier: bool = False) -> bool:
    """True when the value looks like an actual sensitive credential value.

    Detects key prefixes, emails, long hex blobs, and opaque mixed-case
    random strings. Ordinary long snake_case source identifiers and names
    that merely mention credential terminology stay allowed.
    """
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return True
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        return True
    if "\\" in text or text.startswith("/"):
        return True
    for raw in re.split(r"[\s\"'`<>,;:()\[\]{}=|]+", text):
        tok = raw.strip("-_./")
        if not tok:
            continue
        if re.fullmatch(r"(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{6,}", tok):
            return True
        if re.fullmatch(r"AKIA[A-Z0-9]{12,}", tok):
            return True
        if re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", tok):
            return True
        if len(tok) >= 24 and re.fullmatch(r"[0-9a-fA-F]+", tok):
            if any(ch.isdigit() for ch in tok):
                return True
        if not identifier and re.search(r"[A-Za-z0-9_+=-]{24,}", tok):
            return True
        for candidate in re.findall(r"[A-Za-z0-9_+=-]{20,}", tok):
            has_upper = any(ch.isupper() for ch in candidate)
            has_lower = any(ch.islower() for ch in candidate)
            has_digit = any(ch.isdigit() for ch in candidate)
            if has_upper and has_lower and has_digit:
                # Readable versioned code identifiers are not opaque values.
                # Keep strong key-prefix/email/hex checks above this exception.
                number_runs = re.findall(r"\d+", candidate)
                if (
                    identifier
                    and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", candidate)
                    and len(number_runs) <= 2
                    and all(len(run) <= 4 for run in number_runs)
                    and re.search(r"[a-z]{4,}", candidate)
                ):
                    continue
                return True
    return False


def _forbidden_segment(part: str) -> bool:
    lowered = part.strip().lower()
    if part in {".", ".."}:
        return True
    if lowered.startswith(".env"):
        return True
    if lowered in {
        ".codex",
        ".ssh",
        ".aws",
        "auth.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        "private_key.pem",
    }:
        return True
    return False


def _safe_changed_file(value: str) -> bool:
    """Accept ordinary relative repo paths; reject absolute, traversal,
    credential-file, and control/format-character paths."""
    if not value or len(value) > 256:
        return False
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        return False
    if "\\" in value or ":" in value:
        return False
    if any(
        ch not in "_ .@+-/" and not ch.isalnum() and not unicodedata.category(ch).startswith("M")
        for ch in value
    ):
        return False
    parts = value.split("/")
    if any(not part or _forbidden_segment(part) for part in parts):
        return False
    for part in parts:
        stem = part.rsplit(".", 1)[0]
        if _credential_like(part, identifier=True) or (
            stem and _credential_like(stem, identifier=True)
        ):
            return False
    return True


def _readable_code_identifier(value: str) -> bool:
    """Keep readable code syntax after the credential and sensitive-value guards."""
    if not value or len(value) > GOAL_LIMIT or _credential_like(value, identifier=True):
        return False

    def readable_segment(part: str) -> bool:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part):
            return False
        core = part.strip("_")
        if not core:
            return False

        def word(segment: str) -> bool:
            if re.fullmatch(r"(?:[a-z]+|[A-Z]+)\d{0,4}|\d{1,4}", segment):
                return True
            # Retain the established readable CamelCase word structure.
            # Alternating-case random letters have no such word boundaries.
            return bool(re.search(r"[a-z]{4,}[A-Z][a-z]{4,}", segment))

        if "_" in core:
            return all(word(segment) for segment in core.split("_"))
        if len(core) >= 24 and (core.islower() or core.isupper()):
            return False
        return word(core)

    return all(readable_segment(part) for part in value.split("."))


def _safe_code_reference(value: str) -> bool:
    """Recognize code/path data without treating a command as a filename."""
    if re.match(
        r"(?i)^(?:cat|sh|bash|zsh|fish|env|python\d*|node|curl|wget|rm|sudo|git|"
        r"echo|printf|source|eval|exec|open|osascript|powershell|pwsh)\s",
        value,
    ):
        return False
    if len(value) >= 24 and "/" not in value and "." not in value:
        if _credential_like(value) and not _readable_code_identifier(value):
            return False
    return bool(
        _safe_changed_file(value)
        and (not any(ch.isspace() for ch in value) or "/" in value or "." in value)
    )


def _safe_goal_reference(value: str) -> bool:
    """One final slash is directory syntax in a goal, not an empty filename.

    Validate the remaining relative path without normalizing private or invalid
    segments. Credential value positions still use the stricter code guard.
    """
    return _safe_code_reference(value.removesuffix("/"))


def _mask_credentials(text: str) -> str:
    # Explicit assignments are value positions even when the value looks like
    # ordinary prose or a source path. Apply this before recognizing code spans.
    literal = r"(?:`[^`]*`|\"[^\"]*\"|'[^']*'|[^\s;,\"'`]+)"
    assigned = re.compile(
        r"(?i)\b(password|passwd|secret|token|api[ _-]?key|authorization|"
        r"credential|cookie|bearer|private[ _-]?key|session)\b[ \t]*[:=][ \t]*"
        r"(?:(?:Bearer|Basic)[ \t]+)?" + literal
    )
    masked = re.sub(r"(?i)\bcookie[ \t]*:[^\r\n]*", "Cookie: [redacted]", text)
    masked = assigned.sub(lambda match: match.group(1) + "=[redacted]", masked)

    def bare_value(match: re.Match[str]) -> str:
        if match.group("code") is not None:
            return match.group(0)
        value = match.group("value")
        body = value.strip("\"'`")
        code_path = (
            value.startswith("`")
            and value.endswith("`")
            and "/" in body
            and _safe_code_reference(body)
        )
        supplied = bool(re.search(r"(?i)\b(?:use|using|set)\s+$", masked[: match.start()]))
        if not code_path and (
            supplied
            or match.group("label").lower() == "bearer"
            or value[:1] in {"'", '"', "`"}
            or _credential_like(body)
            or any(ch.isdigit() or ch.isupper() or ch in "_-=" for ch in body)
        ):
            return match.group("label") + " [redacted]"
        return match.group(0)

    return re.sub(
        r"(?i)(?P<code>`[^`]*`)|(?<![\w./])"
        r"(?P<label>token|bearer|secret|password|credential|session)\b[ \t]+"
        r"(?P<value>" + literal + ")",
        bare_value,
        masked,
    )


def _mask_goal_line(line: str) -> str:
    masked = _mask_credentials(line)
    # Alternatives are ordered: code spans and quoted paths are atomic, so
    # private fragments cannot be reintroduced through a protected placeholder.
    fragments = re.compile(
        r"(?P<code>`[^`\n]*(?:`|$))"
        r"|(?P<quoted>\"[^\"\n]*[/\\][^\"\n]*\"|'[^'\n]*[/\\][^'\n]*')"
        r"|(?P<url>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s`\"'<>,;]+)"
        r"|(?P<absolute>(?<![\w./])(?:[A-Za-z]:[\\/]|\\\\|~/|/)[^\s`\"'<>,;]*)"
        r"|(?P<path>(?<![\w./])(?:\.{1,2}/|[\w.@+-]+/)[\w.@+/\-]*)"
        r"|(?P<token>[\w.@+%=-]+)"
    )

    def project(match: re.Match[str]) -> str:
        value = match.group(0)
        # This exact device literal is public program syntax, only in goals.
        # Credential values were already masked; URLs and descendants stay private.
        body = value[1:-1] if match.lastgroup in {"code", "quoted"} else value
        public_literal = body == "/dev/null"
        if match.lastgroup == "code":
            safe = (
                len(value) >= 2
                and value.endswith("`")
                and (public_literal or _safe_goal_reference(body))
            )
        elif match.lastgroup == "quoted":
            safe = public_literal or _safe_goal_reference(body)
        elif match.lastgroup == "absolute":
            safe = public_literal
        elif match.lastgroup == "url":
            safe = False
        elif match.lastgroup == "path":
            safe = _safe_goal_reference(value)
        elif re.fullmatch(
            r"[\w.@+-]+\.(?:py|swift|[cm]?js|jsx|tsx?|md|toml|json|ya?ml|sh|[ch]|cpp|rs|go)",
            value,
        ):
            safe = _safe_changed_file(value)
        elif re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", value):
            safe = not _forbidden_segment(value) and (
                not _credential_like(value) or _readable_code_identifier(value)
            )
        else:
            token = value.rstrip(".")
            safe = not token or (not _credential_like(token) and not _forbidden_segment(token))
        return value if safe else "[redacted]"

    return fragments.sub(project, masked)


def _goal(value: str) -> tuple[str, bool, bool]:
    """Preserve safe code references and mark actual sensitive omissions."""
    if len(value) > GOAL_LIMIT:
        return "Original goal exceeds the safe export limit; review it in PAO.", True, True
    redacted = False
    lines: list[str] = []
    in_pem = False
    begin_pattern = re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----")
    end_pattern = re.compile(r"(?i)-----END [A-Z ]*PRIVATE KEY-----")
    # LF/CRLF are paragraph separators; other controls must reach the guard.
    for line in value.replace("\r\n", "\n").split("\n"):
        if any(unicodedata.category(ch).startswith("C") for ch in line):
            if begin_pattern.search(line):
                in_pem = end_pattern.search(line) is None
            elif in_pem and end_pattern.search(line):
                in_pem = False
            lines.append("[Goal detail withheld for safe export]")
            redacted = True
            continue
        begin = begin_pattern.search(line)
        if in_pem or begin:
            start = begin.end() if begin and not in_pem else 0
            end = end_pattern.search(line, start)
            prefix = _mask_goal_line(line[: begin.start()]) if begin and not in_pem else ""
            suffix = _mask_goal_line(line[end.end() :]) if end else ""
            lines.append((prefix + " [redacted] " + suffix).strip())
            in_pem = end is None
            redacted = True
            continue
        masked = _mask_goal_line(line)
        redacted |= masked != line
        lines.append(masked.strip())
    return " ".join(lines).strip() or "Review the original goal in PAO.", redacted, False


def _safe_test_name(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _().:+-]{0,79}", value)
        and not _credential_like(value, identifier=True)
        and _mask_credentials(value) == value
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
        AND audit_id>COALESCE((SELECT MAX(audit_id) FROM audit_events WHERE task_id=?
            AND event_type IN ('RUN_STARTED','MANUAL_TASK_RETRY_PREPARED',
                'MANUAL_TASK_REOPEN_PREPARED')),0)
        ORDER BY audit_id DESC LIMIT ?
        ) SELECT audit_id,
        substr(json_extract(payload_json,'$.to'),1,81) AS to_state,
        substr(json_extract(payload_json,'$.reason'),1,161) AS reason,
        substr(json_extract(payload_json,'$.verification_evidence_sha256'),1,65) AS evidence_sha256
        FROM bounded WHERE
        CASE WHEN json_valid(payload_json) THEN
            json_extract(payload_json,'$.from')='VERIFYING' ELSE 0 END
        ORDER BY audit_id DESC LIMIT 1""",
        (AUDIT_CHARACTER_LIMIT, task_id, task_id, AUDIT_EVENT_LIMIT),
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
        started = _audit(connection, task_id, "RUN_STARTED")
        attested_failure = bool(
            transition
            and transition["to_state"] == "BLOCKED"
            and isinstance(transition["evidence_sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", transition["evidence_sha256"])
            and run_id
            and started
            and started["run_id"] == run_id
            and started["audit_id"] < transition["audit_id"]
            # The composed host safety outcome may block a passing verifier.
            # Its known main-repo guard does not establish failed tests.
            and transition["reason"] != "main repository mutated during owner dispatch"
        )
        if attested_failure:
            report["verification"]["status"] = "FAILED_VERIFICATION"
        elif transition and transition["to_state"] == "BLOCKED":
            report["limitations"].append("VERIFICATION_OUTCOME_NOT_ESTABLISHED")
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
                if (
                    evidence.patch_snapshot is not None
                    and report["verification"]["status"] == "VERIFIED"
                ):
                    snapshot = evidence.patch_snapshot
                    observed = snapshot_status(
                        connection,
                        task_id=task_id,
                        run_id=run_id,
                        base_sha=base,
                        snapshot=snapshot,
                    )
                    report["workspace_status"] = observed
                    if observed == "BINDING_MISMATCH":
                        report["verification"]["status"] = "EVIDENCE_CONFLICT"
                        report["completeness"] = "FAILED"
                    else:
                        report["binding"]["patch_sha256"] = snapshot.sha256
                        report["binding"]["patch_byte_count"] = snapshot.byte_count
                        report["artifacts"].append(
                            {
                                "kind": "VERIFIED_PATCH",
                                "reference": f"pao:patch:{snapshot.sha256}",
                                "sha256": snapshot.sha256,
                                "byte_count": snapshot.byte_count,
                            }
                        )
                        if observed != "CURRENT":
                            report["verification"]["status"] = (
                                "VERIFICATION_STALE"
                                if observed == "STALE"
                                else "WORKSPACE_UNAVAILABLE"
                            )
                    if observed != "CURRENT":
                        report["limitations"].append(
                            "WORKTREE_CHANGED_SINCE_VERIFICATION"
                            if observed == "STALE"
                            else "SNAPSHOT_WORKSPACE_BINDING_UNAVAILABLE"
                        )
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
