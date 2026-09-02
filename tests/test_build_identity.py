"""Build identity must be resolvable, honest, and never invented.

A stale daemon serving a current UI is indistinguishable from a healthy stack
unless both halves can state the commit they were built from. These tests pin
that contract: a real commit is reported when one exists, and anything else is
reported as ``unknown`` rather than guessed.
"""

from __future__ import annotations

import json

import pytest

from personal_ai_orchestrator import build_identity
from personal_ai_orchestrator.build_identity import (
    UNKNOWN,
    BuildIdentity,
    resolve_build_identity,
    short_sha,
)

VALID_SHA = "7421be25ae04e23d9b3f8897894a5438bd1509a2"


def _clear_env(monkeypatch):
    for name in ("PAO_BUILD_COMMIT", "PAO_BUILD_CONFIGURATION", "PAO_BUILD_TIMESTAMP"):
        monkeypatch.delenv(name, raising=False)


def test_short_sha_is_seven_characters():
    assert short_sha(VALID_SHA) == "7421be2"


@pytest.mark.parametrize(
    "value",
    ["", "unknown", "7421be2", "$(PAO_BUILD_COMMIT)", "z" * 40, VALID_SHA.upper()],
)
def test_short_sha_rejects_non_commit_values(value):
    assert short_sha(value) == UNKNOWN


def test_environment_commit_wins(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("PAO_BUILD_COMMIT", VALID_SHA)
    monkeypatch.setenv("PAO_BUILD_CONFIGURATION", "Release")
    monkeypatch.setenv("PAO_BUILD_TIMESTAMP", "2026-09-02T10:00:00Z")

    identity = resolve_build_identity()

    assert identity.commit_sha == VALID_SHA
    assert identity.short_sha == "7421be2"
    assert identity.configuration == "Release"
    assert identity.is_known is True


def test_malformed_environment_commit_is_not_trusted(monkeypatch, tmp_path):
    """A truncated or corrupted value must not be presented as a commit."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("PAO_BUILD_COMMIT", "not-a-sha")
    monkeypatch.setattr(build_identity, "_stamp_paths", lambda: [tmp_path / "absent.json"])
    monkeypatch.setattr(build_identity, "_from_git", lambda: None)

    identity = resolve_build_identity()

    assert identity.commit_sha == UNKNOWN
    assert identity.is_known is False


def test_stamp_file_is_used_when_env_is_absent(monkeypatch, tmp_path):
    """The frozen daemon has no checkout, so the baked stamp is authoritative."""
    _clear_env(monkeypatch)
    stamp = tmp_path / "_build_stamp.json"
    stamp.write_text(
        json.dumps(
            {
                "commit_sha": VALID_SHA,
                "configuration": "Release",
                "built_at": "2026-09-02T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_identity, "_stamp_paths", lambda: [stamp])

    identity = resolve_build_identity()

    assert identity.commit_sha == VALID_SHA
    assert identity.configuration == "Release"


def test_unresolvable_identity_is_reported_as_unknown(monkeypatch, tmp_path):
    """Fail visible: no environment, no stamp, no checkout."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(build_identity, "_stamp_paths", lambda: [tmp_path / "absent.json"])
    monkeypatch.setattr(build_identity, "_from_git", lambda: None)

    identity = resolve_build_identity()

    assert identity == BuildIdentity(UNKNOWN, UNKNOWN, UNKNOWN)
    assert identity.short_sha == UNKNOWN
    assert identity.is_known is False


def test_corrupt_stamp_falls_through_rather_than_crashing(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    stamp = tmp_path / "_build_stamp.json"
    stamp.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(build_identity, "_stamp_paths", lambda: [stamp])
    monkeypatch.setattr(build_identity, "_from_git", lambda: None)

    assert resolve_build_identity().commit_sha == UNKNOWN


def test_as_dict_exposes_only_sanitized_identity(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("PAO_BUILD_COMMIT", VALID_SHA)

    payload = resolve_build_identity().as_dict()

    assert set(payload) == {"commit_sha", "short_sha", "configuration", "built_at"}
    assert payload["commit_sha"] == VALID_SHA
