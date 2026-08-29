#!/usr/bin/env python3
"""Local backend bridge for the Orchestrator Console dashboard.

Serves ``dashboard/index.html`` and a tiny JSON API that shells out to the real
local OpenCode install, so the panel can show real providers / catalogs and run
a real connection test:

    GET  /                 -> the dashboard (same origin, so no CORS needed)
    GET  /api/providers    -> authenticated providers, full model catalogs,
                              endpoints and plan docs (from OpenCode metadata)
    POST /api/test         -> a real, bounded connection test for {provider,model}:
                              switch a disposable session to the model and run one
                              tiny turn; classify ok / model_unavailable / error

Credential safety: this bridge never reads, stores, logs, or returns credential
*values*. Provider auth is reported only as presence + type + the env-var *name*.
OpenCode owns the credentials; the bridge just asks it to run.

Run:  python3 dashboard/serve.py    then open http://127.0.0.1:8899
Env:  OPENCODE_BIN (default "opencode"), PORT (default 8899)
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPENCODE = os.environ.get("OPENCODE_BIN", "opencode")
PORT = int(os.environ.get("PORT", "8899"))
SERVE_PORT = int(os.environ.get("OC_SERVE_PORT", "8898"))
HERE = Path(__file__).resolve().parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")

ZERO = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}


def run_cli(*args: str, timeout: int = 30) -> str:
    try:
        out = subprocess.run(
            [OPENCODE, *args], capture_output=True, text=True, timeout=timeout
        )
        return out.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def opencode_paths() -> dict:
    paths = {}
    for line in run_cli("debug", "paths").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            paths[parts[0]] = parts[1]
    return paths


class OpenCodeServer:
    """A single reused `opencode serve` plus a disposable session workspace."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.fixture = tempfile.mkdtemp(prefix="console-bridge-")
        self.base = f"http://127.0.0.1:{SERVE_PORT}"
        self._init_fixture()

    def _init_fixture(self) -> None:
        f = self.fixture
        subprocess.run(["git", "-C", f, "init", "-q"], check=False)
        subprocess.run(["git", "-C", f, "config", "user.email", "bridge@example.invalid"], check=False)
        subprocess.run(["git", "-C", f, "config", "user.name", "Console Bridge"], check=False)
        Path(f, "README.md").write_text("# Orchestrator Console bridge workspace\n")

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [OPENCODE, "serve", "--port", str(SERVE_PORT), "--hostname", "127.0.0.1"],
            cwd=self.fixture,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            if self._healthy():
                return
            time.sleep(0.5)
        raise RuntimeError("opencode serve did not become healthy")

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base}/api/health", timeout=3) as r:
                return json.loads(r.read()).get("healthy") is True
        except Exception:
            return False

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"x-opencode-directory": self.fixture}
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode(errors="replace")
        return parsed.get("data", parsed) if isinstance(parsed, dict) else parsed

    def test_model(self, provider: str, model: str, log_path: str | None) -> dict:
        t0 = time.time()
        sid = None
        try:
            sid = self._req("POST", "/api/session", {"location": {"directory": self.fixture}})["id"]
            payload = {"model": {"providerID": provider, "id": model}}
            self._req("POST", f"/api/session/{sid}/model", payload)
            self._req(
                "POST",
                f"/api/session/{sid}/prompt",
                {"prompt": {"text": "Reply with exactly the single word: pong"}},
            )
            deadline = time.time() + 25
            assistant = None
            while time.time() < deadline:
                time.sleep(1.5)
                assistant = self._latest_assistant(sid)
                if assistant and (assistant["completed"] or assistant.get("finish")):
                    break
                if unavailable_in_log(log_path, provider, model):
                    break
            latency = int((time.time() - t0) * 1000)

            if unavailable_in_log(log_path, provider, model):
                return {
                    "state": "model_unavailable",
                    "latency_ms": latency,
                    "message": "凭证有效，但该模型当前不可用（OpenCode: ModelUnavailable）",
                }
            if assistant and assistant.get("error"):
                return {"state": "error", "latency_ms": latency,
                        "message": "厂商返回错误", "detail": sanitize(str(assistant.get("error")))}
            if assistant and assistant.get("finish") == "stop":
                return {
                    "state": "ok",
                    "latency_ms": latency,
                    "message": "连接正常，鉴权通过",
                    "tokens": assistant.get("tokens"),
                    "reply": (assistant.get("text") or "").strip()[:40],
                }
            return {"state": "timeout", "latency_ms": latency,
                    "message": "未在时限内收到回复"}
        except urllib.error.HTTPError as exc:
            return {"state": "error", "latency_ms": int((time.time() - t0) * 1000),
                    "message": f"请求失败 HTTP {exc.code}"}
        except Exception as exc:  # noqa: BLE001
            return {"state": "error", "latency_ms": int((time.time() - t0) * 1000),
                    "message": sanitize(str(exc))}
        finally:
            if sid:
                try:
                    self._req("DELETE", f"/api/session/{sid}")
                except Exception:
                    pass

    def _latest_assistant(self, sid: str):
        try:
            msgs = self._req("GET", f"/api/session/{sid}/message") or []
        except Exception:
            return None
        latest = None
        for m in msgs if isinstance(msgs, list) else []:
            info = m.get("info", m)
            if info.get("type") != "assistant":
                continue
            parts = m.get("parts") or info.get("content") or []
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            latest = {
                "text": text,
                "tokens": info.get("tokens"),
                "finish": info.get("finish"),
                "error": info.get("error"),
                "completed": bool((info.get("time") or {}).get("completed")),
            }
        return latest

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.fixture, ignore_errors=True)


def unavailable_in_log(log_path: str | None, provider: str, model: str) -> bool:
    if not log_path or not os.path.exists(log_path):
        return False
    needle = f"{provider}/{model}"
    try:
        with open(log_path, errors="replace") as fh:
            tail = fh.readlines()[-400:]
    except OSError:
        return False
    return any("ModelUnavailableError" in ln and needle in ln for ln in tail)


def sanitize(text: str) -> str:
    text = re.sub(r"sk-[A-Za-z0-9]{6,}", "sk-****", text)
    text = re.sub(r"(?i)(authorization|bearer)\s*[:=]?\s*\S+", r"\1 ****", text)
    return text[:200]


def load_models_json() -> dict:
    cache = opencode_paths().get("cache") or os.path.expanduser("~/.cache/opencode")
    try:
        data = json.load(open(os.path.join(cache, "models.json")))
    except (OSError, ValueError):
        return {}
    return data.get("providers", data)


def authed_provider_names() -> set[str]:
    """Display names in the auth.json credentials block (metadata only)."""
    raw = ANSI.sub("", run_cli("providers", "list"))
    cred_block = raw.split("Environment", 1)[0]
    names = set()
    for line in cred_block.splitlines():
        line = line.strip()
        if line.startswith("●"):
            name = line.lstrip("● ").rsplit("  ", 1)[0].strip()
            # trailing credential-type word (e.g. "api") lives after the name
            name = re.sub(r"\s+(api|oauth)$", "", name)
            names.add(name.strip())
    return names


def build_providers() -> list[dict]:
    registry = load_models_json()
    name_to_id = {v.get("name"): k for k, v in registry.items() if isinstance(v, dict)}
    authed = authed_provider_names()

    out = []
    seen = set()

    def cheap_model(models: list[str]) -> str:
        for pat in ("flash", "highspeed", "turbo", "M2.5"):
            for m in models:
                if pat.lower() in m.lower():
                    return m
        return models[0] if models else ""

    # 1) API-key providers present in auth.json
    for name in authed:
        pid = name_to_id.get(name)
        if not pid or pid in seen:
            continue
        entry = registry.get(pid, {})
        models = sorted((entry.get("models") or {}).keys())
        out.append({
            "id": pid, "name": name, "endpoint": endpoint_of(entry),
            "credType": "api", "connected": True, "envVar": None,
            "doc": entry.get("doc"), "models": models,
            "testModel": cheap_model(models), "health": "unknown",
        })
        seen.add(pid)

    # 2) Environment-variable providers whose var is actually set
    for pid, entry in registry.items():
        if pid in seen or not isinstance(entry, dict):
            continue
        env_vars = entry.get("env") or []
        set_var = next((v for v in env_vars if os.environ.get(v)), None)
        # only surface env providers that have a real catalog and a set var,
        # and that are not merely the env alias of an already-authed api plan
        if set_var and entry.get("models") and pid in {"deepseek"}:
            models = sorted(entry["models"].keys())
            out.append({
                "id": pid, "name": entry.get("name", pid), "endpoint": endpoint_of(entry),
                "credType": "env", "connected": True, "envVar": set_var,
                "doc": entry.get("doc"), "models": models,
                "testModel": cheap_model(models), "health": "unknown",
            })
            seen.add(pid)

    out.sort(key=lambda p: (p["credType"] != "api", p["name"]))
    return out


def endpoint_of(entry: dict) -> str:
    api = entry.get("api") or ""
    m = re.match(r"https?://([^/]+)", api)
    return m.group(1) if m else ""


# ---- HTTP handler ----------------------------------------------------------
SERVER: OpenCodeServer | None = None
LOG_PATH: str | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                html = (HERE / "index.html").read_bytes()
            except OSError:
                self._send(500, b"index.html not found", "text/plain")
                return
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path.startswith("/api/providers"):
            try:
                self._json(200, {"live": True, "providers": build_providers()})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": sanitize(str(exc))})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/api/test"):
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        provider = payload.get("provider")
        model = payload.get("model")
        if not provider or not model:
            self._json(400, {"error": "provider and model required"})
            return
        result = SERVER.test_model(provider, model, LOG_PATH)
        result["provider"] = provider
        result["model"] = model
        self._json(200, result)


def main() -> None:
    global SERVER, LOG_PATH
    if not shutil.which(OPENCODE) and not os.path.exists(OPENCODE):
        raise SystemExit(f"opencode executable not found: {OPENCODE}")
    LOG_PATH = os.path.join(
        opencode_paths().get("log", os.path.expanduser("~/.local/share/opencode/log")),
        "opencode.log",
    )
    SERVER = OpenCodeServer()
    print("· starting opencode serve …")
    SERVER.start()
    atexit.register(SERVER.stop)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: (SERVER.stop(), os._exit(0)))

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"· Orchestrator Console  ->  http://127.0.0.1:{PORT}")
    print("· press Ctrl+C to stop")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
