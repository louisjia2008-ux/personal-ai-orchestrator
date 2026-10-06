# Local-client v2: explicit manual execution

This additive owner-only contract is served at `POST /v2/local-client` on the
existing permission-restricted Unix socket. The JSON CLI command remains
`pao-client-bridge deskpet --installation-id <local-id>`; an explicit
`protocol_version` selects v2 and never falls back to a v1 mutation.
All legacy v1 endpoints, operations, Telegram approval behavior, and targetless
MANUAL tasks created by an AUTO veto retain their previous meaning.

This slice is an execution capability, not a guarantee of eligibility or success.
It does not enable Production ACTIVE, change owner settings, tick the scheduler,
resolve approvals, grant worker permissions, bypass quota admission, retry an
attempt, weaken writer ownership, or clear cleanup quarantine. Owner-initiated
execution remains separate from Production ACTIVE. Existing host gates run.

## Request envelope and exact shapes

Every request has `protocol_version: 2`, `operation`, and `request_id`.
Only the operation-specific fields below are accepted. Extra fields, including
explicit null placeholders, are rejected. IDs match
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. Identity values are 32 lowercase hexadecimal
characters. Versions are nonnegative integers; intent is 1–8192 characters.

- `CAPABILITIES`: no additional fields
- `CONTEXT`: no additional fields; same payload as CAPABILITIES
- `SUBMIT_V2`: `task_id`, `project_id`, `intent`, `execution_target_id`,
  `expected_store_id`, `expected_process_epoch`
- `START`: `task_id`, `task_state_version`, `execution_target_id`,
  `expected_store_id`, `expected_process_epoch`
- `DETAIL`: `task_id`
- `DISPATCH_STATUS`: the original START's `request_id`, `task_id`,
  `task_state_version`, `execution_target_id`, `expected_store_id`,
  `expected_process_epoch`
- `CANCEL`: `task_id`, `expected_store_id`, `expected_process_epoch`

Example explicit submission (identity placeholders must be replaced by the
values observed from CONTEXT):

```json
{
  "protocol_version": 2,
  "operation": "SUBMIT_V2",
  "request_id": "submit-example-1",
  "task_id": "task-example-1",
  "project_id": "project-example",
  "intent": "Fix the requested button behavior",
  "execution_target_id": "target-example",
  "expected_store_id": "11111111111111111111111111111111",
  "expected_process_epoch": "22222222222222222222222222222222"
}
```

Submission pins MANUAL and the explicit registered target. It does not start a
worker. Initial state is **SUBMITTED**, version **0**. START is valid from
SUBMITTED or READY, subject to existing host gates. The existing initiator
transitions SUBMITTED to READY when it reserves and validates a dispatch.

```json
{
  "protocol_version": 2,
  "operation": "START",
  "request_id": "start-example-1",
  "task_id": "task-example-1",
  "task_state_version": 0,
  "execution_target_id": "target-example",
  "expected_store_id": "11111111111111111111111111111111",
  "expected_process_epoch": "22222222222222222222222222222222"
}
```

The immutable START tuple must be persisted before sending and never regenerated
because a response is lost. Reconciliation changes only `operation` to
`DISPATCH_STATUS`. It never launches or retries a worker. A daemon-side duplicate
START retains the existing idempotent dispatch semantics; this is not a client
retry policy. A response's current `task.state_version` never replaces the
original dispatch's `task_state_version`.

## Response envelope

Every successful response has exactly:

```json
{
  "protocol_version": 2,
  "request_id": "read-example",
  "operation": "CONTEXT",
  "store_id": "11111111111111111111111111111111",
  "process_epoch": "22222222222222222222222222222222",
  "payload": {}
}
```

The store identity is random durable metadata, atomically initialized and stable
across connections/restarts. It conveys no additional authority. The process
epoch changes with each daemon process and is reused across request-local service
instances. Mutations compare both expected identities on the handling host before
reservation or effects. A stale store fails with `store_identity_mismatch`; a
stale mutation epoch fails with `process_epoch_mismatch` (HTTP 409).

DISPATCH_STATUS requires matching store and the exact original dispatch tuple;
an earlier epoch is accepted for read-only observation and the response reports
the current epoch. Earlier-epoch RESERVED/STARTED records have
`recovery_required: true`. An absent record returns `dispatch_not_found` (404).
Neither outcome authorizes resending START. Clients must verify response identity,
request, operation and task/dispatch identity before applying a view; this also
applies to DETAIL, whose request itself is read-only and unbound.

HTTP errors are sanitized `{"error":"code"}` with the HTTP status. The CLI
renders `{"error":"code","status":409}` for host errors and exits nonzero;
local schema/transport errors remain sanitized bridge errors. No free-form
exception or provider error text is part of the v2 response.

## Payload allowlists

CAPABILITIES and CONTEXT return:

```json
{
  "capabilities": [
    "manual-submit-v2", "owner-start-v2", "safe-detail-v2",
    "dispatch-status-v2", "manual-cancel-v2", "host-identity-v2"
  ],
  "owner_start": {
    "available": true,
    "owner_enabled": true,
    "executor_available": true,
    "blocking_reasons": []
  },
  "projects": [{"project_id":"project-example","storage_availability":"ONLINE"}],
  "execution_targets": [{
    "execution_target_id":"target-example",
    "runtime_available":true,
    "launch_verified":true
  }],
  "truncated": {"projects":false,"execution_targets":false}
}
```

Catalogs are capped at 100 each. These reads do not discover providers, refresh
projects, or probe project filesystems. Project availability is the last stored
observation, checked afresh by submission/dispatch. Availability values are
ONLINE, OFFLINE, MISSING, INVALID_REPOSITORY. Owner gate reasons are
`owner_initiated_execution_disabled` and `dispatch_executor_unavailable`.
An available owner gate and a listed target are not proof that execution will
pass launch verification, provider connection, quota, permissions or writer gates.

SUBMIT_V2 returns `{"task": SafeTask}`. CANCEL returns
`{"task": SafeTask,"cancelled_now":true}`. CANCEL retains task-level cancellation
and supervisor semantics; it is not an attempt/approval mutation.
V2 START and CANCEL require MANUAL plus a nonempty bound target. Independently,
all host launch boundaries enforce equality whenever a task explicitly binds a
manual target. Legacy targetless-MANUAL dispatch remains valid.

SafeTask contains only `task_id`, `request_id` (original submit request),
`project_id` (nullable), `state`, `state_version`, `scheduling_policy` (nullable),
`manual_execution_target_id` (nullable for observational legacy views),
`created_at`, `updated_at`. It contains no intent, filesystem path, raw result,
worker transcript, credential material or PID.

START and DISPATCH_STATUS return:

```json
{
  "dispatch_id":"owner-dispatch-start-example-1",
  "request_id":"start-example-1",
  "task_id":"task-example-1",
  "task_state_version":0,
  "execution_target_id":"target-example",
  "authority":"OWNER_INITIATED_EXECUTION",
  "status":"RESERVED",
  "accepted":true,
  "failure_code":null,
  "task":{},
  "recovery_required":false
}
```

`task` is SafeTask, with current state/version; dispatch `task_state_version` is
immutable original admission data. Status is RESERVED, STARTED, BLOCKED,
CANCELLED or FINISHED. `accepted` reflects absence of a failure code, not worker
completion or answer quality. Failure codes are bounded uppercase machine tokens;
free-form failure reasons are omitted.

DETAIL is a dedicated bounded SQL read snapshot, not a stripped raw task detail:

```json
{
  "task":{},
  "runs":[{"run_id":"run-example","status":"FINISHED","started_at":"2026-10-06T00:00:00+00:00","finished_at":"2026-10-06T00:00:01+00:00"}],
  "approvals":[{"approval_id":"approval-example","kind":"HIGH_RISK_EXECUTION","status":"PENDING","created_at":"2026-10-06T00:00:00+00:00","resolved_at":null}],
  "verification":{"status":"NOT_OBSERVED"},
  "answer_completeness":"UNAVAILABLE",
  "truncated":{"runs":false,"approvals":false}
}
```

Runs and approvals are newest-first and capped at 50 each. Machine tokens are
uppercase and at most 80 characters; timestamps are bounded ISO-shaped strings
at most 40 characters. `finished_at` and `resolved_at` may be null. Verification
status is NOT_OBSERVED, IN_PROGRESS, or VERIFIED_EVIDENCE_UNAVAILABLE, derived
conservatively from task state. No verification evidence/result files are read
or promoted to success. Answer completeness is always UNAVAILABLE; a worker exit,
FINISHED dispatch, or VERIFIED task is not a final answer.

## Validation boundary

The test suite uses file-backed stores, fake executors/workers and a real HTTP
handler exercised over an in-memory accepted connection. Real Unix-socket,
macOS/Electron runtime, provider runtime, signing and release checks are separate
acceptance gates and must not be implied by these tests.
