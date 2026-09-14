# T030 — canonical delegated-child lineage inventory

## Scope and result

Read-only trace at source head `8b74fb9` (mission-lock commit atop authoritative
`51fbcbd`). No model/provider call and no canonical state mutation occurred.

## Canonical source of truth

| Question | Answer |
|---|---|
| Source of truth | The child task's append-only `TASK_SUBMITTED` audit event payload. |
| Persistence | SQLite `audit_events(task_id, event_type, payload_json, created_at)`, ordered by `audit_id`. |
| Relationship keys | `delegated_parent_task_id` and `delegated_parent_run_id`. |
| Write boundary | `SafetyKernelStore.submit_task(..., delegated_parent=(parent_id, parent_run_id))`. The parent task, parent run, active status, project/base/subpath match, non-self relation, and parent eligibility are checked before the child task and audit event are committed atomically. |
| Existing read API | `SafetyKernelStore.audit_events(task_id)` exposes decoded, task-scoped audit payloads in canonical order. There is no supported typed lineage resolver. |
| TaskRecord | Deliberately contains task state and scheduling fields only. It has no delegated-parent fields; adding them solely for the external harness would misstate the persisted domain model. |

Source anchors: `safety_kernel.py:128-170`, `:420-484`, `:1048-1159`, and
`:2592-2604`.

## Propagation trace

1. `DelegationBrokerContext` binds the host-owned parent task/run/project/base
   identity; the worker cannot supply these fields (`pi5_broker.py:51-70`).
2. After the bounded scope gate consumes its one-call budget, the broker mints a
   deterministic child task identity and constructs `DelegationChildPlan` from
   the host-bound parent context (`pi5_broker.py:201-237`).
3. `PAODelegationChildPort.execute_child` revalidates the active parent task/run
   and submits the child with `delegated_parent=(parent.task_id,
   plan.parent_run_id)` (`pi5_child_execution.py:413-460`).
4. `SafetyKernelStore.submit_task` writes the child task row and its
   `TASK_SUBMITTED` payload, including both lineage keys, in one immediate
   transaction (`safety_kernel.py:1072-1152`).
5. Campaign/observation membership is host orchestration context, not a column
   in `tasks` or a delegated-lineage audit key. The campaign harness already
   constructs the three `CampaignObservationExecutionIdentities` from the fresh
   authoritative campaign ID and retains an `identity_by_parent` map. A repaired
   observer can therefore compare the canonically resolved parent task/run to
   that map and the currently expected observation without parsing any ID.

## Proven external observer defect

The Campaign D external runner reads nonexistent properties in two places:

- `task_rows`: `task.delegated_parent_task_id` and
  `task.delegated_parent_run_id` (`run_campaign.py:445-464`).
- child activation observer: `task.delegated_parent_task_id`
  (`run_campaign.py:614-644`).

The second access occurs after process creation and durable run registration but
before protocol bootstrap, exactly matching Campaign D's preserved evidence.
Constructing any valid `TaskRecord` and performing the old access deterministically
raises `AttributeError`; no live/provider behavior is needed to reproduce it.

## Supported-access gap and repair boundary

Add one small repository-owned typed resolver over `audit_events(task_id)` rather
than a TaskRecord/schema mutation. It must:

- first require that the child task exists;
- require exactly one `TASK_SUBMITTED` event;
- require both non-empty lineage keys and reject partial metadata;
- reject multiple submission/lineage events as ambiguous even if values agree;
- require the referenced parent task and parent run to exist and agree;
- return only the canonical child, parent-task, and parent-run identities;
- fail with stable, sanitized rule/category/stage diagnostics and never mutate
  audit history.

Add a PI-5B3G ownership validator over that resolver. It must accept the
host-owned expected observation identity and the current campaign's
`identity_by_parent` map. It validates exact child identity, current-campaign
parent membership, expected observation number, expected parent task, and
expected parent run. This distinguishes cross-campaign from wrong-observation
relationships without parsing IDs or filenames.

## Failure behavior

All absent, malformed, partial, duplicate, missing-parent, missing/mismatched-run,
parent-as-child, child-ID, cross-campaign, and wrong-observation cases fail
closed before the harness records ownership or permits protocol bootstrap.
The diagnostic result contains only stable identities/booleans/rule/category/
stage; it contains no task intent, dynamic reason, provider transcript,
credential, argv, environment value, or raw model output.

## Existing coverage and required additions

Existing child execution tests prove canonical submission, delegated authority,
distinct worktrees/writers, durable child execution, replay, and parent gating,
but they do not expose a typed lineage lookup or reproduce the external observer.
Permanent additions must cover valid/missing/partial/ambiguous/missing-parent/
run-mismatch lineage, parent-as-child, cross-campaign, wrong observation, exact
Campaign D `AttributeError`, repaired ownership, and a fake/no-egress process →
durable run → ownership → protocol-bootstrap → clean-exit path with zero retry,
fallback, or orphan.
