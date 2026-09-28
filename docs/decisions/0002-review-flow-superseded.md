# Decision 0002: automatic cross-provider reviewer flow — superseded

Status: accepted

Closes the design ambiguity tracked by issue #4.

## Context

The early P2 proposal combined three ideas:

1. provider/runtime plurality;
2. a different-provider reviewer for selected risk classes;
3. the baseline `Primary -> Verifier -> Reviewer -> Verifier` pipeline.

The current product has strong evidence for provider/runtime selection,
one-writer worktree ownership, process failure containment, and deterministic
host verification. It also has an explicit one-level delegation contract with
budget, quota, lineage, worktree, and verifier gates.

It does **not** have a production reviewer planner/executor. The routing-role
wire fixtures intentionally say so. Treating those presentation fixtures as
runtime support would be a false claim, while implementing an automatic
reviewer now would add provider spend and write-coordination policy without an
accepted evidence-based need.

## Decision

- `ExecutionTarget` plus runtime/provider registries replace the proposed thin
  Worker Registry.
- The deterministic host verifier remains the mandatory acceptance authority.
- A second worker is requested only through explicit, bounded, one-level
  delegation; it is not silently attached as a default reviewer.
- Delegated children never inherit recursive delegation and never receive
  provider/model/worktree/verifier authority from worker prose.
- The UI routing-role contract stays forward-compatible but must render absent
  reviewer execution as absent/unassigned, never as completed.

## Consequences

Issue #4 resolves as **superseded by the implemented target registry,
verifier, and bounded delegation architecture**. A future true reviewer stage
requires a new issue with risk-class policy, provider-independence truth,
cost/quota budget, read/write ownership, reviewer-output contract, second
verification, cancellation, and real acceptance evidence.
