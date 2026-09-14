# Ralph final report — PI-5B3G spawn repair and Campaign D

## Final status

`BLOCKED_PI_5B3G_CHILD_WORKER_OWNERSHIP_REGISTRATION_FAILED`

## Completed engineering work

- Preserved historical Campaigns A-C and all canonical rows.
- Proved Campaign C's parent failure was `WORKER_POST_CREATE_ACCOUNTING_ROLE_MISCLASSIFIED` in the external campaign wrapper.
- Added `pao-spawn-diagnostics-v1`, strict campaign worker-role parsing, exact post-create cleanup, durable-run/ownership fault repair, staged audits, and permanent fault-injection tests.
- Kept accepted daemon lifecycle, scope-validator, identity, verifier, quota, retry, and fallback semantics unchanged.
- Passed 297 relevant tests with 1 skip; full Python passed 1284 with 1 skip; Ruff passed; a real `pi --version` spawn-path smoke passed without model egress.
- Spawn fix `57deff6` and exact pre-live head `19adaa95b698ae8d6e9f80abf9ec8fd5eb139b67` were normally pushed; exact-head Python, macOS Swift, and OpenCode CI passed before Campaign D.

## Campaign D live result

All refreshed preflight, identity, quota, fixture, frozen-hash, product-health, and canonical-transition gates passed. Campaign D was created as `delegation-campaign-c129634a908548c497b7c5477851d764`.

Observation 1 proved the repaired parent path live: the campaign-scoped parent was admitted, PID 99586 was created, its run was durably registered, ownership and protocol completed, it exited 0, and it invoked `pao_delegate` exactly once. The deterministic scope validator returned `ALLOW_EXACT_SCOPE` and the broker forwarded one child.

The one child process, PID 99613, was created and durably registered. Before protocol bootstrap, the external Campaign D activation observer raised `AttributeError` at `DURABLE_RUN_REGISTERED`. Source inspection proves that child-only observer reads `TaskRecord.delegated_parent_task_id`, an attribute not present on `TaskRecord`; delegated lineage is stored in the child `TASK_SUBMITTED` audit metadata. The parent branch did not execute this defective access.

The executor's emergency repair terminated and reaped the exact child with signal 9, failed its run, released its writer, and returned a sanitized blocked result. The parent correctly created no parent artifact and became terminal `BLOCKED`. Campaign D stopped immediately. No retry, fallback, grandchild, Observation 2, or Observation 3 occurred.

## Accounting, cleanup, and restore

- admitted observations: 1
- completed observations: 0
- parent workers: 1
- child workers: 1
- total workers: 2
- automatic retries: 0
- fallback: no
- grandchildren: 0
- campaign: `STOPPED / OWNER_STOPPED`
- campaign/orphan processes: 0
- SQLite quick-check: `ok`
- RUNNING/ACTIVE/HELD: `0/0/0`
- fixture: clean at `d764edb4ca65c8ce5f77fcb2964d320ac3a503b9`

The repaired normal PAO product is restored with one GUI, one PyInstaller parent, one serving child, one `0600` canonical socket owner, health `ok`, advancing heartbeat, expected build/helper identity, valid code signature, and healthy idle canonical state.

## Evidence and remaining issue

Campaign D's sanitized evidence is in `docs/acceptance/pi5b3g-spawn-v1-2026-09-14/` and its companion Markdown report. Campaigns A-C remain separate and unchanged. No raw provider transcript, raw dynamic intent, environment value, or credential value is committed.

Remaining issue: the external campaign harness must obtain child lineage through a supported store/audit contract rather than a nonexistent `TaskRecord` attribute, then pass offline tests and a new exact-head CI gate. Campaign D is consumed and grants no retry.
