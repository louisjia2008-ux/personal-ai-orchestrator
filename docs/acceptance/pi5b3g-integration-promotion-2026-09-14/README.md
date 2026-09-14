# PI-5B3G integration and normal-product promotion

## Outcome

`PI_5B3G_INTEGRATION_AND_PRODUCT_PROMOTION_COMPLETE`

The fully accepted PI-5B3G repair lineage was integrated by merging only PR #54 into its verified current base. Merge commit `8a7eba9b91348798339850355c026770892bb40d` is now the head of `feat/pi-delegation-real-shadow-campaign-05b3g`. Its exact integrated-head CI passed. Draft PR #53 remains the next separately gated stack edge to `main`; it was not merged.

No additional real MiniMax campaign or provider call was made.

## Stack and review

The actual stack at integration time was:

```text
main 64a45a3
  <- Draft PR #53: feat/pi-delegation-real-shadow-campaign-05b3g 3f15e6d
       <- Draft PR #54: fix/pao-daemon-graceful-sigterm ed973da
```

PR #54 was exact-head green, GitHub `MERGEABLE / CLEAN`, and sanitized before merge. Its complete 68-file diff contained the accepted lifecycle, deterministic scope, campaign identity, spawn diagnostics, canonical lineage work, their tests, and sanitized evidence. No secret, raw provider transcript, credential value, disposable fixture mutation, runtime database/socket/log artifact, or production mutation after the accepted lineage fix was found.

After merging PR #54, the stack became:

```text
main 64a45a3
  <- Draft PR #53: feat/pi-delegation-real-shadow-campaign-05b3g 8a7eba9
```

PR #53 remains open, draft, and unmerged because no authorization was provided for the next stack merge.

## Integrated Release build and promotion

The repository's normal `scripts/build_and_run_macos_dashboard.sh` path built and launched the product from exact integrated head `8a7eba9b91348798339850355c026770892bb40d`. The final Release app is version `1.0` build `1`; its PyInstaller helper is arm64 with SHA-256 `3208fcd9ac87828027507115f3a8acb792fe343588e176b1e75367db354091c9`. Strict deep code-sign verification passed. Both the app's embedded identity and the live daemon `/v1/build` endpoint report the integrated merge commit.

The last previously restored product had build identity `3b9aaab83d87a2c32adf17e5f3a4afbbfdf16b7e`, but it was not live at the final promotion preflight. Canonical SQLite was healthy and idle, the canonical socket was absent, campaign processes were zero, and no alternate canonical owner existed before launch.

## Canonical lifecycle smoke

The first integrated start produced one GUI, one PyInstaller parent, one serving child, and one `0600` canonical socket owner. Health was `ok`, heartbeat advanced, SQLite quick-check was `ok`, and RUNNING/ACTIVE/HELD were `0/0/0`.

One direct SIGTERM was sent to the verified PyInstaller parent only. The serving child and parent exited, the canonical socket owner disappeared, the socket pathname was removed, and SQLite remained `ok` at `0/0/0`. No SIGINT, SIGKILL, or process-group signal was used. The daemonless GUI then exited through SIGTERM, and the same exact bundle restarted normally. The restarted app and daemon both report `8a7eba9`.

## No-egress infrastructure smoke

A focused deterministic matrix exercised campaign identity, scope validation, spawn diagnostics, canonical child lineage, parent/child ownership, fake child execution, product policy/verifier loading, and verifier fail-closed behavior. All 146 distinct tests passed. One product-daemon Unix-socket test was initially blocked by the Codex sandbox with `EPERM`; the exact test passed with normal host permissions. Four explicit verifier/loading checks also passed.

The frozen verifier profile and implementation still have their accepted SHA-256 values, and the existing 10-case frozen verifier self-test remains fully passing. No model or provider call occurred.

## Final canonical state

The final product has one GUI, one PyInstaller parent, one serving child, and exactly one canonical socket owner. The control socket is `0600`, `/v1/build` reports `8a7eba9`, health is `ok`, heartbeat advances, SQLite quick-check is `ok`, and RUNNING/ACTIVE/HELD remain `0/0/0`. Campaign processes and alternate canonical owners are zero.

PID 41372 from `/Users/louisjia/Developer/pao-p4-full-dashboard` remains isolated on its own database, runtime-state directory, and socket under `/var/folders/.../pao-boot-kfgcsh7d`. It does not touch canonical PAO resources and was left untouched.

## Sanitization

This evidence contains no raw provider transcript, raw dynamic prompt, provider output, credential value, or environment value. It preserves Campaign E's accepted identity and aggregate accounting only. No Campaign F was started.
