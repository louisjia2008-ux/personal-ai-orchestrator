# Current task

T030 — inventory canonical delegated-child lineage

Acceptance:

- Trace TaskRecord/schema, child submission, `TASK_SUBMITTED` lineage metadata, store/audit APIs, observation/campaign linkage, and Campaign D observer.
- Document source of truth, persistence, supported API gap, keys, uniqueness/ambiguity behavior, and fail-closed repair boundary without source changes.

Verification:

- Inventory artifact cites exact source and tests.
- Git diff contains only Ralph/inventory evidence; no model call or canonical mutation.

Campaign D remains consumed and terminal. Campaign E cannot start before T031-T033 pass.
