"""A public null-device literal is syntax, while private paths stay private."""

import pytest

from tests.test_local_client_v2 import service as service
from tests.test_result_delivery import continuation, detail, verified


@pytest.mark.parametrize("reference", ["/dev/null", "`/dev/null`", '"/dev/null"', "'/dev/null'"])
def test_exact_public_null_device_reference_is_delivered_in_goal(service, reference):
    goal = f"Compare {reference} with src/feature.py using the Git whitespace gate"
    verified(service, intent=goal, changed_paths=("src/feature.py",))
    result = detail(service)["result"]
    assert result["completeness"] == "COMPLETE"
    assert result["goal"] == goal
    assert not result["goal_redacted"]
    assert continuation(service)["result"] == result


@pytest.mark.parametrize(
    "reference",
    [
        "/dev/null/../credentials.json",
        "/dev/null/private",
        "/dev/null-backup",
        "/dev/null?token=synthetic",
        "file:///dev/null",
        "/Users/example/private.txt",
        "/tmp/private.txt",
    ],
)
def test_null_device_exception_never_extends_to_other_absolute_references(service, reference):
    verified(
        service,
        intent=f"Compare {reference} with src/feature.py",
        changed_paths=("src/feature.py",),
    )
    result = detail(service)["result"]
    assert reference not in result["goal"]
    assert result["goal_redacted"]
    assert result["completeness"] == "TRUNCATED"
    assert "src/feature.py" in result["goal"]


@pytest.mark.parametrize("label", ["token=", "password=", "session=", "Authorization: Bearer "])
def test_credential_value_context_is_masked_before_public_literal_recognition(service, label):
    verified(service, intent=f"Use {label}/dev/null and edit src/feature.py")
    result = detail(service)["result"]
    assert "/dev/null" not in result["goal"]
    assert result["goal_redacted"]


def test_public_goal_literal_does_not_authorize_absolute_changed_files(service):
    verified(service, intent="Edit src/feature.py", changed_paths=("src/feature.py", "/dev/null"))
    result = detail(service)["result"]
    assert result["changed_files"] == ["src/feature.py"]
    assert result["completeness"] == "TRUNCATED"
