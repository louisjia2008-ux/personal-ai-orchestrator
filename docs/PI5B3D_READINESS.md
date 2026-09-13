# PI-5B3D Delegation Enforcement-Readiness Evaluation

Status: `PI_5B3D_SCOPE_FROZEN_IMPLEMENTATION_PENDING`

Tracking: #45 (parent #37)

Baseline: `9c5450d99ed651cef52e51896641737931fe9f14` (PI-5B3C merged).

## Objective

Determine whether accumulated PI-5B3 SHADOW decisions and append-only execution outcomes are sufficiently complete and non-regressing to justify **considering** a future narrowly scoped enforcement experiment.

This phase is observational only. It does not enable delegation enforcement.

## Authority boundary

The readiness evaluator has **zero** authority to:

- admit or deny a live delegation request;
- select or re-rank a child target;
- modify quota admission or shared-pool gates;
- launch, retry, reroute, or cancel workers;
- mutate task/run/dispatch truth;
- weaken deterministic verification;
- rewrite a SHADOW decision or an outcome record.

It only consumes already-persisted evidence and returns a deterministic readiness report.

## Inputs

The evaluator may use only:

- immutable `DelegationShadowRecord` rows;
- append-only `DelegationOutcomeRecord` rows;
- an explicit host-supplied `DelegationReadinessPolicy`.

It must not infer missing facts from prompts, worker prose, provider/model names, generic BLOCKED rows, current mutable quota state, or wall-clock guesses.

## Explicit policy requirement

There is intentionally **no implicit readiness threshold**. The host must supply a readiness policy containing the minimum complete sample count and any optional pool-coverage requirements.

Without an explicit policy, readiness is `NOT_READY`.

The evaluator itself never turns a report into production enforcement.

## Hard readiness gates

A sample is complete only when the same observation has:

1. a replayable SHADOW decision;
2. SHADOW host evidence that is itself `enforcement_ready`;
3. one `CHILD_FINAL` outcome;
4. one `PARENT_FINAL` outcome;
5. child final truth `VERIFIED` with verifier PASS;
6. parent final truth `VERIFIED` with verifier PASS;
7. comparable quota-before / quota-after evidence identifiers when the readiness policy requires quota-survival evidence;
8. matching parent/child/observation identity across all records.

Unknown or contradictory evidence fails closed.

## Reporting

The report must include at least:

- status: `NOT_READY` or `READY_FOR_LIMITED_EXPERIMENT`;
- deterministic input digest;
- total SHADOW observations;
- replayable SHADOW count;
- `enforcement_ready` SHADOW count;
- matched child-outcome count;
- matched parent-outcome count;
- complete sample count;
- child VERIFIED count;
- parent VERIFIED count;
- quota-comparable count;
- same-pool / different-pool / unknown-pool counts;
- blocking reason codes;
- non-blocking advisories.

No ratio, utility score, or expected-quality-gain estimate may be invented when the source evidence does not provide it.

## Pool coverage

Same-pool and different-pool behavior are counted separately. A host policy may require either coverage class for a future experiment. Missing coverage is a deterministic readiness blocker only when the explicit policy requests it.

## Quota-survival semantics

PI-5B3C currently records quota-before / quota-after identifiers only when comparable evidence exists. PI-5B3D does not infer quota survival from unrelated snapshots or from current quota state.

When `require_quota_comparability=true`, any sample without both comparable identifiers is incomplete. This is intentionally conservative and may keep the system `NOT_READY` until a later campaign captures the needed evidence.

## Acceptance matrix

Synthetic tests must prove:

1. no policy => NOT_READY;
2. no SHADOW evidence => NOT_READY;
3. replay mismatch => NOT_READY;
4. `enforcement_ready=false` SHADOW => sample incomplete;
5. missing child or parent outcome => sample incomplete;
6. child verifier regression => NOT_READY;
7. parent verifier regression => NOT_READY;
8. missing quota comparability blocks when explicitly required;
9. same/different/unknown pool counts are deterministic;
10. optional pool-coverage requirements fail closed;
11. fully synthetic complete evidence can produce `READY_FOR_LIMITED_EXPERIMENT` only with an explicit policy;
12. input order does not change the digest/report;
13. existing SHADOW/outcome files are never modified;
14. CI passes with zero real worker/model calls.

Completion of PI-5B3D means the readiness evaluator exists and is verified. It does **not** mean the real accumulated evidence is ready, and it does **not** authorize production enforcement.
