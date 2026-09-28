#!/usr/bin/env python3
"""Exercise the real packaged PAO helper across its PyInstaller process boundary.

This harness intentionally has no forced-termination fallback.  Each cycle sends
exactly one SIGTERM to either the bootloader parent or its serving child.  A
timeout is a failed lifecycle gate and leaves the disposable evidence intact for
diagnosis instead of disguising the failure with SIGKILL.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def _process_rows() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,command="],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rows: list[dict[str, Any]] = []
    for raw in result.stdout.splitlines():
        parts = raw.strip().split(maxsplit=3)
        if len(parts) != 4:
            continue
        try:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "ppid": int(parts[1]),
                    "pgid": int(parts[2]),
                    "command": parts[3],
                }
            )
        except ValueError:
            continue
    return rows


def _process_row(pid: int) -> dict[str, Any] | None:
    return next((row for row in _process_rows() if row["pid"] == pid), None)


def _wait_for_child(parent_pid: int, *, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        children = [row for row in _process_rows() if row["ppid"] == parent_pid]
        if len(children) == 1:
            return children[0]
        if len(children) > 1:
            raise AssertionError(
                f"PyInstaller parent {parent_pid} has unexpected children: {children}"
            )
        time.sleep(0.05)
    raise AssertionError(f"PyInstaller parent {parent_pid} never exposed one serving child")


def _health(socket_path: Path) -> dict[str, Any]:
    request = b"GET /v1/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2.0)
        connection.connect(str(socket_path))
        connection.sendall(request)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    headers, separator, body = response.partition(b"\r\n\r\n")
    if not separator or b" 200 " not in headers.splitlines()[0]:
        raise AssertionError(f"health response was not HTTP 200: {response[:500]!r}")
    payload = json.loads(body)
    if payload.get("status") != "ok":
        raise AssertionError(f"health status is not ok: {payload!r}")
    return payload


def _wait_for_health(
    socket_path: Path,
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
) -> dict[str, Any]:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"packaged helper exited early with {process.returncode}")
        try:
            return _health(socket_path)
        except (OSError, ValueError, AssertionError) as error:
            last_error = error
            time.sleep(0.05)
    raise AssertionError(f"packaged helper never served health: {last_error}")


def _socket_owner(socket_path: Path) -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", "-U", "-F", "pn"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    owners: list[int] = []
    current_pid: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            current_pid = int(line[1:])
        elif line == f"n{socket_path}" and current_pid is not None:
            owners.append(current_pid)
    return sorted(set(owners))


def _wait_for_exit(process: subprocess.Popen[bytes], *, deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AssertionError("shutdown deadline expired before wait")
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            f"packaged parent {process.pid} did not exit before the shutdown deadline"
        ) from error


def _database_state(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database, timeout=2.0) as connection:
        running_rows = connection.execute(
            "SELECT count(*) FROM tasks WHERE state='RUNNING'"
        ).fetchone()[0]
        active_runs = connection.execute(
            "SELECT count(*) FROM runs WHERE finished_at IS NULL"
        ).fetchone()[0]
        held_writers = connection.execute(
            "SELECT count(*) FROM workspaces WHERE writer_token IS NOT NULL"
        ).fetchone()[0]
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    state = {
        "quick_check": quick_check,
        "running_rows": running_rows,
        "active_runs": active_runs,
        "held_writers": held_writers,
    }
    if state != {
        "quick_check": "ok",
        "running_rows": 0,
        "active_runs": 0,
        "held_writers": 0,
    }:
        raise AssertionError(f"unexpected post-shutdown database state: {state}")
    return state


def _run_cycle(
    *,
    helper: Path,
    home: Path,
    cycle_number: int,
    route: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    socket_path = home / "Library" / "Caches" / "Personal AI Orchestrator" / "control.sock"
    database = (
        home / "Library" / "Application Support" / "Personal AI Orchestrator" / "state.sqlite3"
    )
    stdout_path = home / f"cycle-{cycle_number:02d}.stdout.log"
    stderr_path = home / f"cycle-{cycle_number:02d}.stderr.log"
    started_at = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [str(helper), "--home", str(home), "--port", "0"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=False,
        )
        startup_deadline = time.monotonic() + timeout_seconds
        health = _wait_for_health(socket_path, process, deadline=startup_deadline)
        parent = _process_row(process.pid)
        if parent is None:
            raise AssertionError(f"launched parent {process.pid} is absent after health")
        child = _wait_for_child(process.pid, deadline=startup_deadline)
        owners_before = _socket_owner(socket_path)
        if owners_before != [child["pid"]]:
            raise AssertionError(
                f"socket owner mismatch: expected child {child['pid']}, got {owners_before}"
            )

        target_pid = process.pid if route == "parent" else child["pid"]
        signalled_at = time.monotonic()
        os.kill(target_pid, signal.SIGTERM)
        returncode = _wait_for_exit(process, deadline=signalled_at + timeout_seconds)

    child_after = _process_row(child["pid"])
    parent_after = _process_row(process.pid)
    owners_after = _socket_owner(socket_path)
    socket_exists_after = socket_path.exists()
    if returncode != 0:
        raise AssertionError(f"packaged parent returned {returncode} after {route} SIGTERM")
    if parent_after is not None or child_after is not None:
        raise AssertionError(
            f"processes survived shutdown: parent={parent_after}, child={child_after}"
        )
    if owners_after:
        raise AssertionError(f"socket still owned after shutdown: {owners_after}")
    if socket_exists_after:
        raise AssertionError(f"stale socket path survived shutdown: {socket_path}")

    database_state = _database_state(database)
    return {
        "cycle": cycle_number,
        "route": route,
        "parent": parent,
        "child": child,
        "signal_target_pid": target_pid,
        "signal_count": 1,
        "signal": "SIGTERM",
        "health_status": health["status"],
        "socket_path": str(socket_path),
        "socket_owners_before": owners_before,
        "socket_owners_after": owners_after,
        "socket_exists_after": socket_exists_after,
        "parent_returncode": returncode,
        "parent_present_after": parent_after is not None,
        "child_present_after": child_after is not None,
        "database": database_state,
        "startup_and_shutdown_seconds": round(time.monotonic() - started_at, 3),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "result": "PASS",
    }


def main() -> int:
    args = _parse_args()
    helper = args.helper.resolve(strict=True)
    if not os.access(helper, os.X_OK):
        raise SystemExit(f"helper is not executable: {helper}")
    if args.cycles < 3:
        raise SystemExit("--cycles must be at least 3")

    home = Path(tempfile.mkdtemp(prefix="pao-packaged-", dir="/private/tmp"))
    routes = ["parent" if index % 2 == 0 else "child" for index in range(args.cycles)]
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "helper": str(helper),
        "home": str(home),
        "cycles_requested": args.cycles,
        "routes": routes,
        "cycles": [],
        "sigkill_used": False,
        "result": "RUNNING",
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    try:
        for index, route in enumerate(routes, start=1):
            evidence["cycles"].append(
                _run_cycle(
                    helper=helper,
                    home=home,
                    cycle_number=index,
                    route=route,
                    timeout_seconds=args.timeout_seconds,
                )
            )
            args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    except Exception as error:
        evidence["result"] = "FAIL"
        evidence["error_type"] = type(error).__name__
        evidence["error"] = str(error)
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        raise

    evidence["cycles_passed"] = len(evidence["cycles"])
    evidence["parent_route_passes"] = sum(
        cycle["route"] == "parent" for cycle in evidence["cycles"]
    )
    evidence["child_route_passes"] = sum(cycle["route"] == "child" for cycle in evidence["cycles"])
    evidence["result"] = "PASS"
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
