# P4.2 Full macOS Dashboard + WidgetKit Acceptance

Date: 2026-08-31

Status: `P4_2_APP_DAEMON_WIDGET_SOURCE_ACCEPTED_APPEX_BLOCKED`

## Baseline

- Base branch: `origin/main`
- Required P4.1 merge commit: `6829e113b9c56d85ce44be4630ed29204e100ece`
- P4.2 branch: `feat/p4-full-dashboard`
- P4.2 worktree: `/Users/<user>/Developer/pao-p4-full-dashboard`
- Production ACTIVE: `DISABLED_BY_DESIGN`
- Owner ACTIVE approval: absent
- Provider-exact reset cycles: `0`

## Implemented

- Added read-only `/v1/dashboard`.
- Added read-only `/v1/tasks/<task_id>/detail`.
- Added sanitized dashboard task counts, blockers, and audit-event summaries.
- Added task detail composition over task state, runs, latest routing decision, verification,
  approvals, workspace metadata, and task-local events.
- Added Swift `DashboardSummaryView`, `TaskDetailView`, approval, workspace, and activity models.
- Changed `OrchestratorStore` to refresh through the dashboard aggregate and share state between
  MenuBarExtra and the full dashboard window.
- Added a native SwiftUI `NavigationSplitView` dashboard with sections:
  overview, tasks, agents/execution targets, providers, quota, routing, verification, history,
  and settings.
- Preserved the P4.1 MenuBarExtra and added `Open Dashboard`.
- Added a Settings scene and safe client preferences for launch/login and daemon autostart intent.
- Added `bootstrap_application_support()` for the macOS application-support layout.
- Aligned Swift and Python default socket resolution to the application-support cache socket.
- Added a tested Swift daemon launch-argument contract for the future app-owned lifecycle.
- Added a PyInstaller product daemon entrypoint that bootstraps credential-free app-support
  runtime state and serves the typed UDS control plane without the loopback routing API.
- Added `Contents/Helpers/pao-daemon` packaging to the local bundle builder.
- Added app-owned daemon lifecycle with direct `Process` launch, start locking, health wait,
  version-mismatch state, helper-missing failure state, and no shell interpolation.
- Added daemon lifecycle status display in the dashboard and settings.
- Added a sanitized read-only Widget snapshot bridge.
- Added a WidgetKit source target that reads the sanitized shared snapshot through an App Group
  identifier from the widget bundle info dictionary.

## Security Boundary

- The Swift app remains a client.
- The dashboard does not read Safety Kernel SQLite directly.
- The dashboard does not read OpenCode auth files, provider credential files, browser cookies, or
  Keychain provider secrets.
- Dashboard APIs are whitelisted Pydantic view models.
- Provider credential references are not exposed through the control plane.
- Runtime config bootstrap rejects static `credential_ref` values.
- `VERIFIED` remains a host-owned persisted `VerificationResult`; the UI has no verification
  mutation path.
- Production ACTIVE remains read-only and disabled by design; no enable, force, or override button
  was added.
- Widget snapshots exclude task intent text, socket paths, provider credentials, and submit/cancel
  commands.

## Runtime Layout

```text
~/Library/Application Support/Personal AI Orchestrator/
  runtime.json
  state.sqlite3
  runtime-state/
  logs/

~/Library/Caches/Personal AI Orchestrator/control.sock
```

The socket is intentionally under Caches to keep the AF_UNIX path short.

## Tests Run

- `swift test` in `macos/PAOMenuBar`: PASS, 37 tests.
- `swift build --target PAOWidgetExtension` in `macos/PAOMenuBar`: PASS.
- `.venv/bin/python -m ruff check --no-cache .`: PASS.
- `.venv/bin/python -m pytest -q -p no:cacheprovider`: PASS, 229 tests,
  28 Python 3.14 deprecation warnings from `pytest_asyncio`.
- `npm --prefix integrations/opencode run typecheck`: PASS, TypeScript plus 10 contract tests.
- `bash scripts/build_app_bundle.sh`: PASS, produced
  `macos/PAOMenuBar/dist/Personal AI Orchestrator.app`.
- `git diff --check`: PASS.
- Bundled helper acceptance: PASS. `Contents/Helpers/pao-daemon --home <tmp> --port 0`
  served `/v1/health` as `ok/v1`; dashboard ACTIVE remained `DISABLED_BY_DESIGN`; helper size
  was 14,779,952 bytes.
- App-owned cold-start acceptance: PASS. Direct launch of
  `dist/Personal AI Orchestrator.app/Contents/MacOS/Personal AI Orchestrator` with disposable
  `HOME=<tmp>` started the bundled helper, served `/v1/health` as `ok/v1`, kept ACTIVE
  `DISABLED_BY_DESIGN`, and wrote a sanitized Widget snapshot:
  `CONNECTED / DAEMON_HEALTHY_STARTED_BY_APP / DISABLED_BY_DESIGN`.

## Not Yet Accepted

- Finder/Open launch was not executed; automated app cold-start used direct execution of the
  bundled `.app` binary to keep exact cleanup ownership of app/helper PIDs.
- An installable WidgetKit `.appex` was not produced. SwiftPM compiles the WidgetKit source
  target, but packaging/signing an extension still requires an Xcode app-extension target or
  equivalent project migration.
- Real Notification Center Widget display was not executed.
- Chinese human visual review was not executed.

## Next Required Action

Implement the packaging/signing slice for a real WidgetKit `.appex`, then run Finder/Open,
Notification Center Widget display, and human visual acceptance on the target Mac.
