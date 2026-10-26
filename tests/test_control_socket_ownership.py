"""A rejected second server must never unlink the first server's socket."""

import socket
from http.server import BaseHTTPRequestHandler

import pytest

from personal_ai_orchestrator.control_api import UnixThreadingHTTPServer


def test_rejected_second_server_preserves_live_socket(tmp_path):
    path = tmp_path / "control.sock"
    first = UnixThreadingHTTPServer(path, BaseHTTPRequestHandler)
    try:
        inode = path.stat().st_ino
        with pytest.raises(RuntimeError, match="already served"):
            UnixThreadingHTTPServer(path, BaseHTTPRequestHandler)
        assert path.is_socket()
        assert path.stat().st_ino == inode
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))
    finally:
        first.server_close()
    assert not path.exists()


def test_old_server_close_preserves_replacement_socket(tmp_path):
    path = tmp_path / "control.sock"
    first = UnixThreadingHTTPServer(path, BaseHTTPRequestHandler)
    path.unlink()
    second = UnixThreadingHTTPServer(path, BaseHTTPRequestHandler)
    try:
        first.server_close()
        assert path.is_socket()
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))
    finally:
        second.server_close()
    assert not path.exists()
