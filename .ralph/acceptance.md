# Acceptance

- Historical lifecycle and failed-campaign evidence remains preserved.
- Current scope rules and validation/forwarding/accounting boundaries are inventoried before code changes.
- Offline adversarial corpus and permanent tests cover all requested safe, negated, unsafe, injection, and ambiguous cases with stable rule IDs and fail-closed behavior.
- Rejections retain only validator version, lengths, hashes, rule/category/stage, observation/filename, and child-forward/worker facts.
- Repository-owned validator changes pass required tests/Ruff, are separately committed and normally pushed, and exact-head CI is green before live inference.
- A new campaign ID completes three sequential observations at 3 parent/3 child/6 total with zero retry/fallback/grandchild, or stops on the first refined-scope/invariant failure.
- Terminal campaign cleanup and restored repaired product health/heartbeat/DB/sole ownership pass.
- Final sanitized evidence is normally pushed and exact-head CI is checked without merge.
