# Current task

T001 — Phase 0 mission lock

Acceptance:

- Required Ralph state exists and locks the full user goal.

Verification:

- `test -s .ralph/task_brief.md`
- `python3 -m json.tool .ralph/tasks.json`

Intended file scope: `.ralph/*` only.
