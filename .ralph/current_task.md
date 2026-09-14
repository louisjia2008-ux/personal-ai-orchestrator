# Current task

T034 — finalize sanitized Campaign E evidence and restore checkpoint

Acceptance:

- Preserve Campaigns A-D as immutable separate histories.
- Validate and commit curated Campaign E evidence without raw provider transcripts, raw dynamic arguments, environment values, or credentials.
- Normally push the evidence and require exact-head CI before final closure.

Verification:

- Campaign E remains terminal at 3/3 observations and 3 parent/3 child/6 total workers.
- Campaign processes, broker resources, running rows, active runs, and held writers remain zero.
- The exact repaired product remains healthy with one canonical socket owner and an advancing heartbeat.

Campaign D remains consumed and terminal. Campaign E is complete; only sanitized delivery and exact-head CI remain.
