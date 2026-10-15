# Reliability follow-up — 2026-10-03

## Baseline and scope

Local changes are based on PR #69 commit
`5224d5094782e542a77e78d652d3f6474871f591`. Fresh GitHub checks still showed
PR #69 open/draft and main at `32dd7a3fd3cfe21ed924ec1b5ffb274833d81a3f`.
The code has not been pushed, merged, deployed, or validated against live providers.

The cancellation change follows the repository's issue-first process:
[issue #72](https://github.com/louisjia2008-ux/personal-ai-orchestrator/issues/72)
was created and read back before implementation. Issues #70 and #71 and the
previously deferred combined policy/ACTIVE update are unchanged.

## Fixes

1. **Submission outcome belongs to its invocation.** `quickSubmit` returns its
   own typed outcome. Both UI callers use that result for dispatch, navigation,
   and text clearing instead of a shared previous task ID. Later attempts own
   shared presentation; failed requests preserve drafts, newer edits survive,
   and dismissed requests cannot later navigate or clear text.
2. **AUTO cancel replay preserves the first operation.** A stable task/cancel
   request identity is recognized from the atomically committed veto audit,
   including a duplicate that races the original commit. New cancellation
   requests retain normal behavior. Explicit request-ID provenance is written
   in the same audit transaction; no-ID requests cannot claim a later explicit
   ID. Historical fallback-shaped IDs without provenance return
   `409 cancel_request_identity_ambiguous` rather than assuming either intent.
   Gateway summaries reflect the actual READY/current task state.
3. **Monorepo focus reaches both runtimes.** The stored `working_subpath` is
   added to the worker's task context for OpenCode and Pi. The working directory,
   worktree ownership, tool guards, verification scope, and saved task intent
   remain unchanged. This restores focus context, not subdirectory confinement.
4. **HEAD changes do not invalidate identical submissions.** Replays reuse the
   original host-owned base SHA. A bounded concurrent-admission retry reuses the
   winner's snapshot while retaining all storage-level semantic conflict checks.
5. **Invalid UTF-8 provider state follows corruption handling.** The connection,
   OpenCode registry, and Pi registry loaders treat decoding failure like their
   existing malformed-JSON cases. The daemon can expose failed discovery state;
   corrupt snapshots are not overwritten or silently rediscovered.
6. **Previously observed off-page tasks remain monitored.** The notifier uses
   the supported 200-row discovery page and retains unfinished tasks by ID.
   It fairly rotates at most 200 off-page lookups per poll, preserves state on
   transient errors, and retires explicit missing or terminal off-page records.

## Verification

- Combined focused Python regressions: **64 passed**.
- Independent read-only review: **64 focused tests passed**, including a
  separately reproduced/fixed cancellation-ID collision. No remaining blocking
  findings in the six scoped fixes.
- Extra review probe checked fair rotation over 400 failing off-page lookups.
- Full Python baseline: **1277 passed, 5 skipped, 21 failed, 56 errors**.
- Full Python patched: **1334 passed, 5 skipped, 21 failed, 56 errors**. The
  **same 77 failed/error test IDs** require Unix sockets or daemon startup
  prohibited by this Linux sandbox; no new failing IDs.
- Excluding exactly those baseline-blocked IDs: **1334 passed, 5 skipped,
  77 deselected**. This is not a full-suite pass.
- Ruff lint, Ruff formatting, and diff whitespace checks passed.
- OpenCode TypeScript check and **17 contract tests passed**, using existing
  dependencies whose lockfile matches this baseline. This was not a fresh install.
- DSH and Pi-DSH dispatch tests: **9 + 7 passed**.
- **11 Swift tests added**: nine store/socket cases and two caller source
  contracts. `swift build` and `swift test` cannot execute because Swift is not
  installed. Swift review is static; native UI behavior remains unverified.

## Remaining limits

- The list API has no pagination. Tasks never observed in the newest 200 rows
  cannot be discovered by this notifier, and intermediate transitions between
  polls cannot be reconstructed. Tracking memory scales with observed unfinished
  tasks plus the current page; terminal history is retired.
- A separate pre-existing submission-replay gap remains after a veto changes the
  task's mutable scheduling policy to MANUAL. Storage compares that changed policy
  with the original request. The HEAD fix does not redesign immutable submission
  identity or relax this conflict check.
- Target-Mac, actual dismissal/edit flows, provider execution, and release
  acceptance are separate gates and are not claimed by these local checks.
