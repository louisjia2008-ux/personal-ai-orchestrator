# T005 verification record

All commands ran from the repair worktree unless a subdirectory is stated.

## Targeted lifecycle/control/campaign/process/database tests

Environment used a short `/private/tmp` TMPDIR because macOS AF_UNIX paths are limited to 104 bytes.

Command group: `python -m pytest -q` over `test_daemon_shutdown.py`, `test_product_daemon.py`, `test_daemon_supervisor.py`, `test_control_plane.py`, `test_delegation_campaign.py`, `test_daemon_tick_args.py`, `test_daemon_tick_integration.py`, `test_dispatch_executor.py`, `test_cancellation_controller.py`, `test_safety_kernel_transactions.py`, `test_safety_kernel_and_verifier.py`, and `test_daemon_config.py`.

Result: PASS — 175 passed, 1 rerun, 72.73 seconds.

Focused post-readiness-fix result: PASS — 7 passed, 1 rerun, 12.03 seconds.

## Full Python suite

CI-equivalent inputs:

- Pi SDK: `@earendil-works/pi-coding-agent@0.85.1` installed under `/private/tmp`.
- Node: temporary Node 24 path, matching CI.
- Python: project venv exposed through `/private/tmp/pao-venv-short` so synthetic worker shebangs contain no spaces; its bin directory is first in PATH so existing tests invoking `python` match CI.
- `PAO_PI_SDK_ROOT` points to the pinned package.

Final command: `/private/tmp/pao-venv-short/bin/python -m pytest -q` with the environment above.

Result: PASS — 1196 passed, 0 failed, 125.24 seconds.

Intermediate invalid/local-environment runs were not counted as product failures:

- a fake Pi worker could not spawn because the original venv `sys.executable` contained spaces and was emitted as a shebang; the untouched `3f15e6d` baseline failed identically;
- two control-plane tests could not find a bare `python` after a narrowed PATH omitted the venv bin;
- an existing health test raced socket bind versus HTTP accept. The test now polls actual health, strengthening rather than weakening its assertion.

## Static checks

Command: `python -m ruff check .`

Result: PASS — all checks passed.

Command: `git diff --check`

Result: PASS.

## OpenCode adapter

Commands from `integrations/opencode`:

- `npm install`
- `npm run typecheck`

Result: PASS — TypeScript no-emit check and 17/17 Node contract tests passed under Node 24.

## SwiftPM macOS package

Package products: `PAOControlKit` library, `PAOMenuBar` executable, and `PAOWidgetExtension` executable; test target `PAOControlKitTests`.

- `swift build`: PASS, Debug build completed in 8.01 seconds.
- `swift test`: PASS, 441 tests, 0 failures. Existing Swift 6 isolation and never-mutated-variable warnings remain warnings and are unrelated to the Python lifecycle repair.

## Verification boundary

These results prove source/test/build compatibility only. They do not yet prove the real PyInstaller helper, packaged parent/child signal forwarding, codesign bundle acceptance, canonical production promotion, live provider quota, or PI-5B3G completion.
