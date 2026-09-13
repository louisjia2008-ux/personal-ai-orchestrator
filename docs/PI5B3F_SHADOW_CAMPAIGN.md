# PI-5B3F Operator-Controlled Bounded SHADOW Calibration Campaign

Status: `PI_5B3F_SCOPE_FROZEN_IMPLEMENTATION_PENDING`

Tracking: #50 (parent #37)

Baseline: `534370ecf6772e817544970c0c5c87d3e48f5605` (PI-5B3E merged).

## Objective

Provide an explicit host/operator control surface that can enable PI-5B3E comparable quota-pair capture for a finite number of delegation SHADOW observations. Starting a campaign never launches a task or worker and never grants delegation enforcement authority.

## Default-off and explicit budget

The persisted campaign state fails closed to OFF when absent/corrupt/unknown. A campaign can become ACTIVE only through an explicit owner request carrying a finite `max_observations` budget. There is no implicit campaign and no unlimited mode.

The campaign may optionally be scoped to a host-owned project allowlist. An empty allowlist means all registered projects are eligible; a non-empty allowlist rejects observations outside the listed project ids.

## Observation reservation

One immutable delegation `observation_id` consumes at most one slot. The campaign persists admitted observation ids so process restart/replay cannot double-consume budget.

When the final available slot is admitted, the campaign becomes EXHAUSTED for new observations while the already-admitted observation may still finish its after-snapshot calibration. Re-entry of an admitted observation remains recognized but does not consume another slot.

Stopping a campaign rejects future observations immediately. Already-admitted observations remain eligible to finish their paired calibration so a stop does not strand half-written before evidence.

## Operator control plane

Expose host-owned read/start/stop operations through the existing typed local control API. The surface reports only sanitized state:

- campaign id;
- OFF / ACTIVE / EXHAUSTED / STOPPED;
- configured max observations;
- consumed and remaining observations;
- optional project ids;
- start/stop/update timestamps;
- sanitized reason code.

The API never accepts an execution target, provider, model, runtime, quota value, verifier, worktree, prompt, or credential.

## Execution boundary

The campaign gate may only enable the PI-5B3E evidence path. It cannot:

- create or dispatch a task;
- select/rank a target;
- change quota admission;
- change delegation policy verdicts;
- retry/fallback/cancel a worker;
- change worktree/writer/verifier authority;
- enable production delegation enforcement.

## Acceptance matrix

Synthetic tests must prove:

1. missing/corrupt state is OFF;
2. start requires an explicit bounded budget;
3. optional project scope fails closed outside the allowlist;
4. one observation consumes exactly one slot;
5. replay/restart does not consume another slot;
6. final slot transitions the campaign to EXHAUSTED for new observations;
7. an already-admitted observation can finish after EXHAUSTED/STOPPED;
8. stop rejects new observations immediately;
9. start/stop API calls launch zero workers and perform zero quota refreshes;
10. child path performs zero extra quota refresh when campaign gate does not admit the observation;
11. admitted synthetic child preserves PI-5B3E one-refresh maximum and ordinary VERIFIED semantics;
12. state contains no credentials/provider payloads/prompts;
13. exact-head CI passes with zero real model calls.

Completion of PI-5B3F authorizes only the bounded campaign control mechanism. Running a real campaign still requires separate explicit worker/model budget authorization.
