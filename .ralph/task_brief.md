# PAO daemon graceful SIGTERM and PI-5B3G mission lock

## User Goal

Repair the real Personal AI Orchestrator production daemon so SIGTERM causes lock-safe graceful shutdown, prove the repair in the actual packaged PyInstaller helper and canonical live product, then resume the existing three-observation PI-5B3G real SHADOW campaign without weakening any safety or verifier contract.

## Final Deliverable

A focused lifecycle repair on `fix/pao-daemon-graceful-sigterm`, permanent regression coverage, packaged and live shutdown/restart evidence, and either completed PI-5B3G evidence or an honest unrelated blocker report, committed and normally pushed with exact-head CI evidence.

## Success Criteria

- The signal handler performs only minimal signal notification and no lock-backed cleanup.
- Normal main-thread control flow performs supervisor, control-server, socket, and store cleanup.
- Automated tests cover SIGTERM, SIGINT/KeyboardInterrupt, signal restoration, cleanup location, bounded exit, socket removal, DB reuse, and immediate restart.
- The newly built real PyInstaller onefile helper passes parent-route and child-route SIGTERM acceptance, including at least three complete start/health/stop/cleanup/restart cycles.
- The old canonical daemon is retired only under the prompt's exact one-SIGINT authorization if its identity and idle gates match.
- The fixed canonical product passes real SIGTERM and restart acceptance with one socket owner and healthy DB/accounting.
- PI-5B3G runs exactly three sequential SHADOW observations with at most three parents, three children, six workers, no retries/fallback/grandchildren, unchanged frozen verifier hashes, and clean terminal accounting; or stops for a new unrelated blocker.
- Evidence records old/new build identities, exact commands/results, commits, pushed HEAD, PR, and CI for that exact HEAD without exposing credentials.

## Non-Goals

- No asyncio rewrite, UI redesign, campaign-semantic workaround, verifier weakening, provider fallback, production ACTIVE enablement, merge, force push, unrelated refactor, or unrelated project access.
- No SIGKILL, process-group shutdown as normal lifecycle, broad `pkill`/`killall`, or deletion of a live-owned socket.

## Constraints

- Preserve the clean evidence worktree and all unrelated worktrees; never reset, clean, rebase, stash, or rewrite history.
- Use disposable state for reproduction and packaged acceptance before canonical state.
- Fail closed on ambiguous PID/socket/DB/quota/verifier/accounting state.
- Do not print credential values or infer live truth from stale evidence.
- A real campaign observation consumes its budget even if it blocks; no automatic retry.
- Build, promotion, campaign, push, and CI actions are authorized only within this prompt's exact scope and gates.

## Assumptions

- `3f15e6de8b03f190cb43c25890151f56e55ffd29` is the intended PI-5B3G integration base because it is the exact head of Draft PR #53 and contains the campaign implementation/evidence lineage.
- Existing repository build and acceptance scripts are preferred over new one-off infrastructure when they can prove the required behavior.
- Local signing and provider credentials may already exist, but their values must never be inspected or recorded.

## Risk Areas

- Python signal reentrancy and PyInstaller onefile parent/child forwarding.
- Cleanup ordering across supervisor, HTTP servers, Unix socket, SQLite stores, and Swift lifecycle ownership.
- Accidentally signaling an unrelated or replacement production process.
- Stale socket path versus live socket ownership.
- Mixing old-build evidence with the fixed build or consuming unauthorized provider calls.
- Ambiguous campaign accounting, quota pool identity, or frozen verifier drift.

## Execution Plan

1. Repository and source trace: inspect exact implementations, existing tests/build scripts, branch topology, and current live state read-only. Modify only Ralph state. Accept by recorded repo map and defect confirmation. Roll back by removing only task-owned Ralph files if they are wrong.
2. Minimal lifecycle repair: change the narrow signal-to-main-loop bridge and tests. Accept with focused behavioral tests and diff review. Roll back the focused patch if regression evidence fails.
3. Broader verification: run lifecycle/control/delegation/database/Swift checks and the repository-required broader suite. Modify only test evidence if needed. Roll back only task-owned test/evidence changes on irreparable failure.
4. Packaged acceptance: build the real app/helper, record identity, and run isolated parent/child signal routes plus repeated cycles. Modify build outputs/evidence only. Do not promote if any gate fails.
5. Canonical promotion and lifecycle proof: re-identify live processes and idle/accounting state; use the single authorized SIGINT only if exact old-build conditions match; install/start via normal product path and prove SIGTERM/restart. Stop without improvisation on ambiguity or failure.
6. PI-5B3G: refresh quota/verifier/fixture/campaign truth, run exactly three sequential authorized SHADOW observations, then prove terminal cleanup and post-campaign lifecycle. Stop at the first contract blocker without retry.
7. Evidence and delivery: commit meaningful milestones, push normally, update/create Draft PR as appropriate, and wait for CI on exact pushed HEAD. Do not merge.

## Drift Guard

Reread this brief after every phase. Every action must directly serve the locked goal or a success criterion. Do not expand scope silently; if a changed goal is required, update this brief first and stop when that change needs owner authority.

## Git Safety

Work only in `/Volumes/Taoruide外接/AI LLM TOOLS/pao-daemon-graceful-sigterm` on `fix/pao-daemon-graceful-sigterm`, based on `3f15e6d`. Inspect branch, status, diff, changed-file scope, and secret exposure before each commit. Use focused commits and normal push only. Never reset, clean, rebase, force-push, merge, delete unrelated worktrees, or modify the dirty parent repository. Rollback means reverting only task-owned edits with a new normal commit or stopping for owner direction; no history rewrite.
