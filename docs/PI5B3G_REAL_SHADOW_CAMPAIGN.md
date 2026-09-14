# PI-5B3G Bounded Real SHADOW Calibration Campaign

Status: `PI_5B3G_AUTHORIZED_TARGET_MAC_EXECUTION_PENDING`

Tracking: #52 (parent #37)

Baseline: `64a45a3adaafb42cac2c6a55a3ac0db31c06f059` (PI-5B3F merged).

## Objective

Run the first explicitly authorized real PI-5B3 SHADOW calibration campaign using the host-owned B3F campaign controller and B3E comparable quota-pair capture. This phase gathers evidence only. Production delegation enforcement remains OFF.

## Owner-authorized hard budget

- maximum observations: 3;
- maximum real parent worker processes: 3;
- maximum real child worker processes: 3;
- maximum total real worker processes: 6;
- automatic retries: 0;
- automatic fallback / target switching: forbidden;
- delegated recursion / grandchildren: forbidden;
- real owner projects: forbidden; use disposable fixture repositories only;
- enforcement: OFF / SHADOW only.

Unused process budget from an incomplete observation does not authorize a retry. One observation may start at most one parent and one host-selected child.

## Preferred real parent path

When current auth, quota truth, execution evidence and runtime availability permit:

- execution target: `pi-minimax-cn-coding-plan-MiniMax-M3`;
- runtime: `pi` / `pi-json`;
- provider: `minimax-cn`;
- model: `MiniMax-M3`.

The delegated child target is never supplied by the parent/model. It is selected by the existing host `DispatchRecommendationService`, then frozen. If no candidate is admitted, the observation stops without fallback.

## Campaign configuration

The operator campaign must be started explicitly with `max_observations = 3` and scoped only to the disposable acceptance project id. Starting the campaign itself must launch zero workers and perform zero quota refreshes.

The campaign may reserve exactly one slot per immutable delegation observation. Replay/re-entry consumes no additional slot. After the third admitted observation the campaign must be `EXHAUSTED` for new observations.

## Per-observation acceptance

Each observation must use a fresh disposable parent task and preserve the normal PI-5B2/B3 chain:

1. parent starts through the real Control API / Safety Kernel / Pi executor path;
2. the real parent invokes `pao_delegate` exactly once;
3. broker admits only the host-bound parent request;
4. host creates exactly one durable delegated child task;
5. host recommendation selects and freezes the child target;
6. parent and child use distinct worktrees and writer ownership;
7. child delegation remains disabled;
8. child reaches terminal truth only through the host deterministic verifier;
9. child result returned to the parent is sanitized;
10. parent remains independently non-terminal while the child becomes terminal;
11. parent reaches terminal truth only through its own deterministic verifier;
12. campaign evidence records the immutable SHADOW observation, CHILD_FINAL and PARENT_FINAL outcomes;
13. if the quota snapshots satisfy B3E comparability rules, record the exact before/after snapshot ids; otherwise preserve a truthful limitation without weakening comparison rules;
14. cleanup leaves no RUNNING rows, writer lock, broker socket or orphan process.

## Task fixtures

Use semantic JSON acceptance files rather than byte-sensitive newline contracts. Verifier profiles must be built and offline-self-tested before any real model call, then frozen for that observation.

Recommended child contract:

```json
{"status":"PI5B3G_CHILD_VERIFIED"}
```

Recommended parent contract after receiving `verified=true` from the broker:

```json
{"status":"PI5B3G_PARENT_VERIFIED","child_verified":true}
```

Extra keys, wrong values, missing files and invalid JSON must fail the deterministic verifier. Whitespace/final-LF differences are irrelevant.

## Hard stop conditions

Stop the campaign immediately and do not launch any later observation after any of the following:

- any retry or fallback occurs;
- more than one child is created for one observation;
- any grandchild appears;
- execution target/runtime/provider/model identity differs from durable host truth;
- shared quota or quota-admission behavior is inconsistent;
- parent/child worktree or writer isolation fails;
- deterministic verifier authority is bypassed or contradicted;
- campaign slot accounting is wrong;
- a process, broker socket, writer or durable RUNNING row leaks after cleanup;
- credentials, raw provider payloads or unsanitized transcripts are persisted.

A missing/incomparable quota pair alone is not permission to fabricate evidence or relax B3E rules. Record the limitation truthfully.

## Completion

PI-5B3G may be marked complete only after the campaign is STOPPED or EXHAUSTED, all launched observations have terminal durable truth and cleanup evidence, sanitized acceptance evidence is committed, the existing read-only readiness evaluator is run over the resulting evidence, and exact-head CI is green.

A readiness result of `READY_FOR_LIMITED_EXPERIMENT` is still only evidence. It does not grant enforcement authority. Any real enforcement experiment remains a separate owner-authorized phase.
