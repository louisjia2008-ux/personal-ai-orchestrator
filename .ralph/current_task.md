# Current task

None — terminal blocked state

Acceptance:

- T015 stopped at the first campaign safety invariant failure.
- T016 sanitized evidence push and exact-head CI passed.
- No campaign retry is authorized in this run.

Verification:

- Final Ralph closure commit must be normally pushed and pass exact-head CI.

Blocker: `NEW_CAMPAIGN_REQUEST_ID_CONFLICT`. A future separately authorized run
must mint fresh canonical task/request/dispatch identities offline before any
model dispatch.
