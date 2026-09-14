# T008 live canonical lifecycle

- Remote recovery checkpoint: exact pushed HEAD `3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e`, CI run `34798471675` PASS, Draft PR #54.
- Old recovery: one authorized direct-child SIGKILL to verified PID 71946; child gone; parent 71945 reaped/exited; no replacement.
- Stale socket: exact canonical Unix socket remained unowned and was unlinked; no other path was removed.
- Rebuilt fixed helper: arm64, SHA-256 `2fd4500fb62110e3b9b83ce4265e8cb56a8d8aecdfd1f9b6318629cf6580e127`, signature checks PASS.
- Canonical acceptance: one normal parent SIGTERM; child and parent exited; socket owner/path disappeared; DB `0/0/0`, `quick_check=ok`; normal restart PASS with serving child 21106 as sole owner.
- Repaired-build forced signals: none.
