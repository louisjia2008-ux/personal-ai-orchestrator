# Current task

T024 — trace and reconstruct parent worker spawn contract

Acceptance:

- Map target resolution, executable/argv/env/cwd construction, subprocess/session/pipe creation, durable run registration, protocol startup, monitoring, exception translation, and cleanup ordering.
- Reconstruct Campaign C Observation 1 using canonical historical state and sanitized evidence.
- Compare the failed launch contract with repository evidence for a previously successful parent launch without sending any model prompt.

Verification:

- A read-only inventory artifact cites exact source/schema/evidence and answers whether durable registration occurs before or after subprocess creation and what `WORKER_SPAWN_FAILED` currently covers.

Only the T024 Ralph inventory/state may change; production source remains untouched.
