# P4.2 Full macOS Dashboard + WidgetKit Acceptance

Date: 2026-08-31

Status: `P4_2_DASHBOARD_FOUNDATION_WIDGETKIT_BLOCKED`

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
- Added a local bundle builder for `dist/Personal AI Orchestrator.app`.

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

- `swift test` in `macos/PAOMenuBar`: PASS, 35 tests.
- `.venv/bin/python -m ruff check --no-cache .`: PASS.
- `.venv/bin/python -m pytest -q -p no:cacheprovider`: PASS, 225 tests,
  28 Python 3.14 deprecation warnings from `pytest_asyncio`.
- `npm --prefix integrations/opencode run typecheck`: PASS, TypeScript plus 10 contract tests.
- `bash scripts/build_app_bundle.sh`: PASS, produced
  `macos/PAOMenuBar/dist/Personal AI Orchestrator.app`.
- `git diff --check`: PASS.

## Not Yet Accepted

- App-owned daemon lifecycle is not wired yet.
- Manual Terminal bootstrap is still required for the Python daemon.
- Python runtime packaging is not self-contained yet.
- WidgetKit is not implemented. The current SwiftPM executable does not define an Xcode
  app-extension target or a shared WidgetKit snapshot bridge.
- Real app launch, daemon interruption/reconnect, Chinese visual review, and WidgetKit display
  were not executed on the target Mac.

## Next Required Action

Implement the daemon lifecycle/runtime packaging slice:

1. decide between app-owned `Process`, LaunchAgent, or `SMAppService` after checking signing and
   login-item requirements;
2. launch the daemon from application-support paths, not source worktrees;
3. make the dashboard connect without manual Terminal bootstrap;
4. then add WidgetKit through a read-only sanitized shared snapshot bridge.
