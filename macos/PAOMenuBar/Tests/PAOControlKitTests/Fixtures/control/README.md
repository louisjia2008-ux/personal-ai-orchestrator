# Shared control wire fixtures

These payloads were generated with the repository's Python test-only service and
real `local_client_v2`/control API handlers. The result fixtures use the existing
`tests.test_result_delivery.verified` helper; the recovery fixtures use
`tests.test_local_client_recovery.recoverable_service` and its explicit request.
No real provider, user project, live daemon, or user computer was used.

The immutable result IDs are retained so the portable check can recompute each
host-report digest. Host identity tokens and timestamps belong to these test-only
stores. They are not credentials.

Run the portable fixture and source-order checks from the repository root:

    python3 macos/PAOMenuBar/scripts/check_shared_control_contract.py

This checks contracts only. Run the real Swift unit suite and packaged UI tests on
an authorized Mac before calling the native changes compiled or visually tested.
