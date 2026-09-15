import Foundation
import XCTest

@testable import PAOControlKit

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
        ISO8601DateFormatter().string(from: date)
    }

    func testAutoPlannedHasNoFakeCountdown() {
        let presentation = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_PLANNED"),
            now: now,
            frozenTargetId: "m3-sub"
        )
        XCTAssertEqual(presentation.phase, .planned)
        XCTAssertNil(presentation.deadline)
        XCTAssertNil(presentation.secondsRemaining)
        XCTAssertFalse(presentation.canAck)
        XCTAssertTrue(presentation.canVeto)
        XCTAssertFalse(presentation.canDispatchNow)
        XCTAssertEqual(presentation.frozenTargetId, "m3-sub")
    }

    func testAutoGraceWithoutDeadlineRequiresAck() {
        let presentation = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_GRACE"), now: now
        )
        XCTAssertEqual(presentation.phase, .waitingAck)
        XCTAssertTrue(presentation.requiresAck)
        XCTAssertTrue(presentation.canAck)
        XCTAssertTrue(presentation.canVeto)
        XCTAssertTrue(presentation.canDispatchNow)
    }

    func testFutureDeadlineCalculatesRemaining() {
        let presentation = AutoSupervisionPresentation.derive(
            task: task(
                state: "AUTO_GRACE",
                deadline: iso(now.addingTimeInterval(120))
            ),
            now: now
        )
        XCTAssertEqual(presentation.phase, .countingDown)
        XCTAssertEqual(presentation.secondsRemaining, 120)
        XCTAssertTrue(presentation.canDispatchNow)
        XCTAssertFalse(presentation.canAck)
    }

    func testPastDeadlineStaysZeroAndNeverClaimsRunning() {
        let authoritativeTask = task(
            state: "AUTO_GRACE",
            deadline: iso(now.addingTimeInterval(-1))
        )
        let presentation = AutoSupervisionPresentation.derive(
            task: authoritativeTask, now: now
        )
        XCTAssertEqual(authoritativeTask.state, "AUTO_GRACE")
        XCTAssertEqual(presentation.phase, .expiredRefreshing)
        XCTAssertEqual(presentation.secondsRemaining, 0)
        XCTAssertFalse(presentation.canDispatchNow)
        XCTAssertTrue(presentation.canVeto)
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

    func testClockTextFormatting() {
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 102), "1:42")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 59), "0:59")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 0), "0:00")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: 3600), "1:00:00")
        XCTAssertEqual(AutoSupervisionPresentation.clockText(secondsRemaining: -5), "0:00")
    }

    func testUnknownTargetIsNotGuessed() {
        let presentation = AutoSupervisionPresentation.derive(
            task: task(state: "AUTO_PLANNED"), now: now
        )
        XCTAssertNil(presentation.frozenTargetId)
    }

    func testManualDispatchIsNotOfferedForAutoStates() {
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "AUTO_GRACE"))
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "AUTO_PLANNED"))
        XCTAssertTrue(ManualDispatchPolicy.isOffered(state: "SUBMITTED"))
        XCTAssertTrue(ManualDispatchPolicy.isOffered(state: "READY"))
        XCTAssertFalse(ManualDispatchPolicy.isOffered(state: "RUNNING"))
    }

    func testMenuBarStopIsOfferedOnlyInSupervisedAuto() {
        XCTAssertTrue(MenuBarAutoStop.shouldOffer(currentMode: "SUPERVISED_AUTO"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: "MANUAL"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: "ACTIVE"))
        XCTAssertFalse(MenuBarAutoStop.shouldOffer(currentMode: nil))
    }

    func testActiveIsDisplayOnly() {
        XCTAssertEqual(AutomationModeCatalog.selectable, ["MANUAL", "SUPERVISED_AUTO"])
        XCTAssertFalse(AutomationModeCatalog.selectable.contains("ACTIVE"))
        XCTAssertTrue(AutomationModeCatalog.displayOnly.contains("ACTIVE"))
    }

    func testMissingSchedulingSettingsRemainUnknown() {
        XCTAssertNil(AutomationModePresentation.currentMode(from: nil))
        XCTAssertFalse(AutomationModePresentation.canSelectModes(currentMode: nil))
        XCTAssertTrue(AutomationModePresentation.canSelectModes(currentMode: "MANUAL"))
    }
}
