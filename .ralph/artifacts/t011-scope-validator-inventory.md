# T011 — current PI-5B3G dynamic scope validator inventory

## Boundary and conclusion

The currently executed semantic gate is **not repository-owned production
code**. It is `validate_dynamic_child_scope` in the external campaign host:

`/tmp/pao-acceptance/pi5b3g-real-campaign-execution-20260913/run_campaign.py:82-114`

It combines Python regex/substring checks with structured fields already
validated by Pydantic. It is not a parser, AST, polarity-aware phrase matcher,
semantic classifier, or LLM classifier. The broad forbidden regex is applied
with `search`, so any matching token rejects regardless of grammatical role or
negation. The production broker has no equivalent semantic validator.

This establishes a deterministic false-positive risk for safe restrictions such
as `do not select a provider`; it does **not** identify the phrase that rejected
the historical intent, whose raw content was intentionally not retained.

## Current rule table

| RULE_ID | INPUT_FIELD | MATCH_TYPE | MATCH_PATTERN_OR_SEMANTIC_CATEGORY | FAIL_BEHAVIOR | CURRENT_TEST_COVERAGE |
| --- | --- | --- | --- | --- | --- |
| CUR-OBS-001 | host parent task id | full regex | `pi5b3g-obs([123])-parent` | `known_observation=false`; aggregate reject | none for external gate |
| CUR-LEN-001 | intent | Python code-point length | `1 <= len(intent) <= 2000` | aggregate reject | Pydantic/tool length boundaries exist; no external-gate test |
| CUR-LEN-002 | reason | Python code-point length | `1 <= len(reason) <= 512` | aggregate reject | Pydantic/tool length boundaries exist; no external-gate test |
| CUR-FILE-001 | intent | regex token extraction plus set equality | all `[A-Za-z0-9_.-]+\.json` tokens reduce to the expected child filename | aggregate reject | none for external gate; duplicate expected mentions collapse in the set |
| CUR-SEM-001 | intent | exact substring count | `PI5B3G_CHILD_VERIFIED` occurs exactly once | aggregate reject | frozen output verifier covers final JSON; no prompt-gate test |
| CUR-AUTH-001 | intent | case-insensitive regex search | word `provider` | aggregate reject before child forwarding | none; safe negation also rejects |
| CUR-AUTH-002 | intent | case-insensitive regex search | word `model` | aggregate reject before child forwarding | none; safe negation also rejects |
| CUR-AUTH-003 | intent | case-insensitive regex search | word `runtime` | aggregate reject before child forwarding | none; safe negation also rejects |
| CUR-AUTH-004 | intent | case-insensitive regex search | `execution target` or `execution-target` | aggregate reject before child forwarding | none; safe negation also rejects |
| CUR-RETRY-001 | intent | case-insensitive regex search | `retry` or `retries` | aggregate reject before child forwarding | none; `no retry` also rejects |
| CUR-FALLBACK-001 | intent | case-insensitive regex search | word `fallback` | aggregate reject before child forwarding | none; `no fallback` also rejects |
| CUR-DELEGATE-001 | intent | case-insensitive regex search | word `delegate` or `delegation` | aggregate reject before child forwarding | none; `no additional delegation` also rejects |
| CUR-DELEGATE-002 | intent | case-insensitive regex search | `grandchild` or `grandchildren` | aggregate reject before child forwarding | none; prohibition also rejects |
| CUR-TOOL-001 | intent | case-insensitive regex search | `shell`, `bash`, or `powershell` | aggregate reject before child forwarding | runtime allowlist excludes tools; prompt gate untested; safe negation rejects |
| CUR-NET-001 | intent | case-insensitive regex search | `network` or `web` | aggregate reject before child forwarding | runtime allowlist excludes webfetch; prompt gate untested; safe negation rejects |
| CUR-SECRET-001 | intent | case-insensitive regex search | `credential(s)` or `secret(s)` | aggregate reject before child forwarding | broker sanitization tests exist; prompt gate untested; safe negation rejects |
| CUR-TOOL-002 | intent | case-insensitive regex search | `curl`, `wget`, or `ssh` | aggregate reject before child forwarding | none for prompt gate |
| CUR-PATH-001 | intent | case-insensitive regex search | `http://`, `https://`, or `file://` | aggregate reject before child forwarding | none for prompt gate |
| CUR-PATH-002 | intent | regex search | `..` anywhere | aggregate reject before child forwarding | none; broad match also rejects ordinary ellipsis/dotted prose |
| CUR-PATH-003 | intent | regex search | `/` at start or after whitespace | aggregate reject before child forwarding | none; not a complete absolute-path parser |
| CUR-REASON-001 | reason | same combined broad forbidden regex | all CUR-AUTH/RETRY/FALLBACK/DELEGATE/TOOL/NET/SECRET/PATH categories | aggregate reject before child forwarding | none for external gate; no positive reason-shape proof |
| CUR-AGG-001 | all checks | `all(checks.values())` | boolean aggregation with no ordered rule selection | caller raises `PI5B3G_GENERATED_SCOPE_REJECTED` | historical live rejection only; no deterministic RULE_ID/category/stage |

## Structured argument and authority rules surrounding the external gate

| RULE_ID | INPUT_FIELD | MATCH_TYPE | MATCH_PATTERN_OR_SEMANTIC_CATEGORY | FAIL_BEHAVIOR | CURRENT_TEST_COVERAGE |
| --- | --- | --- | --- | --- | --- |
| WIRE-SCHEMA-001 | tool JSON | Pydantic extra-forbid schema | only schema/tool-call/ordinal/intent/reason; no provider/model/runtime/target/worktree fields | wire request invalid/rejected | `test_wire_request_cannot_choose_parent_target_provider_or_runtime` |
| WIRE-LEN-001 | intent/reason | TypeScript and Pydantic bounds | nonblank; intent <=2000; reason <=512 | generic tool/broker rejection | PI-5 contract/tool activation tests; semantic scope absent |
| BROKER-BUDGET-001 | ordinal/tool call | exact counter/idempotency rules | active parent; stable fingerprint; ordered ordinal; bounded child count | stable broker reason codes; no child call | broker budget/idempotency tests |
| HOST-ID-001 | child plan | host-minted structured fields | parent/run/project/base/worktree from broker context; child id minted; depth=1; delegation disabled | validation/rejection | broker identity/depth tests |
| HOST-ROUTE-001 | child execution | host recommendation and launch gate | child plan contains no provider/model/runtime/target; host selects and freezes target | BLOCKED on no admitted pick/launch failure; no fallback | child execution routing/quota/frozen-target tests |
| TOOL-SURFACE-001 | Pi argv | explicit allowlist | parent gets file tools plus `pao_delegate`; child request namespace suppresses `pao_delegate`; shell/PowerShell/webfetch absent | activation fails closed on mismatch | tool activation and child execution tests |
| BROKER-SAN-001 | child result/exception | Pydantic + `assert_sanitized` + generic catch | only typed sanitized response; exceptions become `CHILD_EXECUTION_ERROR` | raw exception/transcript absent | secret-shaped result test |

## Exact forwarding and rejection order

1. The Pi socket tool validates object shape, field types/nonblank values,
   maximum lengths, byte limit, and per-parent tool-call ordinal before sending
   the wire request (`pi5_tool.py:94-156`).
2. `DelegationRequest` repeats strict typed validation and forbids extra fields
   (`pi5_contract.py:15-32`).
3. `DelegationBrokerSession` checks active session, idempotency/conflict,
   budget, and ordinal; it consumes the parent child-request budget before
   constructing a host-owned `DelegationChildPlan` (`pi5_broker.py:183-224`).
4. The external campaign wrapper invokes its regex gate before calling the
   production child port (`run_campaign.py:487-511`). Thus the previous
   rejection created no child task/worker and consumed no campaign observation.
5. On ALLOW, `PAODelegationChildPort.execute_child` validates live parent/run/
   project/base/workspace authority, creates the child task, obtains a host
   recommendation, freezes a target, and dispatches with delegated-child
   authority (`pi5_child_execution.py:409-527`).
6. Campaign admission is not a parent-worker budget. The campaign slot is
   claimed while recording the shadow/quota baseline for a host-selected child;
   the store persists a new UUID per `start`, scopes projects, makes claims
   idempotent, exhausts at the maximum, and refuses claims after STOPPED
   (`delegation_campaign.py:131-228`).
7. Any exception from the child port is collapsed by the broker to the fixed
   sanitized `CHILD_EXECUTION_ERROR` (`pi5_broker.py:223-257`). The external
   report currently keeps hashes/lengths/check booleans but no validator
   version, ordered RULE_ID, semantic category, or stage.

## Safe-negative false-positive surface

Because CUR-AUTH/RETRY/FALLBACK/DELEGATE/TOOL/NET/SECRET rules are token-presence
searches, each of the following is currently rejected despite expressing a
restriction: `do not select a provider`, `do not choose a model`, `do not use a
runtime override`, `do not invoke shell`, `do not access the network`, `no
retry`, `no fallback`, `no additional delegation`, and `do not read
credentials`. Multi-clause polarity is not parsed, so ambiguous combinations
cannot be distinguished from either safe restrictions or positive expansion.

## Coverage gap that Phase B must freeze

There are production tests for typed wire shape, host-owned identity/route,
budget/idempotency, child delegation suppression, tool allowlists, generic
sanitization, campaign state/claim accounting, and no fallback. There are no
permanent repository tests for the external semantic gate, safe negation,
positive one-artifact/one-JSON prompt scope, stable rule traces, or proof that a
semantic rejection cannot reach `execute_child`. Phase B must first encode the
required adversarial corpus and demonstrate this baseline gap offline.
