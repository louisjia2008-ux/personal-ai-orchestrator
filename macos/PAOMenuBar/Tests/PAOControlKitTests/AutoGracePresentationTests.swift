import Foundation

import XCTest

@testable import PAOControlKit

/// M1 WP5b §24 PRESENTATION matrix + pure policy gates. The countdown
/// model must be deterministic and side-effect free: local expiry is a
/// presentation state that asks for refresh, never an authority claim.
final class AutoGracePresentationTests: XCTestCase {

    private func task(
        state: String,
        version: Int = 5,
        deadline: String? = nil,
        acked: String? = nil,
        decision: String? = "auto-t-1-v4"
    ) -> TaskView {
        TaskView(
            taskId: "t-1",
            requestId: "r-1",
            intent: "fix bug",
            state: state,
            stateVersion: version,
            createdAt: "2026-08-31T00:00:00Z",
            updatedAt: "2026-08-31T00:05:00Z",
            autoDecisionId: decision,
            autoGraceDeadlineAt: deadline,
            autoAckedAt: acked,
            autoReason: "AUTO_PLANNED{auto-t-1-v4,m3-sub}"
        )
    }

    private let now = ISO8601DateFormatter().date(from: "2026-08-31T00:06:00Z")!

    private func iso(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        return formatter.string(from: date)
    }

    // PRESENTATION-1
    func testAutoPlannedHasNoFakeCountdown() {
        let p = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_PLANNED"), now: now, frozenTargetId: "m3-sub"
        )
        XCTAssertEqual(p.phase, .planned)
        XCTAssertNil(p.deadline)
        XCTAssertNil(p.secondsRemaining)
        XCTAssertFalse(p.canAck)
        XCTAssertTrue(p.canVeto)
        XCTAssertFalse(p.canDispatchNow)
        XCTAssertEqual(p.frozenTargetId, "m3-sub")
    }

    // PRESENTATION-2
    func testAutoGraceWithoutDeadlineRequiresAck() {
        let p = AutoSupervisionPresentation.derive(task: task(state: "AUTO_GRACE"), now: now)
        XCTAssertEqual(p.phase, .waitingAck)
        XCTAssertTrue(p.requiresAck)
        XCTAssertTrue(p.canAck)
        XCTAssertTrue(p.canVeto)
        XCTAssertTrue(p.canDispatchNow)
    }

    // PRESENTATION-3
    func testFutureDeadlineCalculatesRemaining() {
        let p = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_GRACE", deadline: iso(now.addingTimeInterval(120))),
            now: now
        )
        XCTAssertEqual(p.phase, .countingDown)
        XCTAssertEqual(p.secondsRemaining, 120)
        XCTAssertTrue(p.canDispatchNow)
        XCTAssertFalse(p.canAck, "an existing deadline must not offer ACK again")
    }

    // PRESENTATION-4
    func testDeadlineExactlyNowGivesZero() {
        let p = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_GRACE", deadline: iso(now)), now: now
        )
        XCTAssertEqual(p.secondsRemaining, 0)
        XCTAssertTrue(p.isExpiredLocally)
    }

    // PRESENTATION-5
    func testPastDeadlineStaysZeroNeverNegative() {
        let p = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_GRACE", deadline: iso(now.addingTimeInterval(-90))),
            now: now
        )
        XCTAssertEqual(p.secondsRemaining, 0)
        XCTAssertGreaterThanOrEqual(p.secondsRemaining ?? -1, 0)
        XCTAssertTrue(p.isExpiredLocally)
    }

    // PRESENTATION-6
    func testLocalExpiryNeverClaimsRunning() {
        let task = task(state: "AUTO_GRACE", deadline: iso(now.addingTimeInterval(-1)))
        let p = AutoSupervisionPresentation.derive(task: task, now: now)
        // The authoritative state is untouched: still AUTO_GRACE. The
        // presentation only says "dispatch confirmation pending".
        XCTAssertEqual(task.state, "AUTO_GRACE")
        XCTAssertEqual(p.phase, .expiredRefreshing)
        XCTAssertFalse(p.canDispatchNow, "expired window must not race the daemon's dispatch")
        XCTAssertTrue(p.canVeto, "the lifecycle is still live; veto remains legal")
    }

    func testNonAutoStatesAreInactive() {
        for state in ["READY", "RUNNING", "BLOCKED", "VERIFIED", "SUBMITTED"] {
            XCTAssertEqual(
                AutoSupervisionPresentation.derive(task: task(state: state), now: now).phase,
                .inactive,
                state
            )
        }
    }

    func testClockTextFormatsMinutesSecondsAndHours() {
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 102), "1:42")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 67), "1:07")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 59), "0:59")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 0), "0:00")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 3600), "1:00:00")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 7322), "2:02:02")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: -5), "0:00")
    }

    func testUnknownTargetIsReportedNotGuessed() {
        let p = AutoSupervisionPresentation.derive(task: task(state: "AUTO_PLANNED"), now: now)
        XCTAssertNil(p.frozenTargetId, "absent routing truth must surface as unknown, never a guess")
    }

    // UI-1
    func testManualDispatchIsNotOfferedForAutoStates() {
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "AUTO_GRACE"))
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "AUTO_PLANNED"))
        XCTAssertTrue(ManualDispatchPolicy.isOffered(state: "SUBMITTED"))
        XCTAssertTrue(ManualDispatchPolicy.isOffered(state: "READY"))
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "RUNNING"))
    }

    // MENU-1
    func testMenuBarStopIsOfferedOnlyInSupervisedAuto() {
        XCTAssertTrue(MenuBarAutoStop.shouldOffer(currentMode: "SUPERVISED_AUTO"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: "MANUAL"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: "ACTIVE"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: nil))
    }

    // MODE-3
    func testActiveIsDisplayedButNotSelectablyOffered() {
        XCTAssertFalse(AutomationModeCatalog.selectable.contains("ACTIVE"))
        XCTAssertEqual(AutomationModeCatalog.selectable, ["MANUAL", "SUPERVISED_AUTO"])
        XCTAssertTrue(AutomationModeCatalog.displayOnly.contains("ACTIVE"))
    }

    /// §5 identifier contract: the lifecycle fixture decodes with the
    /// AUTO fields intact, and the auto id is the lifecycle id shape
    /// (`auto-{task_id}-v{n}`), not a `route-*` routing decision id.
    func testAutoFieldsDecodeWithLifecycleIdSemantics() throws {
        let data = Data(autoGraceTaskBody.utf8)
        let view = try JSONDecoder().decode(TaskView.self, from: data)
        XCTAssertEqual(view.state, "AUTO_GRACE")
        XCTAssertEqual(view.autoDecisionId, "auto-t-1-v4")
        XCTAssertTrue(view.autoDecisionId!.hasPrefix("auto-t-1-v"))
        XCTAssertFalse(view.autoDecisionId!.hasPrefix("route-"))
        XCTAssertNil(view.autoGraceDeadlineAt)
        XCTAssertNil(view.autoAckedAt)
        XCTAssertEqual(view.autoReason, "AUTO_PLANNED{auto-t-1-v4,m3-sub}")
    }

    // MARK: - MODE-UNKNOWN (closeout P1)

    /// MODE-UNKNOWN-1: missing settings stay unknown; they must never
    /// resolve to MANUAL.
    func testMissingSchedulingSettingsAreUnknownNotManual() {
        XCTAssertNil(AutomationModePresentation.currentMode(from: nil))
        XCTAssertNotEqual(
            AutomationModePresentation.currentMode(from: nil),
            Optional("MANUAL")
        )
    }

    /// MODE-UNKNOWN-2: mode selection is not offered until the
    /// authoritative mode exists.
    func testModeSelectionRequiresAuthoritativeTruth() {
        XCTAssertFalse(AutomationModePresentation.canSelectModes(currentMode: nil))
        XCTAssertTrue(AutomationModePresentation.canSelectModes(currentMode: "MANUAL"))
        XCTAssertTrue(AutomationModePresentation.canSelectModes(currentMode: "SUPERVISED_AUTO"))
    }
}
