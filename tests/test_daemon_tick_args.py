"""Tick interval resolution unit tests.

These pure-Python tests live with commit 2 (daemon main loop wiring).
The subprocess-based /v1/health round-trip lives in
test_daemon_tick_integration.py (committed with the control_api /
Swift changes in WP0 commit 3).
"""

from __future__ import annotations

import argparse

import pytest

from personal_ai_orchestrator import daemon as daemon_module
from personal_ai_orchestrator.daemon import _resolve_tick_interval_seconds


def test_resolve_tick_interval_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PAO_TICK_INTERVAL_SECONDS`` overrides the default when no CLI arg is given."""

    args = daemon_module.argparse.Namespace(tick_interval_seconds=None)
    monkeypatch.setenv("PAO_TICK_INTERVAL_SECONDS", "0.25")
    assert _resolve_tick_interval_seconds(args) == 0.25

    args = argparse.Namespace(tick_interval_seconds=1.5)
    monkeypatch.delenv("PAO_TICK_INTERVAL_SECONDS", raising=False)
    assert _resolve_tick_interval_seconds(args) == 1.5

    args = argparse.Namespace(tick_interval_seconds=None)
    monkeypatch.delenv("PAO_TICK_INTERVAL_SECONDS", raising=False)
    assert _resolve_tick_interval_seconds(args) == 5.0


def test_resolve_tick_interval_rejects_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0 / negative tick interval must not silently disable the heartbeat."""

    args = argparse.Namespace(tick_interval_seconds=0)
    with pytest.raises(ValueError):
        _resolve_tick_interval_seconds(args)

    args = argparse.Namespace(tick_interval_seconds=None)
    monkeypatch.setenv("PAO_TICK_INTERVAL_SECONDS", "-1")
    with pytest.raises(ValueError):
        _resolve_tick_interval_seconds(args)


def test_parse_args_exposes_tick_interval_seconds() -> None:
    """``parse_args`` parses the new flag with a float type."""

    args = daemon_module.parse_args(
        [
            "--config",
            "/tmp/r.json",
            "--state-db",
            "/tmp/s.db",
            "--runtime-state-root",
            "/tmp/rs",
            "--tick-interval-seconds",
            "0.5",
        ]
    )
    assert args.tick_interval_seconds == 0.5


def test_parse_args_defaults_tick_interval_to_none() -> None:
    """When omitted, the CLI does NOT bake in a default — env or 5.0 wins."""

    args = daemon_module.parse_args(
        [
            "--config",
            "/tmp/r.json",
            "--state-db",
            "/tmp/s.db",
            "--runtime-state-root",
            "/tmp/rs",
        ]
    )
    assert args.tick_interval_seconds is None