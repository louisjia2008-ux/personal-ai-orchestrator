# Routing Plan / Role / Decision Contract

Status: **FROZEN** (Gate-R, owner-approved design decision D7)

This document is the authoritative shape of routing state as exposed to clients.
It is a *contract*, not a description of current runtime behaviour: the daemon
does not yet plan or execute REVIEWER / FINAL_AUDITOR roles. The contract exists
so that the UI can be built once, against a shape that will not change when
those roles land.

## Why a plan is not a decision

A single "routing decision" cannot carry three roles. A role may be rerouted or
retried, producing several selections over the life of one task, and a reviewer
that was rerouted twice is not the same fact as a reviewer that was chosen once.
The model therefore separates:

```text
Task
 └── RoutingPlan            (identity + revision + declared roles)
      └── RoutingRoleState  (one per declared role: lifecycle status + outcome)
           └── RoutingDecision*   (zero or more, independent, ordered)
```

- A **plan** says which roles this execution requires.
- A **role state** says where that role currently stands.
- A **decision** says which worker/provider was selected, and why, at one moment.

## Authority

`declared_roles` is **authoritative orchestrator execution/routing policy state**.

It is determined by the policy / execution-planning layer. It must never be
decided or inferred by:

- the UI,
- workers,
- provider adapters,
- reviewers.

The UI renders exactly the roles the plan declares. It never adds a role because
a policy name suggests one, and never drops a role it does not recognise.

## Freeze semantics and revisions

`declared_roles` is **not** frozen at task creation. It is frozen when an
execution `RoutingPlan` is created.

If policy or escalation later changes the required roles, the daemon **creates a
new plan revision** and preserves the old one. Plans are never mutated in place.

- `plan_id` identifies a plan lineage.
- `revision` increments within that lineage (1-based).
- `superseded_by_plan_id` points at the plan revision that replaced this one, or
  is null for the plan currently in force.

A client showing history can therefore distinguish "the plan always required a
reviewer" from "a reviewer was added after the primary failed".

## Status vs outcome

Lifecycle status and execution result are separate axes and must never be
collapsed into one field.

| `status` (lifecycle) | Meaning |
|---|---|
| `UNASSIGNED` | Declared by the plan; no decision has been made yet |
| `ASSIGNED` | A decision selected a target; execution has not started |
| `RUNNING` | Execution in progress |
| `COMPLETED` | Execution finished — consult `outcome` for the result |
| `CANCELLED` | Stopped before completion |

| `outcome` (result) | Meaning |
|---|---|
| `NONE` | No result yet; the only valid outcome before `COMPLETED` |
| `PASS` | Role completed successfully |
| `FAIL` | Role completed and rejected the work |
| `ERROR` | Role could not produce a verdict (transport, crash, timeout) |

`COMPLETED` + `FAIL` is a *successful review that failed the work*. It is not an
error, and must not render as one.

## Wire shape

`GET /v1/tasks/{id}/detail` gains one optional key, `routing_plan`. The existing
`routing` key is unchanged and remains the legacy single-decision projection.

```jsonc
"routing_plan": {
  "plan_id": "rp-7f21",
  "revision": 2,
  "task_id": "PT-0042",
  "created_at": "2026-09-03T11:41:02Z",
  "policy_id": "BALANCED",
  "resolved_policy": "BALANCED",
  "policy_resolution_source": "PROJECT_OVERRIDE",
  "superseded_by_plan_id": null,
  "declared_roles": ["PRIMARY", "REVIEWER"],
  "roles": [
    {
      "role": "PRIMARY",
      "status": "COMPLETED",
      "outcome": "PASS",
      "active_decision_id": "rd-8841",
      "decisions": [
        {
          "decision_id": "rd-8841",
          "role": "PRIMARY",
          "created_at": "2026-09-03T11:41:02Z",
          "mode": "ACTIVE",
          "selected_execution_target_id": "minimax-cn-coding-plan/MiniMax-M2.7",
          "provider_id": "minimax-cn-coding-plan",
          "model_display_name": "MiniMax-M2.7",
          "why_selected": "Quality tier satisfied; binding window above reserve.",
          "supersedes_decision_id": null,
          "reroute_reason": null,
          "quota_snapshot_id": "qs-2210",
          "candidates": [
            {
              "execution_target_id": "minimax-cn-coding-plan/MiniMax-M2.7",
              "provider_id": "minimax-cn-coding-plan",
              "model_display_name": "MiniMax-M2.7",
              "model_sku_id": "MiniMax-M2.7",
              "score": 0.87,
              "eligible": true,
              "admitted": true,
              "selected": true,
              "why_not_selected": null,
              "quota_snapshot_id": "qs-2210"
            }
          ]
        }
      ]
    },
    {
      "role": "REVIEWER",
      "status": "UNASSIGNED",
      "outcome": "NONE",
      "active_decision_id": null,
      "decisions": []
    }
  ]
}
```

### Field notes

- `roles` **must** contain exactly one entry per entry in `declared_roles`, in
  the same order. A declared role with no work yet carries an empty `decisions`
  array — that is how "pending" is expressed.
- `decisions` is ordered oldest-first. A reroute appends a new decision whose
  `supersedes_decision_id` names the one it replaced and whose `reroute_reason`
  carries a sanitized code.
- `active_decision_id` names the decision currently in force. It is null when a
  role is `UNASSIGNED`, and it must match one of the `decisions` entries
  otherwise.
- `why_selected` and `candidates` are **per decision**, not per plan: the reason
  a model was chosen to write code is a different fact from the reason a model
  was chosen to review it.
- All identifiers, provider ids, execution target ids, mode values, status and
  outcome enums are machine values and are never localized.

## Backward compatibility

Both directions are required, because an app build and a daemon build are
independently deployable.

**Old daemon, new app.** `routing_plan` is absent. The client synthesizes a
single-role plan from the existing `routing` decision:

- `declared_roles` = `["PRIMARY"]`
- the legacy decision becomes the PRIMARY role's only decision
- the projection is flagged `isLegacySynthesized`, so the UI can avoid claiming
  plan/revision facts it does not have

This is the only case in which a client may construct a role, and it constructs
exactly the one role that the legacy shape already implies.

**New daemon, old app.** The extra key is ignored by clients that do not know
it; `routing` continues to carry the primary decision.

**Unknown enum values.** A client must preserve and display an unrecognised
`role`, `status`, or `outcome` verbatim rather than dropping it. An unknown
status renders as unknown — never as healthy, and never as complete.

## UI projection

Views must not reconstruct routing semantics from decision bookkeeping. The
client exposes a normalized read-only projection, `TaskRoutingSummary`, built
once in `PAOControlKit`:

```text
TaskRoutingSummary
  planId, planRevision, isLegacySynthesized
  policyId, resolvedPolicy, policyResolutionSource
  roles: [RoleSummary]        // exactly the declared roles, canonical order
    role, status, outcome
    activeDecision: RoutingDecisionRecord?
    history: [RoutingDecisionRecord]   // newest first
    isPending                          // declared, no decision yet
    rerouteCount
```

Rules the projection guarantees, so no view has to:

1. A role absent from `declared_roles` produces **no** `RoleSummary`. The UI
   therefore cannot render an empty Reviewer slot for a plan that never wanted
   one.
2. A declared role with no decisions produces a `RoleSummary` with
   `isPending == true`. That is an explicit pending state, not an absence.
3. `history` is newest-first so a view can show the current selection and the
   trail that led to it without re-sorting.
4. Status and outcome stay separate all the way into the view.

## Fixtures

`macos/PAOMenuBar/Tests/PAOControlKitTests/Fixtures/routing/` carries the frozen
wire examples. They are the executable half of this contract — a daemon
implementing roles must produce shapes that decode against them.

| Fixture | Covers |
|---|---|
| `legacy_single_worker.json` | Pre-contract daemon: `routing` only, no `routing_plan` |
| `plan_primary_only.json` | PRIMARY declared and completed |
| `plan_primary_reviewer.json` | PRIMARY + REVIEWER, both assigned |
| `plan_primary_reviewer_auditor.json` | All three roles |
| `plan_reviewer_declared_unassigned.json` | REVIEWER declared, no decision yet |
| `plan_reviewer_reroute.json` | REVIEWER with two decisions, second supersedes |
| `plan_completed_failure_outcome.json` | `COMPLETED` + `FAIL` (a real verdict) |

## Open items for the execution layer

These do not block the contract, but the daemon must answer them when roles are
implemented:

1. Which component writes `declared_roles` — routing policy, task risk class, or
   project configuration. The contract only requires that it is written when the
   plan is created and never mutated afterwards.
2. Whether a plan revision resets role state or carries completed roles forward.
   The contract supports either; the daemon must pick one and document it here.
