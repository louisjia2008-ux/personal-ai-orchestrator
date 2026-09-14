# PAO PI-5B3G scope-validator hardening and new campaign mission lock

## User Goal

Preserve the completed daemon lifecycle repair and previous failed PI-5B3G campaign, make the dynamic child semantic-scope validator strict, deterministic, and diagnosable using offline evidence, then run one newly authorized three-observation PI-5B3G SHADOW campaign without weakening verifier, routing, quota, or delegation safety.

## Final Deliverable

A permanent repository-owned scope validator with an adversarial offline corpus, stable sanitized rule traces, tests proving child execution is unreachable after rejection, a separately committed/pushed exact-head-CI-green validator checkpoint, and either three successful new real observations or an honest new-campaign blocker report followed by canonical product restoration and final exact-head CI.

## Success Criteria

- Existing daemon lifecycle evidence and old failed campaign evidence remain byte-preserved.
- Phase A enumerates every current validation/forwarding/accounting rule without source changes or unsupported inference about the previous raw intent.
- A deterministic offline corpus covers safe exact, safe negated, unsafe authority/file/tool/delegation/retry/fallback/injection, and ambiguous fail-closed cases.
- A repository-owned validator positively proves the one-file/one-marker/one-observation scope, distinguishes supported negation from positive capability requests, emits stable version/rule/category/stage evidence, and never uses an LLM classifier.
- Tests prove every owner-required case, deterministic rule IDs, no raw transcript evidence, and no child execution/worker after rejection.
- Validator changes pass targeted/relevant tests, full Python and Ruff when production Python changes, then are committed, normally pushed, and exact-head CI passes before any MiniMax call.
- A new campaign ID runs exactly three sequential SHADOW observations with at most three new parents, three new children, six new workers, no retries/fallback/grandchildren, unchanged frozen verifier hashes, and clean terminal accounting; otherwise it stops at the first new live invariant failure.
- Final evidence separates historical failure, offline hardening, and the new campaign without credentials or raw provider transcripts; the normal repaired product is restored healthy and final exact-head CI passes.

## Non-Goals

- No daemon lifecycle redesign or repeated packaged acceptance unless directly related production lifecycle code changes.
- No UI redesign, LLM safety classifier, validator deletion/always-allow path, previous-hash whitelist, provider-dependent validation, frozen verifier change, provider fallback, production enforcement, merge, force push, unrelated refactor, or unrelated project access.

## Constraints

- Preserve the clean evidence worktree and all unrelated worktrees; never reset, clean, rebase, stash, or rewrite history.
- Use disposable state for reproduction and packaged acceptance before canonical state.
- Fail closed on ambiguous PID/socket/DB/quota/verifier/accounting state.
- Do not print credential values or infer live truth from stale evidence.
- The previous failed parent remains consumed and historical. The new authorization permits at most three new parents and three new children; any rejected live parent stops the new campaign without retry or later observation.
- Offline work must finish, be committed, normally pushed, and pass exact-head CI before any new model call.
- New campaign egress, normal repaired-daemon SIGTERM transition, product restore, normal pushes, and CI observation are authorized only within this prompt's exact gates.

## Assumptions

- `3f15e6de8b03f190cb43c25890151f56e55ffd29` is the intended PI-5B3G integration base because it is the exact head of Draft PR #53 and contains the campaign implementation/evidence lineage.
- Existing repository build and acceptance scripts are preferred over new one-off infrastructure when they can prove the required behavior.
- Local signing and provider credentials may already exist, but their values must never be inspected or recorded.

## Risk Areas

- False acceptance from lexical negation handling, false rejection from broad token bans, ambiguous multi-clause polarity, and unstable rule ordering.
- Leaking raw dynamic prompts through audit/evidence while improving rejection diagnosis.
- Reaching child execution before ALLOW or losing campaign budget/isolation/accounting truth.
- Mixing previous failed campaign accounting with the new budget, quota drift, frozen verifier drift, or concurrent canonical owners.

## Execution Plan

1. Repository and source trace: inspect exact implementations, existing tests/build scripts, branch topology, and current live state read-only. Modify only Ralph state. Accept by recorded repo map and defect confirmation. Roll back by removing only task-owned Ralph files if they are wrong.
2. Minimal lifecycle repair: change the narrow signal-to-main-loop bridge and tests. Accept with focused behavioral tests and diff review. Roll back the focused patch if regression evidence fails.
3. Broader verification: run lifecycle/control/delegation/database/Swift checks and the repository-required broader suite. Modify only test evidence if needed. Roll back only task-owned test/evidence changes on irreparable failure.
4. Packaged acceptance: build the real app/helper, record identity, and run isolated parent/child signal routes plus repeated cycles. Modify build outputs/evidence only. Do not promote if any gate fails.
5. Remote checkpoint and canonical promotion: normally push the verified repair, require exact-head CI green, rebuild that exact head, re-identify live processes and idle/accounting state, use the single authorized old-child SIGKILL only if every old-build condition matches, unlink only the exact stale unowned socket if required, install/start via normal product path, and prove SIGTERM/restart. Stop without improvisation on ambiguity or failure.
6. Scope-validator recovery: inventory the current external/production validation chain; build the offline corpus; implement the smallest repository-owned deterministic validator and sanitized trace; run targeted, relevant, full Python, and Ruff verification.
7. Pre-live checkpoint: inspect diff/secrets, commit validator hardening separately, normally push, and require exact-head CI green before any MiniMax worker.
8. New PI-5B3G campaign: refresh all live gates, gracefully stop the normal daemon, create a new campaign ID, run exactly three sequential authorized observations, and stop without retry on the first invariant failure.
9. Cleanup and delivery: close admission, prove terminal accounting, restore the repaired product, commit only sanitized evidence, push normally, wait for final exact-head CI, and do not merge.

## Drift Guard

Reread this brief after every phase. Every action must directly serve the locked goal or a success criterion. Do not expand scope silently; if a changed goal is required, update this brief first and stop when that change needs owner authority.

## Git Safety

Work only in `/Volumes/Taoruide外接/AI LLM TOOLS/pao-daemon-graceful-sigterm` on `fix/pao-daemon-graceful-sigterm`, based on `3f15e6d`. Inspect branch, status, diff, changed-file scope, and secret exposure before each commit. Use focused commits and normal push only. Never reset, clean, rebase, force-push, merge, delete unrelated worktrees, or modify the dirty parent repository. Rollback means reverting only task-owned edits with a new normal commit or stopping for owner direction; no history rewrite.
