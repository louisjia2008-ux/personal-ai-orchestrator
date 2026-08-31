# P4.1 macOS Menu Bar Control Plane Acceptance — 2026-08-31

Branch: `feat/p4-macos-menubar` (from merged `main` `5079ecbd609e75964ee9a4fa7cfb286f19257b18`,
PR #22 merge commit). This record covers P4.1 only; P4.2/P4.3 were not started.

## Objective

Native macOS menu-bar client for the orchestrator: a control-plane CLIENT over the existing
P4.0 `/v1` UDS API. The daemon remains authoritative.

## Implementation

```text
macos/PAOMenuBar (SwiftPM package)
  Sources/PAOControlKit    typed /v1 models, POSIX UDS HTTP client, connection state,
                           socket discovery, status summary, @MainActor display store,
                           privacy-conscious logging
  Sources/PAOMenuBar       SwiftUI MenuBarExtra app (.window style), compact control view,
                           quick-submit view
  Tests/PAOControlKitTests XCTest suite incl. a real in-test UDS daemon
```

- Language/UI: Swift + SwiftUI `MenuBarExtra`; **deployment target macOS 13** (minimum for
  `MenuBarExtra`); built/tested on macOS 26.5, Swift 6.3, Xcode 26.6.
- Transport: HTTP/1.1 over the daemon's `0600` Unix Domain Socket with
  `Connection: close` framing (matching the daemon's HTTP/1.0-style responses), bounded
  8 MiB response cap, send/receive timeouts, `SO_NOSIGPIPE` (a peer closing mid-write is an
  error, never a crash), `EINTR`-safe read/write loops.
- No Electron, no web wrapper, no JavaScript runtime.

## API usage (exactly the P4.0 contract)

`GET /v1/health`, `GET /v1/tasks?limit=20`, `GET /v1/tasks/{id}`,
`GET /v1/tasks/{id}/runs|verification|routing`, `POST /v1/tasks`,
`POST /v1/tasks/{id}/cancel`, `GET /v1/providers`, `GET /v1/quota`,
`GET /v1/active-status`, read-only approvals surface unchanged. No second backend; no new
authority added for UI convenience.

## Socket discovery

UserDefaults `controlSocketPath` → env `PAO_CONTROL_SOCKET` →
`~/.personal-ai-orchestrator/control.sock`. Explicit configuration only; no filesystem
scanning. Paths longer than 104 bytes fail closed into a `SOCKET_PATH_TOO_LONG` state
(tested).

## Connection / error states

`CONNECTED`, `DAEMON_NOT_RUNNING` (ENOENT), `SOCKET_INVALID` (ECONNREFUSED/stale),
`ACCESS_DENIED` (EACCES/EPERM), `API_VERSION_MISMATCH` (health `api_version != v1`),
`MALFORMED_RESPONSE`. Sanitized error codes are surfaced verbatim (e.g. `409
running_task_cancellation_requires_execution_supervisor`); no stack traces; transient daemon
restarts never crash the app (verified live).

## Status summary (derived only from API evidence)

Precedence: `disconnected > blocked > quotaLimited > working > healthy`. `quotaLimited`
requires explicit observed availability `EXHAUSTED_OBSERVED`/`COOLDOWN`. Quota rendering:
percentages only for `EXACT`; `ESTIMATED`/`UNKNOWN` render "unknown (confidence: ...)"
(tested).

## ACTIVE display

Read-only label (`DISABLED_BY_DESIGN`) plus daemon-reported blocking reasons. No
enable/force/override/approval control exists anywhere in the client (independently
reviewed).

## Refresh policy

2 s while the menu is open; 15 s background; manual Refresh button; disconnected backoff
2 s → 4 s → … → 60 s. No high-frequency loops.

## Daemon ownership

The client detects and reports daemon state; it never spawns, signals or kills anything
(`kill`/`pkill`/`killall` are absent from the codebase). Daemon lifecycle integration is
deferred by design.

## Tests

Native XCTest: **25 passed / 0 failures**, covering request encoding, response parsing,
daemon-unavailable, stale socket, path-too-long, full UDS round trip, sanitized HTTP errors,
malformed responses, version mismatch, connection recovery across daemon restart, model
decoding, credential-field rejection/ignore, ESTIMATED/UNKNOWN rendering, status precedence,
ACTIVE disabled rendering, quick-submit idempotent shape + duplicate guard, 409 cancel
rendering, refresh state transitions, outage state discard.

Python regression (unchanged authority): ruff PASS; `pytest -p no:cacheprovider`
**217 passed**; `git diff --check` PASS; OpenCode adapter untouched (10/10 on CI).

## MiniMax CN independent read-only review

Executed 2026-08-31 via `minimax-cn-coding-plan/MiniMax-M2.7` in read-only mode against the
committed worktree (no edits made; verified via `git status`). Verdict: **SAFE — no
authority-boundary violations detected**. No findings on credential leakage, quota-confidence
misrepresentation, ACTIVE-control leakage, unnecessary permissions, or daemon-ownership
mistakes. Findings fixed afterward by GLM as the single writer (commit
`fix: address independent P4.1 review findings`):

1. LOW — refresh loop now checks task cancellation between refresh and sleep.
2. LOW — UDS write/read loops retry on `EINTR`.
3. Additional hardening found during fix (not in review): `SO_NOSIGPIPE` set on the client
   socket so a mid-write peer close cannot terminate the app.

All native tests re-run green after fixes (25/25).

## Runtime acceptance (real app + real daemon, target Mac)

Real Python daemon (`--control-socket`) plus `swift build -c release` app binary with
`PAO_MENUBAR_STDERR_LOG=1` (sanitized codes only):

```text
pao-menubar connection=CONNECTED
pao-menubar tasks running=0 ready=0 blocked=0 verified=0
pao-menubar tasks running=0 ready=1 blocked=0 verified=0      (after CLI submit)
pao-menubar connection=DISCONNECTED:SOCKET_INVALID            (daemon stopped)
pao-menubar op=refresh outcome=stale_socket                    (backoff, no crash loop)
pao-menubar connection=CONNECTED                               (daemon restarted)
pao-menubar tasks running=0 ready=1 blocked=0 verified=0      (authoritative reload)
```

Verified live: connected state, disconnected/backoff state, task-list refresh after submit,
daemon restart/reconnect with authoritative state reload, process stability across the full
outage cycle.

**HUMAN_ACCEPTANCE_REQUIRED**: visual confirmation of the menu-bar icon/panel rendering,
click-through of quick submit, and cancel UX cannot be honestly claimed from a terminal
session. Automated evidence above covers the underlying behaviors; visual acceptance is left
to the owner.

## Security / permissions

- No entitlements, no sandbox changes, no Accessibility/Screen Recording/Microphone/Camera/
  Full Disk Access requests; plain unsigned local SwiftPM executable.
- No credential material in Info.plist/UserDefaults/source/build settings/logs; models
  ignore unknown JSON fields, and a canary test proves credential-shaped fields cannot
  surface.
- Logging records connection transitions, operation categories, task counts and sanitized
  codes only — never intent text, tokens or provider stderr.

## Gates

```text
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN (read-only in client)
OWNER_APPROVAL_FOR_ACTIVE: ABSENT
PROVIDER_EXACT_RESET_CYCLES: 0 (unchanged)
P0/P1/P3/P4.0 AUTHORITY: UNCHANGED
```

## Remaining work

- P4.2 full dashboard + WidgetKit; P4.3 DeskPet / optional Telegram.
- Owner visual acceptance of the menu-bar UI.
- Deferred: owner-gated approval mutations, daemon lifecycle integration, RUNNING-task
  cancellation via execution supervisor surfacing.

## Addendum: human-acceptance repair pass (2026-08-31, later same day)

The owner performed real visual acceptance and found three polish issues; all were fixed
on `feat/p4-macos-menubar` (PR #23, commits `6a619eb`, `84cb608`) without touching any
authority boundary.

### 1. Native localization (zh-Hans + en fallback)

- Standard `.lproj`/`Localizable.strings` catalogs (en + zh-Hans) bundled via SwiftPM
  resources in `PAOControlKit`; `defaultLocalization: "en"`. Per-lproj `.copy` is used
  because `.process` lowercases `zh-Hans.lproj` → `zh-hans.lproj`, which CFBundle then
  fails to match on case-sensitive volumes.
- Language selection follows macOS preferred languages (`Locale.preferredLanguages`,
  e.g. `zh-Hans-CN` → `zh-Hans`) with deterministic canonical/script/bare-code matching
  and English fallback; selection is explicit because CFBundle's automatic matching for
  SwiftPM library bundles launched as plain executables misresolves to `en`.
- Presentation layer (`L10n`) maps status titles, connection reasons, sections, actions,
  notices and quota wording; machine protocol enums (`EXACT`, `ESTIMATED`, `UNKNOWN`,
  `DISABLED_BY_DESIGN`, `EXHAUSTED_OBSERVED`, `COOLDOWN`, task states, blocking reasons)
  stay verbatim per API contract. EXACT-only percentage rule preserved when localized.
- Notices became structured enums (`SubmitNotice`/`CancelNotice`) so daemon values stay
  raw until presentation; both catalogs are coverage-tested, and raw enums are asserted
  absent from both catalogs.

### 2. Ephemeral success state cleared on disconnect

`OrchestratorStore.transition` now clears `lastSubmittedTaskId`, `submitNotice` and
`cancellationNotice` whenever the connection leaves `CONNECTED`, alongside the existing
authoritative-cache discard. A stale green "authoritative task id" banner can no longer
visually coexist with a disconnected UI. Durable daemon state is untouched (UI-only).
Regression: `testDisconnectAfterSuccessfulSubmitClearsEphemeralSuccessState`.

### 3. Graceful daemon Ctrl-C

`daemon.main` catches `KeyboardInterrupt` around `serve()` and returns `0`; the `finally`
block still stops the control plane and closes the store. Unexpected exceptions keep
propagating. Regression: `tests/test_daemon_shutdown.py` launches the real daemon, sends
SIGINT, asserts exit 0, no traceback, socket cleaned.

### Repair verification

- Swift: `swift build` + `swift test` → **32/32 passed** (25 previous + 1 disconnect
  regression + 6 localization tests). Release build OK.
- Python: ruff PASS; `pytest -p no:cacheprovider` → **219 passed** (217 + 2 shutdown);
  `git diff --check` PASS; adapter untouched (CI).
- MiniMax CN read-only review of the repair: **ACCEPT** on localization, disconnect UX
  and SIGINT change; no authority/credential/concurrency findings; remaining notes are
  documented design choices (raw enums per contract, capped backoff for invalid paths).
- Live recheck on this Mac (system languages `zh-Hans-CN`, `en-CN`), same app process
  throughout:
  - `ui_language=zh-Hans` at startup; separate binary probe through the same build
    rendered 正常/已连接/未连接/守护进程未运行/最近任务/暂无任务/快速提交/提供商 / 额度/
    尚未注册提供商/生产 ACTIVE/提交/取消/刷新/退出, submit notice
    `已提交任务 menubar-abc（状态 SUBMITTED）。`, quota `未知（置信度：ESTIMATED）`.
  - submit → task counts updated; SIGINT daemon → exit clean, **0 traceback lines**,
    socket cleaned; app → `DISCONNECTED:DAEMON_NOT_RUNNING`; daemon restart → app
    reconnected and reloaded authoritative state; no application restart.
  - Banner clearing is store-level (regression-tested); its visual confirmation belongs
    to the repeated human acceptance.

HUMAN_ACCEPTANCE_REQUIRED (repeat): visual confirmation of zh-Hans rendering in the menu
bar panel, localized success banner after submit, and the banner disappearing on daemon
stop.
