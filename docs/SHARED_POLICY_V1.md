# Shared policy and resource contract v1

PAO remains independently installed and authoritative in this repository. Native
PAO and DeskPet are clients of the same owner-only Unix socket; neither owns a
copy of scheduling, inheritance, quota admission or execution authority. Closing
DeskPet does not stop PAO. This change does not introduce a remote transport.

## Compatibility and permissions

The v2 protocol number and all existing manual task, result, continuation,
recovery and reconciliation operations remain unchanged. Discover capabilities
before showing new controls:

- `scheduling-policy-read-v1`: `POLICY_GET`
- `scheduling-policy-write-v1`: `GLOBAL_POLICY_SET`, `PROJECT_POLICY_SET`
- `resource-snapshot-v1`: `RESOURCES`
- `recovery-candidate-v1`: additive `DETAIL.payload.recovery_candidate`

A client connected to an older daemon retains its supported manual operations
and disables unsupported settings. Never fall back to local task execution or
write PAO's configuration/database directly. Offline snapshots are observations,
not authority; label them stale and disable writes. Reconnection does not replay
settings, submit, START or recovery requests.

Bridge settings edits change only global/project routing policy. They never
accept scheduling mode, project auto/unattended settings, owner execution,
credentials, approvals, activation, refresh, or start fields. They neither tick
the scheduler nor launch a worker. Previously and separately authorized daemon
scheduling continues according to its existing gates. `SUBMIT_V2` still pins an
explicit target and `MANUAL`; a policy edit never rewrites existing tasks.

Native mode controls retain their existing explicit host validation and ACTIVE
authority gate. They now use the same global generation preconditions. Project
supervised-auto settings are a separate pre-existing API, not a bridge capability.

## Writes require an observed generation

Both native HTTP policy PUTs and bridge policy writes require:

- `expected_revision`: the opaque 32-lowercase-hex revision from the edited view
- `expected_store_id`: the observed durable PAO store identity
- `expected_process_epoch`: the observed current daemon identity

Do not derive revisions from timestamps, UI values or result digests. Every
accepted write, including a same-value write, rotates the resource revision.
Global edits compare and persist under the same thread/interprocess file lock;
project edits compare, update and audit in one `BEGIN IMMEDIATE` transaction.
Failed persistence/audit does not publish the proposed revision or values.
Independent resources have independent revisions: changing one project's policy
does not invalidate another project's editor. Long-lived routing/settings readers
reload the same committed file generation, so a separate routing service created
before an edit observes the updated global policy without a daemon restart.

HTTP missing preconditions returns **428 `policy_revision_required`**. Stale
revisions return **409 `policy_revision_conflict`**. Wrong store or epoch returns
**409 `store_identity_mismatch` / `process_epoch_mismatch`**. Invalid v2 shapes
(including omitted required fields) return 400 `invalid_json_schema`. Invalid
policy names retain the existing 400 validation errors.

Old clients can still read and use supported task operations, but old policy
PUTs are deliberately rejected rather than silently clobber another editor.
Upgrade the client, load a fresh view and review before resubmitting. Preserve
an unsaved draft on a conflict; do not automatically replace its revision and
retry. A daemon restart requires a fresh observation even if the persisted
policy revision is unchanged.

Policy request IDs correlate calls; they are **not durable idempotency receipts**.
After a lost response, fetch current state read-only and present it alongside the
draft. An equal current value is not proof of which client wrote it. Do not queue
or automatically replay a mutation. A second attempt with the original revision
will conflict if the first committed, including when the value was unchanged.

### Persistence migration

The existing `scheduling-settings-v1` JSON remains readable. Files without a
revision receive a stable content-derived read token without rewriting the owner
settings file. The next accepted edit atomically writes a random revision with
policy and mode. Corrupt/unknown-schema files retain conservative BALANCED/MANUAL
fallback behavior. A sidecar lock is coordination state, not a settings source.

Existing SQLite projects gain `scheduling_revision`, initialized once with a
random token; new projects receive their token in the registration transaction.
Migration does not change policy, auto settings, tasks or owner permissions.
Global revisions survive restart once persisted. Store and process binding
protect against carrying a draft to another store or daemon. The design assumes
the normal single active PAO daemon; an older daemon that ignores the revision
contract must not be run concurrently against the same state directory.

## Native HTTP contract

`GET /v1/settings/scheduling` retains all fields and adds `revision`, `store_id`,
`process_epoch`. Existing global PUT fields `default_scheduling_policy` and
optional `mode` remain, with the three mandatory preconditions above.

`GET /v1/projects/{id}/scheduling` returns the `ProjectPolicy` shape below.
`PUT /v1/projects/{id}/scheduling` retains `scheduling_policy` and optional
`manual_execution_target_id`, adds the preconditions, and returns the existing
`ProjectView` plus `scheduling_revision`. A ProjectView alone does not bind the
process; use the new scheduling GET when beginning an edit. Python client methods
require the observed typed view; they never silently refresh stale preconditions.

## Bridge request shapes

All requests retain `protocol_version: 2`, `operation`, `request_id`. Apart from
these envelope fields, the exact allowed/required fields are:

| Operation | Fields |
| --- | --- |
| `POLICY_GET` | none |
| `RESOURCES` | none |
| `GLOBAL_POLICY_SET` | `default_scheduling_policy`, three preconditions |
| `PROJECT_POLICY_SET` | `project_id`, `scheduling_policy`, `manual_execution_target_id`, three preconditions |

For a project write, both policy and target fields are explicitly present and
nullable. Null policy plus null target restores inheritance. `MANUAL` requires an
explicit target; non-MANUAL policies reject a non-null target. Global selectable
policies do not include MANUAL. Clients may offer only the modes/values they can
represent faithfully; displaying an existing override does not require offering
an editor for it. The CLI preserves explicit nulls across its typed transport.

## Policy response payload

Both policy writes and `POLICY_GET` return the same payload in the existing v2
identity-bound response envelope:

```json
{
  "schema_version": 1,
  "global": {
    "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "store_id": "11111111111111111111111111111111",
    "process_epoch": "22222222222222222222222222222222",
    "default_scheduling_policy": "BALANCED",
    "selectable_policies": ["BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "BURN_DOWN"],
    "mode": "MANUAL",
    "selectable_modes": ["MANUAL", "SUPERVISED_AUTO", "ACTIVE"]
  },
  "projects": [{
    "project_id": "project-example",
    "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "scheduling_policy": null,
    "manual_execution_target_id": null,
    "effective_scheduling_policy": "BALANCED",
    "effective_manual_execution_target_id": null,
    "policy_source": "GLOBAL",
    "global_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "store_id": "11111111111111111111111111111111",
    "process_epoch": "22222222222222222222222222222222"
  }],
  "truncated": {"projects": false}
}
```

`ProjectPolicy.policy_source` is `GLOBAL` or `PROJECT`. All project resolutions in
a bundle use its captured global revision and a single database read snapshot.
Project policy null means inherit the current global policy; clearing an override
is not a promise to pin the global value the editor previously saw. Global changes
need not rotate a project's own revision; compare `global_revision` for displayed
inheritance freshness. Task-level overrides still take precedence through the
existing backend resolver and are not edited by this API.

Projects are sorted by ID, capped at 100, with an explicit truncation flag. The
safe bridge does not export names, filesystem paths, remote URLs or bookmarks.
Unexpected persisted policy/target/revision values fail closed rather than being
copied into a UI as free-form data.

## Read-only resource snapshot

`RESOURCES` wraps these cached native provider/quota projections:

```json
{
  "schema_version": 1,
  "observed_at": "2026-10-16T00:00:00+00:00",
  "providers": [{
    "provider_id": "provider-example",
    "connection_state": null,
    "auth_state": null,
    "runtime_state": null,
    "last_checked": null,
    "pool_kind": "windowed",
    "execution_targets": [{
      "execution_target_id": "target-example",
      "enabled": true,
      "runtime_available": true,
      "launch_authorized": true,
      "execution_verified_stale": false
    }],
    "quota": {
      "quota_state": "UNKNOWN",
      "confidence": "UNKNOWN",
      "observed_at": null,
      "collection_failure_streak": 0,
      "pools": [{
        "quota_pool_id": "pool-example",
        "state": "UNKNOWN",
        "confidence": "UNKNOWN",
        "windows": [{
          "window_id": "window-example",
          "window_kind": "ROLLING",
          "state": "UNKNOWN",
          "confidence": "UNKNOWN",
          "remaining_fraction": null,
          "reset_at": null
        }]
      }]
    }
  }],
  "truncated": {
    "providers": false, "execution_targets": false,
    "quota_pools": false, "quota_windows": false
  }
}
```

The response timestamp is projection time, not a fresh provider measurement.
Individual quota observations keep their own timestamps. Missing observations
remain UNKNOWN/null; they are never converted into available balance or zero.
Fractions are finite 0–1 or null. Pool kind is `windowed` or `unmetered`; an
unmetered service must not be rendered as a fabricated quota balance.

Each aggregate category is capped at 100 across the whole response. Identifiers,
machine status tokens and timestamps are bounded. No account identities,
credential references/values, provider URLs, raw failure text, worker output,
filesystem paths or project content are included. Reads perform no provider
request, quota refresh, connection discovery or permission change. Target
`launch_authorized` is diagnostic execution-verification/runtime eligibility;
it is not owner approval or proof that all task, quota and safety gates will pass.

## Recovery candidate observation

When `recovery-candidate-v1` is advertised, `DETAIL` includes nullable:

```json
{
  "task_id": "task-example",
  "task_state_version": 3,
  "prior_dispatch_id": "owner-dispatch-start-example"
}
```

The candidate requires a durable matching startup interruption, BLOCKED owner
MANUAL dispatch, current blocked task generation/target, and no prior recovery
receipt. BLOCKED/FAILED alone never creates a candidate. It means the owner can
review a recovery request; it does not certify cleanup or grant launch authority.
The existing RECOVER transaction rechecks all cleanup receipts, attempts, paths,
writer claims, leases, current generation and authority. A successful recovery
only prepares READY. A separately reviewed new START is still required.

## Cross-client acceptance

Use `tests/fixtures/shared_policy_v1.json` as a sanitized generated wire sample,
not as a live configuration. Automated coverage must include native/bridge CAS
interoperability, conflicting writers, restart/store mismatch, rollback, old
clients, explicit null inheritance, bounded safe observations, and no execution
or owner-setting side effects. Real macOS native/DeskPet operation, narrow-window
visual QA, Unix-socket transport, signing and packaging remain separate gates.
