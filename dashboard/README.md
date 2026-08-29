# 编排控制台 · Control Center Console

A front-end control panel for the Personal AI Orchestrator. It runs two ways:

- **Standalone demo** — open `dashboard/index.html` directly in a browser. No
  build, no dependencies. Uses bundled demo data and a simulated connection test.
- **Live backend** — run the local bridge and the panel talks to your real
  OpenCode install (real providers, full catalogs, and a **real** connection
  test that switches a disposable session to the model and runs one tiny turn):

  ```bash
  python3 dashboard/serve.py          # needs a local `opencode` on PATH
  # then open http://127.0.0.1:8899
  ```

  A badge in the top bar shows which mode is active (实时后端 / 演示数据).

> The bridge is a thin read/execute layer over the `opencode` CLI. It does not
> start the Model Resource Orchestrator backend; routing-pool assignments remain
> in-page state (export/persistence is future work).

## What it does

- **连接厂商 / Connect providers** — a masked API-key field per provider with a
  reveal toggle and a **连接 (Connect)** action. The panel is explicit that keys
  are handed to OpenCode's own auth store; the orchestrator never stores or logs
  them. Environment-variable providers (e.g. DeepSeek via `DEEPSEEK_API_KEY`) are
  shown as such instead of a key field.
- **测试连接 / Test connection** — a per-provider button. In live mode it runs a
  **real** turn through OpenCode (switch a disposable session to the model, prompt
  one word, classify the result) and reports latency + token usage inline —
  healthy, or "凭证有效但模型不可用" for a provider whose credential is valid but
  whose models OpenCode reports as unavailable. In standalone mode it is simulated.
- **套餐额度 / Plan quotas** — an honesty-graded meter per provider using the
  spike's quota states (`EXACT / PROVIDER_REPORTED / LOCALLY_MEASURED /
  ESTIMATED / UNKNOWN`). Because OpenCode does not expose plan quota, providers
  default to an `UNKNOWN` hatched track rather than a fake percentage; entering a
  known plan limit turns it into a `LOCALLY_MEASURED` meter.
- **模型池 / Model pools** — drag any model chip between routing pools
  (快速通道 / 重度编程 / 推理 / 兜底 / 已禁用·阻断); counts and KPIs update live.
- **路由模式 / Routing mode** — a 旁路 / 影子 / 主动 (BYPASS / SHADOW / ACTIVE)
  segmented control mirroring the adapter contract, plus a light/dark theme
  toggle.

The seed data reflects the real providers, model catalogs, and health states
discovered during the OpenCode Stage C runtime work (MiniMax connected and
healthy; Z.AI connected but reporting "model unavailable"; DeepSeek via env var).

## The backend bridge (`serve.py`)

A ~300-line stdlib HTTP server that shells out to the real `opencode` CLI:

| Route | What it does |
| --- | --- |
| `GET /` | serves the dashboard (same origin — no CORS) |
| `GET /api/providers` | authenticated providers + **full** catalogs + endpoints + plan docs, from `opencode providers list` and the `models.json` registry |
| `POST /api/test` | a real, bounded connection test for `{provider, model}` — switches a disposable session to the model and runs one tiny turn; returns `ok` / `model_unavailable` / `error` with latency and token usage |

**Credential safety.** The bridge never reads, stores, logs, or returns credential
*values*. Provider auth is reported only as presence + type + the env-var *name*;
error strings are sanitized. OpenCode owns the credentials — the bridge only asks
it to run, exactly like the Stage C harness in `spikes/opencode/`.

### Still future work

- **连接 / Connect** should delegate to `opencode providers login` (currently the
  panel only records connection state client-side).
- Real plan-quota numbers (OpenCode does not expose them; meters stay `UNKNOWN`
  until you enter a limit).
- Persisting pool assignments as the `RoutingDecision` table the routing daemon
  serves — the Model Resource Orchestrator phase.
