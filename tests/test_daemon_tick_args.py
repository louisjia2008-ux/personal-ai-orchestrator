"""Tick interval resolution unit tests.

These pure-Python tests live with commit 2 (daemon main loop wiring).
The subprocess-based /v1/health round-trip lives in
test_daemon_tick_integration.py (committed with the control_api /
Swift changes in WP0 commit 3).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


# --------------------------------------------------------------------- #
# M1 WP2: --model-tiers-path / load_model_tiers
# --------------------------------------------------------------------- #


def test_parse_args_exposes_model_tiers_path() -> None:
    args = daemon_module.parse_args(
        [
            "--config",
            "/tmp/r.json",
            "--state-db",
            "/tmp/s.db",
            "--runtime-state-root",
            "/tmp/rs",
            "--model-tiers-path",
            "/tmp/tiers.json",
        ]
    )
    from pathlib import Path

    assert args.model_tiers_path == Path("/tmp/tiers.json")


def test_parse_args_defaults_model_tiers_path_to_none() -> None:
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
    assert args.model_tiers_path is None


def test_load_model_tiers_owner_file_loads_as_owner_source(tmp_path) -> None:
    from personal_ai_orchestrator.daemon import load_model_tiers

    path = tmp_path / "tiers.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tiers": {
                    "custom-target": {"tier": "T0", "caps": ["flagship"]},
                },
            }
        ),
        encoding="utf-8",
    )
    table, source = load_model_tiers(path)
    assert source == "owner_file"
    entry, reason = table.lookup("custom-target")
    assert entry.tier.value == "T0"
    assert reason == "exact"


def test_load_model_tiers_missing_path_falls_back_to_defaults() -> None:
    from personal_ai_orchestrator.daemon import load_model_tiers

    table, source = load_model_tiers(None)
    assert source == "default_fallback"
    # The shipped defaults cover the known provider families.
    entry, _ = table.lookup("zai-coding-plan-glm-5.3")
    assert entry.tier.value == "T1"


def test_load_model_tiers_malformed_file_records_event_and_falls_back(tmp_path) -> None:
    from personal_ai_orchestrator.daemon import load_model_tiers
    from personal_ai_orchestrator.safety_kernel import SafetyKernelStore

    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    try:
        table, source = load_model_tiers(bad, audit=store)
        assert source == "default_fallback"
        # The system event must be recorded for the owner to see why the
        # fallback fired.
        rows = store.connection.execute(
            "SELECT event_type, payload_json FROM audit_events "
            "WHERE event_type = 'MODEL_TIERS_INVALID'"
        ).fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload["path"] == str(bad)
        assert payload["error_type"] in {"JSONDecodeError", "ValueError"}
        assert "not json" in payload["error"] or "Expecting" in payload["error"]
    finally:
        store.close()


def test_load_model_tiers_invalid_version_records_event_and_falls_back(tmp_path) -> None:
    from personal_ai_orchestrator.daemon import load_model_tiers
    from personal_ai_orchestrator.safety_kernel import SafetyKernelStore

    bad = tmp_path / "bad-version.json"
    bad.write_text(json.dumps({"version": 99, "tiers": {}}), encoding="utf-8")
    store = SafetyKernelStore(tmp_path / "state.sqlite3")
    try:
        table, source = load_model_tiers(bad, audit=store)
        assert source == "default_fallback"
        rows = store.connection.execute(
            "SELECT 1 FROM audit_events WHERE event_type = 'MODEL_TIERS_INVALID'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        store.close()


def test_build_daemon_argv_emits_model_tiers_path_flag() -> None:
    from personal_ai_orchestrator.product_daemon import build_daemon_argv
    from personal_ai_orchestrator.runtime_config import default_application_support_layout

    layout = default_application_support_layout(Path("/Users/example"))
    tiers = Path("/Users/example/runtime-state/policies/model-tiers.json")
    argv = build_daemon_argv(
        layout,
        host="127.0.0.1",
        port=8765,
        model_tiers_path=tiers,
    )
    assert argv[argv.index("--model-tiers-path") + 1] == str(tiers)


def test_build_daemon_argv_omits_model_tiers_path_when_unset() -> None:
    from personal_ai_orchestrator.product_daemon import build_daemon_argv
    from personal_ai_orchestrator.runtime_config import default_application_support_layout

    layout = default_application_support_layout(Path("/Users/example"))
    argv = build_daemon_argv(layout, host="127.0.0.1", port=8765)
    assert "--model-tiers-path" not in argv