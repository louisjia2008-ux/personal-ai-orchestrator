import Foundation

import XCTest

@testable import PAOControlKit

/// B3: the rules the routing section renders by.
///
/// `RoutingContractTests` pins the projection; these pin what the view is
/// allowed to draw from it. The rule that matters most is the one that is
/// invisible when it holds and misleading when it breaks: a role the plan never
/// declared must produce no row at all, because "Reviewer — not assigned" claims
/// a reviewer is expected.
///
/// The view renders exactly `summary.roles`, one row per element, so asserting
/// on that array is asserting on the rendered rows.
final class TaskRoutingPresentationTests: XCTestCase {

    private func summary(_ fixture: String) throws -> TaskRoutingSummary {
        guard
            let url = Bundle.module.url(
                forResource: fixture, withExtension: "json", subdirectory: "Fixtures/routing"
            )
        else {
            XCTFail("missing fixture \(fixture).json")
            throw CocoaError(.fileNoSuchFile)
        }
        let detail = try JSONDecoder().decode(TaskDetailView.self, from: Data(contentsOf: url))
        return try XCTUnwrap(detail.routingSummary, "\(fixture) produced no routing summary")
    }

    // MARK: - Role presence

    func testAPrimaryOnlyPlanRendersNoReviewerRow() throws {
        let summary = try summary("plan_primary_only")
        XCTAssertEqual(summary.roles.map(\.role), [.primary])
        XCTAssertNil(
            summary.role(.reviewer),
            "an undeclared reviewer must produce no row, not a pending one"
        )
        XCTAssertFalse(summary.declares(.reviewer))
        XCTAssertFalse(summary.declares(.finalAuditor))
    }

    func testADeclaredButUnassignedReviewerDoesRenderAsPending() throws {
        // The opposite case, and the reason the rule above cannot simply be
        // "hide roles with no decision".
        let reviewer = try XCTUnwrap(
            try summary("plan_reviewer_declared_unassigned").role(.reviewer)
        )
        XCTAssertTrue(reviewer.isPending)
        XCTAssertEqual(reviewer.status, .unassigned)
        XCTAssertNil(reviewer.activeDecision)
        XCTAssertEqual(
            L10n.routingRoleStatusName(.unassigned),
            L10n.catalogString(key: "routing.roleStatus.unassigned", language: L10n.resolvedLanguageCode)
        )
    }

    func testAThreeRolePlanRendersEveryDeclaredRoleInCanonicalOrder() throws {
        let summary = try summary("plan_primary_reviewer_auditor")
        XCTAssertEqual(summary.roles.map(\.role), [.primary, .reviewer, .finalAuditor])
        for role in summary.roles {
            XCTAssertNotNil(role.activeDecision, "\(role.role.rawValue) has no assignment to show")
        }
    }

    func testALegacyDaemonRendersOnlyThePrimaryItImplies() throws {
        let summary = try summary("legacy_single_worker")
        XCTAssertEqual(summary.roles.map(\.role), [.primary])
        XCTAssertTrue(summary.isLegacySynthesized)
        // Provenance is a compatibility note, not a safety warning: the plan has
        // no id and no revision, and the UI must not claim otherwise.
        XCTAssertNil(summary.planId)
        XCTAssertNil(summary.planRevision)
    }

    // MARK: - Reroute

    func testARerouteResolvesToTheAssignmentCurrentlyInForce() throws {
        let reviewer = try XCTUnwrap(try summary("plan_reviewer_reroute").role(.reviewer))
        let active = try XCTUnwrap(reviewer.activeDecision)
        XCTAssertEqual(active.decisionId, "rd-8845")
        XCTAssertEqual(reviewer.rerouteCount, 1)
        // History is newest-first, so the row the view folds away is the older
        // decision, not the current one.
        XCTAssertEqual(reviewer.history.first?.decisionId, active.decisionId)
        XCTAssertEqual(reviewer.history.count, 2)
    }

    func testARoleWithOneDecisionShowsNoRerouteHistory() throws {
        let primary = try XCTUnwrap(try summary("plan_primary_only").primary)
        XCTAssertEqual(primary.rerouteCount, 0)
        XCTAssertEqual(primary.history.count, 1)
    }

    // MARK: - Outcome presentation

    func testAReviewThatRejectedTheWorkIsNotAReviewThatCouldNotRun() throws {
        let rejected = try XCTUnwrap(
            try summary("plan_completed_failure_outcome").role(.reviewer)
        )
        let errored = try XCTUnwrap(
            try summary("plan_completed_error_outcome").role(.reviewer)
        )
        XCTAssertEqual(rejected.outcome, .fail)
        XCTAssertEqual(errored.outcome, .error)

        let rejectedStyle = StatusStyle.routingRole(
            status: rejected.status, outcome: rejected.outcome
        )
        let erroredStyle = StatusStyle.routingRole(
            status: errored.status, outcome: errored.outcome
        )
        XCTAssertNotEqual(
            rejectedStyle.tone, erroredStyle.tone,
            "a verdict and a breakage must not share a treatment"
        )
        XCTAssertNotEqual(rejectedStyle.symbol, erroredStyle.symbol)
        XCTAssertNotEqual(
            L10n.routingOutcomeName(.fail), L10n.routingOutcomeName(.error)
        )
    }

    func testCompletedFailureIsNeverPresentedAsSuccess() throws {
        let rejected = try XCTUnwrap(
            try summary("plan_completed_failure_outcome").role(.reviewer)
        )
        let style = StatusStyle.routingRole(status: rejected.status, outcome: rejected.outcome)
        XCTAssertNotEqual(style.tone, .positive)
        // And the primary in the same plan still reads as the pass it was.
        let primary = try XCTUnwrap(try summary("plan_completed_failure_outcome").primary)
        XCTAssertEqual(
            StatusStyle.routingRole(status: primary.status, outcome: primary.outcome).tone,
            .positive
        )
    }

    // MARK: - Unknown values

    func testAnUnknownRoleIsRenderedUnderItsMachineName() throws {
        let summary = try summary("plan_unknown_role_status")
        XCTAssertEqual(summary.roles.map(\.role.rawValue), ["PRIMARY", "SECOND_REVIEWER"])
        let unknown = try XCTUnwrap(summary.roles.last)
        XCTAssertFalse(unknown.role.isKnown)
        // No invented label: the machine value is what is shown.
        XCTAssertEqual(L10n.routingRoleName(unknown.role), "SECOND_REVIEWER")
    }

    func testAnUnknownStatusAndOutcomeNeverReadAsSettledOrSuccessful() throws {
        let unknown = try XCTUnwrap(try summary("plan_unknown_role_status").roles.last)
        XCTAssertFalse(unknown.status.isKnown)
        XCTAssertFalse(unknown.status.isTerminal)
        XCTAssertFalse(unknown.outcome.isKnown)

        let style = StatusStyle.routingRole(status: unknown.status, outcome: unknown.outcome)
        XCTAssertEqual(style.tone, .unknown)
        XCTAssertNotEqual(style.tone, .positive)

        XCTAssertEqual(L10n.routingRoleStatusName(unknown.status), "QUARANTINED")
        XCTAssertEqual(L10n.routingOutcomeName(unknown.outcome), "PARTIAL")
    }

    // MARK: - Fixture coverage

    /// The fixtures named by the B3 brief. A missing one is a rule with no
    /// executable example behind it.
    func testEveryRequiredRoutingFixtureIsPresentAndDecodes() throws {
        let required = [
            "legacy_single_worker",
            "plan_primary_only",
            "plan_primary_reviewer",
            "plan_primary_reviewer_auditor",
            "plan_reviewer_declared_unassigned",
            "plan_reviewer_reroute",
            "plan_completed_failure_outcome",
            "plan_completed_error_outcome",
            "plan_unknown_role_status",
        ]
        for fixture in required {
            XCTAssertNoThrow(try summary(fixture), fixture)
        }
    }

    /// The fixtures are UI and contract examples. Nothing in them is evidence
    /// that a daemon has ever emitted a multi-role plan: D7 leaves open which
    /// execution-layer component writes `declared_roles`, and no daemon behavior
    /// was added to populate them.
    func testFixturesDescribeTheContractNotProductionRuntime() throws {
        let summary = try summary("plan_primary_reviewer_auditor")
        XCTAssertFalse(summary.isLegacySynthesized)
        XCTAssertEqual(summary.roles.count, 3)
    }
}
