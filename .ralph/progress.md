# Progress

## 2026-09-14T00:41:16Z — T001 Phase 0 mission lock

- Changed: created dedicated repair worktree and locked the complete lifecycle plus PI-5B3G objective.
- Verification: PASS — required files are non-empty; both JSON files parse; `git diff --check` passes.
- Files changed: `.ralph/` state only.
- Remaining risk: source/live topology has not yet been inspected in this worktree.
- Next task: T002 inventory source lifecycle and live topology.

## 2026-09-14T01:02:00Z — T002 inventory source lifecycle and live topology

- Changed: recorded exact repo/base, unchanged defective signal path, cleanup owners, test gaps, build path, Swift lifecycle risks, and current old-daemon topology.
- Verification: PASS — repo map exists; source comparison proves the defect is unchanged from `64a45a3`; canonical SQLite reports 0/0/0 and quick-check ok.
- Files changed: `.ralph/artifacts/repo_map.md` and Ralph state only.
- Remaining risk: the live old daemon now owns the socket but rejects requests; no signal was sent.
- Next task: T003 add a disposable control-only SIGTERM reproduction that always cleans its own subprocess.

## 2026-09-14T01:17:00Z — T003 deterministic old-behavior reproduction

- Changed: added a host subprocess SIGTERM assertion and a deterministic handler-context guard that simulates delivery during the main wait.
- Verification: PASS as a reproduction boundary — the unbundled subprocess exits normally on host Python 3.13, while the deterministic test fails old source exactly because `_stop` calls `GuardedEvent.set()` inside handler context.
- Files changed: `tests/test_daemon_shutdown.py` and Ralph state.
- Remaining risk: source is intentionally still failing the new design-property test until T004.
- Next task: T004 replace handler Event mutation with scalar notification and ordinary-flow supervisor stop.
