# T018 — PI-5B3G campaign execution identity inventory

## Scope and confirmed defect

This is a read-only source/schema trace at source HEAD `84c7523` (production
source remains unchanged from `11e9f5f` plus the Ralph mission checkpoint).
The failed scope-v2 campaign host constructs these static values for every
campaign in `pi5b3g-real-campaign-execution-20260914-scope-v2/run_campaign.py`:

- parent task: `pi5b3g-obs{N}-parent`
- parent submit request: `pi5b3g-obs{N}-submit`
- parent dispatch request: `pi5b3g-obs{N}-dispatch`
- timeout cancel request: `pi5b3g-obs{N}-timeout-cancel`

They contain the observation number but not the authoritative campaign ID.
Campaign B therefore reused Campaign A's Observation 1 task/request namespace.
The first observable conflict was `tasks.request_id`, before dispatch or model
execution. No evidence supports weakening canonical conflict handling.

## Canonical namespace and replay semantics

| Identifier | Construction / owner | Durable namespace | Constraint / role | Replay behavior |
|---|---|---|---|---|
| `campaign_id` | `DelegationCalibrationCampaignStore.start`; host UUID4 | campaign JSON snapshot | fresh start creates `delegation-campaign-<32 lowercase hex>` | terminal campaigns are not reopened; new start creates a new namespace |
| parent `task_id` | current external host, static | `tasks.task_id` | global primary key | must name the same logical task; a distinct request cannot reuse it |
| parent submit `request_id` | current external host, static | `tasks.request_id` | global unique key | same full submission payload returns the existing task; any differing field raises the canonical conflict |
| parent dispatch request | current external host, static | `owner_dispatches.request_id` | global unique key | same complete dispatch tuple is idempotent; differing tuple conflicts |
| parent `dispatch_id` | `initiate_owner_dispatch` | `owner_dispatches.dispatch_id` | global primary key; `owner-dispatch-{dispatch_request_id}` | deterministically follows the request ID |
| parent `run_id` | `OwnerDispatchExecutor` | `runs.run_id` | global primary key; `run-{dispatch_id}` | deterministic for the durable dispatch; one RUNNING run per task |
| worker identity | execution target plus spawned process | run row / process | `worker_id` is target identity, PID is ephemeral observation | not an idempotency key |
| broker session key | `PiOwnerDispatchExecutor` | in-process `_delegation_brokers` | keyed by parent dispatch request; lifetime is one parent execution | campaign-scoped once parent dispatch request is scoped |
| `tool_call_id` | model protocol event | one `DelegationBrokerSession` in memory | max 256; session-local replay fingerprint | identical call ID and payload replays; differing payload rejects `TOOL_CALL_ID_CONFLICT`; it is not a global task/run identity |
| delegation ordinal | model argument bounded by host | one broker session | 1..3 generally; live host reduces maximum child count to 1 | contributes to host child ID and must equal next unconsumed ordinal |
| child `task_id` | `delegation_child_task_id` host SHA-256 derivation | `tasks.task_id` | `pi5-child-<20 hex>` from parent task, parent run, ordinal | stable for reconstruction; differs when campaign-scoped parent task/run differs |
| child submit request | `PAODelegationChildPort` | `tasks.request_id` | `pi5-child-submit-{child_task_id}` global unique | stable, distinct from parent and other operations |
| child dispatch request | `PAODelegationChildPort` | `owner_dispatches.request_id` | `pi5-child-dispatch-{child_task_id}` global unique and reserved from owner input | stable, distinct from child submit and parent dispatch |
| child `dispatch_id` | `initiate_owner_dispatch` | `owner_dispatches.dispatch_id` | `owner-dispatch-{child_dispatch_request}` global primary key | stable for replay |
| child `run_id` | `OwnerDispatchExecutor` | `runs.run_id` | `run-{child_dispatch_id}` global primary key | stable for the durable child dispatch |
| shadow `observation_id` | `DelegationShadowJournal` | append-only runtime-state filename | hash of policy version, parent run, child task | stable and campaign-distinct once parent/run/child are scoped |
| campaign admitted observation | campaign store claim | `admitted_observations` map | keyed by shadow observation ID | same observation/project replays true; different project fails |

The SQLite schema defines separate tables, so task, request, dispatch, and run
keys do not literally share one SQL namespace. They should still have distinct
role/operation strings for diagnostics, future-proofing, and the owner's stated
invariant. `TaskSubmitRequest`, `DispatchTaskRequest`, and identifier validation
bound task/request values to 1..128 characters and `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.

## Exact Observation 1 flow

1. Campaign start generates and persists the authoritative campaign ID; it
   launches no worker.
2. The external host constructs Observation 1 parent task/submit/dispatch
   request IDs. Today these omit the campaign ID.
3. `ControlPlaneService.submit_task` validates identifier syntax and passes the
   full payload to `SafetyKernelStore.submit_task`.
4. `submit_task` queries globally unique `tasks.request_id`. Exact payload replay
   returns the one existing row; any differing task or payload field raises and
   maps to `conflicting_request_id`.
5. Dispatch validation passes the host request to `initiate_owner_dispatch`,
   which derives `dispatch_id = owner-dispatch-{request_id}` and reserves it.
   Exact tuple replay returns the existing dispatch; a changed tuple maps to
   `conflicting_dispatch_request_id`.
6. The executor derives `run_id = run-{dispatch_id}`, starts one run, and builds
   a host-bound broker context containing parent task/run/project/base identity.
7. The model may supply only bounded `tool_call_id`, ordinal, intent, and reason.
   The scope gate and one-call campaign wrapper run before child forwarding.
8. The broker derives the child task ID from parent task, parent run, and ordinal.
9. The child port derives separate child submit and dispatch request IDs from
   that child task ID, uses canonical submit/reserve paths, and never exposes
   identity selection to the model.
10. Child dispatch and run IDs derive from the child dispatch request exactly as
    on the parent path; shadow/outcome evidence derives from parent run and child
    task identities.

## Minimal repair boundary

Introduce a small repository-owned `pi5b3g-campaign-identity-v1` factory that:

- validates the exact authoritative campaign ID form;
- accepts only observations 1..3 and finite host-owned role/operation values;
- uses the full 32-hex campaign UUID as the namespace rather than a timestamp,
  PID, random per-reconstruction nonce, prompt, model, or provider;
- produces deterministic parent task, submit-request, dispatch-request,
  derived dispatch, derived run, and bounded timeout-cancel identities;
- exposes/uses named child submit and dispatch derivation helpers so tests audit
  the actual production path rather than duplicating string literals;
- keeps every API-bound identifier within 128 characters and the canonical
  identifier alphabet.

The new external third-campaign host will import this repository-owned factory,
map parent task IDs back to their precomputed observation bundles instead of the
old `pi5b3g-obsN-parent` regex, precompute all three parent bundles, and query
canonical tasks/requests/dispatches/runs for absence before any model prompt.

## Required proof matrix

- identical campaign/observation reconstruction is byte-identical;
- different campaigns with the same prompt/fixture/model are distinct;
- observations 1, 2, and 3 are distinct;
- parent/child and task/submit/dispatch/delegation labels are distinct;
- a seeded historical `pi5b3g-obs1-submit` row does not collide with a new
  campaign Observation 1;
- two three-observation campaigns have no identity intersections;
- actual control-plane submit replay admits exactly one task;
- same request with a different payload still returns canonical conflict;
- canonical dispatch replay remains idempotent and differing payload conflicts;
- production child task/submit/dispatch/run identities are stable inside one
  campaign and differ across campaigns.

No daemon, scope-validator, verifier, target, quota, retry, fallback, worker, or
campaign-observation semantic change is needed.
