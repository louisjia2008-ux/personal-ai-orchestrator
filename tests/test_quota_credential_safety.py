"""P4.2.6.5 §32 — a quota credential must not escape the request that uses it.

Written after a credential was accidentally printed into an agent transcript.
Each test drives a canary value through one path that could plausibly leak it —
formatting, logging, exception text, persisted state, the API surface — and
asserts the canary is absent from the output.

The canary is a fixture-only literal. It is never a real credential, and no
test here reads one.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.quota_cache import (
    PlanProjectionCache,
    QuotaSnapshotCache,
    QuotaSnapshotJournal,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionStatus,
    QuotaTransportError,
)
from personal_ai_orchestrator.quota_collectors.zai import ZAIQuotaCollector
from personal_ai_orchestrator.quota_credentials import (
    REDACTED,
    CredentialSource,
    QuotaCredentialResolver,
    QuotaCredentialSpec,
    SecretValue,
)

#: Fixture-only literal. Distinctive enough that a substring search cannot
#: match it by accident.
CANARY = "pao-canary-8f3a1d7c-never-a-real-credential"
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)

GLM_PAYLOAD = {
    "code": 200,
    "success": True,
    "data": {
        "level": "lite",
        "limits": [
            {
                "type": "CREDIT_LIMIT",
                "unit": 3,
                "number": 5,
                "usage": 2000,
                "currentValue": 500,
                "remaining": 1500,
                "percentage": 25,
            }
        ],
    },
}


class RecordingTransport:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload if payload is not None else GLM_PAYLOAD
        self.headers: list[dict[str, str]] = []

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict:
        self.headers.append(dict(headers))
        return self.payload


# ---------------------------------------------------------------------------
# SecretValue containment
# ---------------------------------------------------------------------------


def test_secret_redacts_through_every_standard_formatting_path() -> None:
    secret = SecretValue(CANARY)

    rendered = [
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!s}",
        f"{secret!r}",
        "{}".format(secret),  # noqa: UP032 - the .format path is the point
        f"{secret:>40}",
    ]

    for text in rendered:
        assert CANARY not in text
        assert REDACTED in text


def test_secret_redacts_inside_a_containing_structure() -> None:
    """A credential most often leaks as a field of something else being logged."""

    payload = {"provider": "zai", "credential": SecretValue(CANARY)}

    assert CANARY not in repr(payload)
    assert CANARY not in str(payload)


def test_secret_refuses_to_serialize() -> None:
    with pytest.raises(TypeError, match="never be pickled"):
        pickle.dumps(SecretValue(CANARY))


def test_secret_is_not_written_to_a_log_record(caplog) -> None:
    logger = logging.getLogger("pao.test.quota")

    with caplog.at_level(logging.INFO, logger="pao.test.quota"):
        logger.info("resolved credential: %s", SecretValue(CANARY))
        logger.info("resolved credential: %r", SecretValue(CANARY))

    assert CANARY not in caplog.text
    assert REDACTED in caplog.text


def test_only_reveal_returns_the_value() -> None:
    """One deliberate accessor, so the leak paths are countable."""

    assert SecretValue(CANARY).reveal() == CANARY


# ---------------------------------------------------------------------------
# Resolution: environment, auth store, and the boundary between them
# ---------------------------------------------------------------------------


def _resolver(environ: dict[str, str], auth_paths: tuple[Path, ...] = ()):
    return QuotaCredentialResolver(
        specs={
            "zai-coding-plan": QuotaCredentialSpec(
                provider_id="zai-coding-plan",
                env_var="ZAI_API_KEY",
                opencode_auth_provider_ids=("zai-coding-plan",),
            )
        },
        environ=environ,
        auth_store_paths=auth_paths,
    )


def _auth_store(tmp_path: Path, payload: dict) -> tuple[Path, ...]:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return (path,)


def test_environment_credential_is_resolved_and_labelled() -> None:
    resolved = _resolver({"ZAI_API_KEY": CANARY}).resolve("zai-coding-plan")

    assert resolved.present is True
    assert resolved.source is CredentialSource.ENVIRONMENT
    assert CANARY not in repr(resolved)


def test_auth_store_covers_the_gui_launched_daemon(tmp_path: Path) -> None:
    """The regression this fixes.

    A GUI-launched daemon inherits LaunchServices' environment, not a login
    shell's, so ``ZAI_API_KEY`` is absent exactly when the owner cares — while
    OpenCode holds a working credential for the same account. Reporting
    CREDENTIAL_NOT_AVAILABLE there is true of the environment and misleading
    about the product.
    """

    paths = _auth_store(tmp_path, {"zai-coding-plan": {"type": "api", "key": CANARY}})

    resolved = _resolver({}, paths).resolve("zai-coding-plan")

    assert resolved.present is True
    assert resolved.source is CredentialSource.OPENCODE_AUTH_STORE


def test_environment_takes_precedence_over_the_store(tmp_path: Path) -> None:
    paths = _auth_store(tmp_path, {"zai-coding-plan": {"type": "api", "key": "store"}})

    resolved = _resolver({"ZAI_API_KEY": CANARY}, paths).resolve("zai-coding-plan")

    assert resolved.source is CredentialSource.ENVIRONMENT
    assert resolved.secret is not None
    assert resolved.secret.reveal() == CANARY


def test_only_the_allowlisted_store_entry_is_read(tmp_path: Path) -> None:
    """The quota contract is one variable and one set of entries per surface.

    A credential for an unrelated provider must not be picked up by shape.
    """

    paths = _auth_store(tmp_path, {"some-other-provider": {"type": "api", "key": CANARY}})

    resolved = _resolver({}, paths).resolve("zai-coding-plan")

    assert resolved.present is False
    assert resolved.source is CredentialSource.NONE


def test_an_unsupported_auth_type_is_skipped_not_guessed_at(tmp_path: Path) -> None:
    """Sending an OAuth refresh token as an API key produces a 401 the owner
    would reasonably read as a broken subscription."""

    paths = _auth_store(tmp_path, {"zai-coding-plan": {"type": "oauth", "refresh": CANARY}})

    resolved = _resolver({}, paths).resolve("zai-coding-plan")

    assert resolved.present is False


def test_an_unknown_provider_resolves_to_nothing() -> None:
    resolved = _resolver({"ZAI_API_KEY": CANARY}).resolve("some-unmapped-provider")

    assert resolved.present is False
    assert resolved.secret is None


def test_presence_probe_reports_a_boolean_and_nothing_else() -> None:
    resolver = _resolver({"ZAI_API_KEY": CANARY})

    presence = resolver.presence("zai-coding-plan")

    assert presence is True
    assert CANARY not in repr(presence)


def test_a_malformed_store_is_survived_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")

    resolved = _resolver({}, (path,)).resolve("zai-coding-plan")

    assert resolved.present is False


# ---------------------------------------------------------------------------
# The credential in flight, and what it leaves behind
# ---------------------------------------------------------------------------


def test_the_credential_reaches_the_request_header_and_nowhere_else() -> None:
    transport = RecordingTransport()
    collector = ZAIQuotaCollector(authorization_token=SecretValue(CANARY), transport=transport)

    result = collector.collect()

    # It must actually authenticate, or the test proves nothing.
    assert transport.headers[0]["Authorization"] == CANARY
    assert result.status is QuotaCollectionStatus.SUCCESS
    # ...and it must not survive into the result the control plane returns.
    assert CANARY not in json.dumps(result.model_dump(mode="json"))
    assert CANARY not in repr(result)


def test_persisted_quota_state_never_contains_the_credential(tmp_path: Path) -> None:
    transport = RecordingTransport()
    collector = ZAIQuotaCollector(authorization_token=SecretValue(CANARY), transport=transport)
    result = collector.collect()
    assert result.snapshot is not None and result.projection is not None

    QuotaSnapshotCache(tmp_path).write(result.snapshot)
    QuotaSnapshotJournal(tmp_path).append(result.snapshot)
    PlanProjectionCache(tmp_path).write(result.projection)

    written = list(tmp_path.rglob("*.json"))
    assert written, "the test must actually have persisted something"
    for path in written:
        assert CANARY not in path.read_text(encoding="utf-8")


def test_a_transport_failure_message_carries_no_credential() -> None:
    class FailingTransport:
        def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict:
            raise QuotaTransportError(QuotaCollectionStatus.PROVIDER_ERROR, "HTTP_500")

    result = ZAIQuotaCollector(
        authorization_token=SecretValue(CANARY), transport=FailingTransport()
    ).collect()

    assert result.status is QuotaCollectionStatus.PROVIDER_ERROR
    assert result.error_category == "HTTP_500"
    assert CANARY not in json.dumps(result.model_dump(mode="json"))


def test_an_unexpected_exception_does_not_print_the_credential() -> None:
    """Tracebacks render locals; a bare string credential would appear in one."""

    class ExplodingTransport:
        def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict:
            raise RuntimeError("provider exploded")

    collector = ZAIQuotaCollector(
        authorization_token=SecretValue(CANARY), transport=ExplodingTransport()
    )

    with pytest.raises(RuntimeError) as excinfo:
        collector.collect()

    assert CANARY not in str(excinfo.value)
    assert CANARY not in repr(collector)


def test_the_projection_the_dashboard_receives_carries_no_credential() -> None:
    result = ZAIQuotaCollector(
        authorization_token=SecretValue(CANARY), transport=RecordingTransport()
    ).collect()

    assert result.projection is not None
    serialized = json.dumps(result.projection.model_dump(mode="json"))
    assert CANARY not in serialized
    # Nor any field that would hold one.
    assert "authorization" not in serialized.lower()
    assert "api_key" not in serialized.lower()
