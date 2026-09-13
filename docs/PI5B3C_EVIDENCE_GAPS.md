# PI-5B3C Delegation Evidence Gap Closure

Status: `PI_5B3C_EVIDENCE_GAPS_COMPLETE`

Tracking: #43 (parent #37)

Baseline: `e1d98c13acc461bdd6489a2d1587723c1c91a322` (PI-5B3B merged).

## Objective

Close the host-evidence gaps that intentionally kept PI-5B3B `enforcement_ready=false`, and add append-only execution outcome evidence keyed to the immutable SHADOW observation. **No delegation enforcement is enabled in this phase.**

## Host-owned input contract

PI-5B3C uses only existing authoritative host sources:

- parent `TaskProfile` when one is present in the host runtime configuration / routing context;
- resolved task > project > global `RoutingPolicy`, using the same policy resolution semantics as normal routing;
- `TaskProfile.failure_count`, `risk`, and `predicted_quota_fraction_p90` only when they are actually present on the applicable profile;
- `RoutingPolicy.allow_paid_usage`, `failure_escalation_after`, and burn-estimate requirements from the resolved policy;
- canonical parent/child quota-pool identity from `quota_pool_id_for_target`;
- child commercial semantics from the resolved quota pool -> plan lineage and explicit unmetered quota-window truth;
- existing recommendation/candidate evidence for admitted child choices and usable headroom;
- durable task/run/dispatch timestamps and states for outcome enrichment.

The adapter never infers quality failure from generic `BLOCKED` dispatch rows, provider names, model names, or prompt text.

## Signals that remain honestly unavailable

There is currently no general durable host-required-delegation or independent-review flag in the task schema. When no authoritative source exists, PI-5B3C preserves that absence as a limitation and keeps `enforcement_ready=false`.

A child burn estimate is used only when an authoritative profile applies to the delegated child. Parent burn estimates are never silently reused as child estimates. The target-adjusted child estimate, when present, is derived through the active target-specific `ConsumptionRule`.

This phase therefore closes the **evidence plumbing contract**, not the empirical/calibration requirement for enforcement. A record with unresolved authoritative signals remains non-enforcement-ready by construction.

## Commercial semantics

For a selected child target, PI-5B3C resolves the active canonical quota pool first and then classifies from host registry truth:

- `UNMETERED` only when the selected target's canonical availability/window explicitly represents unmetered capacity;
- otherwise the pool's plan kind: `SUBSCRIPTION`, `PREPAID`, `PAY_AS_YOU_GO`, or `UNKNOWN`.

`paid_usage_required=true` only for an authoritative PAY_AS_YOU_GO classification. Unknown plan semantics stay unknown via limitations; no provider-name heuristic is used.

An admitted candidate with observed quota fractions but unresolved commercial lineage retains PI-5B3B's conservative missing-burn gate. This does not relabel the commercial mode: the mode remains `UNKNOWN`; it only prevents a newer evidence adapter from weakening the previously accepted fail-closed behavior.

## Immutable decision + append-only outcome

The original `DelegationShadowRecord` remains immutable after append.

Execution outcomes are recorded separately under deterministic identities derived from the original observation and phase:

- `CHILD_FINAL` captures actual child target/pool, terminal/verified truth, parent state observed at child completion, bounded durable-run timing/count information, and explicit limitations;
- `PARENT_FINAL` is appended only after the parent reaches host terminal truth and captures parent verifier/terminal evidence without rewriting either the original SHADOW record or `CHILD_FINAL`.

Quota before/after remains unavailable unless comparable fresh observations can be proven. PI-5B3C records that limitation instead of inventing a delta.

No outcome record contains prompts, transcripts, credentials, raw provider payloads, or raw exception text. Capture failures remain best-effort observational failures and have zero execution/task authority.

## Safety boundary

This phase does **not**:

- enforce `ALLOW`, `DENY`, or `SHADOW_ONLY`;
- change target ranking or selected execution target;
- change quota admission or shared-pool blocking;
- change broker budget, depth, recursion, fallback, or retry behavior;
- change worktree/writer ownership;
- change deterministic verifier authority;
- enable real model calls for acceptance;
- activate production automatic delegation.

## Accepted properties

Synthetic/regression coverage proves:

1. canonical parent profile failure/risk values are used when present;
2. generic infra/policy `BLOCKED` rows do not become quality failure count;
3. task > project > global routing-policy resolution matches the normal host precedence and preserves non-objective policy knobs;
4. resolved `allow_paid_usage` and failure-escalation threshold are captured;
5. PAYG / subscription / prepaid / unmetered / unknown semantics come from registry/quota truth;
6. parent burn is never reused as child burn; an authoritative child profile is target-adjusted through the active consumption rule;
7. reserve + policy uncertainty are reflected in usable child headroom;
8. same-pool vs different-pool identity remains canonical;
9. missing host-required / independence signals remain explicit limitations;
10. original SHADOW records remain immutable and replayable;
11. child outcome is written as a separate append-only phase record;
12. a later parent-final outcome adds a new phase record instead of rewriting prior evidence;
13. journal/evidence capture remains non-authoritative and cannot block/reroute child execution;
14. PI-5B3B's accepted conservative missing-burn behavior remains intact when commercial lineage is incomplete;
15. no real worker/model call is introduced or authorized by this phase.

## Verification

Implementation head: `339867527b410e494fa8dfb406a3135c3fbdc568`.

Exact-head CI run `34747445446` / #337: **SUCCESS**.

- Python Ruff: PASS;
- Python tests: **1165 passed, 4 skipped**;
- exact base/head `git diff --check`: PASS;
- OpenCode adapter typecheck/contracts: PASS;
- macOS Swift build: PASS;
- real model/worker calls introduced by PI-5B3C: **0**.

An earlier implementation checkpoint exposed one intentional backward-compatibility regression in the already accepted B3B missing-burn SHADOW test. The final source restored the fail-closed burn requirement for an observed metered candidate whose commercial lineage is still `UNKNOWN`, without fabricating its plan kind. CI #337 verifies the corrected behavior.

This completion commit changes documentation only. PR merge still requires the final documentation HEAD to remain exact-head CI green.

PI-5B3D, if pursued, is the earliest phase allowed to consider a narrowly scoped enforcement experiment. It must consume accumulated SHADOW/outcome evidence rather than treating `PI_5B3C_EVIDENCE_GAPS_COMPLETE` as proof that enforcement is already calibrated.
