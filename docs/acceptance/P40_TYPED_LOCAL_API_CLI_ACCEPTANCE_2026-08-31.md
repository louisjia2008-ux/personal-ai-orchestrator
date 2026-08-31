# P4.0 Typed Local API + CLI Acceptance — 2026-08-31

Branch: `feat/p4-control-plane` (from merged `main` `c8b4c8107f6cd7744ba60364dc889475442e0c49`,
PR #21 merge commit). This record covers P4.0 only; P4.1+ was not started.

## Objective

Expose the authoritative orchestrator core through a typed local control-plane API and a thin
CLI without weakening Safety Kernel authority.

## Transport

- Primary local transport: Unix Domain Socket, permission-restricted to `0600`, stale-socket
  fail-closed handling (a live socket refuses double-bind; only provably stale sockets are
  replaced).
- Protocol: HTTP/1.1 over UDS with the same hardening family as the existing loopback routing
  API — JSON-only bodies (`415` otherwise), bounded request bodies with bounded drain (`413`),
  Host/Origin validation where headers are present (`403`), sanitized error codes, no raw
  exception text.
- API version: `/v1/...` path namespace with an explicit `/v1/health` version report.
- The established OpenCode adapter contract (`local_api.py`, loopback `/v1/opencode/*`) is
  unchanged; the daemon can serve both surfaces side by side via `--control-socket`.

## API surface

```text
GET  /v1/health
POST /v1/tasks                                  idempotent submit (unique request_id)
GET  /v1/tasks?limit=N
GET  /v1/tasks/{task_id}
GET  /v1/tasks/{task_id}/runs
POST /v1/tasks/{task_id}/cancel                 bounded cancel, idempotent for CANCELLED
GET  /v1/tasks/{task_id}/verification           immutable-journal-backed report
GET  /v1/tasks/{task_id}/routing                latest durable routing decision
GET  /v1/tasks/{task_id}/approvals              read-only
GET  /v1/runs/{run_id}
GET  /v1/providers                              sanitized provider health
GET  /v1/quota                                  sanitized quota health
GET  /v1/active-status                          read-only activation gate state
```

## CLI

`pao` console script (`python -m personal_ai_orchestrator.cli`), commands `submit`, `status`,
`list`, `runs`, `report`, `routing`, `cancel`, `approvals`, `providers`, `quota`,
`active-status`, plus `--json` raw rendering and `--socket` / `PAO_CONTROL_SOCKET` resolution.
Natural-language arguments are stored as task intent only; they never become shell commands.

## Authority boundary (tested)

- No endpoint accepts verification payloads; `POST /v1/tasks/{id}/verify` → `404` and task
  state is untouched.
- Verification reports render only from the immutable verification-evidence journal via
  host-recorded audit transitions; missing evidence fails visible
  (`VERIFIED_EVIDENCE_MISSING`), never fabricated.
- Quota/provider health is projected from whitelisted view models; account `credential_ref`
  and any token/key-like material cannot transit the API (negative test with a canary
  credential reference).
- `EXACT`/`ESTIMATED`/`UNKNOWN` confidence and `PROVIDER_EXACT`/`LOCALLY_MEASURED`
  measurement-source semantics are preserved verbatim from registry/availability records.
- Approvals are read-only; `POST /v1/approvals` → `404`. No client path can create Production
  ACTIVE owner approval.
- `active-status` reports `DISABLED_BY_DESIGN` with full blocking reasons; no client switch
  exists; switch leases remain reachable only through the existing activation authority.
- RUNNING-task cancellation fails closed with `409
  running_task_cancellation_requires_execution_supervisor` because only the host execution
  supervisor may cancel a supervised process.
- Submit/cancel reuse Safety Kernel transactions and idempotency (request-id uniqueness,
  re-cancel returns `cancelled_now=false` without a second state transition).

## Verification evidence (2026-08-31, target Mac)

```text
RUFF: PASS
CONTROL_PLANE_TESTS: 37 passed (UDS transport, typed validation, idempotency, cancel
  semantics, verification report, routing view, sanitization, authority negatives,
  unavailable daemon, CLI end-to-end, daemon wiring)
FULL_PYTEST: 217 passed (180 pre-existing + 37 new; no regressions)
GIT_DIFF_CHECK: PASS
OPENCODE_ADAPTER: unchanged files; base contract tests 10/10 PASS on merged main
REAL_DAEMON_SMOKE: daemon + --control-socket + pao submit/status/list/report/routing/
  providers/active-status/cancel all behaved as designed (socket mode 0600 verified)
MACOS_NOTE: AF_UNIX sun_path is limited to 104 bytes; tests and deployments must keep
  socket paths short
```

## Security

- No credential material is read, copied, printed or transmitted by the control plane.
- Provider health responses contain only whitelisted normalized fields.
- All errors are sanitized codes; internal failures map to `503 control_plane_unavailable`.
- Socket directory permissions are the deployer's responsibility; the socket itself is `0600`.

## Remaining P4 work (not started)

- P4.1 macOS menu bar control plane;
- P4.2 full dashboard + WidgetKit;
- P4.3 DeskPet integration and optional Telegram client;
- approval mutation surface (owner-gated) and richer daemon supervision integration
  (RUNNING-task cancellation through the execution supervisor) are deferred by design.

## Gate status

```text
PRODUCTION_ACTIVE: DISABLED_BY_DESIGN
OWNER_APPROVAL_FOR_ACTIVE: ABSENT
PROVIDER_EXACT_RESET_CYCLES: 0 (unchanged)
P0/P1 AUTHORITY: UNCHANGED AND AUTHORITATIVE
```
