"""Daemon shutdown behaviour: an intentional SIGINT must exit cleanly."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_ai_orchestrator.control_client import ControlPlaneClient, ControlPlaneUnavailable
from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.runtime_config import RuntimeConfig


def test_control_only_signal_handler_avoids_event_set_and_cleanup(monkeypatch, tmp_path) -> None:
    """Deliver SIGTERM while the main wait is active and guard lock-backed work."""

    import personal_ai_orchestrator.daemon as daemon_module

    calls: list[str] = []
    signal_state: dict[str, object] = {
        "handler": None,
        "inside_handler": False,
        "delivered": False,
    }

    class GuardedEvent:
        def __init__(self) -> None:
            self.requested = False

        def is_set(self) -> bool:
            return self.requested

        def set(self) -> None:
            assert not signal_state["inside_handler"], "signal handler called threading.Event.set()"
            calls.append("supervisor_stop.set")
            self.requested = True

        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            deliver_sigterm()
            return self.requested

    class FakeThread:
        def __init__(self, *, target, args, daemon) -> None:
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self) -> None:
            calls.append("supervisor_thread.start")

        def join(self, timeout: float | None = None) -> None:
            calls.append("supervisor_thread.join")

    class FakeStore:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            assert not signal_state["inside_handler"]
            calls.append(f"{self.name}.close")

    service = SimpleNamespace(store=FakeStore("routing_store"))
    control_service = SimpleNamespace(store=FakeStore("control_store"))
    control_service.__dict__["_delegation_campaign_store"] = object()
    manager = SimpleNamespace(_audit=None)
    supervisor = SimpleNamespace(run=lambda _stop: None)

    class FakeServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start_background(self) -> None:
            calls.append("control_server.start")

        def stop(self) -> None:
            assert not signal_state["inside_handler"]
            calls.append("control_server.stop")

    previous_term = object()

    def fake_signal(sig, disposition):
        assert sig == signal.SIGTERM
        if callable(disposition):
            signal_state["handler"] = disposition
            calls.append("signal.install")
        else:
            assert disposition is previous_term
            calls.append("signal.restore")

    def deliver_sigterm() -> None:
        if signal_state["delivered"]:
            return
        signal_state["delivered"] = True
        handler = signal_state["handler"]
        assert callable(handler)
        signal_state["inside_handler"] = True
        try:
            handler(signal.SIGTERM, None)
        finally:
            signal_state["inside_handler"] = False

    monkeypatch.setattr(daemon_module, "load_runtime_config", lambda _path: object())
    monkeypatch.setattr(daemon_module, "ProviderRegistryManager", lambda **_kwargs: manager)
    monkeypatch.setattr(daemon_module, "build_service", lambda **_kwargs: service)
    monkeypatch.setattr(daemon_module, "build_control_service", lambda **_kwargs: control_service)
    monkeypatch.setattr(daemon_module, "build_default_supervisor", lambda **_kwargs: supervisor)
    monkeypatch.setattr(daemon_module, "DelegationCampaignControlPlaneServer", FakeServer)
    monkeypatch.setattr(daemon_module.threading, "Event", GuardedEvent)
    monkeypatch.setattr(daemon_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(daemon_module.signal, "getsignal", lambda _sig: previous_term)
    monkeypatch.setattr(daemon_module.signal, "signal", fake_signal)
    monkeypatch.setattr(
        daemon_module,
        "time",
        SimpleNamespace(sleep=lambda _seconds: deliver_sigterm()),
        raising=False,
    )

    result = daemon_module.main(
        [
            "--config",
            str(tmp_path / "runtime.json"),
            "--state-db",
            str(tmp_path / "state.sqlite3"),
            "--runtime-state-root",
            str(tmp_path / "runtime-state"),
            "--control-socket",
            str(tmp_path / "control.sock"),
            "--control-only",
        ]
    )

    assert result == 0
    assert calls == [
        "control_server.start",
        "signal.install",
        "supervisor_thread.start",
        "signal.restore",
        "supervisor_stop.set",
        "supervisor_thread.join",
        "control_server.stop",
        "control_store.close",
        "routing_store.close",
    ]


# A5 (fix/m0-trust): the asyncio event-loop teardown sometimes races the
# SIGINT round-trip on slow CI, surfacing a spurious traceback on stdout.
# pytest-rerunfailures lets us retry the flake up to 3 times before the
# test is reported as a real failure.
@pytest.mark.flaky(reruns=3, reruns_delay=1)
def test_sigint_shuts_daemon_down_cleanly_without_traceback() -> None:
    # macOS limits AF_UNIX sun_path to 104 bytes; keep the socket directory shallow.
    root = Path(tempfile.mkdtemp(prefix="pao-sigint-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    config_path = root / "runtime.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    socket_path = root / "control.sock"

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
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not socket_path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=5)
                raise AssertionError(f"daemon exited early: {out}\n{err}")
            if time.monotonic() > deadline:
                raise AssertionError("daemon never created the control socket")
            time.sleep(0.1)

        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=15)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, f"unexpected exit status: {out}\n{err}"
    assert "Traceback" not in out
    assert "Traceback" not in err
    # The control socket is cleaned up by the control-plane stop path.
    assert not socket_path.exists()


def test_sigterm_shuts_control_only_daemon_down_cleanly() -> None:
    """SIGTERM must leave the ordinary cleanup path reachable.

    This is the daemon mode used by the bundled product.  The defensive
    SIGINT below is test-owned recovery only: it makes an old broken build
    fail the assertion without leaking its disposable subprocess.
    """

    root = Path(tempfile.mkdtemp(prefix="pao-sigterm-"))
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    config_path = root / "runtime.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    socket_path = root / "control.sock"
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
            "0.1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    timed_out = False
    out = ""
    err = ""
    try:
        deadline = time.monotonic() + 15.0
        while not socket_path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=5)
                raise AssertionError(f"daemon exited early: {out}\n{err}")
            if time.monotonic() > deadline:
                raise AssertionError("daemon never created the control socket")
            time.sleep(0.05)

        # Socket bind precedes signal-handler installation.  A completed
        # supervisor tick is the observable readiness boundary after the
        # handler and supervisor thread have both started.
        client = ControlPlaneClient(socket_path)
        while True:
            try:
                health = client.health()
            except ControlPlaneUnavailable:
                health = None
            if health is not None and health.last_tick_at is not None:
                break
            if time.monotonic() > deadline:
                raise AssertionError("daemon never completed a supervisor tick")
            time.sleep(0.05)

        process.send_signal(signal.SIGTERM)
        try:
            out, err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            out, err = process.communicate(timeout=5)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)

    assert not timed_out, "control-only daemon did not exit after SIGTERM"
    assert process.returncode == 0, f"unexpected exit status: {out}\n{err}"
    assert "Traceback" not in out
    assert "Traceback" not in err
    assert not socket_path.exists()


def test_runtime_config_with_empty_registry_is_valid_json_for_daemon() -> None:
    config = RuntimeConfig(catalog_snapshot_id="catalog-empty", registry=ModelRegistry())
    payload = json.loads(config.model_dump_json())
    assert payload["schema_version"] == 1
    assert payload["policy"]["objective"] == "BALANCED"
