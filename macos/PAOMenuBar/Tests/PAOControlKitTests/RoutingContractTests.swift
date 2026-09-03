import Foundation

import XCTest

@testable import PAOControlKit

/// Gate-R: the routing plan / role / decision contract.
///
/// The fixtures in `Fixtures/routing` are the frozen wire examples described by
/// docs/ROUTING_ROLE_CONTRACT.md. These tests pin the two things the UI depends
/// on and cannot re-derive: that every documented shape decodes, and that the
/// normalized projection preserves the semantics the contract promises.
final class RoutingContractTests: XCTestCase {

    // MARK: - Fixture loading

    private func detail(_ fixture: String) throws -> TaskDetailView {
        guard let url = Bundle.module.url(
            forResource: fixture,
            withExtension: "json",
            subdirectory: "Fixtures/routing"
        ) else {
            XCTFail("missing fixture \(fixture).json")
            throw CocoaError(.fileNoSuchFile)
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(TaskDetailView.self, from: data)
    }

    private func summary(_ fixture: String) throws -> TaskRoutingSummary {
        guard let summary = try detail(fixture).routingSummary else {
            XCTFail("\(fixture) produced no routing summary")
            throw CocoaError(.coderInvalidValue)
        }
        return summary
    }

    func testEveryContractFixtureDecodes() throws {
        let fixtures = [
            "legacy_single_worker",
            "plan_primary_only",
            "plan_primary_reviewer",
            "plan_primary_reviewer_auditor",
            "plan_reviewer_declared_unassigned",
            "plan_reviewer_reroute",
            "plan_completed_failure_outcome",
        ]
        for fixture in fixtures {
            XCTAssertNoThrow(try detail(fixture), fixture)
        }
    }

    // MARK: - Backward compatibility

    func testLegacyDaemonYieldsSynthesizedPrimaryOnlyPlan() throws {
        let detail = try detail("legacy_single_worker")
        XCTAssertNil(detail.routingPlan, "legacy payload carries no routing_plan")

        let summary = try summary("legacy_single_worker")
        XCTAssertTrue(summary.isLegacySynthesized)
        XCTAssertNil(summary.planId, "a synthesized plan must not claim a plan id")
        XCTAssertNil(summary.planRevision, "a synthesized plan must not claim a revision")
        XCTAssertEqual(summary.roles.map(\.role), [.primary])
        XCTAssertEqual(
            summary.primary?.activeDecision?.selectedExecutionTargetId,
            "minimax-cn-coding-plan/MiniMax-M2.7"
        )
        // Policy resolution survives the legacy adapter.
        XCTAssertEqual(summary.resolvedPolicy, "BALANCED")
        XCTAssertEqual(summary.policyResolutionSource, "PROJECT_OVERRIDE")
    }

    func testLegacyAdapterNeverClaimsALifecycleItCannotObserve() throws {
        // A legacy decision reports a selection, not an execution lifecycle.
        // Reporting COMPLETED here would fabricate an outcome.
        let summary = try summary("legacy_single_worker")
        XCTAssertEqual(summary.primary?.status, .assigned)
        XCTAssertEqual(summary.primary?.outcome, RoutingOutcome.none)
    }

    func testLegacyCandidatesAreCarriedIntoTheProjection() throws {
        let summary = try summary("legacy_single_worker")
        let candidates = summary.primary?.activeDecision?.candidates ?? []
        XCTAssertEqual(candidates.count, 2)
        XCTAssertEqual(candidates.first?.score, 0.87)
        XCTAssertEqual(candidates.first?.selected, true)
        XCTAssertEqual(
            candidates.last?.whyNotSelected,
            "Lower score than selected candidate."
        )
    }

    func testTaskDetailWithoutAnyRoutingProducesNoSummary() throws {
        // A task that has not been routed yet must not manufacture a plan.
        var payload = try fixtureJSON("legacy_single_worker")
        payload["routing"] = nil
        payload.removeValue(forKey: "routing")
        let data = try JSONSerialization.data(withJSONObject: payload)
        let detail = try JSONDecoder().decode(TaskDetailView.self, from: data)
        XCTAssertNil(detail.routingSummary)
    }

    private func fixtureJSON(_ fixture: String) throws -> [String: Any] {
        let url = Bundle.module.url(
            forResource: fixture, withExtension: "json", subdirectory: "Fixtures/routing"
        )!
        let data = try Data(contentsOf: url)
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    // MARK: - Declared roles are authoritative

    func testPrimaryOnlyPlanDeclaresNoReviewer() throws {
        let summary = try summary("plan_primary_only")
        XCTAssertEqual(summary.roles.map(\.role), [.primary])
        XCTAssertFalse(
            summary.declares(.reviewer),
            "a plan that never declared a reviewer must not expose a reviewer lane"
        )
        XCTAssertNil(summary.role(.reviewer))
        XCTAssertFalse(summary.isLegacySynthesized)
        XCTAssertEqual(summary.planId, "rp-7f21")
        XCTAssertEqual(summary.planRevision, 1)
    }

    func testDeclaredRolesDriveOrderAndMembership() throws {
        let summary = try summary("plan_primary_reviewer_auditor")
        XCTAssertEqual(summary.roles.map(\.role), [.primary, .reviewer, .finalAuditor])
    }

    func testDeclaredButUnassignedReviewerIsPendingNotAbsent() throws {
        let summary = try summary("plan_reviewer_declared_unassigned")
        XCTAssertTrue(summary.declares(.reviewer))

        let reviewer = try XCTUnwrap(summary.role(.reviewer))
        XCTAssertTrue(reviewer.isPending, "declared with no decision is an explicit pending state")
        XCTAssertEqual(reviewer.status, .unassigned)
        XCTAssertNil(reviewer.activeDecision)
        XCTAssertTrue(reviewer.history.isEmpty)
        XCTAssertEqual(reviewer.rerouteCount, 0)
    }

    func testPendingIsDistinguishableFromNotDeclared() throws {
        // The pair the UI must never conflate: one plan wants a reviewer and is
        // waiting; the other never wanted one.
        let pending = try summary("plan_reviewer_declared_unassigned")
        let notDeclared = try summary("plan_primary_only")

        XCTAssertTrue(pending.declares(.reviewer))
        XCTAssertEqual(pending.role(.reviewer)?.isPending, true)

        XCTAssertFalse(notDeclared.declares(.reviewer))
        XCTAssertNil(notDeclared.role(.reviewer))
    }

    // MARK: - Independent decisions per role

    func testEachRoleCarriesItsOwnDecisionAndReason() throws {
        let summary = try summary("plan_primary_reviewer")
        let primary = try XCTUnwrap(summary.role(.primary))
        let reviewer = try XCTUnwrap(summary.role(.reviewer))

        XCTAssertNotEqual(
            primary.activeDecision?.decisionId,
            reviewer.activeDecision?.decisionId,
            "roles must not share one decision record"
        )
        XCTAssertEqual(
            primary.activeDecision?.whySelected,
            "Quality tier satisfied; binding window above reserve."
        )
        XCTAssertEqual(
            reviewer.activeDecision?.whySelected,
            "Different provider than primary, as the risk class requires."
        )
        XCTAssertEqual(primary.activeDecision?.role, .primary)
        XCTAssertEqual(reviewer.activeDecision?.role, .reviewer)
    }

    func testRerouteKeepsHistoryAndMarksTheActiveDecision() throws {
        let summary = try summary("plan_reviewer_reroute")
        let reviewer = try XCTUnwrap(summary.role(.reviewer))

        XCTAssertEqual(reviewer.history.count, 2)
        XCTAssertEqual(reviewer.rerouteCount, 1)

        // History is newest-first so a view can render the current selection and
        // the trail behind it without re-sorting.
        XCTAssertEqual(reviewer.history.first?.decisionId, "rd-8845")
        XCTAssertEqual(reviewer.history.last?.decisionId, "rd-8844")

        // The active assignment stays distinguishable from the history.
        XCTAssertEqual(reviewer.activeDecision?.decisionId, "rd-8845")
        XCTAssertTrue(reviewer.activeDecision?.isReroute == true)
        XCTAssertEqual(reviewer.activeDecision?.supersedesDecisionId, "rd-8844")
        XCTAssertEqual(
            reviewer.activeDecision?.rerouteReason,
            "EXECUTION_TARGET_EXHAUSTED"
        )
        XCTAssertFalse(reviewer.isPending)
    }

    func testARoleMayHaveZeroOneOrManyDecisions() throws {
        let zero = try summary("plan_reviewer_declared_unassigned").role(.reviewer)
        let one = try summary("plan_primary_reviewer").role(.reviewer)
        let many = try summary("plan_reviewer_reroute").role(.reviewer)

        XCTAssertEqual(zero?.history.count, 0)
        XCTAssertEqual(one?.history.count, 1)
        XCTAssertEqual(many?.history.count, 2)
    }

    // MARK: - Status and outcome stay separate

    func testCompletedFailureIsAVerdictNotAnError() throws {
        let summary = try summary("plan_completed_failure_outcome")
        let reviewer = try XCTUnwrap(summary.role(.reviewer))

        XCTAssertEqual(reviewer.status, .completed)
        XCTAssertEqual(reviewer.outcome, .fail)
        XCTAssertNotEqual(
            reviewer.outcome, .error,
            "a review that rejected the work is not a transport error"
        )
        XCTAssertFalse(reviewer.isPending)

        // The primary in the same plan completed successfully: one plan carries
        // two different outcomes, and neither overwrites the other.
        XCTAssertEqual(summary.role(.primary)?.status, .completed)
        XCTAssertEqual(summary.role(.primary)?.outcome, .pass)
    }

    func testStatusAndOutcomeAreIndependentAxes() throws {
        // Running work has no result yet; that is NONE, not PASS and not FAIL.
        let running = try summary("plan_primary_reviewer").role(.reviewer)
        XCTAssertEqual(running?.status, .running)
        XCTAssertEqual(running?.outcome, RoutingOutcome.none)
    }

    // MARK: - Unknown values survive

    func testUnknownRoleIsPreservedRatherThanDropped() throws {
        let role = RoutingRole(rawValue: "SECOND_REVIEWER")
        XCTAssertFalse(role.isKnown)
        XCTAssertEqual(role.rawValue, "SECOND_REVIEWER")

        let plan = RoutingPlanView(
            planId: "rp-1", revision: 1, taskId: "PT-1",
            createdAt: "2026-09-03T00:00:00Z",
            declaredRoles: ["PRIMARY", "SECOND_REVIEWER"],
            roles: [
                RoutingRoleStateView(role: .primary, status: .completed, outcome: .pass),
                RoutingRoleStateView(role: role, status: .running),
            ]
        )
        let summary = try XCTUnwrap(TaskRoutingSummary.make(plan: plan, legacyDecision: nil))
        XCTAssertEqual(summary.roles.map(\.role.rawValue), ["PRIMARY", "SECOND_REVIEWER"])
    }

    func testUnknownStatusIsNeverTreatedAsSettledWork() {
        let status = RoutingRoleStatus(rawValue: "QUARANTINED")
        XCTAssertFalse(status.isKnown)
        XCTAssertFalse(status.isTerminal, "an unrecognized status must not read as finished")
        XCTAssertEqual(status.rawValue, "QUARANTINED")

        let outcome = RoutingOutcome(rawValue: "PARTIAL")
        XCTAssertFalse(outcome.isKnown)
        XCTAssertEqual(outcome.rawValue, "PARTIAL")
    }

    func testDeclaredRoleWithoutStateEntryStillRenders() throws {
        // A malformed plan (declared but no matching role entry) must degrade to
        // "unassigned", never to a silently missing lane.
        let plan = RoutingPlanView(
            planId: "rp-2", revision: 3, taskId: "PT-2",
            createdAt: "2026-09-03T00:00:00Z",
            declaredRoles: ["PRIMARY", "REVIEWER"],
            roles: [RoutingRoleStateView(role: .primary, status: .running)]
        )
        let summary = try XCTUnwrap(TaskRoutingSummary.make(plan: plan, legacyDecision: nil))
        XCTAssertEqual(summary.roles.count, 2)
        XCTAssertEqual(summary.role(.reviewer)?.status, .unassigned)
        XCTAssertEqual(summary.role(.reviewer)?.isPending, true)
        XCTAssertEqual(summary.planRevision, 3)
    }

    // MARK: - Plan identity and revision

    func testPlanIdentityAndRevisionAreExposed() throws {
        let summary = try summary("plan_primary_reviewer")
        XCTAssertEqual(summary.planId, "rp-7f21")
        XCTAssertEqual(summary.planRevision, 1)
        XCTAssertFalse(summary.isSuperseded)
    }

    func testSupersededPlanIsMarked() throws {
        let plan = RoutingPlanView(
            planId: "rp-3", revision: 1, taskId: "PT-3",
            createdAt: "2026-09-03T00:00:00Z",
            supersededByPlanId: "rp-4",
            declaredRoles: ["PRIMARY"],
            roles: [RoutingRoleStateView(role: .primary, status: .cancelled)]
        )
        XCTAssertTrue(plan.isSuperseded)
        let summary = try XCTUnwrap(TaskRoutingSummary.make(plan: plan, legacyDecision: nil))
        XCTAssertTrue(summary.isSuperseded)
    }

    // MARK: - Precedence

    func testAuthoritativePlanWinsOverLegacyDecision() throws {
        // When a daemon sends both, the plan is authoritative: the legacy field is
        // a compatibility projection of the primary, not a competing source.
        var payload = try fixtureJSON("plan_primary_reviewer")
        let legacy = try fixtureJSON("legacy_single_worker")
        payload["routing"] = legacy["routing"]
        let data = try JSONSerialization.data(withJSONObject: payload)
        let detail = try JSONDecoder().decode(TaskDetailView.self, from: data)

        let summary = try XCTUnwrap(detail.routingSummary)
        XCTAssertFalse(summary.isLegacySynthesized)
        XCTAssertEqual(summary.roles.map(\.role), [.primary, .reviewer])
    }
}
