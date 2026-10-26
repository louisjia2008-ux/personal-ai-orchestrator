"""Safe directory references keep complete reports; private paths stay masked."""

import json

import pytest

from personal_ai_orchestrator.result_delivery import _goal
from tests.test_local_client_v2 import counts
from tests.test_local_client_v2 import service as service
from tests.test_result_delivery import continuation, detail, verified


@pytest.mark.parametrize(
    "reference",
    (
        "node_modules/pao-streaming-reference/",
        "docs/",
        "src/auth/",
        "参考/修复资料/",
    ),
)
@pytest.mark.parametrize("quote", ("", "`", '"', "'"))
def test_relative_directory_keeps_original_goal_and_complete_result(service, reference, quote):
    goal = f"Read {quote}{reference}{quote} before fixing src/result.py."
    verified(service, intent=goal, changed_paths=("src/result.py",))
    before = counts(service)
    result = detail(service)["result"]
    exported = continuation(service)
    assert result["goal"] == goal
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"
    assert result["verification"]["status"] == "VERIFIED"
    assert not any(result["truncated"].values())
    assert exported["result"] == result
    assert exported["continuation"]["result_id"] == result["result_id"]
    assert counts(service) == before


@pytest.mark.parametrize("quote", ("`", '"', "'"))
def test_quoted_unicode_directory_with_spaces_keeps_its_bytes(quote):
    goal = f"Read {quote}参考/修复 资料/{quote} before editing."
    assert _goal(goal) == (goal, False, False)


@pytest.mark.parametrize(
    "reference",
    (
        "/Users/fixture/private-canary/",
        "~/private-canary/",
        "../private-canary/",
        "src/../private-canary/",
        "docs//private-canary/",
        "private-canary//",
        "config/.env/private-canary/",
        "config/.ssh/private-canary/",
        "config/.aws/private-canary/",
        "config/.codex/private-canary/",
        "config/auth.json/private-canary/",
        "config/credentials.json/private-canary/",
        "src/ghp_PRIVATECANARY12345678901234567890/",
        "src/abcdef0123456789abcdef0123456789/",
        "docs/private\u202ecanary/",
    ),
)
@pytest.mark.parametrize("quote", ("", "`"))
def test_directory_suffix_does_not_rescue_unsafe_path(reference, quote):
    goal, redacted, truncated = _goal(f"Read {quote}{reference}{quote} before editing.")
    assert redacted and not truncated
    assert reference not in goal


@pytest.mark.parametrize(
    "fragment",
    (
        "password=docs/private-canary/",
        "Bearer `docs/private-canary/`",
        "secret `docs/private-canary/`",
        "Use token docs/private-canary/",
        "`cat docs/private-canary/`",
    ),
)
def test_directory_reference_is_preserved_beside_masked_value(service, fragment):
    verified(service, intent=f"Read `docs/reference/`; {fragment}")
    exported = continuation(service)
    assert "private-canary" not in json.dumps(exported)
    assert "docs/reference/" in exported["result"]["goal"]
    assert exported["result"]["goal_redacted"] is True
    assert exported["result"]["completeness"] == "TRUNCATED"
    assert exported["result"]["verification"]["status"] == "VERIFIED"


def test_directory_is_still_invalid_as_changed_file_evidence(service):
    verified(service, intent="Read `docs/reference/`.", changed_paths=("docs/", "src/result.py"))
    result = detail(service)["result"]
    assert result["changed_files"] == ["src/result.py"]
    assert result["truncated"]["changed_files"] is True
    assert result["completeness"] == "TRUNCATED"
    assert result["verification"]["status"] == "VERIFIED"
