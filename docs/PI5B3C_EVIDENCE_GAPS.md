# PI-5B3C Delegation Evidence Gap Closure

Status: `PI_5B3C_SCOPE_FROZEN_IMPLEMENTATION_PENDING`

Tracking: #43 (parent #37)

Baseline: `e1d98c13acc461bdd6489a2d1587723c1c91a322` (PI-5B3B merged).

## Objective

Close the host-evidence gaps that intentionally kept PI-5B3B `enforcement_ready=false`, and add append-only execution outcome evidence keyed to the immutable SHADOW observation. **No delegation enforcement is enabled in this phase.**

## Host-owned input contract

PI-5B3C may use only existing authoritative host sources:

- parent `TaskProfile` when one is present in the host runtime configuration / routing context;
- resolved task > project > global `RoutingPolicy`, using the same policy resolution semantics as normal routing;
- `TaskProfile.failure_count`, `risk`, and `predicted_quota_fraction_p90` only when they are actually present on the applicable profile;
- `RoutingPolicy.allow_paid_usage`, `failure_escalation_after`, and burn-estimate requirements from the resolved policy;
- canonical parent/child quota-pool identity from `quota_pool_id_for_target`;
- child commercial semantics from the resolved quota pool -> plan lineage and explicit unmetered quota-window truth;
- existing recommendation/candidate evidence for admitted child choices and usable headroom;
- durable task/run/dispatch timestamps and states for outcome enrichment.

The adapter must never infer quality failure from generic `BLOCKED` dispatch rows, provider names, model names, or prompt text.

## Signals that may remain unavailable

There is currently no general durable host-required-delegation or independent-review flag in the task schema. If no authoritative source exists, PI-5B3C must preserve that absence as a limitation and keep `enforcement_ready=false`.

A child burn estimate may be used only when an authoritative profile applies to the delegated child. Parent burn estimates must not be silently reused as child estimates.

## Commercial semantics

For a selected child target, resolve the active canonical quota pool first. Then classify using host registry truth:

- `UNMETERED` only when the selected target's canonical quota observation/window explicitly represents unmetered capacity;
- otherwise the pool's plan kind: `SUBSCRIPTION`, `PREPAID`, `PAY_AS_YOU_GO`, or `UNKNOWN`.

`paid_usage_required=true` only for an authoritative PAY_AS_YOU_GO classification. Unknown plan semantics stay unknown via limitations; no provider-name heuristic is allowed.

## Immutable decision + append-only outcome

The original `DelegationShadowRecord` remains immutable after append.

Execution outcomes are recorded separately under a deterministic identity derived from the original observation. Outcome records may contain only sanitized host truth, including when available:

- actual child execution target and canonical quota pool;
- child terminal state and verified flag;
- parent state observed when the child completed;
- parent terminal state / verifier truth when it becomes available through a later host closeout hook;
- bounded latency derived from durable timestamps;
- attempts-to-green only when derivable from durable run history;
- quota before/after only when two comparable, fresh observations of the same canonical pool exist.

No outcome record may contain prompts, transcripts, credentials, raw provider payloads, or raw exception text.

Outcome enrichment is append-only: later parent-final evidence creates a new phase record rather than rewriting the initial SHADOW decision or earlier child outcome.

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

Any evidence-adapter or journal failure remains observational and must not alter PI-5B2 child execution.

## Acceptance matrix

Synthetic tests must prove:

1. canonical parent profile failure/risk values are used when present;
2. generic infra/policy BLOCKED rows do not become quality failure count;
3. task > project > global routing-policy resolution matches the normal host path;
4. resolved `allow_paid_usage` and failure-escalation threshold are captured;
5. PAYG / subscription / prepaid / unmetered / unknown semantics come from registry/quota truth;
6. parent burn is never reused as child burn; a child profile may provide an authoritative estimate;
7. same-pool vs different-pool identity remains canonical;
8. missing host-required / independence signals remain explicit limitations;
9. original SHADOW record remains immutable and replayable;
10. child outcome is written separately and append-only;
11. a later parent-final outcome adds a new record rather than rewriting prior evidence;
12. journal/evidence failures cannot block or reroute child execution;
13. no secrets/raw provider bodies/prose are persisted;
14. exact-head CI passes with zero real worker/model calls.

Only after these gates pass may PI-5B3C be marked `PI_5B3C_EVIDENCE_GAPS_COMPLETE`. PI-5B3D, if pursued, is the earliest phase allowed to consider a narrowly scoped enforcement experiment.
