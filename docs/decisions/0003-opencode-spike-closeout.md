# Decision 0003: OpenCode V2 spike closeout

Status: accepted

Closes the spike boundary requested by issue #12 without converting historical
provider observations into a production-activation claim.

## Context

The spike asked for three progressively stronger stages: a typed fake-daemon
contract, a disposable OpenCode runtime, and live MiniMax plus Z.AI provider
tasks. The repository now contains the production-shaped thin adapter and the
host side of that contract:

- typed BYPASS, SHADOW, ACTIVE, and SUPERVISED_AUTO decision validation;
- fail-closed behavior for unavailable, malformed, mismatched, or stale
  decisions;
- session-scoped `switchModel` integration with no global-config mutation;
- immutable catalog, quota, policy, task-version, and execution-target refs;
- a short-lived daemon-owned switch lease immediately before an ACTIVE side
  effect;
- contract tests for non-invasive SHADOW behavior and legacy-mode regression;
- provider credentials remaining entirely in OpenCode/provider-native stores.

Historical target-Mac evidence proves the local routing/SHADOW path. Historical
provider probes are deliberately mixed: one MiniMax China micro-probe succeeded,
an international MiniMax surface returned an auth error, and Z.AI was not
configured on that host. That is not equivalent to completing one live task on
both providers, and it remains **PARTIALLY OBSERVED**.

## Decision

The spike is closed as **superseded by the narrow production adapter contract**.
Stage A is implemented and continuously tested. The adapter integration and
local SHADOW path are implemented; any future OpenCode-version acceptance must
be recorded against the exact installed beta version. Stage C is not a release
or correctness gate for the host-owned Safety Kernel and is not silently
re-run merely to close an issue.

Provider execution acceptance is tracked per concrete execution target. It may
only be claimed after an authorized, credential-safe live run for that target.
No paid provider call, credential copying, Production ACTIVE enablement, or
global OpenCode configuration change is authorized by this decision.

## Consequences

- Issue #12 closes with a truthful replacement decision, not a claim that both
  provider tasks passed.
- OpenCode API/version drift remains isolated to `integrations/opencode/`.
- Daemon unavailability or contract mismatch preserves the current session
  model.
- Production ACTIVE remains subject to its independent Safety Kernel,
  verification, quota, evidence, and owner-approval gates.
