# Native shared-control validation

## Implemented

- Overview starts with an explicitly bounded recent-task action sample: pending
  approval, blocked/failed, authoritative recovery candidates, running/verifying.
  Policy/resource context and existing risks precede the retained global statistics.
- Task detail leads with the bounded host report and manual next steps. COMPLETE
  labels report delivery; acceptance/publication/native/provider checks remain
  separate. Copying a continuation performs no execution.
- Recovery confirmation captures task generation, prior dispatch, store and
  process identity. A changed confirmation is rejected. An uncertain reply keeps
  that exact request for read-only status reconciliation; recovery never starts a
  worker. Existing explicit native Dispatch remains a separate action.
- Global/project scheduling uses revision- and host-bound drafts, explicit saves,
  conflict preservation and deliberate reload/review. Project inheritance is
  explicit JSON null. Polling never silently rebases a draft.
- English and Simplified Chinese controls use the existing localization catalogs,
  spacing tokens and five-destination navigation.

## Observed in the cloud Linux workspace

- Portable contract checker: passed. Covers all seven backend-generated fixtures,
  report digests/bindings, recovery boundaries, policy revisions, source ordering,
  safety guards and 52 bilingual keys.
- Python Ruff check and format check for the portable checker: passed.
- Tree-sitter Swift syntax parsing: all 97 Swift files parsed with no syntax errors.
  Parsing is not compiler/type checking.
- Independent read-only code review found stale-result, confirmation-rebinding and
  host-cache identity races; those findings were corrected and re-reviewed.
- `git diff --check`: passed.

## Added, but not run here

- 18 Swift unit/transport tests in `SharedControlContractTests.swift`.
- One packaged UI test for action/policy/statistics order; existing KPI UI tests
  now scroll to the intentionally lower statistics row.

This executor has no Swift toolchain, Xcode, macOS runtime or authorized Mac UI
session. Therefore no native build, Swift test result, UI-test result, app launch
or genuine before/after screenshot is claimed.

On an authorized Mac, from `macos/PAOMenuBar`:

    swift build
    swift test --filter SharedControl
    swift test

Then use the existing packaged-app UI-test/screenshot workflow to check Chinese
and English, small/large windows, repeated Save/Recover clicks, conflict reload,
task switching during requests/confirmation, host restart and missing evidence.
Do not run real-provider acceptance merely to validate these presentation changes.
