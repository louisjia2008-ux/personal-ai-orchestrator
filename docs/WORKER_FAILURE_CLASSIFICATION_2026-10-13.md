# Worker failure classification and privacy

## Scope

Offline reliability change based on `main` at `919e219`. No provider calls,
credential changes, automatic retry/fallback, new execution authority, publication,
merge, or deployment. Task transitions and deterministic host verification remain
separate from worker diagnostics.

## Failure contract

The former merged quota/rate result is replaced by:

- `QUOTA_EXHAUSTED`: explicit insufficient/exhausted quota or reached usage limit.
- `RATE_LIMITED`: transient 429/rate-limit/too-many-requests evidence.
- `SERVER_OVERLOADED`: explicit overload or HTTP 503/529.
- `CONTEXT_RESTRICTED`: explicit context-window/prompt-length restriction.
- `AUTH`: authentication/access failure.
- `UNCLASSIFIED`: missing, contradictory, unsupported, or unrelated failure evidence.
- `NONE`: zero worker exit only; this does not establish verified task success.

Known transport code/type/status fields take precedence over prose. The classifier
accepts adapter-owned transport errors and whole JSON error envelopes from the
error channel. It does not search arbitrary nested objects or model stdout.
Conflicting specific transport classes remain unclassified. A generic 429 may be
refined by an explicit quota/overload/context error, but never proves exhaustion
on its own. Text fallback is bounded and phrase based; numeric substrings in
paths, request IDs, and source lines do not count as HTTP status evidence.

Pi's final assistant `errorMessage` is an error-channel diagnostic only when the
JSON stream is structurally valid, has session/start/end events, is not truncated,
and ends with the error stop reason. Assistant content and malformed/incomplete
streams cannot establish provider failure classes. No new typed transport fields
are claimed from a runtime that only supplies error text.

The old `QUOTA_OR_RATE_LIMIT` value is not reinterpreted as exhausted quota on
legacy reads. An unrecognized recorded class is unclassified.

## Cooldowns and evidence

Only explicit exhaustion creates a one-hour exhaustion observation and a
`QUOTA_BLOCKED` execution-evidence row. Transient throttling uses the existing
15-minute local cooldown on both windowed and unmetered targets, with
`WORKER_RATE_LIMIT` and no new exhaustion timestamp. Overload, context, auth, and
unknown diagnostics do not mutate quota observations.

Rate observations preserve stronger active exhaustion, historical exhaustion and
recovery evidence, the original baseline across repeated throttles, and quota
collection failure streaks. A prior recovered exhaustion does not prevent a new
rate-limit cooldown from extending. An explicit exhaustion on an unmetered target
still needs recovery evidence after expiry; it cannot assume availability merely
because the target normally has no metered quota window.

Shadow campaigns use the same classes. A 429 still pauses unfinished campaign
cases using the existing cooldown queue, whose legacy state name is
`DEFERRED_QUOTA`, but its reason is `WORKER_RATE_LIMIT` and no quota exhaustion is
recorded. Overload and context restrictions are operational failures. Nothing in
classification or cooldown expiry retries, redispatches, or switches providers.

## Safe diagnostics

New failed owner/Pi run results expose `worker_failure_class` and a static,
allowlisted `worker_failure_message`. Persistence reconstructs the message from
the enum; caller-supplied message text is never copied. Failed stdout/stderr tails
and raw provider `errorMessage` bodies are excluded. Valid SHA-256 digests, bounded
integer byte counts, boolean timeout/truncation flags, and host exit/signal
metadata remain available. Host verification artifacts are unchanged. This change
does not retroactively scrub historic stored run records.

## Offline verification

Synthetic fixtures cover all classes, structured-field precedence/conflicts,
unknown statuses, irrelevant numeric/path substrings, arbitrary stdout objects,
malformed JSON, and privacy. Local fake-worker regressions verify durable class
persistence, one failed run on repeated dispatch calls, cleared writer ownership,
and unchanged source repositories. No tests invoke a paid provider.

Observed gates in this worktree:

- Classifier, taxonomy/dispatch, quota availability, Pi dispatch, shadow runner,
  execution controller, and failed-worker recovery tests: **153 passed**.
- Broader dispatch/recommender/quota-refresh/shadow-evidence tests: **111 passed**;
  the HTTP cancellation integration test is blocked by cloud runtime
  `AF_UNIX` socket creation (`PermissionError: Operation not permitted`).
- Combined targeted rerun excluding only that blocked socket test: **264 passed,
  1 deselected**.
- `ruff check .`, `ruff format --check .`, and `git diff --check`: passed.

The full Python suite, native build/test, target Mac, provider runtime, signing,
realtime behavior, and release gates are not claimed here. Combined-patch integration
must verify the complete changeset and compare any environmental failures to baseline.
