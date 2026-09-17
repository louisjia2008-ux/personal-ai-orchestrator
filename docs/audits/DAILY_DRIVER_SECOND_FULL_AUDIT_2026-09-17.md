# Daily Driver Second Full Repository Audit — 2026-09-17

Status: **AUDIT COMPLETE / REMEDIATION PARTIAL / MERGE GATE NOT SATISFIED**

Repository: `louisjia2008-ux/personal-ai-orchestrator`

Audit baseline:
- `main@d42878b668e5ee572d57df1460f51338046d9d6c`
- PR #55: `design/daily-driver-ui-redesign`
- pre-audit PR head: `1323a7c5e7044289c32271dac73ed2ccb5885e79`

This audit was performed after the first Daily Driver closeout pass. It is a repository-wide static and evidence review, not a substitute for exact-head execution on a real macOS host.

## Scope reviewed

The second pass rechecked:

1. repository / PR / issue state and recent merge history;
2. product daemon packaging and the actual macOS helper entry path;
3. MANUAL and SUPERVISED_AUTO authority boundaries;
4. owner-execution, project opt-in, launchability and verification gates;
5. Pi runtime/provider projection and MiniMax / GLM quota surfaces;
6. quota availability, exhaustion, cooldown and recovery semantics;
7. Daily Driver Home readiness versus actual daemon admission;
8. task / resource / menu-bar owner action surfaces;
9. daemon lifecycle ownership and packaged PyInstaller shutdown assumptions;
10. CI workflow coverage and current GitHub Actions execution evidence;
11. existing acceptance artifacts and stale tracking issues.

## Executive result

The architecture remains substantially safer and more complete than the product state suggested before PR #55: Safety Kernel authority, deterministic verification, worktree isolation, owner-execution gating, Pi product wiring, quota-aware routing, packaged daemon startup, and bounded SHADOW evidence are already present.

The audit did **not** find a reason to redesign the scheduler, Safety Kernel, verifier, provider model, or runtime architecture again.

It did find several Daily Driver closeout defects / gaps that matter before this should be treated as a reliable everyday product.

## Findings

### DD-AUDIT-01 — Home could claim SUPERVISED_AUTO was ready while the host AUTO tick would fail closed

Severity: **P1 product-truth defect**

Before this audit, `DailyDriverReadiness` treated missing quota observation as non-blocking in all modes. That is reasonable for MANUAL owner dispatch because the dispatch boundary can perform a read-only quota refresh, but it is not true for SUPERVISED_AUTO.

The host-owned AUTO tick checks quota availability before planning side effects and accepts only its explicit AUTO-safe observed states. Therefore the previous Home projection could show `Ready to work` while the daemon would repeatedly skip the same task.

**Remediation applied on PR #55:**

- readiness is now mode-aware;
- SUPERVISED_AUTO fails closed unless a launchable target already has daemon-compatible observed quota truth;
- MANUAL may remain ready with missing/UNKNOWN quota because dispatch-time collection still exists;
- MANUAL now also treats `UNCERTAIN_LOCKED` as an explicit capacity blocker;
- dedicated Swift regression coverage was added in `DailyDriverQuotaReadinessTests.swift`.

### DD-AUDIT-02 — `RECOVERED_OBSERVED` is not admitted by the current SUPERVISED_AUTO quota gate

Severity: **P1 backend semantic defect**

`QuotaAvailabilityState` contains `RECOVERED_OBSERVED`, and the P3.9.2 recovery-governor acceptance contract establishes that a strong positive observation after a proven exhaustion clears suppression and resumes ordinary routing.

The current SUPERVISED_AUTO gate admits only:

- `AVAILABLE_OBSERVED`
- `AVAILABLE_UNMETERED`

That means a windowed target can recover truthfully from exhaustion and still remain AUTO-ineligible.

**Action:** tracked explicitly as #58. The Swift readiness code intentionally does not invent support for `RECOVERED_OBSERVED` until the daemon admits it too.

### DD-AUDIT-03 — macOS CI built the package but did not execute the Swift test suite

Severity: **P1 verification gap**

PR #55 adds or changes important Swift contract tests covering launchability, quota readiness, SUPERVISED_AUTO controls, localization, Activity grouping and layout. The GitHub workflow previously ran only `swift build` for the macOS package.

**Remediation applied on PR #55:**

- macOS CI now runs both `swift build` and `swift test`;
- macOS job timeout was raised from 15 to 20 minutes to avoid turning the expanded test gate into an artificial timeout.

### DD-AUDIT-04 — GitHub Actions is currently failing before runner allocation

Severity: **merge-gate infrastructure blocker**

The exact PR-head workflow observed during this audit completed red, but all three jobs had no executed steps / no allocated runner. This is not evidence that Python, OpenCode, Swift build, or Swift tests failed.

It is also not a pass.

**Action:** tracked as #59. Cause remains unproven and must not be guessed. PR #55 stays Draft until an exact head obtains real runners or equivalent owner-Mac evidence is recorded.

### DD-AUDIT-05 — Pi-only execution auth still does not authorize quota telemetry

Severity: **known product/security gap**

Pi READY auth is enough to make a Pi target routing-connected, but quota collectors intentionally resolve credentials from separately approved quota credential sources. If the owner authenticated only inside Pi, execution can be ready while MiniMax / GLM quota remains UNKNOWN.

Implicitly scraping/copying Pi secrets would collapse separate authorities and is not an acceptable fix.

**Action:** remains tracked as #57 for explicit owner-mediated quota credential onboarding.

### DD-AUDIT-06 — execution-verification projection can remain visually current after the final launch-age gate expires

Severity: **known actionability mismatch; daemon remains fail-closed**

The client can see historical VERIFIED projection but does not receive the final canonical age-based launch authority bit / timestamp required to reproduce the host's stricter verification-age gate exactly.

The branch already fails closed on `execution_verified_stale`, but it does not fabricate a 30-day expiry in Swift.

**Action:** remains tracked as #56. This is an owner-facing actionability problem, not an authority bypass.

### DD-AUDIT-07 — macOS lifecycle still uses helper-path `pkill -f` as process ownership

Severity: **P2 lifecycle hardening / potentially P1 if reproduced**

The app lifecycle controller uses broad command-line matching against the helper path before start/restart. Packaged acceptance elsewhere in the repository already uses stricter exact process ownership because PyInstaller has a parent/child topology.

No unrelated-process kill was reproduced in this static audit, so this was not rewritten inside the already-large Daily Driver PR.

**Action:** tracked as #60 with exact PID/process-identity acceptance requirements.

### DD-AUDIT-08 — PI-5B3G campaign issue remained open after canonical mainline closure

Severity: **tracking drift**

Issue #52 still appeared open even though the canonical mainline closure artifact records Campaign E terminal/exhausted, three completed observations, bounded parent/child counts, no retry/fallback/grandchild, SHADOW-only conclusion, packaged lifecycle validation, and exact closure evidence.

**Remediation applied:** #52 was closed as `completed` with the canonical closure commits recorded in the issue conversation.

## Product-path verification from static wiring

The packaged macOS app does use the product daemon path under review:

- `scripts/pao_daemon_entry.py` enters `personal_ai_orchestrator.product_daemon.main`;
- the bundle build packages that entry into the app helper;
- the Swift lifecycle configuration resolves the bundled `pao-daemon` helper;
- product daemon argv explicitly enables the bounded SUPERVISED_AUTO supervisor tick while generic `--control-only` remains inert by default;
- fresh-install scheduling remains MANUAL and Owner-Initiated Execution remains OFF, so registering the supervisor step does not itself authorize work.

This means the earlier product SUPERVISED_AUTO wiring fix is on the real application route rather than dead test-only code.

## Changes made during this audit

Code / test / CI changes on PR #55:

- `97f01bed` — mode-aware Daily Driver quota readiness;
- `ec5395e9` / `d9e5453b` — new and hardened quota-readiness Swift regressions;
- `a9cfa73e` — macOS CI now executes `swift test`.

Repository tracking changes:

- #58 — recovered observed quota is not AUTO-admissible;
- #59 — GitHub Actions runner allocation failure;
- #60 — exact daemon process ownership hardening;
- #52 — closed as completed from canonical PI-5B3G closure evidence.

## What was deliberately not changed

This audit did **not**:

- enable Production ACTIVE;
- weaken any quota / verification / Safety Kernel gate;
- add a new provider or harness;
- read, copy, log, or persist Pi credentials;
- fabricate quota certainty;
- reopen or extend the completed Campaign E;
- rewrite daemon lifecycle ownership without a focused implementation/test contract;
- merge or mark PR #55 Ready for Review without exact-head execution evidence.

## Remaining release gates

PR #55 should remain Draft until all release-critical evidence below is satisfied:

1. restore a real CI runner path (#59) or record equivalent exact-head owner-Mac validation;
2. run Python lint/full tests, OpenCode typecheck/contracts, Swift build **and Swift tests** on the same exact PR head;
3. resolve the `RECOVERED_OBSERVED` SUPERVISED_AUTO semantic mismatch (#58) before calling AUTO recovery production-ready;
4. perform real MiniMax and GLM Daily Driver smoke on the exact accepted head;
5. exercise MANUAL, SUPERVISED_AUTO, ACK, VETO, Dispatch Now, emergency fallback and deterministic verification;
6. restart the packaged daemon and prove no orphan process, stale socket, writer leak, durable RUNNING row or main-repo mutation;
7. visually accept Home / Tasks / Resources / Activity / Settings / menu bar at normal and minimum widths, including zh-Hans;
8. decide the release disposition of #56 and #57 explicitly rather than silently accepting them.

## Final audit status

`DAILY_DRIVER_SECOND_FULL_AUDIT_COMPLETE_WITH_OPEN_RELEASE_GATES`

The next engineering work should be closeout of the named gates, not another architecture expansion.