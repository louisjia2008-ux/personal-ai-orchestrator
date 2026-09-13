# PI 5B2.2 quota observation identity fix

Baseline: `bb376a18db9d5c9a9711e702543cf53c150de163`, PR #36 (open, draft).

## Root cause (recorded before implementation)

The previous real run admitted one parent and one real delegation request,
but selected no child target and launched zero child workers. The host rejected
`pi-minimax-cn-coding-plan-MiniMax-M3` with
`no quota observation reported for this target`. The frozen parent verifier
failed because its result file was absent after child blockage; it is unchanged.

`daemon.default_quota_collectors` used MiniMax constructor defaults, producing
`minimax-token-plan-cn`. `QUOTA_SOURCES` instead addressed the coding surface as
`minimax-coding-plan-cn`. Refresh persisted the snapshot's own pool ID in the
JSON last-known-good cache and immutable snapshot journal. SQLite's bounded
`quota_observation_history` also records the snapshot pool ID, but recommendation
does not query that history: it reads `QuotaRefreshService.observations()`.
The ephemeral `_observed_pool_id` bridged the mismatch only in the refreshing
process. Parent startup independently called the collector and saved availability
under the provider ID, a third identity, without supplying recommendation windows.

Discovery registries contain targets and models but no quota binding facts.
Explicit `QuotaBinding` and `ConsumptionRule` facts already exist for configured
registries. The existing `quota_pool_id_for_target` resolver used those bindings,
but guessed provider identity when resolution failed. Recommendation separately
grouped windows by provider rather than the resolved pool.

The global MiniMax default collector also inherited CN defaults. ZAI's default
pool matches its source metadata; OpenCode explicitly defines an unmetered pool
named `opencode`. Unknown providers must not acquire a guessed pool.

## Chosen source of truth

Reuse the host target resolver: explicit temporal `QuotaBinding` facts take
precedence; discovery-only targets use existing `QUOTA_SOURCES` metadata.
Invalid or ambiguous explicit bindings cannot fall back to static metadata.
MiniMax coding/token surfaces in each region address their canonical regional
Token Plan pool. Target IDs, runtime provider IDs, plan labels, and pool IDs
remain distinct. No second mapping table or string rewriting is introduced.

Persistence remains option A: one snapshot per pool, with immutable history by
snapshot ID. No alias copies or migration of historical acceptance evidence.
Real acceptance has NOT been rerun; a new explicit worker budget is required.

## Final data flow and boundaries

- Collector construction reuses the existing source factories and pool IDs.
  The default collector provider allowlist is unchanged. Global MiniMax now
  uses its global endpoint and pool, never the CN collector defaults.
- `runtime_quota_routing.quota_pool_id_for_target` is the single host target
  resolver. A valid explicit binding wins. Missing discovery-only bindings
  use static source metadata; unknown providers and invalid/ambiguous explicit
  facts do not guess identities. OpenCode's pre-existing `opencode` unmetered
  pool contract is explicit.
- `QuotaRefreshService.snapshot_for_pool` reads the existing pool-addressed
  cache. It neither refreshes nor needs `_observed_pool_id`. Misplaced or corrupt
  snapshot records cannot supply another pool's evidence. The runtime mapping
  remains only as a legacy display fallback, outside recommendation correctness.
- Recommendation preserves the connected-provider boundary, resolves each
  target's pool, reads each distinct pool once, and projects the same windows
  onto its targets. A disconnected target cannot gain eligibility from a cache.
- Startup admission and worker-exhaustion recording resolve that same pool.
  Startup still performs its existing launch-time collection; this fix does not
  substitute cached permission for that check. The offline consistency test feeds
  the exact persisted fixture through its collector seam and invokes the actual
  `_admit_quota` method, without calling execution or spawning a worker.
  An unrelated collector pool cannot establish availability for the target.
- Shared runtime/child checks use pool identity across provider surfaces. Legacy
  availability rows whose pool field equals their provider ID are interpreted
  through existing source metadata on read, preserving cooldowns without
  rewriting historical files. Canonical pool IDs are host-wide identities;
  explicit account-specific bindings stay distinct.

The snapshot ID, observation time, confidence, reset times, fractions, and
journal semantics are preserved. Recommendation now retains validity when
projecting a newly visible snapshot: it uses the existing `RoutingPolicy`
600-second quota-age default, rejects future observations, UNKNOWN/EXHAUSTED
snapshot states and unknown active-window headroom, and uses active windows.
Identity resolution itself never refreshes a timestamp or promotes confidence.
A source that previously disappeared must not become eligible with stale data.
The scoring algorithm, availability state machine, reserve policy,
ConsumptionRule/multiplier semantics, delegation authority, broker, PI tool,
verifiers, worktrees, writer ownership, and fallback policy are unchanged.

## Offline evidence and tests

An isolated `git archive` of the exact baseline, executed in two fixture-only
Python processes, reproduced:

```json
[
  {"warm_found": true, "warm_pool": "minimax-token-plan-cn"},
  {"cold_found": false, "cold_lookup_pool": "minimax-coding-plan-cn", "ephemeral_mapping": {}}
]
```

The acceptance database was inspected with SQLite `mode=ro&immutable=1`.
Its `quota_observation_history` schema has separate provider and pool columns;
there are zero rows in that saved database. Thus no SQLite alias migration is
required or performed. The source's history insertion uses the snapshot pool;
the last-known-good JSON snapshot remains the actual recommendation input.

`tests/test_quota_observation_identity.py` proves:

1. Process A constructs the MiniMax collector, ingests a fixture via fake
   transport, and persists its canonical snapshot; it then exits.
2. Process B reconstructs the PI discovery registry, with no refresh and an
   empty `_observed_pool_id`, and obtains eligible M3 and M2.7 recommendations.
3. The reader invokes startup admission through a fixture collector returning
   that persisted snapshot: admission reports `AVAILABLE_OBSERVED` under
   `minimax-token-plan-cn`, the same pool used for child recommendation.
4. Both targets see exactly two windows, `(0.8, 0.2)`, and only one current
   snapshot file exists. A sibling exhaustion under another connected surface
   blocks both targets; no second balance or capacity is created.
5. Genuinely missing, stale, future, UNKNOWN, EXHAUSTED, expired, disconnected,
   corrupt and misplaced evidence cannot create an eligible recommendation.
6. Explicit pool bindings override static metadata; ambiguous, expired and
   unknown-confidence bindings do not fall back. Unknown providers do not
   infer a pool from their target names.
7. Startup rejects unrelated pool evidence; legacy cooldown reads leave the
   stored bytes unchanged; default collector identities and regions agree.

Validation covered 406 distinct tests across 28 focused modules: quota refresh,
cache/schema, collectors, credentials, observability, availability, shared plans,
workload scopes, equivalent capacity, capabilities, consumption/burn, registry,
runtime routing, recommendation, PI planning, child execution/lifecycle,
delegation contract/broker, PI dispatch/product wiring, supervised-auto,
owner dispatch, daemon configuration, and routing-service persistence/API.
External socket connections were blocked and provider API-key environment
variables removed by the local test runner. All workers in existing integration
tests were synthetic scripts. Two Unix-socket tests initially hit macOS's path
length limit; three loopback HTTP tests hit the runner's initial all-TCP ban.
Rerunning just those two fixture modules with a short path and local-only
connections passed all 25 tests. No production behavior was changed for these
setup issues. The final small collector/read-path refinements were retested
with the affected focused modules.

Repository lint and `git diff --check` pass. Historical acceptance files and
frozen broker/tool/verifier/worktree sources are unchanged relative to baseline.
Exact-head CI is checked on PR #36 after the focused commit is pushed.
The first CI attempt caught an overlong new test assertion; a formatting-only
follow-up corrected it. The next full CI run passed 1,131 tests and found one
crash-recovery fixture that rebuilt an unbound registry and expected replanning
from a 30-minute-old snapshot. That failure was reproduced locally. The fixture
now reuses the explicitly bound test registry and provides a fresh offline
snapshot at its simulated restart time; its lifecycle assertions and production
freshness gates remain intact. The affected recovery module is retested locally
before the final exact-head CI run.

## Readiness, not real acceptance

```text
pi-minimax-cn-coding-plan-MiniMax-M3
  -> host resolver / QUOTA_SOURCES["minimax-cn-coding-plan"]
  -> minimax-token-plan-cn
  -> persisted fixture snapshot: 5h=0.8, weekly=0.2
  -> non-empty eligible recommendation with other gates permitted
```

Real parent runs: 0. Real child runs: 0. Real model calls: 0.
The earlier real acceptance blocker and parent verifier failure remain truthful
historical evidence. PI 5B2 real delegation acceptance is NOT complete.
A new real acceptance run requires new explicit worker-budget authorization.
PR #36 must remain OPEN / DRAFT and must not be merged by this task.
