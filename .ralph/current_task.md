# Current task

T025 — add spawn observability and prove root cause offline

Acceptance:

- Add sanitized, deterministic spawn-stage diagnostics without environment values, raw prompts, credentials, or provider transcripts.
- Reproduce the Campaign C failure through the same `ProcessSupervisor.start` and post-create campaign wrapper ordering using only a local fake worker.
- Prove one precise root-cause classification and the child-created/ownership/cleanup facts.

Verification:

- Focused tests prove diagnostic stage/class/errno and post-create process observation without model egress.
- Root-cause artifact records the deterministic reproduction and why the transient PID does not prove provider egress.

No model call. Production changes are limited to spawn observability and exact post-create cleanup plumbing required by the proven defect.
