"""Readable code identifiers survive export; sensitive contexts stay masked."""

from __future__ import annotations

import json

import pytest

from tests.test_local_client_v2 import counts
from tests.test_local_client_v2 import service as service
from tests.test_result_delivery import continuation, detail, verified

READABLE_IDENTIFIERS = (
    "_structured_stderr_verdict",
    "__structured_stderr_verdict__",
    "_provider_error_sample_sha256",
    "HTTP_STATUS_CODE_503",
    "ProviderErrorDiagnosticTests",
    "WorkerFailureClass.UNCLASSIFIED",
    "_DiagnosticEnvelopeParserV2.from_error_payload",
)


@pytest.mark.parametrize("name", READABLE_IDENTIFIERS)
def test_readable_identifier_survives_code_span_and_bare_text(service, name):
    goal = f"Fix `{name}` after reading {name} in the journal."
    verified(service, intent=goal)
    before = counts(service)
    result = detail(service)["result"]
    exported = continuation(service)
    assert result["goal"] == goal
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"
    assert not any(result["truncated"].values())
    assert exported["result"] == result
    assert exported["continuation"]["goal"] == goal
    # Reads never dispatch new work.
    assert counts(service) == before


def test_ordinary_prose_words_survive_plain_token_fallback(service):
    # Readable code is an exception for credential-like length; ordinary words
    # like Fix, a, and using must keep flowing through the safe prose fallback.
    goal = "Fix a delivery regression using the shared journal helper."
    verified(service, intent=goal)
    before = counts(service)
    result = detail(service)["result"]
    exported = continuation(service)
    assert result["goal"] == goal
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"
    assert not any(result["truncated"].values())
    assert exported["result"] == result
    assert counts(service) == before


@pytest.mark.parametrize(
    "fragment,canary",
    (
        ("api_key = _provider_error_sample_sha256", "_provider_error_sample_sha256"),
        ("token _structured_stderr_verdict", "_structured_stderr_verdict"),
        ("secret `HTTP_STATUS_CODE_503`", "HTTP_STATUS_CODE_503"),
        ("sk-proj-EXPORT_FIXTURE_ONLY_0000000000000000", "EXPORT_FIXTURE_ONLY"),
        ("Use RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID", "RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID"),
        ("Use `RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID`", "RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID"),
        ("Use rvtqplzmhgnbdfksjwcxuayseokprtid", "rvtqplzmhgnbdfksjwcxuayseokprtid"),
        ("Use `rvtqplzmhgnbdfksjwcxuayseokprtid`", "rvtqplzmhgnbdfksjwcxuayseokprtid"),
        ("Use az1bc2de3fg4hi5jk6lm7no8pq9rs0tu", "az1bc2de3fg4hi5jk6lm7no8pq9rs0tu"),
        ("Trace abcdef0123456789abcdef0123456789", "abcdef0123456789abcdef0123456789"),
        ("A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
        ("/Users/export-fixture/private.py", "export-fixture"),
        ("~/export-fixture-only", "export-fixture-only"),
        ("src/../secrets.py", "../secrets"),
        ("`cat src/export-fixture-only.py`", "export-fixture-only"),
        ("\n\x0bfragment export-fixture-only", "export-fixture-only"),
    ),
)
def test_sensitive_contexts_stay_masked_without_upgrading_acceptance(service, fragment, canary):
    path = "src/personal_ai_orchestrator/result_delivery.py"
    verified(service, intent=f"Fix `{path}`; {fragment}", changed_paths=(path,))
    before = counts(service)
    exported = continuation(service)
    assert canary not in json.dumps(exported, ensure_ascii=False)
    result = exported["result"]
    assert path in result["goal"]
    assert result["verification"]["status"] == "VERIFIED"
    assert result["goal_redacted"] is True
    assert result["completeness"] == "TRUNCATED"
    assert result["goal"] == exported["continuation"]["goal"]
    assert counts(service) == before


@pytest.mark.parametrize(
    "opaque",
    (
        "rVtQpLzMhGnBdFkSjWcXuAySeOkPrTiD",
        "_rVtQpLzMhGnBdFkSjWcXuAySeOkPrTiD",
        "rVtQpLzM_hGnBdFkS_jWcXuAyS_eOkPrTiD",
    ),
)
@pytest.mark.parametrize("quoted", (False, True))
def test_opaque_alphabetic_values_remain_redacted(service, opaque, quoted):
    safe = "src/personal_ai_orchestrator/result_delivery.py"
    reference = f"`{opaque}`" if quoted else opaque
    verified(service, intent=f"Fix `{safe}`; use {reference} now.", changed_paths=(safe,))
    exported = continuation(service)
    assert opaque not in json.dumps(exported)
    assert safe in exported["result"]["goal"]
    assert exported["result"]["goal_redacted"] is True
    assert exported["result"]["completeness"] == "TRUNCATED"
    assert exported["result"]["verification"]["status"] == "VERIFIED"
