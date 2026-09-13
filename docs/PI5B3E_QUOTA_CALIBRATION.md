# PI-5B3E Shadow Campaign Comparable Quota-Pair Capture

Status: `PI_5B3E_QUOTA_CALIBRATION_COMPLETE`

Tracking: #48 (parent #37)

Baseline: `a3d1fd833733d839c05fd1a322cbad84aaccb75e` (PI-5B3D merged).

## Objective

Attach validated comparable quota-before / quota-after snapshot identities to new delegation outcome evidence during an explicitly enabled SHADOW calibration campaign.

This phase remains observational. It does not enable delegation enforcement.

## Default-off boundary

Calibration is disabled by default. When disabled, the existing PI-5B2/B3 child path performs no additional quota refresh and produces the same execution outcome semantics as before.

When explicitly enabled for a campaign, calibration may perform at most one post-child read-only quota refresh for the host-selected child provider. It may not invoke a model, retry a worker, change the selected target, or affect quota admission.

## Before snapshot

The before snapshot comes from the existing persisted quota cache used by the host recommendation path. It is captured after the recommendation selected a child target and before the child dispatch begins.

The capture is accepted only when:

- the selected child target resolves to one canonical quota pool;
- the cached snapshot names that exact pool;
- the snapshot confidence is not UNKNOWN;
- every active binding window used for comparison has precise remaining quota;
- the snapshot identity is durable.

No provider-name or runtime-name heuristic substitutes for canonical pool identity.

## After snapshot

After the child reaches terminal truth, an enabled calibration observer may call the existing `QuotaRefreshService.refresh(provider_id)` path once. That path is structurally read-only and uses documented quota/usage endpoints only.

The resulting snapshot is accepted as the after observation only when it is comparable to the frozen before snapshot:

- same canonical quota pool;
- distinct snapshot id;
- observation time is not earlier than the before observation;
- non-UNKNOWN confidence;
- same set of active window identities used for comparison;
- each matched window has the same window kind and reset-cycle identity;
- precise remaining quota is present in both observations.

A provider correction may increase or decrease remaining quota; PI-5B3E records comparability, not a fabricated causal burn attribution.

## Outcome semantics

Only a validated pair populates `quota_before_snapshot_id` and `quota_after_snapshot_id` on a newly appended `CHILD_FINAL` outcome.

`PARENT_FINAL` inherits the exact already-frozen pair from `CHILD_FINAL`. It performs no additional quota refresh.

Existing SHADOW and outcome records remain immutable. A record written before PI-5B3E is never rewritten to add quota evidence retroactively.

## Failure semantics

Calibration is best-effort and non-authoritative. Any missing cache, unavailable credential, provider read error, UNKNOWN/mismatched snapshot, comparison failure, or journal error:

- leaves the quota pair absent;
- records only sanitized limitations/events where appropriate;
- never blocks/reroutes/retries/cancels the child;
- never changes parent/child task truth.

## Acceptance matrix

Synthetic tests prove:

1. default OFF performs zero extra refreshes;
2. exact canonical before snapshot is captured when valid;
3. wrong-pool / UNKNOWN / imprecise before snapshots are rejected;
4. one enabled child performs at most one post-child refresh;
5. same-pool, same-window/reset-cycle before/after snapshots are accepted;
6. pool/window-id/reset-cycle mismatch is rejected;
7. after observation earlier than before is rejected;
8. identical snapshot id is rejected as not a before/after pair;
9. child VERIFIED remains VERIFIED when calibration fails;
10. CHILD_FINAL receives ids only for a validated pair;
11. PARENT_FINAL reuses CHILD_FINAL ids with zero additional refreshes;
12. replay/re-entry does not cause a second refresh or rewrite the existing outcome;
13. exact-head CI passes with zero real model calls.

## Verification

Implementation head `3032c0d820a5b65d1846482bac11d1d9a48c391c` passed exact-head CI #348 (`34750097917`):

- Ruff: PASS;
- Python: 1186 passed, 4 skipped;
- exact base/head diff hygiene: PASS;
- OpenCode adapter: PASS;
- macOS Swift build: PASS;
- real worker/model calls: 0.

## Completion boundary

PI-5B3E is complete as an **opt-in capability**. Production calibration remains disabled by default and the daemon/product does not automatically run a SHADOW campaign. No production delegation enforcement is enabled.

The next safe phase is a bounded operator-controlled SHADOW campaign surface that can explicitly enable this calibration capability for selected disposable/approved tasks and collect real evidence under a separately authorized worker budget.
