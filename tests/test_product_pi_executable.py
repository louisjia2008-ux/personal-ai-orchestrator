"""A host executable override must remain explicit, local and process-scoped."""

import tempfile
from pathlib import Path

import pytest

from personal_ai_orchestrator import product_daemon


@pytest.fixture
def short_home():
    with tempfile.TemporaryDirectory(prefix="pao-pi-bin-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.mark.parametrize("value", ["pi", "/nonexistent-pao-pi-binary", "/private/tmp"])
def test_pi_override_rejects_relative_missing_or_directory(value: str) -> None:
    with pytest.raises(SystemExit):
        product_daemon.parse_args(["--pi-bin", value])


def test_pi_override_rejects_nonexecutable_file(tmp_path: Path) -> None:
    pi = tmp_path / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n")
    pi.chmod(0o600)
    with pytest.raises(SystemExit):
        product_daemon.parse_args(["--pi-bin", str(pi)])


@pytest.mark.parametrize("override", [False, True])
def test_product_launch_forwards_override_without_persisting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: bool,
    short_home: Path,
) -> None:
    pi = tmp_path / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n")
    pi.chmod(0o700)
    home = short_home
    sentinel = object()
    monkeypatch.setattr(product_daemon, "resolve_dynamic_registry", lambda **kwargs: sentinel)
    captured = {}

    def main(argv, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(product_daemon, "daemon_main", main)
    args = ["--home", str(home)] + (["--pi-bin", str(pi)] if override else [])
    assert product_daemon.main(args) == 0
    assert captured["provider_registry_manager"] is sentinel
    if override:
        assert captured["pi_runtime"].pi_bin == str(pi)
    else:
        assert "pi_runtime" not in captured
    assert product_daemon.parse_args([]).pi_bin is None
    config = home / "Library/Application Support/Personal AI Orchestrator/runtime.json"
    assert str(pi) not in config.read_text()
