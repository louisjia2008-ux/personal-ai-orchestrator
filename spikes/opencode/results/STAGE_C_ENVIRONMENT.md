# OpenCode Stage C — Environment & Runtime Decision

Provider-native Stage C was executed on a local macOS machine against a real,
already-authenticated OpenCode installation. This file records the runtime facts
and the (evidence-based) decision to run Stage C on the standalone `opencode`
runtime instead of the Stage A/B pinned `opencode2` beta.

## Host

| Field | Value |
| --- | --- |
| macOS | 26.5.1 (arm64 / Apple Silicon) |
| OpenCode executable | `/Users/<redacted>/.opencode/bin/opencode` |
| OpenCode version | **1.18.23** |
| Node | v22.22.0 |
| npm | 10.9.4 |
| Python | 3.13 (repo `.venv`, editable install of the package) |
| git | 2.54.0 |
| Repo branch | `spike/opencode-shadow-routing` |
| Repo HEAD at takeover | `9ca1524cad1214a39209ee2ee12436f05a780a95` |

## Runtime decision (recorded drift from the Stage A/B pin)

Stage A/B pin the matched pair
`@opencode-ai/cli@0.0.0-beta-18387` + `@opencode-ai/plugin@0.0.0-beta-18387`
and drive it through the `opencode2` executable. That pair **cannot run Stage C
on this Mac**, for two independently sufficient reasons:

1. **No macOS binary for the pinned CLI.** `@opencode-ai/cli-darwin-arm64`
   publishes versions only up to `1.16.2`; there is **no `darwin-arm64` binary
   for `0.0.0-beta-18387`** (nor for any `beta-17xxx/18xxx`). `npm install`
   fails with `ETARGET … @opencode-ai/cli-darwin-arm64@0.0.0-beta-18387`.
2. **The real provider credentials live in the standalone runtime.** MiniMax and
   Z.AI are authenticated in `opencode` 1.18.23's own auth store
   (`~/.local/share/opencode/auth.json`), which is the maintained darwin
   distribution.

The matched pair **on this machine** is therefore `opencode` 1.18.23 (runtime,
already installed, holds the credentials) with `@opencode-ai/plugin@1.18.23` /
`@opencode-ai/sdk@1.18.23` (the `latest` line, installed to a disposable prefix
only for type/SDK access). Stage A/B artifacts and their beta-18387 pin are left
untouched; their Linux CI still exercises the pinned pair.

### Adapter realization on 1.18.23

The beta-18387 plugin API used by the committed `integrations/opencode/plugin.ts`
(`Plugin.define` → `ctx.command.transform(draft.add(... execute ...))` →
`ctx.session.switchModel`) does **not** exist unchanged in 1.18.23: the v2
plugin `CommandDraft` exposes only `list/get/update/remove` (no `add`), and
`switchModel` is no longer a plugin-context method. In 1.18.23 the session-scoped
switch is the documented HTTP operation **`POST /api/session/{id}/model`**, which
emits the same **`session.next.model.switched`** durable event the Stage B ACTIVE
harness asserts on.

Stage C therefore realizes the thin adapter as a **host-side client over that
same OpenCode operation**, driven by the committed contract logic
(`resolve_adapter_outcome`) and the committed credential-free `fake_daemon.py`.
The routing/safety contract is unchanged; only the transport of the switch
differs from the beta in-process plugin.

## Credential safety

- The orchestrator (fake daemon) and the Stage C driver never read, copy,
  serialize, log, or transmit provider credentials.
- `auth.json` was **never** opened, printed, `cat`/`grep`/`jq`-ed, or copied.
- Provider authentication was confirmed only through supported metadata surfaces
  (`opencode providers list`, `opencode debug v2`, `opencode debug paths`, and
  the non-secret `~/.cache/opencode/models.json` registry).
- Evidence files contain provider ids, model ids, token/usage counters, finish
  reasons, and the disposable fixture's own H1 heading — no secrets.

## Providers discovered (authenticated)

| Display name (auth.json) | Provider id | Credential type |
| --- | --- | --- |
| MiniMax Token Plan (minimaxi.com) | `minimax-cn-coding-plan` | api |
| Z.AI Coding Plan | `zai-coding-plan` | api |
