# PI-5B3B Delegation Shadow Evidence

Status: `PI_5B3B_SHADOW_EVIDENCE_IN_PROGRESS`

Tracking: #41 (parent PI-5B3: #37)

Baseline: `a787be7c0cb2f213333231f7e955b635a8332ad6`, the merge of PI-5B3A.

## Goal

PI-5B3A defined the deterministic decision contract. PI-5B3B connects that contract to host-owned evidence in **SHADOW** mode only. A shadow verdict is observational and cannot change PI-5B2 child execution.

## Implemented in this slice

`delegation_shadow.py` builds a sanitized, versioned record from durable host truth:

- parent task/run/worker identity from the Safety Kernel run row;
- eligible child count and selected candidate from the existing dispatch recommendation;
- canonical parent/child quota pools through `quota_pool_id_for_target`;
- selected candidate quota headroom from the same `remaining_fractions` used by recommendation;
- whether an admitted different-provider candidate exists;
- explicit evidence limitations instead of fabricated values.

The record contains the complete `DelegationPolicyInput` and resulting versioned `DelegationDecision`, so it can be replayed deterministically later. It intentionally excludes worker intent/reason prose, transcripts, provider payloads and credentials. `enforcement_ready` remains false while any listed evidence gap exists.

`DelegationShadowJournal` is append-only per parent-run/child identity, writes atomically with mode `0600`, and preserves the first observation on replay instead of time-shifting historical evidence after quota state changes.

`PAODelegationChildPort` now has an optional shadow-journal hook immediately after host recommendation and before durable child dispatch. The hook is best-effort. Any build/write/audit failure is reduced to the fixed reason code `SHADOW_CAPTURE_FAILED` and **must not** block, reroute or retry the child.

## Evidence gaps preserved honestly

The current child-dispatch path does **not** expose a canonical `TaskProfile.failure_count`. In particular, a BLOCKED owner-dispatch row is not a valid substitute: it may represent policy, quota, infrastructure or invocation failure rather than model/task quality failure. PI-5B3B therefore records `failure_count = 0` together with `task_profile_failure_count_not_available_in_child_dispatch_context`; it does not trigger failure escalation from an invalid proxy.

The current owner-dispatch recommender also does not expose a calibrated per-task predicted burn. For a metered selected candidate, PI-5B3B therefore records:

- `predicted_child_burn_fraction = null`;
- `predicted_child_burn_unavailable` limitation;
- the PI-5B3A hard policy can consequently recommend `DENY / REQUIRED_BURN_ESTIMATE_MISSING` in SHADOW while the unchanged PI-5B2 child still executes normally.

This is intentional. Shadow evidence exists to show what is missing before enforcement, not to manufacture economics inputs.

The child-dispatch context does not yet expose an authoritative host-required-delegation signal, independence requirement, resolved full `RoutingPolicy`, or reliable PAYG-requirement bit. This slice records those limitations and does not infer them from provider names, prompts, or BLOCKED rows. These missing inputs keep the record explicitly non-enforcement-ready.

## Non-goals / safety boundary

This slice does not:

- enforce any shadow verdict;
- change target ranking or target selection;
- change quota admission;
- change broker request budgets;
- change worktree/writer ownership;
- enable recursion;
- change retry/fallback behavior;
- alter deterministic verifier authority;
- authorize real model calls.

The daemon-level default journal wiring is intentionally left for the next small commit after the host evidence/journal contract passes CI; the child port hook is optional, preserving byte/semantic behavior when no journal is supplied.

## Acceptance for this checkpoint

- a synthetic verified child can produce a replayable SHADOW record;
- persisted JSON contains no child intent/reason prose;
- unavailable failure-count/burn/PAYG/policy signals are recorded rather than invented;
- limited evidence is explicitly `enforcement_ready = false`;
- a SHADOW `DENY` does not prevent the ordinary child from reaching VERIFIED;
- journal failure cannot change the child result and does not persist exception text;
- repeated child replay does not rewrite the original observation;
- exact-head CI must pass with zero real model calls before advancing to daemon-level shadow wiring.
