"""Source references survive export while actual private fragments do not."""

from __future__ import annotations

import json

import pytest

from personal_ai_orchestrator.verifier import VerificationStage
from tests.test_local_client_v2 import counts
from tests.test_local_client_v2 import service as service
from tests.test_result_delivery import continuation, detail, verified


@pytest.mark.parametrize(
    "path",
    (
        "src/personal_ai_orchestrator/result_delivery.py",
        "tests/test_result_delivery_redaction.py",
        "src/auth/session.py",
        "src/credential_store.py",
        "src/JSON_token_parser.py",
        "src/ConversationHistoryController2.py",
        "src/API_response_serializer_v2.py",
        "src/token API.py",
        "src/cafe\u0301_parser.py",
        "文档/结果  摘要.md",
        "源码/ＡＰＩ/session.py",
        "src/a_readable_module_name_that_is_longer_than_twenty_four_characters.py",
    ),
)
def test_safe_code_reference_retains_exact_path_and_complete_delivery(service, path):
    goal = f"Fix `{path}`."
    verified(service, intent=goal, changed_paths=(path,))
    before = counts(service)
    result = detail(service)["result"]
    exported = continuation(service)
    assert result["goal"] == goal
    assert result["goal_redacted"] is False
    assert result["changed_files"] == [path]
    assert result["completeness"] == "COMPLETE"
    assert not any(result["truncated"].values())
    assert exported["result"] == result
    assert exported["continuation"]["result_id"] == result["result_id"]
    assert exported["continuation"]["goal"] == goal
    assert counts(service) == before


@pytest.mark.parametrize(
    "path", ("ConversationHistoryController2.py", "API_response_serializer_v2.py")
)
def test_unquoted_versioned_source_basename_is_not_an_opaque_credential(service, path):
    verified(service, intent=f"Fix {path}", changed_paths=(path,))
    result = detail(service)["result"]
    assert result["goal"] == f"Fix {path}"
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"


@pytest.mark.parametrize("name", ("personal_ai_orchestrator", "task_result_delivery_contract"))
def test_readable_module_identifiers_are_not_opaque_goal_values(service, name):
    verified(service, intent=f"Fix {name}")
    result = detail(service)["result"]
    assert result["goal"] == f"Fix {name}"
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"


@pytest.mark.parametrize(
    "name",
    (
        "personal_ai_orchestrator",
        "DEPENDENCY_PREPARATION_FAILED",
        "ConversationHistoryController2",
    ),
)
def test_readable_code_identifiers_survive_code_spans(service, name):
    verified(service, intent=f"Fix `{name}`")
    result = detail(service)["result"]
    assert result["goal"] == f"Fix `{name}`"
    assert result["goal_redacted"] is False
    assert result["completeness"] == "COMPLETE"


@pytest.mark.parametrize(
    "path",
    (
        "/Users/export-fixture/private.py",
        "~/export-fixture",
        "C:\\Users\\export-fixture\\file.py",
        "\\\\export-fixture\\share\\file.py",
        "src/../private.py",
        "src/./file.py",
        ".env.production",
        "config/.env.local",
        "config/.aws/credentials",
        "config/.ssh/id_rsa",
        "config/.codex/sessions/export.json",
        "config/auth.json",
        "config/.netrc",
        "config/.npmrc",
        "config/.pypirc",
        "config/.git-credentials",
        "keys/id_ed25519",
        "keys/private_key.pem",
        "config/credentials.json",
        "src/ghp_EXPORT_FIXTURE_ONLY_0000000000000000.py",
        "src/abcdef0123456789abcdef0123456789.py",
        "src/A1b2C3d4E5f6G7h8I9j0K1l2M3n4.py",
        "src/export-fixture@example.invalid.py",
        "src/bidi\u202efixture.py",
    ),
)
def test_unsafe_path_omission_does_not_upgrade_the_verification_result(service, path):
    safe = "src/auth/session.py"
    verified(service, changed_paths=(safe, path))
    result = detail(service)["result"]
    assert result["changed_files"] == [safe]
    assert result["verification"]["status"] == "VERIFIED"
    assert result["completeness"] == "TRUNCATED"
    assert result["truncated"]["changed_files"] is True
    assert "SAFE_EXPORT_OMISSIONS" in result["limitations"]


@pytest.mark.parametrize(
    "fragment,canary",
    (
        ("password=export-fixture-only", "export-fixture-only"),
        ("api_key='export-fixture-only'", "export-fixture-only"),
        ('password="export fixture only"', "export fixture only"),
        ("Authorization: Basic export-fixture-only", "export-fixture-only"),
        ("token EXPORT_FIXTURE_ONLY", "EXPORT_FIXTURE_ONLY"),
        ("Bearer export-fixture-only", "export-fixture-only"),
        ("Cookie: pao=export-fixture-only; trace=export-fixture-only", "export-fixture-only"),
        ("fixture.A1b2C3d4E5f6G7h8I9j0K1l2M3n4.signature", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
        ("Use rvtqplzmhgnbdfksjwcxuayseokprtid", "rvtqplzmhgnbdfksjwcxuayseokprtid"),
        ("Use RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID", "RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID"),
        ("Use az1bc2de3fg4hi5jk6lm7no8pq9rs0tu", "az1bc2de3fg4hi5jk6lm7no8pq9rs0tu"),
        ("Use `rvtqplzmhgnbdfksjwcxuayseokprtid`", "rvtqplzmhgnbdfksjwcxuayseokprtid"),
        ("Use `RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID`", "RVTQPLZMHGNBDFKSJWCXUAYSEOKPRTID"),
        ("Use token exportfixture", "exportfixture"),
        ("/Users/示例/export-fixture-only", "export-fixture-only"),
        ('"/Users/example name/export-fixture-only"', "export-fixture-only"),
        ("~/export-fixture-only", "export-fixture-only"),
        ("../export-fixture-only", "export-fixture-only"),
        ("`cat src/export-fixture-only.py`", "export-fixture-only"),
        ("sk-proj-EXPORT_FIXTURE_ONLY_0000000000000000", "EXPORT_FIXTURE_ONLY"),
        ("export-fixture-only@example.invalid", "export-fixture-only"),
        ("https://export:export-fixture-only@example.invalid", "export-fixture-only"),
    ),
)
def test_mixed_goal_masks_values_and_retains_neighboring_source_reference(
    service, fragment, canary
):
    path = "src/personal_ai_orchestrator/result_delivery.py"
    verified(service, intent=f"Fix `{path}`; {fragment}", changed_paths=(path,))
    exported = continuation(service)
    assert canary not in json.dumps(exported, ensure_ascii=False)
    result = exported["result"]
    assert path in result["goal"]
    assert result["changed_files"] == [path]
    assert result["goal_redacted"] is True
    assert result["completeness"] == "TRUNCATED"
    assert result["goal"] == exported["continuation"]["goal"]


def test_control_line_and_multiline_private_key_remain_withheld(service):
    path = "src/auth/session.py"
    goal = (
        f"Fix `{path}`.\n"
        "\x1b-----BEGIN PRIVATE KEY-----\n"
        "export-fixture-only\n"
        "-----END PRIVATE KEY-----"
    )
    verified(service, intent=goal, changed_paths=(path,))
    result = continuation(service)
    assert "export-fixture-only" not in json.dumps(result)
    assert path in result["result"]["goal"]
    assert result["result"]["goal_redacted"] is True


@pytest.mark.parametrize("control", ("\x0b", "\x0c", "\x85", "\u202e", "\x1b", "\t"))
def test_line_splitting_cannot_hide_control_bearing_fragments(service, control):
    path = "src/auth/session.py"
    verified(service, intent=f"Fix `{path}`.\n{control}export-fixture-only", changed_paths=(path,))
    exported = continuation(service)
    assert "export-fixture-only" not in json.dumps(exported)
    assert path in exported["result"]["goal"]
    assert exported["result"]["goal_redacted"] is True
    assert exported["result"]["completeness"] == "TRUNCATED"


@pytest.mark.parametrize(
    "name",
    (
        "ProjectDependencyPreparation",
        "ProjectDependencyPreparation2",
        "test_token_rotation",
        "Authorization check",
    ),
)
def test_ordinary_check_names_are_not_misclassified_as_credential_values(service, name):
    verified(service, stages=(VerificationStage(name=name, argv=("check",), returncode=0),))
    result = detail(service)["result"]
    assert result["tests"] == [{"name": name, "status": "PASSED"}]
    assert result["completeness"] == "COMPLETE"


@pytest.mark.parametrize(
    "name", ("password=fixture-only", "token EXPORT_FIXTURE_ONLY", "sk-EXPORT_FIXTURE_ONLY")
)
def test_sensitive_check_name_values_remain_omitted(service, name):
    verified(service, stages=(VerificationStage(name=name, argv=("check",), returncode=0),))
    result = detail(service)["result"]
    assert result["tests"] == []
    assert result["truncated"]["tests"] is True
    assert result["completeness"] == "TRUNCATED"
