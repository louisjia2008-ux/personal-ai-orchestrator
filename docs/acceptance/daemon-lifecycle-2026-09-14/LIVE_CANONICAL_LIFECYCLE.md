# PAO live canonical lifecycle acceptance

Status: `PASS`

The lifecycle repair was normally pushed at exact HEAD
`3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e`; CI run `34798471675`
passed all required checks before forced recovery began. Draft PR #54 is scoped
to this repair and is based on the separate Draft PR #53 campaign branch.

The old build `64a45a3adaafb42cac2c6a55a3ac0db31c06f059` had already
failed process-group SIGTERM, direct-child SIGTERM, and direct-child SIGINT.
Immediately before recovery, parent `71945` and serving child `71946` retained
their diagnosed identity, the child remained the sole canonical socket owner,
campaign state was OFF with zero consumption, and SQLite was healthy and idle.
Exactly one SIGKILL was sent to child `71946`. The child exited; the bootloader
parent reaped it and exited; no replacement appeared. The exact unowned stale
Unix socket pathname remained and was therefore unlinked under the narrow
authorization. No other file or process was targeted.

The exact pushed build was rebuilt using the normal Release bundle path. Its
arm64 helper SHA-256 is
`2fd4500fb62110e3b9b83ce4265e8cb56a8d8aecdfd1f9b6318629cf6580e127`;
strict helper and deep bundle signature verification passed.

On canonical state, the repaired product reported health `ok` and the exact
pushed build. The serving child alone owned the mode `srw-------` socket. One
normal SIGTERM to the PyInstaller parent retired both parent and child, removed
the socket owner and pathname, and preserved SQLite `quick_check=ok` with
RUNNING/ACTIVE/HELD `0/0/0`. No SIGINT or SIGKILL was used against the repaired
build. The same product then restarted normally; child `21106` became the sole
canonical socket owner and all health, identity, permission, and database gates
passed again.
