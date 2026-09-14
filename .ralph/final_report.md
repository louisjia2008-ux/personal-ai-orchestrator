# Ralph final report — PI-5B3G integration and product promotion

## Final status

`PI_5B3G_INTEGRATION_AND_PRODUCT_PROMOTION_COMPLETE`

## Stack integration

The accepted PI-5B3G repair head `ed973da3f2deb18ebeca0013fc73d2e8d8543bc6` was reviewed as the complete 68-file PR #54 diff. Diff, JSON, acceptance sanitization, secret/transcript, fixture, runtime-artifact, and post-lineage source-mutation checks passed. GitHub reported the exact head mergeable and CI-green.

Only PR #54 was merged, into its verified base `feat/pi-delegation-real-shadow-campaign-05b3g`, as merge commit `8a7eba9b91348798339850355c026770892bb40d`. Integrated-head CI passed. Draft PR #53 remains open and unmerged; no next-stack merge was performed.

## Product build and promotion

The repository's normal Release build path produced the signed arm64 app from exact source `8a7eba9b91348798339850355c026770892bb40d`. The final packaged helper SHA-256 is `3208fcd9ac87828027507115f3a8acb792fe343588e176b1e75367db354091c9`; strict deep code-sign verification passed.

Canonical promotion produced exactly one normal GUI, one PyInstaller parent, one serving child, and one owner of the canonical `0600` socket. `/v1/build` reported the exact integrated identity, health was `ok`, heartbeat advanced, SQLite quick-check was `ok`, and RUNNING/ACTIVE/HELD were `0/0/0`.

One normal SIGTERM was sent to the verified PyInstaller parent. Parent and child exited, socket ownership disappeared, the pathname was removed, and database state remained healthy at `0/0/0`. No SIGINT, SIGKILL, or process-group signal was used. The exact same bundle restarted normally and remained healthy.

## Delegation infrastructure smoke

The no-egress deterministic matrix passed 146 distinct identity, scope, spawn, lineage, ownership, fake-child, product-policy, and verifier-loading tests plus four explicit verifier tests. One Unix-socket test was blocked by sandbox EPERM and passed unchanged with host permissions. Frozen verifier hashes matched and its existing self-test passed 10/10.

No Campaign F was created. No MiniMax, model, or provider call occurred.

## Delivery

Sanitized promotion evidence is under `docs/acceptance/pi5b3g-integration-promotion-2026-09-14/`. Evidence commit `16dc91186ef4dc851beb7027480a584e7858b90a` was normally pushed and passed exact-head CI: Python 1m50s, macOS Swift 56s, and OpenCode 16s.

No raw provider transcript, raw dynamic prompt, credential value, provider output, or environment value was committed. The final Ralph closure is the only subsequent metadata commit and requires the same exact-head CI gate. PR #53 remains Draft and unmerged.
