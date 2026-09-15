import XCTest

@testable import PAOControlKit

final class ActivityPresentationTests: XCTestCase {
    func testSafetyTermsOutrankGenericTaskIdentity() {
        XCTAssertEqual(
            ActivityPresentation.category(
                eventType: "task_permission_denied",
                taskId: "task-1"
            ),
            .safety
        )
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "auto_veto_accepted", taskId: "task-2"),
            .safety
        )
    }

    func testQuotaAndRoutingHaveDedicatedBuckets() {
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "quota_window_exhausted", taskId: nil),
            .quota
        )
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "dispatch_reserved", taskId: "task-1"),
            .routing
        )
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "routing_decision_recorded", taskId: nil),
            .routing
        )
    }

    func testTaskIdentityProvidesTaskFallback() {
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "state_changed", taskId: "task-1"),
            .task
        )
        XCTAssertEqual(
            ActivityPresentation.category(eventType: "verification_finished", taskId: nil),
            .task
        )
    }

    func testUnknownEventRemainsOnlyInAllRatherThanBeingInventedIntoBucket() {
        XCTAssertNil(ActivityPresentation.category(eventType: "daemon_heartbeat", taskId: nil))
        XCTAssertTrue(
            ActivityPresentation.matches(
                eventType: "daemon_heartbeat",
                taskId: nil,
                category: .all
            )
        )
        XCTAssertFalse(
            ActivityPresentation.matches(
                eventType: "daemon_heartbeat",
                taskId: nil,
                category: .task
            )
        )
    }

    func testSearchMatchesSummaryRawTypeAndTaskId() {
        XCTAssertTrue(
            ActivityPresentation.matchesQuery(
                summary: "MiniMax worker finished",
                eventType: "worker_finished",
                taskId: "task-123",
                query: "minimax"
            )
        )
        XCTAssertTrue(
            ActivityPresentation.matchesQuery(
                summary: "MiniMax worker finished",
                eventType: "worker_finished",
                taskId: "task-123",
                query: "WORKER_FINISHED"
            )
        )
        XCTAssertTrue(
            ActivityPresentation.matchesQuery(
                summary: "MiniMax worker finished",
                eventType: "worker_finished",
                taskId: "task-123",
                query: "123"
            )
        )
        XCTAssertFalse(
            ActivityPresentation.matchesQuery(
                summary: "MiniMax worker finished",
                eventType: "worker_finished",
                taskId: "task-123",
                query: "glm"
            )
        )
    }
}
