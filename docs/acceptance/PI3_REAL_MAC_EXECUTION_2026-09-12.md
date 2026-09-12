# PI-3 real Mac execution acceptance — 2026-09-12

FINAL_STATUS: PI_3_REAL_MAC_EXECUTION_ACCEPTANCE_BLOCKED_PROVIDER_ERROR

| Field | Evidence |
| --- | --- |
| BASELINE_HEAD | `ee5b260d7890c9176c7c4ddd98671e468dc5cadf` (freshly fetched remote) |
| FINAL_HEAD | The commit containing this record; resolve with `git log -1 --format=%H -- docs/acceptance/PI3_REAL_MAC_EXECUTION_2026-09-12.md` |
| BRANCH / PR | `feat/pi-product-wiring-03` / #31 DRAFT; no merge |
| PI_BINARY | `/Users/louisjia/.local/state/fnm_multishells/18191_1789226614404/bin/pi` |
| PI_VERSION | `0.85.1` |
| MINIMAX_CN_AUTH_STATUS | READY (`pi auth check --provider minimax-cn --json --no-refresh`) |
| PAO_PROVIDER_ID | `minimax-cn-coding-plan` |
| PI_RUNTIME_PROVIDER_ID | `minimax-cn` |
| MODEL_SKU | `minimax-cn-coding-plan/MiniMax-M3` |
| PI_EXECUTION_TARGET | `pi-minimax-cn-coding-plan-MiniMax-M3` |
| PI_MODEL_REF | `minimax-cn/MiniMax-M3` |
| DISCOVERY_STATUS | Real Pi catalog includes MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed; mapping test passes; discovery leaves execution_verified=False |
| EXACT_TARGET_PROBE_STATUS | UNKNOWN / PI_PROBE_PROVIDER_ERROR |
| PRODUCT_DISPATCH_STATUS | NOT_RUN: exact-target probe did not verify; no second call |
| ACTUAL_RUNTIME | Real Pi process used for probe; product dispatch not run |
| ACTUAL_PROVIDER / ACTUAL_MODEL | Requested minimax-cn / MiniMax-M3; successful final identity NOT_ESTABLISHED |
| DETERMINISTIC_VERIFIER | NOT_RUN (product dispatch gated) |
| EXECUTION_EVIDENCE_STATUS | Durable exact-target UNKNOWN; no VERIFIED evidence |
| ZAI_STATUS | DEFERRED_QUOTA_EXHAUSTED; zero ZAI calls |
| MODEL_CALL_COUNT | 1 real Pi probe invocation out of cap 2; no retry |
| PYTHON_TARGETED_TEST_STATUS | PASS: 36 tests |
| PYTHON_TEST_STATUS | 1036 passed, 1 failed; full suite run once (123.93s) |
| RUFF_STATUS | PASS: full repository |
| SWIFT_BUILD_STATUS | PASS: swift build, once |
| SWIFT_TEST_STATUS | PASS: 441 tests, once; prior localization assertion passed |
| OPENCODE_REGRESSION_STATUS | PASS: TypeScript 5.9.3 noEmit; 17 Node decision contracts |
| CREDENTIAL_VALUES_READ | NO (agent/PAO inspection; Pi manages its own authentication internally) |
| CREDENTIAL_VALUES_EXPOSED | NO |

[Exact-target probe journal](pi3-2026-09-12/minimax-probe.json) records the actual result at 2026-09-12T15:26:45.623894Z. The existing probe classifies a final provider error but does not retain the upstream error body, so a more specific provider cause is unknown. No quota recovery polling or additional inference was attempted. The second call and real product acceptance remain unperformed.

Only the MiniMax China discovery pair and its PI-specific regression test changed. Existing commercial identity and runtime alias are reused. Safety Kernel, scheduling, quota admission, verifier, worktree/single-writer semantics, and OpenCode runtime behavior are unchanged. No Pi credential bridge was created; no live PAO quota observation was needed after the probe failed. UNKNOWN was preserved.

The initial catalog query required Pi lock-file permissions and succeeded when allowed. The first TypeScript command could not resolve local dependencies and encountered sandbox network denial. The typecheck then passed using the existing local dependency installation: every shared lock entry matches, extra lock entries are optional platform packages, and TypeScript/plugin versions match. Node contracts were not repeated.

No completion claim is made. Investigate the MiniMax provider error without inference first; a new probe requires a separately authorized follow-up. Product dispatch remains gated on exact-target VERIFIED evidence.

Python failure: `tests/test_daemon_tick_integration.py::test_daemon_tick_advances_last_tick_at_between_health_polls` raised `ControlPlaneUnavailable` from `ConnectionRefusedError` on its first health poll. The unchanged test waits for socket-file existence and is already marked flaky for daemon startup races. This is consistent with that documented race, but pre-existence was not independently proven with a baseline rerun. No unrelated fix or full-suite rerun was made; this local regression remains non-green. Diff hygiene passed after removing a trailing blank line from the new test.


## Post-quota-reset attempt — 2026-09-13 Asia/Shanghai

CURRENT_FINAL_STATUS: PI_3_REAL_MAC_EXECUTION_ACCEPTANCE_BLOCKED_VERIFIER

The 2026-09-12 section above is preserved as historical evidence. BASELINE_HEAD for this continuation is `116560cab75b1011217054033d99c5fb16926bd0`; its three GitHub CI checks were confirmed green. FINAL_HEAD is the documentation/evidence commit containing this appended section (resolve with the command above).

| Field | New evidence |
| --- | --- |
| PREVIOUS_PROBE | UNKNOWN / PI_PROBE_PROVIDER_ERROR; preserved unchanged |
| PREVIOUS_CONTEXT | MiniMax Token Plan exhausted, subsequently confirmed by owner |
| OWNER_CONFIRMED_QUOTA_RESET | YES; owner report, independently separate from quota collection |
| MINIMAX_CN_AUTH / CATALOG | READY / MiniMax-M3 present; Pi 0.85.1 |
| NEW_EXACT_TARGET_PROBE | PASS / VERIFIED / REAL_PI_WORKER_PROBE_SUCCEEDED |
| NEW_EVIDENCE_ID | exec-verify-036bf9e0fd346a32a23cf0df |
| EXACT_TARGET_VERIFIED | YES; exact-target lookup returned new probe evidence with no stale fallback before dispatch |
| PRODUCT_DISPATCH | Real Control API dispatch executed; FINISHED / TASK_BLOCKED |
| ACTUAL_RUNTIME | Registry runtime pi; worker protocol pi-json |
| ACTUAL_PROVIDER / ACTUAL_MODEL | minimax-cn / MiniMax-M3 |
| QUOTA_ADMISSION | AVAILABLE_OBSERVED / ESTIMATED; collected=true via unchanged default PAO collector |
| DETERMINISTIC_VERIFIER | pi3-minimax-host-file-check; diff check exit 0, exact-content exit 1 |
| FINAL_TASK_STATE | BLOCKED; never VERIFIED |
| WRITER_RELEASED | YES |
| OPENCODE_FALLBACK | NO; one run, Pi argv and protocol evidence |
| REAL_MODEL_CALL_COUNT_THIS_ATTEMPT | 2 authorized Pi invocations: one probe plus one product dispatch; no retry. The dispatch itself contains two provider response turns around its single write tool call. |
| SOURCE_CODE_CHANGED | NO |
| TESTS_RUN | Acceptance sanity checks and git diff --check only; no Python/Swift/OpenCode suite rerun |
| CREDENTIAL_VALUES_READ / EXPOSED | NO / NO by agent inspection; existing PAO collector consumes its configured environment credential internally, with no Pi credential copy |
| ZAI_GL5_3 | DEFERRED_QUOTA_EXHAUSTED; no ZAI invocation |

The real `ControlPlaneServer` and `ControlPlaneClient` used a disposable UDS endpoint with the unchanged daemon `build_control_service` factory. API project registration, task submission, owner-execution enablement and one dispatch request led to Safety Kernel reservation, worktree creation, writer acquisition, real quota admission, Pi execution, host verification, BLOCKED transition and writer release. The fixture main repository stayed unchanged. Pi-only metadata discovery populated the standard persisted runtime registry; discovery still set execution_verified=False. No product policy or quota gate was patched.

The requested file was `PI3_MINIMAX_PRODUCT_OK` followed by LF. The worker wrote only `PI3_MINIMAX_PRODUCT_OK` (21 bytes, no LF), despite normal process exit 0, valid JSON lifecycle, final stop, and zero extension errors. The original temporary verifier command also had an escaping defect: its bytes literal expected a literal backslash and n instead of LF. That verifier failure is preserved, not presented as a correct initial LF check. A separate non-billable check using `b'PI3_MINIMAX_PRODUCT_OK' + bytes([10])` also exited 1. Neither the artifact nor task state was changed, and no worker was retried. Future acceptance must fix the temporary profile before execution; completion is not claimed.

The product execution journal additionally records `exec-verify-753fb9b993b43a99588cf2c0` as VERIFIED / REAL_WORKER_DISPATCH_SUCCEEDED: this proves runtime execution, **not task correctness**. After dispatch, latest_verified_for_target returns this newer exact-target evidence with no stale fallback. Only the deterministic verifier controls task completion; task remains BLOCKED.

New sanitized artifacts: [probe](pi3-2026-09-13/minimax-probe.json), [runtime execution](pi3-2026-09-13/product-execution.json), [original verifier result](pi3-2026-09-13/product-verification.json), [product audit and corrected offline check](pi3-2026-09-13/product-summary.json). Raw model output and auth contents are excluded. The original durable UNKNOWN remains in both the local journal and the prior committed artifact. No additional inference is authorized within this attempt; PR #31 stays DRAFT and unmerged.
