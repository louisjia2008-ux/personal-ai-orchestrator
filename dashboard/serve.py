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
import urllib.parse
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
            "pricing": price_map_for(registry, pid, models),
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
                "pricing": price_map_for(registry, pid, models),
                "testModel": cheap_model(models), "health": "unknown",
            })
            seen.add(pid)

    out.sort(key=lambda p: (p["credType"] != "api", p["name"]))
    return out


# A curated set of well-known providers offered for "connect via login".
CONNECTABLE = ["anthropic", "openai", "minimax", "zai", "openrouter", "google"]


def list_projects() -> list[dict]:
    try:
        data = json.loads(run_cli("debug", "scrap", timeout=15) or "[]")
    except (ValueError, json.JSONDecodeError):
        return []
    out = []
    for p in data if isinstance(data, list) else []:
        wt = p.get("worktree") or ""
        if p.get("id") == "global" or not wt or wt == "/":
            continue
        # skip disposable temp workspaces (mktemp fixtures)
        if "/T/tmp." in wt or wt.startswith("/tmp/") or "/var/folders/" in wt:
            continue
        out.append({"id": p.get("id"), "path": wt, "name": os.path.basename(wt.rstrip("/")) or wt})
    return out


def available_providers(connected_ids: set[str]) -> list[dict]:
    registry = load_models_json()
    out = []
    for pid in CONNECTABLE:
        if pid in connected_ids:
            continue
        entry = registry.get(pid)
        if isinstance(entry, dict):
            out.append({"id": pid, "name": entry.get("name", pid)})
    return out


def endpoint_of(entry: dict) -> str:
    api = entry.get("api") or ""
    m = re.match(r"https?://([^/]+)", api)
    return m.group(1) if m else ""


# ---- Pricing & spend (all figures from the real models.dev registry) -------
# Base providers that carry standalone pay-as-you-go prices for a model that a
# subscription plan lists at $0. Used to compute the API-equivalent value a plan
# covered ("how much you'd have paid without the plan").
BASE_PRICE_PROVIDERS = {
    "minimax": ["minimax-cn", "minimax"],
    "glm": ["zhipuai", "zai"],
}
COST_KEYS = ("input", "output", "cache_read", "cache_write")


def _model_cost(registry: dict, provider: str, model: str) -> dict | None:
    e = registry.get(provider, {})
    m = (e.get("models") or {}).get(model)
    return m.get("cost") if isinstance(m, dict) else None


def _family(provider: str, model: str) -> str | None:
    s = f"{provider} {model}".lower()
    if "minimax" in s:
        return "minimax"
    if "glm" in s or "zai" in s or "zhipu" in s:
        return "glm"
    return None


def resolve_price(registry: dict, provider: str, model: str) -> dict:
    """Return {price, plan, source}. If the provider's own price is all-zero
    (a plan), fall back to a base provider's standalone price for the same model."""
    own = _model_cost(registry, provider, model)
    if own and any(float(own.get(k, 0) or 0) for k in COST_KEYS):
        return {"price": own, "plan": False, "source": provider}
    fam = _family(provider, model)
    for base in BASE_PRICE_PROVIDERS.get(fam, []):
        cost = _model_cost(registry, base, model)
        if cost and any(float(cost.get(k, 0) or 0) for k in COST_KEYS):
            return {"price": cost, "plan": True, "source": base}
    return {"price": own or {k: 0 for k in COST_KEYS}, "plan": own is not None, "source": provider}


def cost_usd(tokens: dict, price: dict) -> float:
    # prices are USD per 1,000,000 tokens
    return round(sum((tokens.get(k, 0) or 0) / 1e6 * float(price.get(k, 0) or 0) for k in COST_KEYS), 4)


def _num(text: str) -> float:
    text = text.replace(",", "").strip().rstrip("$").strip()
    mult = 1.0
    if text and text[-1] in "KkMmBb":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[text[-1].lower()]
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return 0.0


STRIP_BOX = str.maketrans("", "", "│├─┤┌┐└┘")


def parse_model_stats(days: int | None = None, project: str | None = None) -> list[dict]:
    args = ["stats", "--models"]
    if days:
        args += ["--days", str(days)]
    if project:
        args += ["--project", project]
    raw = run_cli(*args, timeout=40)
    lines = raw.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "MODEL USAGE" in ln)
    except StopIteration:
        return []
    labels = {
        "Input Tokens": "input", "Output Tokens": "output",
        "Cache Read": "cache_read", "Cache Write": "cache_write", "Cost": "reported_cost",
    }
    out: list[dict] = []
    cur: dict | None = None
    for ln in lines[start + 1:]:
        s = ln.translate(STRIP_BOX).strip()
        if not s:
            continue
        matched = None
        for lab, key in labels.items():
            if s.startswith(lab):
                matched = (key, s[len(lab):].strip())
                break
        if matched:
            key, valtext = matched
            if cur is not None:
                if key == "reported_cost":
                    cur["reported_cost"] = _num(valtext)
                else:
                    cur["tokens"][key] = _num(valtext)
        elif "/" in s and " " not in s.split("/")[0]:
            if cur:
                out.append(cur)
            provider, _, model = s.partition("/")
            cur = {"provider": provider, "model": model,
                   "tokens": {k: 0 for k in COST_KEYS}, "reported_cost": 0.0}
    if cur:
        out.append(cur)
    return out


def spend_report(days: int | None = None, project: str | None = None) -> dict:
    registry = load_models_json()
    rows = []
    api_actual = 0.0
    plan_saved = 0.0
    by_provider: dict[str, dict] = {}
    for m in parse_model_stats(days, project):
        pr = resolve_price(registry, m["provider"], m["model"])
        value = cost_usd(m["tokens"], pr["price"])
        is_plan = pr["plan"]
        row = {
            "provider": m["provider"], "model": m["model"], "tokens": m["tokens"],
            "plan": is_plan, "price": {k: pr["price"].get(k, 0) for k in COST_KEYS},
            "price_source": pr["source"], "value_usd": value,
            "reported_cost": round(m.get("reported_cost", 0.0), 4),
        }
        rows.append(row)
        agg = by_provider.setdefault(m["provider"], {"plan": is_plan, "value_usd": 0.0, "actual_usd": 0.0})
        if is_plan:
            plan_saved += value
            agg["value_usd"] += value
            agg["plan"] = True
        else:
            api_actual += value
            agg["actual_usd"] += value
    rows.sort(key=lambda r: r["value_usd"], reverse=True)
    return {
        "days": days, "project": project, "rows": rows,
        "api_actual_usd": round(api_actual, 2),
        "plan_saved_usd": round(plan_saved, 2),
        "by_provider": {k: {"plan": v["plan"], "value_usd": round(v["value_usd"], 2),
                            "actual_usd": round(v["actual_usd"], 4)} for k, v in by_provider.items()},
        "state": "LOCALLY_MEASURED",
    }


def price_map_for(registry: dict, provider: str, models: list[str]) -> dict:
    out = {}
    for model in models:
        pr = resolve_price(registry, provider, model)
        out[model] = {**{k: pr["price"].get(k, 0) for k in COST_KEYS},
                      "plan": pr["plan"], "source": pr["source"]}
    return out


# ---- HTTP handler ----------------------------------------------------------
SERVER: OpenCodeServer | None = None
LOG_PATH: str | None = None
POOLS_PATH = os.environ.get("POOLS_PATH", str(HERE / "pools.json"))
LOGIN_PROCS: list[subprocess.Popen] = []


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
                provs = build_providers()
                self._json(200, {"live": True, "providers": provs,
                                 "available": available_providers({p["id"] for p in provs})})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": sanitize(str(exc))})
        elif self.path.startswith("/api/stats"):
            days = None
            m = re.search(r"[?&]days=(\d+)", self.path)
            if m:
                days = int(m.group(1))
            pm = re.search(r"[?&]project=([^&]+)", self.path)
            project = urllib.parse.unquote(pm.group(1)) if pm else None
            try:
                self._json(200, spend_report(days, project))
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": sanitize(str(exc))})
        elif self.path.startswith("/api/projects"):
            try:
                self._json(200, {"projects": list_projects()})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": sanitize(str(exc))})
        else:
            self._send(404, b"not found", "text/plain")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_POST(self):
        if self.path.startswith("/api/test"):
            payload = self._body()
            provider, model = payload.get("provider"), payload.get("model")
            if not provider or not model:
                self._json(400, {"error": "provider and model required"})
                return
            result = SERVER.test_model(provider, model, LOG_PATH)
            result["provider"], result["model"] = provider, model
            self._json(200, result)
        elif self.path.startswith("/api/pools"):
            payload = self._body()
            pools = payload.get("pools")
            if not isinstance(pools, list):
                self._json(400, {"error": "pools array required"})
                return
            doc = {
                "version": 1,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "mode": payload.get("mode", "SHADOW"),
                "pools": pools,
            }
            try:
                with open(POOLS_PATH, "w") as fh:
                    json.dump(doc, fh, indent=2, ensure_ascii=False)
                self._json(200, {"ok": True, "path": POOLS_PATH,
                                 "pools": len(pools),
                                 "models": sum(len(p.get("models", [])) for p in pools)})
            except OSError as exc:
                self._json(500, {"error": sanitize(str(exc))})
        elif self.path.startswith("/api/login"):
            payload = self._body()
            provider = payload.get("provider")
            if not provider:
                self._json(400, {"error": "provider required"})
                return
            # Kick off OpenCode's real login flow (may open a browser / print a
            # device code). We never handle the credential ourselves.
            try:
                proc = subprocess.Popen(
                    [OPENCODE, "providers", "login", provider],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                LOGIN_PROCS.append(proc)
                self._json(200, {
                    "started": True, "provider": provider,
                    "command": f"opencode providers login {provider}",
                    "hint": "已发起 OpenCode 登录。若未自动打开浏览器，请在终端运行上面的命令完成，然后点“刷新”。",
                })
            except Exception as exc:  # noqa: BLE001
                self._json(200, {
                    "started": False, "provider": provider,
                    "command": f"opencode providers login {provider}",
                    "hint": "无法自动发起，请在终端运行上面的命令完成登录后点“刷新”。",
                    "error": sanitize(str(exc)),
                })
        else:
            self._send(404, b"not found", "text/plain")


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
    atexit.register(lambda: [p.terminate() for p in LOGIN_PROCS if p.poll() is None])
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: (SERVER.stop(), os._exit(0)))

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"· Orchestrator Console  ->  http://127.0.0.1:{PORT}")
    print("· press Ctrl+C to stop")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
