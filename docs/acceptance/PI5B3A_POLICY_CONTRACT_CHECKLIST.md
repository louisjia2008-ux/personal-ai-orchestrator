# PI-5B3A Policy Contract Acceptance Checklist

Baseline: `781f589cbb7caa02a2ab05aa38b44782e2eefbeb`

This slice is offline-only and must consume zero real worker/model budget.

## Required checks

- [ ] `DelegationPolicyMode.OFF` cannot authorize delegation.
- [ ] Hard denial reasons precede positive justification.
- [ ] Missing eligible child fails closed.
- [ ] Missing required quota truth fails closed.
- [ ] Missing required burn estimate fails closed.
- [ ] Predicted child burn above usable headroom fails closed.
- [ ] Paid usage without approval fails closed.
- [ ] Required provider independence without an eligible different-provider candidate fails closed.
- [ ] Host-required delegation can produce `ALLOW` only after hard gates pass.
- [ ] Existing failure-escalation threshold semantics can produce `ALLOW` only after hard gates pass.
- [ ] Passing hard gates without a host justification produces `SHADOW_ONLY`.
- [ ] `SHADOW_ONLY` is never enforceable.
- [ ] `SHADOW` mode never marks a decision enforceable.
- [ ] Same-pool vs different-pool identity is explanatory only in this slice.
- [ ] The policy contract contains no provider/model/target special casing.
- [ ] Identical inputs replay to identical decision JSON.
- [ ] No PI-5B2 broker, authority, worktree, quota-admission, retry/fallback, or verifier semantics changed.
- [ ] Exact-head CI passes.
- [ ] Real model calls: 0.

## Exit status

Only use `PI_5B3A_POLICY_CONTRACT_COMPLETE` when every item above is satisfied on the exact PR head.
