import Foundation

import XCTest

@testable import PAOControlKit

/// B3: the execution surfaces of Task Detail — lifecycle, changes, verification,
/// attention — against the frozen wire fixtures.
///
/// The through-line is that absence stays absence. A stage with no record is not
/// a stage that failed, an unmeasured change set is not zero files, and a
/// verifier whose evidence is missing has not passed.
final class TaskDetailSurfaceTests: XCTestCase {

    // MARK: - Fixtures

    private func detail(_ fixture: String) throws -> TaskDetailView {
        guard
            let url = Bundle.module.url(
                forResource: fixture, withExtension: "json", subdirectory: "Fixtures/routing"
            )
        else {
            XCTFail("missing fixture \(fixture).json")
            throw CocoaError(.fileNoSuchFile)
        }
        return try JSONDecoder().decode(TaskDetailView.self, from: Data(contentsOf: url))
    }

    private func step(
        _ fixture: String, _ stage: TaskLifecycleStage
    ) throws -> TaskLifecycleStep {
        let steps = TaskLifecycle.steps(for: try detail(fixture))
        return try XCTUnwrap(steps.first { $0.stage == stage })
    }

    // MARK: - Lifecycle

    func testLifecycleReportsEveryStageExactlyOnceInOrder() {
        XCTAssertEqual(
            TaskLifecycleStage.allCases,
            [.submitted, .routing, .workspace, .execution, .verification, .completion]
        )
    }

    func testARunningTaskReportsWhereTheWorkActuallyIs() throws {
        let steps = TaskLifecycle.steps(for: try detail("plan_primary_reviewer"))
        let byStage = Dictionary(uniqueKeysWithValues: steps.map { ($0.stage, $0) })

        XCTAssertEqual(byStage[.submitted]?.status, .reached)
        XCTAssertEqual(byStage[.submitted]?.at, "2026-09-03T11:40:00Z")
        // The reviewer role is RUNNING, so routing is still in flight.
        XCTAssertEqual(byStage[.routing]?.status, .inProgress)
        XCTAssertEqual(byStage[.workspace]?.status, .reached)
        XCTAssertEqual(byStage[.execution]?.status, .inProgress)
        // Nothing has been verified. That is "not yet", not "failed".
        XCTAssertEqual(byStage[.verification]?.status, .notReached)
        XCTAssertEqual(byStage[.completion]?.status, .notReached)
    }

    func testAStageWithNoRecordReportsNotReachedRatherThanBeingInferred() throws {
        // The legacy fixture carries no verification result at all.
        XCTAssertEqual(try step("legacy_single_worker", .verification).status, .notReached)
        XCTAssertNil(try step("legacy_single_worker", .verification).at)
    }

    func testRejectedVerificationFailsBothVerificationAndCompletion() throws {
        XCTAssertEqual(try step("plan_completed_failure_outcome", .verification).status, .failed)
        // The task is BLOCKED: completion did not happen and did not simply stall.
        XCTAssertEqual(try step("plan_completed_failure_outcome", .completion).status, .failed)
    }

    func testAnUnrecognizedTaskStateNeverReportsProgress() throws {
        let completion = try step("plan_unknown_role_status", .completion)
        XCTAssertEqual(completion.status, .unknown)
        XCTAssertEqual(completion.detail, "AWAITING_HUMAN_REVIEW")
        XCTAssertNotEqual(completion.status, .reached)
    }

    func testCurrentStageIsTheOneReportingItself() throws {
        XCTAssertEqual(
            TaskLifecycle.currentStage(for: try detail("plan_primary_reviewer")), .routing
        )
    }

    // MARK: - Changes

    func testChangedFilesAreListedNotCounted() throws {
        let changes = TaskChangeSet.derive(from: try detail("plan_completed_failure_outcome"))
        XCTAssertTrue(changes.isMeasured)
        XCTAssertEqual(
            changes.files.map(\.path).sorted(),
            [".env", "scripts/x.sh", "src/kernel.py"]
        )
    }

    func testOutOfScopeWritesAreMarkedFromTheVerifiersOwnFinding() throws {
        let changes = TaskChangeSet.derive(from: try detail("plan_completed_failure_outcome"))
        XCTAssertEqual(changes.unexpectedFiles.map(\.path), [".env"])
        XCTAssertFalse(
            changes.files.first { $0.path == "src/kernel.py" }?.isUnexpected ?? true
        )
    }

    func testNoVerifierResultIsUnavailableRatherThanZeroFiles() throws {
        // "3 files changed" and "we never looked" must not look alike, and
        // neither may be rendered as 0.
        let changes = TaskChangeSet.derive(from: try detail("plan_primary_reviewer"))
        XCTAssertEqual(changes, .unavailable(.verificationNotRun))
        XCTAssertFalse(changes.isMeasured)
        XCTAssertTrue(changes.files.isEmpty)
    }

    func testATaskWithNoWorktreeSaysSoInsteadOfBlamingVerification() throws {
        var payload = try fixtureJSON("plan_primary_reviewer")
        payload.removeValue(forKey: "workspace")
        let detail = try decode(payload)
        XCTAssertEqual(TaskChangeSet.derive(from: detail), .unavailable(.noWorkspace))
    }

    func testAMeasuredEmptyListIsNotTheSameAsUnmeasured() {
        XCTAssertNotEqual(TaskChangeSet.files([]), .unavailable(.verificationNotRun))
        XCTAssertTrue(TaskChangeSet.files([]).isMeasured)
    }

    func testFileNameAndDirectorySplitForReadableRows() {
        let nested = TaskChangedFile(path: "src/kernel.py", isUnexpected: false)
        XCTAssertEqual(nested.fileName, "kernel.py")
        XCTAssertEqual(nested.directory, "src")
        let root = TaskChangedFile(path: ".env", isUnexpected: true)
        XCTAssertEqual(root.fileName, ".env")
        XCTAssertNil(root.directory)
    }

    // MARK: - Verification

    func testEveryDaemonVerificationStatusIsRecognized() {
        // These are exactly the values `ControlPlane.verification_report` emits.
        let statuses = [
            "VERIFIED", "VERIFIED_EVIDENCE_MISSING", "VERIFIED_EVIDENCE_UNAVAILABLE",
            "FAILED_VERIFICATION", "IN_PROGRESS", "NOT_VERIFIED",
        ]
        for raw in statuses {
            let status = TaskVerificationStatus(rawValue: raw)
            XCTAssertTrue(status.isKnown, "\(raw) fell through to the unknown branch")
            XCTAssertEqual(status.rawValue, raw)
        }
    }

    func testRejectedWorkIsDistinguishableFromUnavailableEvidence() {
        let rejected = TaskVerificationStatus(rawValue: "FAILED_VERIFICATION")
        let missing = TaskVerificationStatus(rawValue: "VERIFIED_EVIDENCE_MISSING")

        XCTAssertTrue(rejected.isWorkRejection)
        XCTAssertFalse(rejected.isEvidenceProblem)
        XCTAssertFalse(missing.isWorkRejection)
        XCTAssertTrue(missing.isEvidenceProblem)

        // And they must not present alike: one is a verdict about the work, the
        // other a fact about the infrastructure.
        XCTAssertNotEqual(
            StatusStyle.verification(rejected).tone, StatusStyle.verification(missing).tone
        )
        XCTAssertNotEqual(
            StatusStyle.verification(rejected).symbol, StatusStyle.verification(missing).symbol
        )
    }

    func testEvidenceProblemsNeverRenderAsAPass() {
        for raw in ["VERIFIED_EVIDENCE_MISSING", "VERIFIED_EVIDENCE_UNAVAILABLE"] {
            let tone = StatusStyle.verification(raw).tone
            XCTAssertEqual(tone, .unknown)
            XCTAssertNotEqual(tone, .positive, "\(raw) must not read as verified")
        }
        XCTAssertEqual(StatusStyle.verification("VERIFIED").tone, .positive)
    }

    func testAnUnrecognizedVerificationStatusIsNeverSuccessful() {
        let status = TaskVerificationStatus(rawValue: "PARTIALLY_VERIFIED")
        XCTAssertFalse(status.isKnown)
        XCTAssertEqual(status.rawValue, "PARTIALLY_VERIFIED")
        XCTAssertEqual(StatusStyle.verification(status).tone, .unknown)
    }

    func testVerifierProfileAndChecksAreProjectedFromTheResult() throws {
        let summary = TaskVerificationSummary(
            report: try detail("plan_completed_failure_outcome").verification
        )
        XCTAssertEqual(summary.status, .failedVerification)
        XCTAssertEqual(summary.profile, "python-default")
        XCTAssertEqual(summary.resultPassed, false)
        XCTAssertEqual(summary.stages.map(\.name), ["git", "python"])
        XCTAssertEqual(summary.failedStages.map(\.name), ["python"])
        XCTAssertEqual(summary.evidenceId, "ev-3301")
        XCTAssertEqual(summary.failureReason, "REVIEW_REJECTED")
    }

    func testATaskWithNoVerifierResultReportsNoneRatherThanAnEmptyPass() throws {
        let summary = TaskVerificationSummary(
            report: try detail("plan_primary_reviewer").verification
        )
        XCTAssertEqual(summary.status, .notVerified)
        XCTAssertFalse(summary.hasResult)
        XCTAssertNil(summary.resultPassed)
        XCTAssertTrue(summary.stages.isEmpty)
    }

    // MARK: - Attention

    func testAHealthyRunningTaskNeedsNoAttention() throws {
        XCTAssertTrue(TaskAttention.reasons(for: try detail("plan_primary_reviewer")).isEmpty)
    }

    func testRejectedVerificationLeadsWithTheDaemonsOwnReason() throws {
        let reasons = TaskAttention.reasons(for: try detail("plan_completed_failure_outcome"))
        XCTAssertEqual(reasons.first?.kind, .verificationFailed)
        XCTAssertEqual(reasons.first?.detail, "REVIEW_REJECTED")
        // The same task is BLOCKED and wrote outside the allowed paths; both are
        // real and both are reported.
        XCTAssertTrue(reasons.contains { $0.kind == .blocked })
        XCTAssertTrue(reasons.contains { $0.kind == .unexpectedChanges })
    }

    func testAnUnrecognizedStateIsSurfacedRatherThanIgnored() throws {
        let reasons = TaskAttention.reasons(for: try detail("plan_unknown_role_status"))
        let unknown = try XCTUnwrap(reasons.first { $0.kind == .unknownState })
        XCTAssertEqual(unknown.detail, "AWAITING_HUMAN_REVIEW")
        XCTAssertEqual(StatusStyle.attention(.unknownState).tone, .unknown)
    }

    func testAPendingApprovalIsAnAttentionReason() throws {
        var payload = try fixtureJSON("plan_primary_reviewer")
        payload["approvals"] = [
            "approvals": [
                [
                    "approval_id": "ap-1", "task_id": "PT-0042",
                    "kind": "HIGH_RISK_EXECUTION", "status": "PENDING",
                    "created_at": "2026-09-03T11:45:00Z", "resolved_at": NSNull(),
                ]
            ]
        ]
        let reasons = TaskAttention.reasons(for: try decode(payload))
        let approval = try XCTUnwrap(reasons.first { $0.kind == .awaitingApproval })
        XCTAssertEqual(approval.detail, "HIGH_RISK_EXECUTION")
    }

    func testAResolvedApprovalIsNotAnAttentionReason() throws {
        var payload = try fixtureJSON("plan_primary_reviewer")
        payload["approvals"] = [
            "approvals": [
                [
                    "approval_id": "ap-1", "task_id": "PT-0042",
                    "kind": "HIGH_RISK_EXECUTION", "status": "APPROVED",
                    "created_at": "2026-09-03T11:45:00Z",
                    "resolved_at": "2026-09-03T11:46:00Z",
                ]
            ]
        ]
        XCTAssertTrue(TaskAttention.reasons(for: try decode(payload)).isEmpty)
    }

    func testNoAttentionReasonEverPresentsAsHealthy() {
        let kinds: [TaskAttention.Kind] = [
            .verificationFailed, .failed, .blocked, .unknownState,
            .verificationEvidenceUnavailable, .unexpectedChanges, .awaitingApproval,
        ]
        for kind in kinds {
            let style = StatusStyle.attention(kind)
            XCTAssertNotEqual(style.tone, .positive, "\(kind.rawValue) must not read as fine")
            XCTAssertFalse(style.symbol.isEmpty, "\(kind.rawValue) needs a non-colour channel")
        }
    }

    // MARK: - Task state presentation

    func testEveryAuthoritativeTaskStateHasARecognizedPresentation() {
        // WORKER_FINISHED was falling through to the unknown branch, so an
        // ordinary state rendered with a question mark.
        for state in TaskStates.all {
            let style = StatusStyle.task(state: state)
            XCTAssertNotEqual(
                style.symbol, "questionmark.circle",
                "\(state) is authoritative and must not render as unknown"
            )
        }
        XCTAssertEqual(StatusStyle.task(state: "AWAITING_HUMAN_REVIEW").tone, .unknown)
    }

    // MARK: - Inspector independence

    /// Task Detail answers the owner's questions without any of the material the
    /// inspector holds.
    ///
    /// Strip the worktree, the runs and the routing plan — every inspector
    /// section's source — and the core surfaces must still say something true
    /// rather than going blank or claiming a positive state.
    func testCoreTaskDetailSurvivesWithoutAnyInspectorMaterial() throws {
        var payload = try fixtureJSON("plan_primary_reviewer")
        payload.removeValue(forKey: "workspace")
        payload.removeValue(forKey: "routing_plan")
        payload.removeValue(forKey: "routing")
        payload["runs"] = []
        let detail = try decode(payload)

        // Status: still resolved, still from the task's own state.
        XCTAssertEqual(StatusStyle.task(state: detail.task.state).tone, .neutral)

        // Lifecycle: complete, and honest about what has no record.
        let steps = TaskLifecycle.steps(for: detail)
        XCTAssertEqual(steps.count, TaskLifecycleStage.allCases.count)
        XCTAssertEqual(steps.first { $0.stage == .routing }?.status, .notReached)
        XCTAssertEqual(steps.first { $0.stage == .workspace }?.status, .notReached)
        XCTAssertEqual(steps.first { $0.stage == .execution }?.status, .notReached)

        // Changes and verification: explained absences, never a positive claim.
        XCTAssertEqual(TaskChangeSet.derive(from: detail), .unavailable(.noWorkspace))
        XCTAssertEqual(TaskVerificationSummary(report: detail.verification).status, .notVerified)

        // Routing: no summary at all, which the section renders as "not yet
        // decided" rather than as an empty role list.
        XCTAssertNil(detail.routingSummary)
    }

    /// The inspector's own material is present when the daemon supplies it, so
    /// the pane is optional rather than empty.
    func testInspectorMaterialIsAvailableWhenTheDaemonSuppliesIt() throws {
        let detail = try detail("plan_primary_reviewer_auditor")
        XCTAssertEqual(detail.workspace?.worktreePath, "/repo/.worktrees/PT-0042")
        XCTAssertEqual(detail.workspace?.baseSha, "4f9a21c")
        XCTAssertEqual(detail.runs.first?.runId, "run-01")
        XCTAssertEqual(detail.routingSummary?.planId, "rp-7f21")
        XCTAssertEqual(detail.routingSummary?.planRevision, 1)
    }

    // MARK: - Helpers

    private func fixtureJSON(_ fixture: String) throws -> [String: Any] {
        let url = try XCTUnwrap(
            Bundle.module.url(
                forResource: fixture, withExtension: "json", subdirectory: "Fixtures/routing"
            )
        )
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url))
        return try XCTUnwrap(object as? [String: Any])
    }

    private func decode(_ payload: [String: Any]) throws -> TaskDetailView {
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(TaskDetailView.self, from: data)
    }
}
