# Reliability fixes — 2026-10-02

## Baseline and scope

Reviewed current `main` at `32dd7a3fd3cfe21ed924ec1b5ffb274833d81a3f`.
PR #68 (`39c58ca8def084bfe351b20713936fd37113d543`) was still open and
unmerged; its Telegram changes are not included here.

This patch addresses local state consistency and presentation reliability.
It does not change activation authority, execution policy, credential handling,
or paid provider behavior. No live provider calls, deployment, or merge occurred.

## Defects and fixes

1. **Failed settings saves changed running behavior.** Both settings setters
   published their new values before the atomic file replacement completed.
   A failed replacement returned an error but left memory different from disk.
   Proposed values are now persisted before publication. Invalid UTF-8 settings
   also fall back safely instead of crashing construction.
2. **A provider refresh could split one routing decision across registries.**
   Scheduling, adapter serialization, and shadow evidence repeatedly fetched the
   dynamic registry. Removing a target between reads caused a `KeyError` or
   inconsistent evidence. One request now uses one registry snapshot; subsequent
   requests still fetch fresh state.
3. **Damaged quota presentation caches broke quota listing.** Invalid UTF-8 and
   unreadable projection files escaped the read path, even when a valid normalized
   quota snapshot existed. Projection reads now discard unusable evidence while
   preserving provider visibility and valid snapshots. Misplaced projections
   whose pool identity does not match the requested pool are also ignored.
4. **Task detail responses could overwrite a newer selection.** Selection changes
   immediately clear the previous inspector, and request identity checks prevent
   older responses/errors from publishing after a newer request, deselection, or
   disconnect.
5. **Quota refresh could erase the last known display.** If both the refresh POST
   and fallback GET failed, the client assigned `nil` to quota. It now retains
   the prior projection and exposes the refresh error.
6. **Menu-bar status ignored tasks beyond the fetched page.** Global counts and
   blocked/running status now use the daemon's whole-store dashboard counts,
   falling back to fetched task rows only when those counts are unavailable.
7. **Clean adapter installation failed.** The lockfile lacked five platform
   packages already declared by `msgpackr-extract@3.0.4`. Those exact optional
   entries are restored, with no existing package/version changes.

## Verification

Environment: Linux, Python 3.12.14, Node 24.19.0.

- Ten new Python regression cases failed against the original implementations
  and passed after their fixes (five quota cases, five routing/settings cases).
- Full Python baseline: **1258 passed, 5 skipped, 21 failed, 56 errors**.
- Full Python patched run: **1268 passed, 5 skipped, 21 failed, 56 errors**.
- The **same 77 test IDs** fail/error in both runs. They require local Unix-domain
  sockets, which this execution sandbox rejects with `Operation not permitted`;
  daemon-start assertions consequently fail too. No new failing test IDs.
- Rerun excluding exactly those baseline-blocked test IDs: **1268 passed,
  5 skipped, 77 deselected**.
- `ruff check .`, `ruff format --check .`, and `git diff --check`: passed.
- OpenCode adapter: clean `npm ci --ignore-scripts --no-audit --no-fund` passed;
  `npm run typecheck` passed, including **17 contract tests**.
- Dispatch adapters: **9 DSH tests and 7 Pi-DSH tests passed**.
- Added **9 native XCTest regressions** with controlled response ordering.
  **Swift is unavailable in this environment.** Native build/test and UI behavior
  are source-reviewed only and must be validated on macOS before merge.
- Independent source review found no actionable regressions in the Python,
  Swift, or lockfile changes; it independently ran 67 relevant Python tests.

These results are not a claim that the full suite, native app, target Mac,
signing, real provider runtime, or release gates passed.

## Deferred finding: rejected combined settings update partially persists

`ControlPlaneService.update_scheduling_settings`,
`src/personal_ai_orchestrator/control_api.py:1682–1704`, saves the requested
policy at lines 1685–1687 before validating the requested mode and activation
authority at lines 1690–1704.

An isolated temporary fixture reproduced:

- Before: `BALANCED / MANUAL`
- Request: `{"default_scheduling_policy":"QUALITY_FIRST","mode":"ACTIVE"}`
- Result: rejected with `production_active_not_authorized`
- After, including reloaded disk settings: `QUALITY_FIRST / MANUAL`

ACTIVE remains denied, but a rejected operation unexpectedly changes future
routing policy. Existing tests assert mode denial without checking rollback of
a simultaneously changed policy. No live daemon or owner settings were touched.

This path is deliberately not modified here: CONTRIBUTING requires an issue
first for Production ACTIVE behavior. A separately scoped follow-up should
validate the entire request before publishing any settings, preserve existing
activation checks, and add regression coverage for rejection/atomicity.
