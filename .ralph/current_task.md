# Current task

T008 — Promote and prove canonical live lifecycle

Acceptance:

- The exact old stuck daemon is re-identified and retired only under the prompt's one-SIGINT authorization, then the fixed build is launched on canonical state and passes real SIGTERM plus normal restart.

Verification:

- Exactly one healthy fixed daemon owns the canonical socket after restart; DB/accounting and socket invariants remain healthy throughout.

Intended file scope: canonical process lifecycle actions explicitly authorized by the prompt and new evidence only; no campaign begins in this task.
