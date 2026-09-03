# P4.2 Full macOS Dashboard + WidgetKit Acceptance

Date: 2026-08-31 (takeover completion 2026-08-31 evening session; interactive
dashboard repair P4.2.3 same evening)

Status: `P4_2_INTERACTIVE_DASHBOARD_TECHNICAL_COMPLETE_HUMAN_RECHECK_REQUIRED`

Owner acceptance history:
- First human pass: `FAILED_NEEDS_UX_REPAIR` — dashboard visually present but
  sidebar navigation appeared non-functional and screens were too passive.
- P4.2.3 repair landed (below); a fresh owner interaction pass is required.

## P4.2.3 Interactive Dashboard Repair

- SIDEBAR_FIXED: the sidebar now uses a canonical `NavigationSplitView` +
  `List(selection:)` with an explicit `ForEach` + `.tag(DashboardSection)`
  per row (no reliance on implicit data-driven selection). Selection persists
  through `SceneStorage`, and `DashboardSection` moved into `PAOControlKit`
  with regression coverage.
- OVERVIEW_METRICS_CLICKABLE: RUNNING/READY/BLOCKED/VERIFIED/COMPLETED tiles
  navigate to the Tasks list filtered to the matching state(s).
  Navigation only — authoritative state is never mutated. `MetricsFilter`
  encodes the composite-counter semantics (READY includes SUBMITTED;
  VERIFIED includes COMPLETED) with unit tests.
- DASHBOARD_NEW_TASK: prominent `新建任务` toolbar action (⌘N) opens a sheet
  that submits through the existing typed P4 control API with request-id
  idempotency and duplicate guard; on success it navigates to Tasks with the
  authoritative task selected and its detail loaded.
- TASK_ROW_SELECTION: explicit `.tag(task.taskId)` rows; selection loads the
  detail pane; search and state filter operate on composite semantics.
- ROUTING/VERIFICATION_INTERACTION: direct entry now presents a task picker
  (retaining last selection); with no tasks the sections show actionable empty
  states with a `新建任务` entry point instead of dead panels.
- PROVIDER_DRILLDOWN: execution-target cards expand (DisclosureGroup) to show
  normalized health: observed state, measurement source, confidence, observed
  time, sanitized reason code, runtime availability.
- QUOTA_DRILLDOWN: quota cards explain confidence (EXACT / ESTIMATED /
  UNKNOWN), measurement source (PROVIDER_EXACT / LOCALLY_MEASURED /
  LOCALLY_INFERRED), pool state (EXHAUSTED / RECOVERED / UNKNOWN with a
  scheduling-impact note), and last observation time. UNKNOWN never renders a
  fabricated percentage (unit-tested).
- SETTINGS_REALITY_CHECK: `Launch at Login` is labeled preference-only (footer
  states the system login item is not wired up in P4.2); `Auto Start Daemon`
  is real — the app reads the preference at launch and skips daemon startup
  when off (verified by a unit test). ACTIVE remains read-only with blocking
  reasons listed.
- REFRESH_INTERACTION: the toolbar refresh shows a progress state, coalesces
  repeated clicks through an `isRefreshing` guard (unit-tested), and recovers
  from daemon unavailability.
- EMPTY_STATES: tasks/no-match/no-events/no-providers/no-routing/no-verification
  states carry context-sensitive copy and safe actions; no fabricated data.
- ACTIVE_MUTATION_SURFACE: none added. Clickable means navigate/inspect/
  expand/filter/refresh only.

### P4.2.3 verification evidence

- `swift test`: 46 tests PASS (6 new interactive-dashboard regressions).
- Python: `ruff` clean; `pytest` 229 PASS (one transient timing flake in
  `test_no_client_path_can_mark_verified` passed on immediate rerun; no
  Python sources were touched in this phase).
- Xcode: host + widget extension Release build PASS with the new
  `PAODashboardUITests` target compiled (XCUITest suite covering sidebar
  navigation, metric navigation, task creation through the sheet, and empty
  states).
- Real packaged app: `/Applications/Personal AI Orchestrator.app` launched
  via LaunchServices; daemon healthy; a task created through the daemon API
  appeared in the app's refreshed dashboard data within one refresh cycle
  (total 0 → 1, CONNECTED), evidencing the live refresh pipeline.
- Automated UI clicking on this Mac is currently blocked by TCC (osascript
  assistive access denied; XCUITest automation mode timed out; screencapture
  denied). Granting those permissions requires owner action in System
  Settings (no sudo was used). The XCUITest suite is ready to run once the
  owner grants automation permissions; until then the real click matrix is
  the owner's recheck list:
  click 任务 / Agents / 提供商 / 额度 / 调度决策 / 验证 / 历史 / 设置 →
  page changes; overview metric → filtered tasks; 新建任务 → task created;
  task click → detail pane; refresh works; settings labeled honestly.

Baseline

- Base branch: `origin/main`
- Required P4.1 merge commit: `6829e113b9c56d85ce44be4630ed29204e100ece`
- P4.2 branch: `feat/p4-full-dashboard`
- P4.2 worktree: `/Users/louisjia/Developer/pao-p4-full-dashboard`
- Production ACTIVE: `DISABLED_BY_DESIGN`
- Owner ACTIVE approval: absent
- Provider-exact reset cycles: `0`

## Separated Acceptance Status

- CODE_COMPLETE: PASS
  - Dashboard aggregate APIs (`/v1/dashboard`, `/v1/tasks/<id>/detail`), SwiftUI
    `NavigationSplitView` dashboard, MenuBarExtra, Settings scene, localized presentation.
  - App-owned daemon lifecycle: direct `Process` launch, non-blocking start lock
    (`flock LOCK_EX | LOCK_NB`), health wait, version mismatch, helper-missing failure,
    no shell interpolation.
  - Widget staleness: `isStale` fails closed on malformed timestamps; widget renders
    `UPDATED/STALE`, quota state/confidence, and never mutates authority.
- APP_BUNDLE_COMPLETE: PASS
  - `Personal AI Orchestrator.app` with `Contents/MacOS`, `Contents/Helpers/pao-daemon`,
    `Contents/PlugIns/PAOWidgetExtension.appex`, `Contents/Resources`.
  - SwiftPM resource bundle lives under `Contents/Resources` (previous app-root copy
    broke the host seal; repaired).
- SELF_CONTAINED_DAEMON_COMPLETE: PASS
  - `Contents/Helpers/pao-daemon` is a PyInstaller onefile Mach-O arm64 binary.
  - No project venv, no system Python, credential-free bootstrap.
- XCODE_HOST_TARGET: PASS
  - `project.yml` (XcodeGen) target `Personal AI Orchestrator`, macOS 13.0,
    links `PAOControlKit` package product, embeds the extension.
- XCODE_WIDGET_TARGET: PASS
  - XcodeGen target `PAOWidgetExtension` (`app-extension`), reuses `PAOControlKit`
    instead of duplicating models; `NSExtensionPointIdentifier
    com.apple.widgetkit-extension` metadata is generated from the spec.
- APPEX_EMBEDDED: PASS
  - `xcodebuild -scheme "Personal AI Orchestrator" -configuration Release` builds both
    products; `ValidateEmbeddedBinary` and nested `codesign --verify --deep --strict`
    pass after final assembly.
- LOCAL_SIGNING_STATE: PASS (local only, not distribution)
  - Host, helper, and appex are signed with the owner's local
    `Apple Development: LIHAO ZHAO` identity (TeamIdentifier `GZGN84XDLC`) when
    `PAO_CODESIGN_IDENTITY` is exported; the script defaults to ad-hoc `-`.
  - Host entitlements: App Group only. Widget entitlements: App Group + app sandbox.
  - No notarization, no Gatekeeper claims for arbitrary users.
- FINDER_OPEN_ACCEPTANCE: PASS
  - `open "/Applications/Personal AI Orchestrator.app"` through LaunchServices:
    cold start to `/v1/health` `{"status":"ok"}` in ~10 s, ACTIVE
    `DISABLED_BY_DESIGN`, Dashboard/MenuBar connected, snapshot written.
- WIDGET_DISCOVERY: PASS (local)
  - `pluginkit -m -A -vv -i com.personal-ai-orchestrator.dashboard.PAOWidgetExtension`
    lists the extension (`SDK = com.apple.widgetkit-extension`, display name
    `Personal AI Status`, parent bundle resolved).
  - Root cause of the earlier non-discovery: ad-hoc signatures without a team identity
    plus a missing `com.apple.security.app-sandbox` entitlement on the appex. After
    development-identity signing with sandboxed widget entitlements, registration
    succeeded without touching any private database.
- WIDGET_RUNTIME: PENDING_SYSTEM_RESTART
  - A local App Group container authorization stall (system-level, reproduced with
    minimized entitled probe apps: any mutating `open`/`unlink` inside the group
    container from an entitled app blocks indefinitely) currently prevents the host
    from refreshing the shared snapshot. The app degrades safely: background writes
    with a 2 s timeout fall back to the app-owned snapshot path, the UI never
    freezes, and the widget fails closed to `STALE`/`NO SNAPSHOT`.
  - The writer retries the App Group primary every 300 s and will heal after the
    owner restarts macOS. No sudo was used; no private databases were edited.
- HUMAN_VISUAL_ACCEPTANCE: REQUIRED
  - Owner double-clicks `Personal AI Orchestrator.app` in `/Applications`; inspects
    Dashboard, MenuBar, Chinese localization, task views, quota uncertainty wording,
    read-only ACTIVE state, and adds the `Personal AI Status` widget from the
    Notification Center gallery after the restart above.

## Takeover Record (2026-08-31)

- Codex stopped mid P4.2.2 with uncommitted work; remote head `d5856a3`.
- Verified and preserved: executable widget product in `Package.swift`, bundle
  assembler extension, resource-bundle sealing repair, lifecycle fixes (no weak
  capture race, non-blocking lock, sanitized stderr diagnostics), widget staleness.
- Completed in this session: XcodeGen metadata loss repair (`info/entitlements`
  properties moved into `project.yml` so regeneration is deterministic), Xcode
  product-based assembly, development-identity signing, WidgetKit discovery,
  lifecycle matrix, and the non-blocking snapshot writer (`WidgetSnapshotWriter`)
  with regression tests.

## Security Boundary

- The Swift app remains a client; the dashboard does not read Safety Kernel SQLite,
  OpenCode auth files, provider credentials, browser cookies, or Keychain secrets.
- Dashboard APIs are whitelisted Pydantic view models; `VERIFIED` remains a
  host-owned persisted `VerificationResult` with no UI mutation path.
- Production ACTIVE remains read-only, `DISABLED_BY_DESIGN`; no enable, force, or
  override path exists.
- The App Group container carries only the sanitized snapshot (connection state,
  lifecycle label, counts, provider quota state/confidence strings, ACTIVE label).
  It never contains `state.sqlite3`, credentials, tokens, auth files, task intent,
  raw provider logs, verification authority, quota truth, or owner approval.
- The Widget remains read-only: no submit, cancel, ACTIVE enable, approval creation,
  verification forging, or quota evidence writes.
- `WidgetSnapshotWriter` logs sanitized outcomes only (`ok`, `write_failed`,
  `primary_timeout_fallback`, `fallback_timeout`).

## Runtime Layout

```text
~/Library/Application Support/Personal AI Orchestrator/
  runtime.json
  state.sqlite3
  runtime-state/
  logs/
  WidgetSnapshot/snapshot.json        <- app-owned fallback snapshot

~/Library/Caches/Personal AI Orchestrator/control.sock

~/Library/Group Containers/group.com.personal-ai-orchestrator.dashboard/
  WidgetSnapshot/snapshot.json        <- sanitized presentation snapshot
```

## Tests Run (final session)

- `swift test` in `macos/PAOMenuBar`: PASS, 40 tests (38 prior + 2 new writer tests).
- `xcodebuild ... -scheme "Personal AI Orchestrator" -configuration Release build`:
  PASS (host + embedded appex; `CODE_SIGN_ENTITLEMENTS=""` at build time avoids the
  provisioning gate; entitlements applied at assembly signing).
- `bash scripts/build_app_bundle.sh` with `PAO_CODESIGN_IDENTITY`: PASS; nested
  `codesign --verify --deep --strict` PASS for host, appex, helper.
- `.venv/bin/python -m ruff check .`: PASS.
- `.venv/bin/python -m pytest -q -p no:cacheprovider`: PASS, 229 tests
  (28 Python 3.14 deprecation warnings from `pytest_asyncio`).
- `npm --prefix integrations/opencode run typecheck`: PASS, TypeScript plus
  10 contract tests.
- `git diff --check`: PASS.
- LaunchServices matrix: cold start ~10 s to health; second `open` spawns no second
  daemon; two concurrent `open -n` keep exactly one socket owner (loser reports
  `startup_lock_unavailable` per non-blocking lock); UI quit leaves the helper
  resident; relaunch attaches `DAEMON_HEALTHY_PREEXISTING`; exact-PID helper
  termination keeps the app alive with no duplicate daemon and the snapshot
  degrades to disconnected.
- Widget metadata audit: `CFBundleIdentifier
  com.personal-ai-orchestrator.dashboard.PAOWidgetExtension`, `CFBundleExecutable
  PAOWidgetExtension`, `CFBundlePackageType XPC!`, `NSExtension ->
  NSExtensionPointIdentifier com.apple.widgetkit-extension`, host/embed prefix
  relationship, entitlements present in both signatures.

## Next Required Action

1. Owner restarts macOS (clears the local App Group authorization stall), relaunches
   the app, and confirms the group snapshot resumes (widget heals automatically).
2. Owner repeats interactive acceptance per the P4.2.3 recheck list: sidebar
   sections change pages, metric tiles navigate to filtered tasks, 新建任务
   creates an authoritative task, task clicks load detail, routing/verification
   offer task pickers, quota cards explain themselves, settings are labeled
   honestly, refresh works.
   > **Superseded in part by B2.** "Routing/verification offer task pickers"
   > no longer describes the product: Routing and Verification stopped being
   > first-level destinations, and task selection happens in the Tasks browser.
   > See "B2 Navigation Architecture" at the end of this document for the
   > current navigation contract. Every other item in this list still holds.
3. Owner adds the widget in Notification Center and performs visual acceptance.

---

# P4.2.4-A Unified Dashboard Shell + Real Provider Registry

Date: 2026-09-01

Status: `P4_2_PROVIDER_REGISTRY_IMPLEMENTED_DISPATCH_PENDING`

## Owner UX blocker resolved

The owner reported that the Tasks screen appeared as a different application
shell because the previous nested `NavigationSplitView` rendered the task list
as a second sidebar. P4.2.4-A removed the nested split view and rewrote the
Tasks master/detail as a content-area HStack inside the shared page container.
The primary sidebar is now stable across every section.

## Unified page chrome

`DashboardPageContainer` is the single rendering wrapper used by:

总览 / 任务 / Agents / 提供商 / 额度 / 调度 / 验证 / 历史 / 设置

It enforces identical padding, background, top-baseline, toolbar alignment,
and empty-state visual language for every section. `DashboardBrowserPanel`
and `DashboardDetailPanel` are the only place where a list/detail composition
appears, and they are explicitly subordinate to the primary sidebar.

## Real provider registry

The empty product runtime that previously rendered no providers now exposes
real GLM / Z.AI and MiniMax CN discovery through the bundled daemon:

```text
provider_discovery.discover()
        |
opencode providers list (ANSI stripped, sanitized)
        |
opencode models <family> per PROVIDER_FAMILIES
        |
DISCOVERED_FROM_CATALOG / AUTH_FROM_ENV_PRESENCE / AVAILABLE_FOR_CATALOG
        |
persisted to provider-registry.json
        |
projected through /v1/providers, /v1/providers/status, /v1/providers/refresh
```

Provider families supported:

- zai-coding-plan (GLM / Z.AI)
- minimax-cn (MiniMax CN)
- minimax-cn-coding-plan (MiniMax CN Coding Plan)
- minimax (MiniMax International)
- minimax-coding-plan (MiniMax International Coding Plan)

For each provider, the Dashboard renders the discovered model SKUs as
synthetic execution targets (one per `<provider>/<model>`) with
`runtime_id=opencode`, `account_id=<provider>`, `enabled=True`. CN vs
international MiniMax endpoints are exposed via a `region` tag.

## Empty-bootstrap upgrade

Legacy `product-bootstrap-empty-registry-v1` is upgraded in place on the
first launch of an existing installation. No manual deletion of
`runtime.json` is required. User-authored registries are preserved.

## Credential safety

- Subprocess is spawned with explicit argv, never a shell.
- 8 s wall-clock timeout, 256 KB stdout cap per subprocess.
- ANSI color codes stripped; any string matching `sk-...`,
  `Bearer ...`, or `AKIA...` is replaced with `<redacted>` before parsing.
- `assert_sanitized` is applied to every persisted snapshot and every
  per-provider record. Negative tests with `sk-canary...`,
  `Bearer canary...`, `AKIAEXAMPLEKEY123` canaries confirm none of them
  ever survive the pipeline into the runtime registry, the Control API,
  or Dashboard logs.

## Control API additions

- `GET /v1/providers/status` returns
  `{discovery_state, last_discovered_at, provider_count,
  execution_target_count, last_error_code, catalog_snapshot_id,
  source_method}` with a refresh-coalesced manager under the hood.
- `POST /v1/providers/refresh` re-runs the credential-safe discovery
  cycle and returns the new status. Body is optional.

## Test counts at this head

- Python: 272 passed (was 229; +43 for discovery / sanitization / secrets)
- Swift: 50 passed (was 47; +3 for ProviderDiscoveryStatusView decoding and
  `testProviderDiscoveryStatusIsLoadedAndRefreshable`)
- OpenCode: 10/10 contract tests + typecheck pass
- Xcode host Release build: pass
- Ruff: clean

## Remaining before full P4.2 acceptance

- Installable WidgetKit `.appex` packaging/signing.
- Owner visual acceptance on the target Mac (this phase closes the
  Tasks-shell consistency blocker; the provider registry now displays
  real content from `opencode models`).
- P4.2.4-B: owner-initiated execution, OpenCode dispatch, host verifier.

---

# B2 Navigation Architecture (2026-09-03)

This section is a **contract**, not acceptance evidence. No screenshot or owner
pass recorded above showed this information architecture; every P4.2.x pass
above exercised the nine-destination sidebar and remains valid as a historical
record of that build.

## Superseded by B2

The first-level navigation asserted throughout this document —

```
Overview · Projects · Tasks · Providers · Quota · Routing · Verification · History · Settings
```

— no longer exists, and neither does the legacy `.agents` case that was
storable but excluded from `allCases`.

## Expected navigation contract

Exactly five first-level destinations, in this order:

```
Overview · Tasks · Resources · Activity · Settings
```

Nothing was deleted from the product; capabilities were relocated:

| Was a destination | Now reached through |
| --- | --- |
| Projects | Settings → Projects |
| Providers | Resources → Models & Providers |
| `agents` (legacy, unreachable) | Resources → Execution targets |
| Quota | Resources → Quota |
| Routing | Task Detail (routing explanation panel) |
| Verification | Task Detail (verification panel) and Activity |
| History | Activity |

## Persisted-selection migration

A navigation value stored by any earlier build resolves deterministically:
`projects → settings`; `agents`, `providers`, `quota` → `resources`;
`routing → tasks`; `verification`, `history` → `activity`; canonical values
pass through; anything unrecognized or empty falls back to Overview. Decoding
failure is never used as the migration mechanism.

## Risk / attention deep links

The daemon's semantic risk destinations stay decoupled from the sidebar enum
and are translated `RiskDestination → NavigationIntent → DashboardSection`:

| Daemon destination | Intent |
| --- | --- |
| `projects` | Settings, Projects context |
| `models_providers` | Resources, providers context |
| `quota` | Resources, quota context |
| `settings` | Settings, general context |
| `verification` | Activity |

**Documented fallback:** `verification` prefers task-specific navigation
(Tasks → selected task → verification context), but `RiskItemView` carries no
task identity today, so it always resolves to Activity. Task identity is never
invented to satisfy the preferred route.

## What B2 is not

B2 is navigation architecture only. It does not redesign Task Detail (B3),
Resources (B4), Activity (B6), or perform the Projects capability migration
into Settings (B7). Routing, quota, task, verification and daemon execution
semantics are unchanged.

The two D7 execution-layer questions — which component writes `declared_roles`,
and whether a new `RoutingPlan` revision carries completed role state forward or
resets it — stay open and unchanged in `docs/ROUTING_ROLE_CONTRACT.md`. B2
deliberately did not resolve them inside navigation work; they remain inputs for
B3 / the execution layer.

## Carried debt

- `sidebar.group.*` and the routing/verification task-picker strings
  (`label.taskContext`, `picker.*`, `routing.noSelection`, `verification.*`)
  remain in both catalogs but no longer have a call site. Kept, not deleted, so
  zh-Hans/en parity and the existing sweep tests stay meaningful; B3/B4/B6 should
  either reuse or retire them.
- Settings → General shows both the default-policy picker and the richer active-
  policy card that moved out of the Routing destination. Both are preserved
  because either alone would lose capability; B7 merges them.
- Projects moved location only. Security-scoped bookmark authorization and the
  `resolveProject` preview/confirm flow are untouched and unverified beyond the
  existing tests; B7 owns the capability-by-capability acceptance.
