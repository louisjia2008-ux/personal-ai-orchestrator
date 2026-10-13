# Reliability implementation: offline validation, 2026-10-13

Base: `919e2196aa21b60bad6614aae3d92447dd342d6f` (merged #69).
This is an unpublished local patch, not a release or a claim of native acceptance.

## Implemented scope

1. Worker failure diagnostics distinguish explicit quota exhaustion, transient
   rate limiting, server overload, context restriction, authentication/access,
   and unknown failure. Structured error-channel fields take precedence where
   consistent. Only explicit exhaustion creates new exhaustion evidence.
   Failed run diagnostics contain allowlisted messages and bounded metadata,
   rather than provider transcripts. No automatic provider retry was added.
2. Startup closes stale owner dispatch records truthfully and preserves exact
   interruption generations, attempt fences and cleanup quarantine. Explicit
   MANUAL recovery requires matching immutable cleanup receipts, no retained
   writer, no active execution or switch lease, and unchanged target binding.
   RECOVER only returns the task to READY; a separate START is necessary.
   RECOVERY_STATUS resolves lost responses read-only using the original tuple.
3. DETAIL delivers a versioned host-verification report with task/run/base-commit
   and evidence binding. The accepted evidence bytes are digest-attested in the
   same durable verification transition. Missing, truncated, failed and complete
   report delivery remain distinct. CONTINUATION exports a scrubbed, manual-only
   summary using PAO-owned data. It does not read external or hidden sessions.
4. Scheduling settings validate the whole request, including ACTIVE authority,
   before one atomic policy/mode persistence operation. Rejected requests and
   failed writes leave both live and durable values unchanged.

See the dedicated failure, recovery and local-client contracts for exact bounds.

## Observed checks

- Python 3.12.14, pytest 8.4.2, Ruff 0.16.9, Node 24.19.0 on Linux.
- Complete baseline run: 1,532 passed, 5 skipped, 21 failed, 56 errors.
  The 77 failing test identities require Unix sockets that this runtime denies,
  including the daemon/broker cases that depend on those sockets.
- Integrated full run before final test-fixture hardening: 1,770 passed,
  5 skipped, 24 failed, 57 errors. All 77 baseline socket-blocked identities
  remained. Three additional timing-sensitive fixture identities were examined:
  two observed a newly created PID file before its contents were written, and
  one TERM-handler test could start its 0.2-second timer before handler setup.
- Full supported suite, deselecting only the exact 77 baseline socket-blocked
  identities: **1,775 passed, 5 skipped, 77 deselected**. No production code was
  changed after this run. The final test-only readiness hardening was followed
  by **91 passing process-cleanup and worker-lifecycle tests**. It retains the
  timeout, cleanup, process-identity, pipe-drain and failure-state assertions.
- The TERM readiness race was independently reproduced with delayed interpreter
  startup. All four delayed variants passed after readiness synchronization.
  The original temporary failure evidence was unavailable, so its exact timing
  cannot be established retrospectively.
- Final recovery HTTP-handler/CLI tests: 20 passed; result/v2/recovery client
  regression set before two added HTTP cases: 131 passed.
- Ruff check, Ruff format check (220 Python files), and diff hygiene passed.
- OpenCode adapter typecheck and 17 contract tests passed; dsh-plugin 9 passed;
  pi-dsh 7 passed. Existing dependencies were reused with the identical lockfile;
  this was not a fresh dependency installation.

The 5 skips are one optional installed Pi 0.85.1 SDK activation test and four
OpenCode CLI discovery tests; those tools are not installed in this environment.
The supported-suite result is not an unrestricted full-suite or CI pass.

## Remaining gates and deliberate limits

- No Swift toolchain/macOS runtime is available here. No native UI button,
  target-Mac behavior, app signing, packaging or Swift acceptance is claimed.
  New recovery/result surfaces are backend API/CLI contracts.
- No live provider, paid call, external session transfer, push, PR, merge,
  deployment or external issue was performed.
- COMPLETE describes delivery of the configured host-verification report. It
  does not attest all natural-language goals, unconfigured tests or release
  work. `result_commit` remains null because PAO does not record one.
- Older VERIFIED records without a digest attestation remain MISSING in the new
  result contract; legacy REPORT is unchanged. No successful history is forged.
- Legacy reservations, unknown cleanup, historical interrupted tasks lacking a
  provable generation, residual writers and non-MANUAL tasks remain blocked.
  Cleanup confirms its existing process-group scope, not absence of prior side
  effects or arbitrary descendants outside that scope.
- Settings-file atomicity does not make subsequent AUTO-task aborts part of the
  same database transaction; the existing fail-closed abort/backstop remains.
- Static independent code review found no remaining blocking issue in the
  reviewed recovery/settings surfaces after fixes. It is not maintainer approval.
  Issue #70 tracks recovery; issue-first scope and human maintainer review for
  new result-attestation/settings safety changes remain required before opening
  or publishing the affected changes.
