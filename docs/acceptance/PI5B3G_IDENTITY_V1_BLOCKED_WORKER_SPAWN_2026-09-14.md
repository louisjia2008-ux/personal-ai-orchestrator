# PI-5B3G campaign identity v1 — blocked at parent worker spawn

## Outcome

`pi5b3g-campaign-identity-v1` corrected the historical cross-campaign request-ID conflict. The third campaign used a new authoritative campaign UUID, and a pre-dispatch query proved all 30 parent/child task, request, dispatch, and run identities for Observations 1–3 were absent from canonical state.

Observation 1 was admitted under the new campaign-scoped identity. Its single authorized parent launch attempt then failed at `PARENT_WORKER_SPAWN`, before a durable run, scope validation, delegation, or child existed. The host stopped admission immediately and did not retry. The resulting status is:

`BLOCKED_PI_5B3G_PARENT_WORKER_SPAWN_FAILED`

## Identity repair

- Baseline: `11e9f5f0fcff79e109bc5235352bb37718457fbd`
- Fix commit: `885f006a3294a758b0808616c83b60c1b4c97d6d`
- Pre-live exact head: `55f376b00d63b29572a2b093bc18410d35872849`
- Scheme: `pi5b3g-campaign-identity-v1`
- Factory SHA-256: `79ebc21d33d4a39d05bb0a6049b43c1963cfa8ace8c26a4268a6844b9382c03a`
- Offline verification: same-campaign idempotency, cross-campaign uniqueness, observation/role/operation separation, historical-static-ID regression, two-campaign construction, canonical replay, and conflict safety all passed.
- Full Python: 1260 passed, 1 skipped.
- Ruff: pass.
- Pre-live exact-head CI: Python, macOS Swift, and OpenCode jobs passed.

## Preserved campaign lineage

1. `delegation-campaign-82fe8f1a311044ceb950fe9a659f34ff`: historical dynamic-scope rejection; 1 parent, 0 child.
2. `delegation-campaign-fb69fd7288714eeaacbb9e5550ab5c82`: historical static request-ID conflict before worker launch; 0 parent, 0 child.
3. `delegation-campaign-4c61456b20d84cb491d041e6c0cb88ed`: identity-fixed campaign; stopped at the first parent worker spawn failure.

No historical campaign or canonical row was reopened, deleted, or mutated to avoid a conflict.

## Third-campaign identity assertion

Observation 1 used:

- Task: `pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-task`
- Submission request: `pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-submit`
- Dispatch request: `pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-dispatch`
- Dispatch: `owner-dispatch-pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-dispatch`
- Expected run: `run-owner-dispatch-pi5b3g-4c61456b20d84cb491d041e6c0cb88ed-obs1-parent-dispatch`

All 30 precomputed identities for the three observations were absent before model execution. The Observation 1 task and dispatch were then admitted without an identity conflict.

## Blocking evidence

Canonical state records the task and dispatch as `BLOCKED`; the dispatch has `failure_code=WORKER_SPAWN_FAILED` and sanitized reason `worker process could not be spawned: RuntimeError`. No durable run row exists. Audit events include argument construction, writer acquisition/release, and the blocked dispatch, but no recorded worker-start event.

One transient subprocess PID was observed and is now gone. Therefore the bounded parent launch attempt is conservatively counted as consumed. Provider egress is `UNKNOWN_NOT_PROVEN`: it is neither claimed absent nor claimed successful. No raw provider transcript was retained.

Scope validations: 0. Child forwarding: no. Child workers: 0. Observations 2 and 3 were not started. Retry: 0. Fallback: no. Grandchildren: 0.

## Cleanup and restore

The campaign is terminal `STOPPED / OWNER_STOPPED`, with zero completed observations. Campaign processes and owned processes are zero; the exact campaign worktree was removed; no broker socket or broker directory was created; the fixture remains clean at `d764edb4ca65c8ce5f77fcb2964d320ac3a503b9`.

Canonical SQLite reports `quick_check=ok` and RUNNING/ACTIVE/HELD = 0/0/0.

The repaired normal product was restored from build `3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e`. It has one GUI, one PyInstaller parent, one serving child, and one canonical socket owner. The helper SHA-256 remains `2fd4500fb62110e3b9b83ce4265e8cb56a8d8aecdfd1f9b6318629cf6580e127`; strict code-sign verification passes; health is `ok`; and heartbeat advanced from `2026-09-14T05:38:14.067855+00:00` to `2026-09-14T05:38:24.072013+00:00`.

The scope validator and frozen verifier were not changed. No campaign retry or replacement ID was minted live.
