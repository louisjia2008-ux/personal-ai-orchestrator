"""Regression tests for _run output decoding of non-UTF-8 subprocess output."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from personal_ai_orchestrator.verifier import _run

FIXTURES = Path(__file__).parent / "fixtures"


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_illegal_stdout_bytes_are_replaced(tmp_path: Path) -> None:
    stage = _run(
        _python("import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe bad')"),
        cwd=tmp_path,
        timeout=30,
    )
    assert stage.returncode == 0
    assert stage.passed
    assert stage.stdout.startswith("ok \ufffd\ufffd bad")


def test_illegal_stderr_bytes_are_replaced(tmp_path: Path) -> None:
    stage = _run(
        _python("import sys; sys.stderr.buffer.write(b'err \\x80\\x81 bad')"),
        cwd=tmp_path,
        timeout=30,
    )
    assert stage.returncode == 0
    assert stage.stderr.startswith("err \ufffd\ufffd bad")


def test_valid_unicode_is_preserved(tmp_path: Path) -> None:
    stage = _run(
        _python("print('中文输出 🎉 emoji', end='')"),
        cwd=tmp_path,
        timeout=30,
    )
    assert stage.stdout == "中文输出 🎉 emoji"
    assert stage.returncode == 0


def test_mixed_output_with_invalid_bytes(tmp_path: Path) -> None:
    stage = _run(
        _python(
            "import sys; sys.stdout.buffer.write('中文'.encode() + b'\\xff'); "
            "sys.stderr.buffer.write('错误'.encode() + b'\\xfe')"
        ),
        cwd=tmp_path,
        timeout=30,
    )
    assert stage.stdout == "中文\ufffd"
    assert stage.stderr == "错误\ufffd"


def test_nonzero_exit_code_preserved(tmp_path: Path) -> None:
    stage = _run(
        _python("import sys; sys.stdout.buffer.write(b'\\xff'); sys.exit(3)"),
        cwd=tmp_path,
        timeout=30,
    )
    assert stage.returncode == 3
    assert not stage.passed


def test_tail_limit_20000_chars(tmp_path: Path) -> None:
    stage = _run(
        _python("print('a' * 25000, end='')"),
        cwd=tmp_path,
        timeout=30,
    )
    assert len(stage.stdout) == 20000
    assert stage.stdout == "a" * 20000


def test_fixture_raw_bytes_round_trip(tmp_path: Path) -> None:
    """Run a child that cats the raw-bytes fixture; decoding must not raise."""
    fixture = FIXTURES / "non_utf8_export.txt"
    raw = fixture.read_bytes()
    assert raw  # host-provided fixture must contain bytes
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")

    if sys.platform == "win32":
        argv = ("cmd", "/c", f"type {fixture}")
    else:
        argv = ("cat", str(fixture))
    stage = _run(argv, cwd=tmp_path, timeout=30)
    assert stage.returncode == 0
    assert "\ufffd" in stage.stdout


def test_timeout_raises_subprocess_timeout_expired(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run(
            _python("import time; time.sleep(10)"),
            cwd=tmp_path,
            timeout=0.5,
        )
    assert time.monotonic() - start < 5
