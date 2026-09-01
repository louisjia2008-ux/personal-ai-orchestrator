# Development Policy

## Branching and milestone cadence

The repository should remain usable after every meaningful milestone.

Recommended workflow:

1. Create a focused branch for one engineering milestone.
2. Make small commits with descriptive messages.
3. Run deterministic checks before opening a PR.
4. Keep the PR narrowly scoped to one acceptance gate.
5. Merge only after evidence is attached and reviewed.

For active development, pushing at least once per day is reasonable. Prefer pushing whenever a small milestone becomes independently reviewable rather than batching unrelated work.

## Commit conventions

Use conventional prefixes when practical:

- `feat:` new behavior
- `fix:` defect correction
- `test:` tests only
- `docs:` documentation
- `refactor:` behavior-preserving code restructuring
- `chore:` tooling/repository maintenance
- `security:` safety-boundary or permission changes

## Definition of done

A coding agent reporting completion does not satisfy Definition of Done.

A milestone is done only when:

- expected files changed and unexpected files did not;
- deterministic verifier commands pass;
- relevant tests pass;
- `git diff --check` passes;
- structured evidence is recorded;
- known limitations are documented;
- no secret material was added to the repository;
- task state was advanced by the orchestrator/verifier rather than worker prose.

## Agent safety rules

Workers must not:

- write directly to the main repository;
- create or destroy their own worktree boundary;
- define trusted verifier commands through prompt content;
- merge their own changes into protected branches;
- reinterpret a failed verifier as success;
- store provider credentials in repository files;
- use destructive Git operations against host-owned state unless explicitly exposed by the safety kernel.

## Human review policy

Human review is required initially for:

- changes to the Safety Kernel;
- permission policy changes;
- credential handling;
- repository integration/merge logic;
- quota policy that could trigger paid usage;
- any expansion of filesystem or network privileges.

This requirement can be relaxed only after the system has accumulated reliable task history and corresponding tests.

## Architecture ownership

Provider transport belongs in adapters/registry code.

Routing belongs in routing policy.

Safety state belongs in the Safety Kernel.

Verification belongs in host-owned verifier profiles.

Client presentation belongs in Telegram/DeskPet/front-end adapters.

Do not let one layer absorb another merely because a model/provider exposes a convenient feature.

## Local control plane and CLI (P4.0)

Start the daemon with an optional control-plane socket:

```bash
python -m personal_ai_orchestrator.daemon \
  --config runtime.json \
  --state-db state.sqlite3 \
  --runtime-state-root runtime-state \
  --control-socket ~/Library/Caches/Personal\ AI\ Orchestrator/control.sock
```

The `pao` console script (or `python -m personal_ai_orchestrator.cli`) submits structured
requests only; CLI arguments never become shell commands. Socket resolution order:
`--socket`, then `PAO_CONTROL_SOCKET`, then
`~/Library/Caches/Personal AI Orchestrator/control.sock`.

```bash
pao submit --task-id T1 --request-id R1 --intent "natural-language intent"
pao status T1
pao list
pao runs T1
pao report T1            # verification evidence report
pao routing T1           # latest durable routing decision
pao cancel T1 --request-id cancel-R1
pao approvals T1         # read-only approval records
pao providers            # sanitized provider/quota health
pao quota
pao active-status        # Production ACTIVE gate (read-only)
```

`--json` renders raw API responses. The control plane binds a `0600` Unix Domain Socket,
accepts bounded JSON only, validates Host/Origin where present, and returns sanitized error
codes. macOS limits socket paths to 104 bytes; keep the socket near the filesystem root of
its state directory.

## Native macOS menu bar client (P4.1)

```bash
cd macos/PAOMenuBar
swift build          # debug build
swift test           # XCTest suite (real UDS round-trips against an in-test daemon)
swift build -c release
PAO_CONTROL_SOCKET=/path/to/c.sock .build/release/PAOMenuBar
```

- Deployment target: macOS 13 (SwiftUI `MenuBarExtra`); built and tested on macOS 26.5 /
  Swift 6.3.
- Socket resolution: `defaults write PAOMenuBar controlSocketPath /abs/path.sock`
  (app domain), then `PAO_CONTROL_SOCKET`, then
  `~/Library/Caches/Personal AI Orchestrator/control.sock`.
- `PAO_MENUBAR_STDERR_LOG=1` mirrors sanitized connection/op logs to stderr for acceptance
  evidence.
- Refresh: 2 s while the menu is open, 15 s in background, exponential backoff (2 s → 60 s)
  while disconnected. No high-frequency polling.
- The app requests no entitlements and no Accessibility/Screen/Full-Disk permissions; it is
  a plain (unsigned, local) SwiftPM executable talking to one UDS endpoint.

## Native macOS dashboard bundle (P4.2)

```bash
cd macos/PAOMenuBar
swift test
bash scripts/build_app_bundle.sh
open "dist/Personal AI Orchestrator.app"
```

The bundle script performs a SwiftPM release build, builds a project-local PyInstaller
`pao-daemon` helper, and assembles `dist/Personal AI Orchestrator.app`. The SwiftUI client
starts the bundled helper with `Process` when the typed control socket is unavailable; the
daemon remains external and authoritative. P4.2.3 polished Tasks shell consistency.
P4.2.4-A added the unified dashboard shell and the real GLM / MiniMax provider
registry: `/v1/dashboard`, `/v1/providers`, `/v1/providers/status`, and
`/v1/providers/refresh` so the shared store can refresh one aggregate dashboard view
rather than polling each screen independently.

Runtime state for product launches should be bootstrapped outside source worktrees:

```text
~/Library/Application Support/Personal AI Orchestrator/
  runtime.json
  state.sqlite3
  runtime-state/
  provider-registry.json  # sanitized dynamic registry (credential-free)
  logs/
~/Library/Caches/Personal AI Orchestrator/control.sock
```

`bootstrap_application_support()` writes `runtime.json` atomically and fails if the static
runtime config contains provider credential references. Static config still cannot enable
Production ACTIVE. The legacy empty-bootstrap snapshot id
(`product-bootstrap-empty-registry-v1`) triggers a credential-safe discovery cycle on
first launch; user-authored registries are preserved.

Current P4.2 limitations:

- the app-owned daemon uses direct `Process` launch and an app-support lock, not a LaunchAgent
  or `SMAppService`;
- the bundled helper is PyInstaller-based and built from project-local packaging dependencies;
- WidgetKit source and a sanitized shared snapshot bridge exist, but producing an installable
  `.appex` still requires an Xcode app-extension packaging/signing path;
- owner-initiated execution / OpenCode dispatch / host verifier integration are deferred
  to P4.2.4-B; the bundled daemon intentionally has no dispatch endpoint yet.
