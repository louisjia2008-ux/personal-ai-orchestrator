# Open-source license decision

Decision: **MIT License**, recorded in the repository root `LICENSE` and in
`pyproject.toml`.

## Options considered

| License | Commercial reuse | Redistribution/modifications | Patent grant | Fit for PAO |
|---|---|---|---|---|
| MIT | Permitted with copyright/license notice | Permissive; modified source need not be published | No explicit patent grant | Chosen for simple adoption by coding-agent hosts, provider adapters, and future macOS distributions |
| Apache-2.0 | Permitted | Permissive; preserves notices and marks changes | Explicit contributor patent license and termination | Stronger patent terms, but more notice/compliance text than the current small pre-alpha project needs |
| MPL-2.0 | Permitted | File-level copyleft for modified MPL-covered files | Explicit patent provisions | Useful when modifications to core files must remain open, but less suitable for the project's goal of easy embedding in heterogeneous hosts |

MIT allows commercial and non-commercial use, modification, redistribution,
sublicensing, and sale, subject to retaining the copyright and license notice.
It does not require contributors or downstream integrators to publish modified
source and does not include Apache-2.0's explicit patent grant.

Contributions are accepted under the same MIT terms. Provider/gateway
integrations and a future distributed macOS client remain subject to their own
third-party dependency, provider, trademark, App Store, signing, and service
terms; the MIT license does not grant rights to third-party services or marks.

Changing the project license later requires an explicit maintainer decision
and may require permission from contributors. No contributor license agreement
is required at this stage.
