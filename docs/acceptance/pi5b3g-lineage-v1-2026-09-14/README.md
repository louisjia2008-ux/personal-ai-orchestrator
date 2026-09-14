# PI-5B3G Campaign E acceptance after child-lineage repair

## Result

`PI_5B3G_COMPLETE_AFTER_CHILD_LINEAGE_REPAIR`

Campaign E, `delegation-campaign-a42bd2a96d024dc39aa07645c83d32f0`, completed exactly three sequential SHADOW observations using MiniMax-M3. It consumed the complete authorized budget of three parents and three children. There were no retries, fallbacks, grandchildren, overlapping observations, identity collisions, unexpected writers, or campaign-state divergence.

## Repair boundary

Campaign D failed in an external observer because it attempted to read delegated lineage from attributes that do not exist on `TaskRecord`. The repository repair adds a typed, fail-closed accessor for the canonical child `TASK_SUBMITTED` audit metadata and a deterministic campaign ownership validator. It does not parse identifiers, trust model text, or alter the accepted daemon lifecycle, scope validator, identity scheme, frozen semantic verifier, provider target, quota logic, retry policy, or fallback policy.

The repair is commit `f62d515`. `pi5b3g-child-lineage-v1` has SHA-256 `0131d0cc68dea1a24da919d270e9619d3e937c13292aaea8b9091efe937b59a0`. Offline verification passed 36 focused tests, 180 relevant tests, the full Python suite at 1301 passed with 1 skip, and Ruff. The no-egress fake child pipeline reached process creation, durable run registration, canonical lineage ownership, protocol completion, and clean exit. Its injected post-durable lineage failure also reaped the child, made task/run state terminal, released the writer, and performed no retry.

## Live gates and observations

Exact-head CI passed on pre-live head `a5f652b6fddb6be51854c1d10398299de62db126`. The refreshed live preflight proved the repaired product healthy, canonical SQLite healthy and idle, Campaign D terminal and immutable, the fixture clean at its frozen head, all verifier/validator/identity/lineage/spawn hashes exact, the exact MiniMax-M3 target available, and refreshed quota `AVAILABLE / EXACT` with no shared-pool blocker.

Before model egress, the host created a fresh campaign ID and proved all 30 planned parent and child task/request/dispatch/run identities absent from canonical state. The repaired normal daemon then exited through its already-proven SIGTERM path, releasing the canonical socket and leaving SQLite at `0/0/0` running rows, active runs, and held writers.

For each observation, authoritative process, protocol, broker, store, and frozen-verifier evidence proved:

- one parent process, one `pao_delegate` call, and one parent artifact write;
- deterministic scope rule `ALLOW_EXACT_SCOPE` before child forwarding;
- one child process launched only through the sanitized broker path;
- canonical lineage rule `ALLOW_CANONICAL_CHILD_LINEAGE`, with campaign, observation, parent task, and parent run all matching;
- child and parent exited normally, and both frozen semantic verifiers passed;
- the broker returned `verified=true` and `child_state=VERIFIED`;
- terminal per-observation cleanup with no owned/orphan process, broker socket/directory, held writer, retry, fallback, or grandchild.

The exact fixed owner-approved prompts are preserved in `campaign-success.json`. The model-generated intent and reason are retained only as lengths and SHA-256 values.

## Terminal cleanup and product restore

Campaign E is terminal `EXHAUSTED / BUDGET_EXHAUSTED` with `3/3` completed observations. Worker accounting is exactly 3 parents, 3 children, and 6 total. The campaign has zero live or orphan processes; all broker sockets and directories are absent; the fixture remains clean; SQLite quick-check is `ok`; and running rows, active runs, and held writers are `0/0/0`.

The normal repaired product was restored from build `3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e`. One GUI, one PyInstaller parent, and one serving child are present. The serving child exclusively owns the canonical `0600` socket. Health is `ok`, the supervisor heartbeat advances, the helper hash is `2fd4500fb62110e3b9b83ce4265e8cb56a8d8aecdfd1f9b6318629cf6580e127`, code-sign verification passes, SQLite quick-check is `ok`, and canonical accounting remains `0/0/0`.

## Sanitization

This directory contains curated evidence only. It does not contain raw provider transcripts, raw dynamic intent/reason text, provider output content, environment values, or credentials. Historical Campaigns A-D remain in their existing separate evidence directories and canonical records; none was reopened, deleted, or rewritten.
