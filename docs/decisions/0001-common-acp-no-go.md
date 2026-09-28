# Decision 0001: common ACP production transport — NO-GO

Status: accepted

Closes the architectural decision requested by issue #1 without fabricating
missing provider evidence.

## Context

The original spike asked PAO to prove Codex, Claude Code, and OpenCode/MiniMax
through one pinned ACP-oriented lifecycle before P0. The repository never
produced complete, comparable lifecycle evidence for all three workers through
one common ACP wrapper. In particular, provider-native auth, cancellation,
permission behavior, and telemetry were not all proven under one pinned
transport. That missing evidence remains **NOT OBSERVED**, not a pass.

Subsequent product work did prove the safety boundary and real execution using
narrower paths:

- the OpenCode adapter has typed BYPASS/SHADOW/ACTIVE decision validation,
  per-session isolation, idempotency, and exact-head contract tests;
- the Pi host runtime/broker has real target-Mac parent/child execution,
  worktree isolation, cancellation/cleanup, deterministic verification, and
  preserved main-repository identity;
- the Safety Kernel owns process identity, durable state, worktrees, approvals,
  verification, and recovery independently of worker prose.

## Decision

ACP is **not** the required production transport. PAO uses one host-owned worker
contract with narrow adapter implementations, and each adapter must prove its
own lifecycle and permission behavior. An ACP adapter may be added later, but
it receives no special authority and cannot weaken the same host gates.

Claude Code and Codex are not advertised as production PAO execution adapters
until a concrete adapter and its own evidence exist. Their quota capability
entries are observability facts, not worker availability claims.

## Consequences

- Issue #1 resolves as a documented NO-GO/replacement decision, not as a claim
  that all three ACP experiments passed.
- P0/P1 evidence stands on the implemented Pi/OpenCode paths and does not
  retroactively prove ACP.
- Provider-native credentials remain in their native stores.
- A future adapter must cover read-only work, edit/test work where applicable,
  streaming, cancellation/child cleanup, permission requests, repository
  immutability, and sanitized telemetry before it is enabled.
