# T032 — delegated-child lineage verification checkpoint

## Repair identity

- Repair commit: `f62d515` (`fix: resolve delegated child lineage through canonical metadata`)
- Lineage validator version: `pi5b3g-child-lineage-v1`
- Lineage validator implementation SHA-256:
  `0131d0cc68dea1a24da919d270e9619d3e937c13292aaea8b9091efe937b59a0`
- Accepted campaign identity SHA-256 remains:
  `79ebc21d33d4a39d05bb0a6049b43c1963cfa8ace8c26a4268a6844b9382c03a`
- Accepted scope validator SHA-256 remains:
  `a5d25539804ab01164b063876d8def568f9f13bae4152fe82e0a7b0b67d640b6`

## Implementation

- `SafetyKernelStore.resolve_delegated_task_lineage(child_task_id)` reads only
  the canonical task-scoped `TASK_SUBMITTED` audit history and requires exactly
  one valid parent task/run relationship plus matching immutable task context.
- Stable fail-closed rules cover missing child/submission/lineage, partial or
  malformed metadata, ambiguous submissions, missing parent/run, run ownership
  mismatch, and task-context mismatch.
- `validate_pi5b3g_child_lineage` validates exact canonical host-generated
  campaign identities, current-campaign membership, observation number,
  expected parent/run, and child identity without parsing identifiers or
  filenames.
- Its serialized diagnostic is transcript-free and passes PAO's sanitization
  assertion.
- `TaskRecord` and the task schema were not expanded with harness-only lineage
  fields.

## Offline acceptance

- Focused lineage + child-path tests: `36 passed`.
- Complete relevant lineage/delegation/broker/child/campaign/identity/scope/
  spawn/dispatch matrix: `180 passed`.
- Full Python suite: `1301 passed, 1 skipped` in 133.94 seconds.
- Ruff repository check: PASS.
- `git diff --check`: PASS before commit.
- Secret-pattern scan of changed source/tests: PASS (no matches).
- Provider/model calls during offline repair: 0.

## Required behavioral proof

- Old Campaign D observer behavior: deterministic `AttributeError` reproduced.
- Valid canonical resolver and repaired observer: PASS.
- Missing/partial/malformed/ambiguous lineage: FAIL CLOSED.
- Missing parent/run and parent-run mismatch: FAIL CLOSED.
- Parent task used as child: FAIL CLOSED.
- Different-campaign child: FAIL CLOSED.
- Wrong-observation parent: FAIL CLOSED.
- Fake local child process creation: PASS.
- Durable child run registration: PASS.
- Ownership validation after durable registration: PASS.
- Protocol bootstrap started/completed only after ownership PASS: PASS.
- Clean fake child exit and frozen verifier path: PASS.
- Post-durable lineage failure exact process reap, terminal child run/task,
  released writer, zero registered execution, one spawn/no retry: PASS.
- Automatic fallback: none present or introduced.

## Remaining gate

Normal push and exact-head PR #54 Python/macOS Swift/OpenCode CI must pass before
any Campaign E preflight, canonical daemon transition, or MiniMax prompt.
