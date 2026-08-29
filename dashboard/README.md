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
  **real** turn through OpenCode in a disposable session. Success now requires the
  completed assistant message to report the **exact requested provider and model**,
  `finish == stop`, and the deterministic reply `pong`; a fallback model cannot be
  misreported as a successful test of the requested target. The bridge reports
  latency + token usage inline. In standalone mode the test is simulated.
- **套餐额度 / Plan quotas** — quota **confidence** follows the core contract:
  `EXACT / ESTIMATED / UNKNOWN`. Measurement/source method is a separate dimension,
  e.g. `PROVIDER_REPORTED / LOCALLY_MEASURED / INFERRED / MANUAL`. The UI must not
  merge those two concepts into one enum. Because OpenCode does not expose plan
  quota, providers default to an `UNKNOWN` hatched track rather than a fake
  percentage. A manually entered limit is local measurement metadata, not magically
  provider-reported `EXACT` quota truth.
- **支出与节省 / Spend & savings** — separates pay-as-you-go **API 实付** from
  subscription **套餐等值/已省**. Plan usage is priced at each model's *standalone*
  API rate (`实际单价 × 实际消耗`) to show how much value the flat plan covered —
  computed from real `opencode stats --models` × the models.dev registry prices
  (live), or a real on-machine snapshot (standalone). The backend returns
  `measurement_method: LOCALLY_MEASURED` for this calculation rather than treating
  it as quota confidence.
- **成本 / 定价 · 延迟** — each provider card expands to a per-model list with the
  registry unit price ($ in / out per 1M tokens), a 套餐/按量 tag, and the measured
  latency from the last connection test.
- **导出 pools.json / Export** — writes the current pool assignment to
  `dashboard/pools.json` (live) or downloads it (standalone) for a routing daemon
  to consume.
- **连接更多厂商 / Connect more** — a "添加厂商" card lists connectable providers;
  the button triggers OpenCode's real `opencode providers login` (credentials
  handled entirely by OpenCode), then "刷新状态" re-reads auth.
- **模型池 / Model pools** — drag any model chip between routing pools
  (未分配 / 快速通道 / 重度编程 / 推理 / 兜底 / 已禁用·阻断); counts and KPIs update live.
- **路由模式 / Routing mode** — a 旁路 / 影子 / 主动 (BYPASS / SHADOW / ACTIVE)
  segmented control mirroring the adapter contract, plus a light/dark theme
  toggle. This prototype control does not itself authorize production ACTIVE routing.

The seed data reflects the real providers, model catalogs, and health states
discovered during the OpenCode Stage C runtime work (MiniMax connected and
healthy; Z.AI connected but reporting "model unavailable"; DeepSeek via env var).

## The backend bridge (`serve.py`)

A stdlib loopback HTTP server that shells out to the real `opencode` CLI:

| Route | What it does |
| --- | --- |
| `GET /` | serves the dashboard |
| `GET /api/providers` | authenticated providers + full catalogs + prices + endpoints + plan docs + connectable providers |
| `POST /api/test` | bounded exact-provider/model connection test in a disposable session |
| `GET /api/stats` | real spend/value estimates from `opencode stats --models` × registry prices |
| `POST /api/pools` | writes the current pool assignment to `dashboard/pools.json` |
| `POST /api/login` | starts OpenCode's supported provider-login flow for an allowlisted provider |

**Credential safety.** The bridge never reads, stores, logs, or returns credential
*values*. Provider auth is reported only as presence + type + the env-var *name*;
error strings are sanitized. OpenCode owns the credentials — the bridge only asks
it to run.

**Local control-plane safety.** The server binds to `127.0.0.1` and API requests
also enforce loopback `Host` plus same-origin `Origin` when a browser supplies one.
POST endpoints require `application/json`; this blocks cross-origin `no-cors`
form/text requests from driving local side effects. Provider login is allowlisted,
model connection tests are rate-limited, and pool export validates routing mode.

### Still future work

- Wire the visible **连接 / Connect** flow entirely through the supported OpenCode
  login action instead of keeping any demo-only client-side connection state.
- Real plan-quota numbers from the P3 quota collectors; OpenCode itself does not
  expose authoritative plan quota.
- Persist pool assignments into the orchestrator's durable routing-policy state
  rather than treating `dashboard/pools.json` as production authority.
- Consume the scheduler's separate `confidence` and `measurement_method` fields
  directly once the dashboard is wired to the real daemon.
