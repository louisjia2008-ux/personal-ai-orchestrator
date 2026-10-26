"""Test-only real SIGINT at synchronous daemon startup boundaries."""

from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import personal_ai_orchestrator.daemon as daemon


def main() -> int:
    gate, mode, directory = sys.argv[1:]
    root = Path(directory)
    events: list[str] = []
    servers = []
    supervisors = []
    original_int = signal.getsignal(signal.SIGINT)
    fired = False

    def checkpoint(name: str) -> None:
        nonlocal fired
        if name == gate and not fired:
            fired = True
            events.append("SIGINT:" + name)
            # A real OS signal, synchronously dispatched before this returns.
            signal.raise_signal(signal.SIGINT)

    original_lstat = Path.lstat

    def lstat(self, *args, **kwargs):
        if self == root / "control.sock":
            # This is the exact naturally observed failure: bind has created
            # the pathname but server_bind has not recorded its identity yet.
            checkpoint("socket_bound")
        return original_lstat(self, *args, **kwargs)

    Path.lstat = lstat

    def record_store(factory, label):
        def build(**kwargs):
            service = factory(**kwargs)
            close = service.store.close

            def closed():
                close()
                events.append(label + ":closed")

            service.store.close = closed
            return service

        return build

    daemon.build_service = record_store(daemon.build_service, "routing")
    daemon.build_control_service = record_store(daemon.build_control_service, "control")
    server_type = daemon.DelegationCampaignControlPlaneServer
    original_init = server_type.__init__
    original_background = server_type.start_background

    def constructed(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        servers.append(self)
        checkpoint("control_constructed")

    def background(self):
        if gate == "error_control_before":
            raise RuntimeError("startup_probe_failure")
        original_background(self)
        if gate == "error_control_after":
            raise RuntimeError("startup_probe_failure")
        checkpoint("control_started")

    server_type.__init__ = constructed
    server_type.start_background = background
    original_start = threading.Thread.start

    def start(self):
        target = getattr(self, "_target", None)
        supervisor = isinstance(getattr(target, "__self__", None), daemon.DaemonSupervisor)
        if supervisor:
            supervisors.append(self)
            checkpoint("supervisor_before")
            if gate == "error_supervisor_before":
                raise RuntimeError("startup_probe_failure")
        original_start(self)
        if supervisor:
            checkpoint("supervisor_after")
            if gate == "error_supervisor_after":
                raise RuntimeError("startup_probe_failure")

    threading.Thread.start = start
    original_sleep = daemon.time.sleep

    def sleep(seconds):
        checkpoint("active_control_only")
        original_sleep(seconds)

    daemon.time = SimpleNamespace(sleep=sleep)

    def serve(*_args, **_kwargs):
        checkpoint("active_full")
        raise AssertionError("startup SIGINT was not honored before serving")

    daemon.serve = serve
    argv = [
        "--config",
        str(root / "runtime.json"),
        "--state-db",
        str(root / "state.sqlite3"),
        "--runtime-state-root",
        str(root / "runtime-state"),
        "--control-socket",
        str(root / "control.sock"),
        "--port",
        "0",
    ]
    if mode == "control-only":
        argv.append("--control-only")
    result = None
    try:
        result = daemon.main(argv)
        return result
    finally:
        receipt = dict(
            gate=gate,
            mode=mode,
            fired=fired,
            result=result,
            events=events,
            socket_remains=(root / "control.sock").exists(),
            signal_handler_restored=signal.getsignal(signal.SIGINT) is original_int,
            threads_alive=any(t.is_alive() for t in supervisors)
            or any(s._thread is not None and s._thread.is_alive() for s in servers),
        )
        (root / "probe-result.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
