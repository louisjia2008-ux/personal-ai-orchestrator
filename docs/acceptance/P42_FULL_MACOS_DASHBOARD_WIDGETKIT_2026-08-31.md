# P4.2 Full macOS Dashboard + WidgetKit Acceptance

Date: 2026-08-31 (takeover completion 2026-08-31 evening session)

Status: `P4_2_FULL_MACOS_DASHBOARD_WIDGETKIT_TECHNICAL_COMPLETE_HUMAN_ACCEPTANCE_REQUIRED`

Baseline

- Base branch: `origin/main`
- Required P4.1 merge commit: `6829e113b9c56d85ce44be4630ed29204e100ece`
- P4.2 branch: `feat/p4-full-dashboard`
- P4.2 worktree: `/Users/<user>/Developer/pao-p4-full-dashboard`
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
2. Owner performs human visual acceptance and adds the widget in Notification Center.
