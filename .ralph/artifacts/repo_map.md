# T002 repository, lifecycle, and live topology inventory

Observed 2026-09-14 (Asia/Shanghai), before source edits.

## Repository and integration base

- Repository root: `/tmp/pao-acceptance/pao-daemon-graceful-sigterm`.
- Branch: `fix/pao-daemon-graceful-sigterm`.
- Baseline: `3f15e6de8b03f190cb43c25890151f56e55ffd29`, the exact head of Draft PR #53, `feat/pi-delegation-real-shadow-campaign-05b3g`.
- `origin/main`: `01646ebe435b9fd5ae607506d3b3c64241e517e1` after a normal fetch.
- Baseline is 50 commits ahead of `origin/main` and contains merged PI-5B2 plus PI-5B3A–G lineage.
- The old deployed build commit `64a45a3adaafb42cac2c6a55a3ac0db31c06f059` is an ancestor of the baseline.
- Original evidence and detached production worktrees were clean and left unchanged. The dirty outer `/tmp/pao-acceptance` repository is out of scope.

## Exact signal and cleanup path

- `scripts/pao_daemon_entry.py` delegates directly to `product_daemon.main()`.
- `product_daemon.main()` bootstraps the per-user layout and calls `daemon.main(... --control-only ...)`.
- `src/personal_ai_orchestrator/daemon.py:511-530` is unchanged between deployed `64a45a3` and the current baseline.
- The control-only path creates one `threading.Event`, installs a SIGTERM handler whose body calls `stop.set()`, then keeps the main thread in `stop.wait(timeout=3600)`.
- The `finally` block restores the prior SIGTERM disposition, joins the supervisor, stops the campaign-aware control server, closes the control service store, and closes the service store.
- The supervisor may safely continue to use a `threading.Event`; its sleep path already uses bounded 0.5-second event waits.
- `DelegationCampaignControlPlaneServer.stop()` calls `httpd.shutdown()`, joins its background thread, then calls `server_close()`.
- `UnixThreadingHTTPServer.server_close()` closes the server and unlinks the Unix socket path.
- Both service stores are `SafetyKernelStore` instances with explicit SQLite `close()`.

Classification: `SIGTERM_HANDLER_INSTALLED_BUT_STUCK`. Current source still contains the defective signal-to-lock-backed-Event path. There is no newer lifecycle fix to preserve or duplicate.

## Existing lifecycle coverage

- `tests/test_product_daemon.py` starts the unbundled product daemon and sends SIGTERM, but does not prove PyInstaller parent/child behavior or repeated restart cycles.
- `tests/test_daemon_tick_integration.py` uses SIGTERM for defensive subprocess cleanup but kills on timeout and does not assert graceful exit.
- `tests/test_daemon_shutdown.py` asserts SIGINT cleanup only, and does not enter the control-only SIGTERM handler path.
- Control server socket cleanup has focused tests in `tests/test_control_plane.py` and `tests/test_delegation_campaign.py`.
- Supervisor Event behavior is covered in `tests/test_daemon_supervisor.py`.
- There is no packaged-helper parent/child signal matrix regression.

## Swift and build lifecycle

- `DaemonLifecycleController.ensureStarted()` can attach or launch, but has no public graceful stop/restart API.
- Its build-mismatch recovery uses `/usr/bin/pkill -f <helper path>` and treats failure as best effort, which is too broad for the normal repaired lifecycle but is not the immediate Python defect.
- `scripts/build_and_run_macos_dashboard.sh` identifies exact helper paths, sends SIGTERM, then falls back to exact-PID SIGKILL after a bound. That fallback must not be used to claim graceful acceptance.
- The normal product build is `macos/PAOMenuBar/scripts/build_app_bundle.sh`: Xcode Release host, PyInstaller `--onefile` helper, injected Git build stamp, ad-hoc signing, and strict codesign verification.
- Project-required checks are pytest, Ruff, OpenCode adapter tests from CI, and Swift Package tests for `macos/PAOMenuBar`.

## Refreshed live production state

- Exact process tree still matches the diagnosed old daemon:
  - outer PyInstaller parent PID 71945, PPID 1, PGID 71945;
  - serving child PID 71946, PPID 71945, PGID 71945;
  - both started 2026-09-13 19:51:35 local time;
  - executable for both is the `pao-product-main-64a45a3` packaged helper.
- The live app bundle reports build `64a45a3adaafb42cac2c6a55a3ac0db31c06f059`.
- Live helper SHA-256: `979658a25f506ee19275afca7c36bbdc88e05f5d53cd26f4ca7edb42bbe76930`.
- Helper is a thin arm64 Mach-O with an ad-hoc signature.
- PID 71946 still owns canonical `control.sock` and many descriptors for canonical `state.sqlite3` and its WAL.
- Canonical socket is `srw-------`, uid 501, gid 20.
- Unlike the earlier diagnosis snapshot, all current `/v1/health`, `/v1/build`, and campaign requests fail to connect. The socket pathname exists and is owned, but the server is no longer accepting connections.
- Canonical SQLite read-only truth: `RUNNING_ROWS=0`, `ACTIVE_RUNS=0`, `HELD_WRITERS=0`, `PRAGMA quick_check=ok`.
- `runtime-state/delegation-calibration-campaign.json` is absent, consistent with campaign OFF and zero consumed observations, but the live endpoint is currently unavailable and must be refreshed after repair.
- `/Applications/Personal AI Orchestrator.app` is not the executable backing the live daemon; its helper hash differs. Promotion must use the task-built bundle and normal lifecycle, not infer identity from `/Applications` alone.

## Minimal repair direction

Use a scalar shutdown request set by the SIGTERM handler, observed by a short bounded main-thread polling loop. In ordinary control flow, set the supervisor's Event and run all joins/server/store cleanup. Preserve default SIGINT/KeyboardInterrupt semantics and restore the prior SIGTERM disposition in `finally`.
