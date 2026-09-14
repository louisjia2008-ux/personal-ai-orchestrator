# Ralph final report — PI-5B3G child-lineage repair and Campaign E

## Current status

`AWAITING_FINAL_EVIDENCE_CI`

## Engineering result

Campaign D's external observer defect was reproduced without model calls: delegated lineage exists in canonical child `TASK_SUBMITTED` audit metadata, not as `TaskRecord` attributes. Commit `f62d515` adds a typed fail-closed store resolver and `pi5b3g-child-lineage-v1`, which validates exact child, parent task, parent run, campaign, and observation relationships from canonical metadata and host-owned identity maps without parsing identifiers.

Offline verification passed 36 focused tests, 180 relevant tests, the full Python suite at 1301 passed with 1 skip, and Ruff. The fake child pipeline proved process creation, durable run registration, lineage ownership, protocol completion, and clean exit. Injected failure after durable registration reaped the exact child, made run/task state terminal, released the writer, and did not retry.

The repair and verification head `a5f652b6fddb6be51854c1d10398299de62db126` was normally pushed, matched Draft PR #54 exactly, and passed Python, macOS Swift, and OpenCode CI before live use.

## Campaign E

Fresh Campaign E `delegation-campaign-a42bd2a96d024dc39aa07645c83d32f0` passed refreshed build, health, database, fixture, frozen verifier, scope-validator, identity, lineage, spawn, exact-target, quota, shared-pool, process, and identity-absence gates. The repaired normal daemon released canonical state through its proven graceful SIGTERM path before the campaign host began.

All three observations ran sequentially. Each used one MiniMax-M3 parent, one `pao_delegate`, deterministic `ALLOW_EXACT_SCOPE`, one broker-launched MiniMax-M3 child, canonical `ALLOW_CANONICAL_CHILD_LINEAGE`, a frozen-verifier-approved child artifact, a sanitized verified broker result, and a frozen-verifier-approved parent artifact. All six workers exited normally.

Campaign E is terminal `EXHAUSTED / BUDGET_EXHAUSTED` with 3 completed observations, 3 parents, 3 children, and 6 total workers. Automatic retries are 0, automatic fallback is false, and grandchildren are 0.

## Cleanup and restore

Campaign processes and orphans are zero; broker sockets/directories are absent; the fixture is clean at `d764edb4ca65c8ce5f77fcb2964d320ac3a503b9`; SQLite quick-check is `ok`; RUNNING/ACTIVE/HELD are `0/0/0`.

The repaired normal product is restored from build `3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e` with one GUI, one PyInstaller parent, one serving child, one canonical `0600` socket owner, health `ok`, advancing heartbeat, strict code-sign validation, expected helper hash, healthy SQLite, and no campaign state owner.

## Delivery gate

Curated evidence is prepared under `docs/acceptance/pi5b3g-lineage-v1-2026-09-14/`. It retains the exact fixed owner-approved prompts and dynamic argument hashes/lengths, but no raw provider transcript, raw dynamic intent/reason, provider output content, environment value, or credential value. Final completion remains gated on evidence commit, normal push, and exact-head CI; Draft PR #54 must remain unmerged.
