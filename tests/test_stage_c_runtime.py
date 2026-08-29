from io import BytesIO
from urllib.error import HTTPError

import pytest

from spikes.opencode.stage_c_runtime import (
    REPO_READ_PROMPT,
    OpenCode,
    OpenCodeHTTPError,
    StageCError,
    completion_oracle,
    expected_repository_h1,
    sanitize_error_category,
)

PROVIDER = "minimax-cn-coding-plan"
MODEL = "MiniMax-M2.5"
EXPECTED = "# PAO Stage C Fixture A91D7"


def completion(**overrides: object) -> dict:
    value = {
        "text": EXPECTED,
        "error": None,
        "finish": "stop",
        "model": {"providerID": PROVIDER, "id": MODEL, "variant": "default"},
    }
    value.update(overrides)
    return value


def oracle(value: dict | None) -> tuple[bool, str]:
    return completion_oracle(
        value,
        expected_text=EXPECTED,
        provider=PROVIDER,
        model=MODEL,
    )


def test_exact_completion_oracle_passes_only_exact_answer() -> None:
    assert oracle(completion()) == (True, "PASS")


def test_wrong_answer_fails_completion_oracle() -> None:
    assert oracle(completion(text="# different")) == (False, "OUTPUT_MISMATCH")


def test_finish_must_be_stop() -> None:
    assert oracle(completion(finish="length")) == (False, "FINISH_NOT_STOP")


def test_wrong_model_fails_completion_oracle() -> None:
    value = completion(model={"providerID": PROVIDER, "id": "other"})
    assert oracle(value) == (False, "MODEL_MISMATCH")


def test_wrong_provider_fails_completion_oracle() -> None:
    value = completion(model={"providerID": "other", "id": MODEL})
    assert oracle(value) == (False, "PROVIDER_MISMATCH")


def test_assistant_error_is_sanitized() -> None:
    value = completion(error={"message": "Model unavailable: provider/model"})
    assert oracle(value) == (False, "ASSISTANT_ERROR:MODEL_UNAVAILABLE")


def test_repo_read_prompt_does_not_contain_expected_heading() -> None:
    assert EXPECTED not in REPO_READ_PROMPT
    assert "Read README.md" in REPO_READ_PROMPT


def test_host_oracle_reads_h1_from_disposable_repository(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        EXPECTED + "\n\nThis repository exists only for runtime validation.\n",
        encoding="utf-8",
    )
    assert expected_repository_h1(str(tmp_path)) == EXPECTED


def test_http_error_does_not_read_or_emit_raw_body(monkeypatch) -> None:
    secret = b"Authorization: Bearer TOP-SECRET"

    def raise_http_error(*_args, **_kwargs):
        raise HTTPError(
            "http://127.0.0.1/api/session",
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(secret),
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    oc = OpenCode("http://127.0.0.1:9999", "/tmp/disposable")
    with pytest.raises(StageCError) as caught:
        oc._request("GET", "/api/session")

    assert "HTTP_401" in str(caught.value)
    assert "TOP-SECRET" not in str(caught.value)


def test_sanitizer_uses_bounded_categories() -> None:
    assert sanitize_error_category({"message": "rate limit exceeded"}) == "HTTP_429"
    assert sanitize_error_category({"message": "arbitrary opaque failure"}) == (
        "UNKNOWN_PROVIDER_ERROR"
    )


def test_session_deletion_is_verified_on_not_found(monkeypatch) -> None:
    oc = OpenCode("http://127.0.0.1:9999", "/tmp/disposable")

    def request(method: str, path: str, body=None):
        if method == "DELETE":
            return None
        raise OpenCodeHTTPError("GET", path, 404)

    monkeypatch.setattr(oc, "_request", request)
    result = oc.delete_session("session-a")

    assert result["attempted"] is True
    assert result["verified"] is True


def test_session_deletion_is_not_claimed_when_session_still_exists(monkeypatch) -> None:
    oc = OpenCode("http://127.0.0.1:9999", "/tmp/disposable")

    def request(method: str, _path: str, body=None):
        if method == "DELETE":
            return None
        return {"id": "session-a"}

    monkeypatch.setattr(oc, "_request", request)
    result = oc.delete_session("session-a")

    assert result["attempted"] is True
    assert result["verified"] is False
