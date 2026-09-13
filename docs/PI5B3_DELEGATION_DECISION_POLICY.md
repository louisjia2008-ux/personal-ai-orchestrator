# PI-5B3 Delegation Decision Policy

Status: `PI_5B3A_POLICY_CONTRACT_IN_PROGRESS`

Tracking: #37

Baseline: `781f589cbb7caa02a2ab05aa38b44782e2eefbeb` (`main` after PI-5B2 merge).

## Why this phase exists

PI-5B2 proved that a real Pi parent can call `pao_delegate`, PAO can retain host authority, select and run one isolated child, verify the child, return the result to the parent, and independently verify the parent.

PI-5B3 changes the question from **can PAO delegate?** to **should PAO spend a second worker on this request?**

The first slice intentionally does not build a learned router or a weighted "delegation value" score. The existing model-resource architecture requires deterministic hard constraints and shadow evidence before ACTIVE routing. We do not yet have enough observed data to calibrate expected quality gain, coordination cost, independence value, or provider-specific delegation utility truthfully.

## Phase A contract

`delegation_policy.py` is a pure host-owned policy contract. It consumes explicit facts and emits a deterministic, machine-readable decision:

- `ALLOW` — hard gates pass and the host has an explicit justification, currently a host-required delegation or the existing failure-escalation threshold;
- `DENY` — a hard gate fails;
- `SHADOW_ONLY` — execution is mechanically possible, but current evidence does not justify active delegation.

Policy modes are separate from verdicts:

- `OFF` — delegation policy cannot authorize execution;
- `SHADOW` — calculate and record a recommendation, never enforce it;
- `ENFORCE` — a caller may enforce `ALLOW`/`DENY`, but downstream scheduler/quota/Safety Kernel/verifier gates still remain authoritative.

Phase A is provider-agnostic by construction. Worker-supplied provider/model/runtime/target identities are not inputs to this policy.

## Constraint-first order

The initial hard-denial order is deterministic:

1. delegation feature/mode disabled;
2. no eligible child candidate;
3. required quota truth missing;
4. required burn estimate missing;
5. predicted child burn exceeds usable headroom;
6. paid usage would be required without approval;
7. an explicit independence requirement cannot be satisfied.

Only after those gates pass does the policy inspect positive host justification.

This mirrors the repository's existing scheduler rule: a ranking/benefit signal can never compensate for a failed hard safety or quota constraint.

## Why shared quota is not an automatic denial

PI-5B2's accepted real run used the same `minimax-token-plan-cn` pool for parent and child. A same-pool child clearly has scarcity cost, but the repository does not yet contain calibrated evidence proving a universal threshold at which same-pool delegation becomes irrational.

Therefore Phase A records `SHARED_QUOTA_POOL` or `DIFFERENT_QUOTA_POOL` as an explanation only. It does not invent a numeric penalty. Later shadow evidence can justify a stronger rule.

## Positive justification

Phase A permits active `ALLOW` only from host-owned evidence that already has deterministic semantics:

- `host_required=True`, for a future host policy that explicitly requires delegation/review;
- `failure_count >= failure_escalation_after`, reusing the scheduler's existing escalation threshold concept.

Passing hard gates without either justification yields `SHADOW_ONLY`, not `ALLOW`.

## Safety boundary

This module does **not**:

- expose `pao_delegate` to a child;
- choose a provider/model/runtime/target;
- create a task or worktree;
- grant a writer;
- bypass quota admission;
- change verifier commands;
- enable paid overage;
- retry/fallback;
- modify parent or child terminal truth.

PI-5B2's broker, `DELEGATED_CHILD` authority, worktree isolation, single-writer enforcement, quota admission, and deterministic verifier remain unchanged.

## Phase A acceptance gate

Before wiring this policy into the broker/child execution path:

1. fixed inputs must replay to byte-equivalent decision JSON;
2. hard denials must take precedence over positive justification;
3. `SHADOW_ONLY` must never be enforceable;
4. `SHADOW` mode must never mark any decision enforceable;
5. same-pool scarcity must remain explanatory rather than an uncalibrated hidden score;
6. the contract must contain no provider/model special casing;
7. CI must pass with zero real model calls.

## Planned next slices

### PI-5B3B — host evidence adapter + shadow journal

Build the facts from existing PAO truth and persist a replayable shadow decision, without changing child execution behavior. Candidate inputs include task risk/failure count, canonical parent/child quota pools, absolute usable headroom, paid/unmetered status, and sufficiently supported performance telemetry.

### PI-5B3C — enforced admission experiment

After shadow evidence is adequate, allow a narrowly scoped feature flag to enforce policy decisions in disposable fixtures. `DENY` must create no child; `ALLOW` still must pass the existing child scheduler/quota/verifier chain.

### PI-5B3D — production activation gate

Production activation requires evidence that delegation does not cause unacceptable verified-quality regression and does not materially worsen quota survival/utilization. Adaptive/ML delegation remains out of scope until sample size is sufficient.
