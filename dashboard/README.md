# 编排控制台 · Control Center Console

A self-contained, front-end **prototype** of the Personal AI Orchestrator control
panel. Open `dashboard/index.html` directly in a browser — no build, no server,
no dependencies.

> Prototype only. This is a UI design in a single HTML file with in-page state;
> it is **not** wired to a live backend yet. It deliberately does not start the
> Model Resource Orchestrator implementation.

## What it does

- **连接厂商 / Connect providers** — a masked API-key field per provider with a
  reveal toggle and a **连接 (Connect)** action. The panel is explicit that keys
  are handed to OpenCode's own auth store; the orchestrator never stores or logs
  them. Environment-variable providers (e.g. DeepSeek via `DEEPSEEK_API_KEY`) are
  shown as such instead of a key field.
- **测试连接 / Test connection** — a per-provider button that runs a connection
  check and reports the result inline (healthy + latency, or "鉴权通过但模型不可用"
  for a provider whose credential is valid but whose models are unavailable).
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

## How it would wire to the real system (next phase)

- Connection state and quota meters read from `opencode providers list` and the
  session/usage API.
- **连接 / Connect** delegates to `opencode providers login`, so credentials
  never touch the orchestrator.
- Pool assignments become the `RoutingDecision` table the routing daemon serves,
  consumed by the thin OpenCode adapter (see `spikes/opencode/`).

That backend belongs to the Model Resource Orchestrator phase and is intentionally
out of scope for this prototype.
