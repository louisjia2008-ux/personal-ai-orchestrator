# T027 spawn repair verification

Date: 2026-09-14
Baseline HEAD: `ec2f3bcda281fed483fd70ea44a97bda93f2dd18`
Spawn repair commit: `57deff6`
Branch: `fix/pao-daemon-graceful-sigterm`
PR: #54 (Draft; unmerged)

## Local results

- precise root cause: `WORKER_POST_CREATE_ACCOUNTING_ROLE_MISCLASSIFIED`;
- spawn diagnostics: `pao-spawn-diagnostics-v1`;
- focused spawn/role/dispatch checks after final PATH-resolution adjustment:
  68 passed, 1 socket-only test deselected;
- full relevant process/dispatch/control/Pi/broker/delegation/campaign/identity/
  scope/state matrix: 297 passed, 1 skipped;
- full Python: 1284 passed, 1 skipped in 134.85 seconds;
- repository-wide Ruff: pass;
- `git diff --check`: pass;
- Ralph JSON parse: pass;
- secret-pattern scan: no candidate credential/private-key/provider-transcript
  material;
- real Pi no-egress supervisor smoke: `pi --version`, exit 0, stdout 7 bytes,
  stderr 0 bytes, zero owned PIDs after wait.

The initial restricted-sandbox UDS run failed at local socket bind and the
first Pi Node E2E exposed its pre-existing space-sensitive generated shebang.
The intended suite was rerun with short `/tmp` UDS paths; the fake-worker
shebang now uses `/usr/bin/env python3`. Neither event involved model egress.

## Frozen anchors

- identity implementation SHA-256:
  `79ebc21d33d4a39d05bb0a6049b43c1963cfa8ace8c26a4268a6844b9382c03a`
- scope validator SHA-256:
  `a5d25539804ab01164b063876d8def568f9f13bae4152fe82e0a7b0b67d640b6`
- spawn supervisor SHA-256 after repair:
  `f04c688e92639ef4469f299cec3b5a01b50c10735f5cad28a64a4ec49c3dd88e`
- frozen verifier/profile: untouched.
- daemon lifecycle, quota, retry, fallback, and target-selection semantics:
  untouched.

## Remote gate

Pending normal push and exact-head required CI. No Campaign D or MiniMax prompt
is permitted until this section is updated to PASS.
