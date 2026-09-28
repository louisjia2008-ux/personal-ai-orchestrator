"""Deterministic, transcript-free scope validation for PI-5B3G children."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum

from pydantic import Field

from personal_ai_orchestrator.model_registry import RegistryModel

PI5B3G_SCOPE_VALIDATOR_VERSION = "pi5b3g-child-scope-v2"
PI5B3G_CHILD_STATUS = "PI5B3G_CHILD_VERIFIED"


class ScopeDecision(StrEnum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


class PI5B3GScopeValidationResult(RegistryModel):
    """Sanitized validation trace. Raw dynamic arguments are intentionally absent."""

    validator_version: str = PI5B3G_SCOPE_VALIDATOR_VERSION
    decision: ScopeDecision
    rule_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    stage: str = "PRE_CHILD_FORWARD"
    input_field: str = Field(default="intent", max_length=16)
    observation_number: int
    expected_filename: str = Field(min_length=1, max_length=128)
    normalized_intent_length: int
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_length: int
    reason_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    child_forwarded: bool = False
    child_worker_started: bool = False


_SAFE_NEGATIONS = (
    r"\bdo not use (?:a |any )?runtime override or select (?:an |any )?execution target\b",
    r"\bdo not (?:select|choose|suggest) (?:a |any )?provider\b",
    r"\bdo not (?:select|choose|suggest) (?:a |any )?model\b",
    r"\bdo not use (?:a |any )?runtime override\b",
    r"\bdo not (?:select|choose|suggest) (?:an |any )?execution target\b",
    r"\bdo not (?:invoke|run|execute|use) (?:the )?(?:shell|bash|powershell)\b",
    r"\bdo not (?:access|use) (?:the )?network\b",
    r"\bdo not (?:fetch|access|use) (?:the )?(?:web|internet)\b",
    r"\bdo not modify (?:another|any other) file\b",
    r"\bmodify no other file\b",
    r"\bno (?:automatic )?retr(?:y|ies)\b",
    r"\bdo not retr(?:y|ies)\b",
    r"\bno (?:automatic )?fallback\b",
    r"\bdo not fall back\b",
    r"\bdo not delegate again\b",
    r"\bno additional delegation\b",
    r"\bcreate no grandchild\b",
    r"\bdo not (?:create|spawn) (?:a |any )?grandchild\b",
)

_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "SCOPE_PROMPT_INJECTION",
        "PROMPT_INJECTION",
        r"\b(?:ignore previous|override campaign|"
        r"treat .{0,40} rules? as suggestions?|disregard .{0,40} constraints?)\b",
    ),
    (
        "SCOPE_VERIFIER_EVASION",
        "VERIFIER_EVASION",
        r"\b(?:disable|bypass|weaken|evade|modify|change) "
        r"(?:the |frozen )?(?:semantic )?verifier\b",
    ),
    (
        "SCOPE_PROVIDER_AUTHORITY",
        "PROVIDER_AUTHORITY",
        r"\b(?:choose|select|suggest) (?:a |the )?provider\b|\broute .{0,40} through\b",
    ),
    (
        "SCOPE_MODEL_AUTHORITY",
        "MODEL_AUTHORITY",
        r"\b(?:choose|select|suggest|switch) (?:a |the )?model\b|"
        r"\buse [a-z][a-z0-9_.-]*-[a-z0-9_.-]+ to\b",
    ),
    (
        "SCOPE_RUNTIME_AUTHORITY",
        "RUNTIME_AUTHORITY",
        r"\b(?:choose|select|switch|override) (?:a |the )?runtime\b|"
        r"\buse (?:a |the )?runtime override\b",
    ),
    (
        "SCOPE_EXECUTION_TARGET_AUTHORITY",
        "EXECUTION_TARGET_AUTHORITY",
        r"\b(?:use|choose|select|switch|suggest) "
        r"(?:another |a |the )?execution target\b",
    ),
    (
        "SCOPE_SHELL_CAPABILITY",
        "TOOL_CAPABILITY",
        r"\b(?:run|invoke|execute|use) (?:the )?(?:shell|bash|powershell)\b",
    ),
    (
        "SCOPE_NETWORK_CAPABILITY",
        "NETWORK_CAPABILITY",
        r"\b(?:use curl|use wget|access (?:the )?network|fetch (?:a )?web page|"
        r"access (?:the )?(?:web|internet))\b|https?://|file://",
    ),
    (
        "SCOPE_CREDENTIAL_ACCESS",
        "CREDENTIAL_ACCESS",
        r"\b(?:read|inspect|access|obtain) (?:environment )?"
        r"(?:credentials?|secrets?|tokens?|api keys?)\b",
    ),
    (
        "SCOPE_ADDITIONAL_DELEGATION",
        "DELEGATION",
        r"\b(?:spawn another child|ask another agent|create (?:a )?grandchild|"
        r"delegate again|additional delegation)\b",
    ),
    (
        "SCOPE_RETRY_REQUEST",
        "RETRY_FALLBACK",
        r"\b(?:retry|try again|if .{0,30} fails?,? (?:then )?try again)\b",
    ),
    ("SCOPE_FALLBACK_REQUEST", "RETRY_FALLBACK", r"\b(?:fall back|fallback)\b"),
)

_AMBIGUOUS_RULES: tuple[tuple[str, str], ...] = (
    ("SCOPE_AMBIGUOUS_NEGATION", r"\b(?:do not|never|no) .{0,80}\bunless\b"),
    ("SCOPE_AMBIGUOUS_AUTHORITY", r"\b(?:provider|model|runtime|execution target)\b"),
    ("SCOPE_AMBIGUOUS_FILE_SCOPE", r"\b(?:other|additional) files?\b"),
    ("SCOPE_AMBIGUOUS_CAPABILITY", r"\b(?:tools?|capabilities)\b"),
)


def _normalize(value: str) -> str:
    return " ".join(value.strip().split())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask_safe_negations(value: str) -> str:
    masked = value.lower()
    for pattern in _SAFE_NEGATIONS:
        masked = re.sub(pattern, lambda match: " " * len(match.group(0)), masked)
    return masked


def _result(
    *,
    observation_number: int,
    expected_filename: str,
    intent: str,
    reason: str,
    decision: ScopeDecision,
    rule_id: str,
    category: str,
    input_field: str = "intent",
) -> PI5B3GScopeValidationResult:
    normalized_intent = _normalize(intent)
    return PI5B3GScopeValidationResult(
        decision=decision,
        rule_id=rule_id,
        category=category,
        input_field=input_field,
        observation_number=observation_number,
        expected_filename=expected_filename,
        normalized_intent_length=len(normalized_intent),
        intent_sha256=_sha256(intent),
        reason_length=len(reason),
        reason_sha256=_sha256(reason),
    )


def validate_pi5b3g_child_scope(
    *, observation_number: int, intent: str, reason: str
) -> PI5B3GScopeValidationResult:
    """Prove the single-file child task or fail closed with a stable rule ID."""

    expected_filename = f"pi5b3g_obs{observation_number}_child.json"

    def reject(rule: str, category: str, field: str = "intent") -> PI5B3GScopeValidationResult:
        return _result(
            observation_number=observation_number,
            expected_filename=expected_filename,
            intent=intent,
            reason=reason,
            decision=ScopeDecision.REJECT,
            rule_id=rule,
            category=category,
            input_field=field,
        )

    if observation_number not in (1, 2, 3):
        return reject("SCOPE_OBSERVATION_INVALID", "OBSERVATION_IDENTITY")

    for field, raw in (("intent", intent), ("reason", reason)):
        if re.search(r"\b(?:do not|never|no) .{0,80}\bunless\b", raw, flags=re.IGNORECASE):
            return reject("SCOPE_AMBIGUOUS_NEGATION", "AMBIGUOUS", field)
        value = _mask_safe_negations(_normalize(raw))
        for rule_id, category, pattern in _RULES:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return reject(rule_id, category, field)
        for rule_id, pattern in _AMBIGUOUS_RULES:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return reject(rule_id, "AMBIGUOUS", field)

    if re.search(r"(?:^|\s)(?:/|~\/|\.\.?\/)", intent) or ".." in intent:
        return reject("SCOPE_PATH_ESCAPE", "FILE_SCOPE")

    filename_tokens = re.findall(r"(?<![\w.-])([\w.-]+\.[A-Za-z0-9]{1,12})(?![\w-])", intent)
    filenames = [name.rstrip(".") for name in filename_tokens]
    expected_mentions = [name for name in filenames if name == expected_filename]
    other_filenames = [name for name in filenames if name != expected_filename]
    if not expected_mentions:
        return reject("SCOPE_WRONG_FILENAME", "FILE_SCOPE")
    if len(expected_mentions) != 1 or other_filenames:
        return reject("SCOPE_SECOND_FILE", "FILE_SCOPE")
    if re.search(
        r"\b(?:inspect|read|modify|edit|write) (?:another (?:project )?|other |project )file\b",
        _mask_safe_negations(intent),
    ):
        return reject("SCOPE_FILE_EXPANSION", "FILE_SCOPE")

    decoded: list[object] = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", intent):
        try:
            value, _ = decoder.raw_decode(intent[match.start() :])
        except json.JSONDecodeError:
            continue
        decoded.append(value)
    if decoded != [{"status": PI5B3G_CHILD_STATUS}]:
        return reject("SCOPE_SEMANTIC_TARGET_INVALID", "SEMANTIC_TARGET")

    return _result(
        observation_number=observation_number,
        expected_filename=expected_filename,
        intent=intent,
        reason=reason,
        decision=ScopeDecision.ALLOW,
        rule_id="ALLOW_EXACT_SCOPE",
        category="ALLOWED",
    )


__all__ = [
    "PI5B3G_CHILD_STATUS",
    "PI5B3G_SCOPE_VALIDATOR_VERSION",
    "PI5B3GScopeValidationResult",
    "ScopeDecision",
    "validate_pi5b3g_child_scope",
]
