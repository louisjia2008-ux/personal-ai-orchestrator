"""End-to-end daemon tick: real product daemon subprocess + ``/v1/health``.

WP0 ships a supervisor so the bundled daemon has a single heartbeat
source that ``/v1/health`` can expose. This test boots a real
subprocess (no mocks for the supervisor) and polls ``/v1/health``
across two supervisor intervals to prove ``last_tick_at`` advances
without committing to any wall-clock precision assertion.

The test is decorated ``@pytest.mark.flaky`` because socket + sqlite
startup can race a busy CI runner (same family of flakes as
``test_product_daemon_bootstraps_runtime_and_serves_control_plane``).
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from personal_ai_orchestrator.control_client import ControlPlaneClient
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


def _wait_for_socket(socket_path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"daemon never created {socket_path}")


def _parse_isoformat(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.flaky(reruns=3, reruns_delay=1)
def test_daemon_tick_advances_last_tick_at_between_health_polls() -> None:
    """Two ``/v1/health`` polls spaced >= 2 intervals see ``last_tick_at`` advance."""

    tick_interval_seconds = 0.5
    min_gap_seconds = 2 * tick_interval_seconds + 0.25  # 1.25s

    # macOS AF_UNIX sun_path is 104 bytes; use a shallow temp dir so the
    # socket path stays well under the limit even after pytest's tmp_path
    # prefix expansion (the existing shutdown test does the same).
    root = Path(tempfile.mkdtemp(prefix="pao-tick-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    config_path = root / "runtime.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    socket_path = root / "control.sock"
    state_db = root / "state.sqlite3"
    runtime_state_root = root / "runtime-state"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.daemon",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--runtime-state-root",
            str(runtime_state_root),
            "--port",
            "0",
            "--control-socket",
            str(socket_path),
            "--control-only",
            "--tick-interval-seconds",
            str(tick_interval_seconds),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_socket(socket_path, timeout_seconds=15.0)

        client = ControlPlaneClient(socket_path)
        first = client.health()
        first_tick_at_str = first.last_tick_at
        assert first_tick_at_str is not None, "first /v1/health had no last_tick_at"
        assert first.tick_interval_seconds == tick_interval_seconds
        # Heartbeat is the only step WP0 registers.
        step_names = [step.name for step in first.supervisor_steps]
        assert step_names == ["heartbeat"]
        assert first.supervisor_steps[0].consecutive_failures == 0
        assert first.supervisor_steps[0].in_backoff is False
        first_tick_at = _parse_isoformat(first_tick_at_str)

        # Sleep long enough for two full ticks, then probe again.
        time.sleep(min_gap_seconds)
        second = client.health()
        second_tick_at_str = second.last_tick_at
        assert second_tick_at_str is not None
        second_tick_at = _parse_isoformat(second_tick_at_str)
        assert second_tick_at > first_tick_at, (
            f"last_tick_at did not advance: first={first_tick_at} second={second_tick_at}"
        )
        assert [step.name for step in second.supervisor_steps] == ["heartbeat"]
        assert second.supervisor_steps[0].consecutive_failures == 0
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
                process.wait(timeout=5)


def test_daemon_health_poll_does_not_require_credential_handlers() -> None:
    """``/v1/health`` must answer without provider secrets in the env.

    No collector env vars are set; the daemon still reports ok and the
    supervisor registers its single ``heartbeat`` step.
    """

    root = Path(tempfile.mkdtemp(prefix="pao-cred-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    config_path = root / "runtime.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    socket_path = root / "control.sock"

    env = {
        # Inherit PATH (the test runner needs Python on PATH); strip every
        # other PAO_* / provider-secret var.
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "personal_ai_orchestrator.daemon",
            "--config",
            str(config_path),
            "--state-db",
            str(root / "state.sqlite3"),
            "--runtime-state-root",
            str(root / "runtime-state"),
            "--port",
            "0",
            "--control-socket",
            str(socket_path),
            "--control-only",
            "--tick-interval-seconds",
            "0.5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _wait_for_socket(socket_path, timeout_seconds=15.0)
        client = ControlPlaneClient(socket_path)
        health = client.health()
        assert health.status == "ok"
        assert health.api_version == "v1"
        assert [step.name for step in health.supervisor_steps] == ["heartbeat"]
        # Sanity: backward-compatible fields stay shape-stable when the new
        # optional fields are present. Old clients see status/api_version
        # only; new clients also see the supervisor view.
        json_view = json.loads(
            json.dumps(
                {
                    "status": health.status,
                    "api_version": health.api_version,
                    "last_tick_at": health.last_tick_at,
                    "tick_interval_seconds": health.tick_interval_seconds,
                    "supervisor_steps": [
                        {
                            "name": s.name,
                            "last_run_at": s.last_run_at,
                            "last_duration_ms": s.last_duration_ms,
                            "consecutive_failures": s.consecutive_failures,
                            "in_backoff": s.in_backoff,
                        }
                        for s in health.supervisor_steps
                    ],
                }
            )
        )
        assert json_view["status"] == "ok"
        assert json_view["supervisor_steps"][0]["name"] == "heartbeat"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
                process.wait(timeout=5)