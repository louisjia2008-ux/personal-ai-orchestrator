import Foundation

import XCTest

@testable import PAOControlKit

@MainActor
final class OrchestratorStoreTests: XCTestCase {
    func testQuickSubmitUsesIdempotentRequestShapeAndDuplicateGuard() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("submit")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        var generated = 0
        let store = OrchestratorStore(socketPath: path, idFactory: {
            generated += 1
            return "id\(generated)"
        })
        await store.quickSubmit(intent: "demo intent")
        XCTAssertEqual(store.lastSubmittedTaskId, "menubar-abc")
        XCTAssertEqual(store.submitNotice?.contains("Submitted task menubar-abc"), true)

        // Duplicate guard: identical intent inside the window must not hit the daemon again.
        let postsBefore = daemon.receivedRequests.filter { $0.method == "POST" && $0.path == "/v1/tasks" }.count
        await store.quickSubmit(intent: "demo intent")
        let postsAfter = daemon.receivedRequests.filter { $0.method == "POST" && $0.path == "/v1/tasks" }.count
        XCTAssertEqual(postsBefore, postsAfter)
        XCTAssertEqual(postsAfter, 1)
        XCTAssertTrue(store.submitNotice?.contains("Duplicate submission blocked") ?? false)

        // A different intent submits again with a fresh idempotent request pair.
        await store.quickSubmit(intent: "another intent")
        let postsFinal = daemon.receivedRequests.filter { $0.method == "POST" && $0.path == "/v1/tasks" }.count
        XCTAssertEqual(postsFinal, 2)
        let bodies = daemon.receivedRequests
            .filter { $0.method == "POST" && $0.path == "/v1/tasks" }
            .map(\.body)
        XCTAssertTrue(bodies.allSatisfy { $0.contains("menubar-req-") })
    }

    func testCancelSurfacesRunningConflictVerbatim() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route(
            "POST",
            "/v1/tasks/t-1/cancel",
            status: 409,
            body: "{\"error\":\"running_task_cancellation_requires_execution_supervisor\"}"
        )
        let path = temporarySocketPath("cancel")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.cancel(taskId: "t-1")
        XCTAssertEqual(
            store.cancellationNotice,
            "Task t-1 is RUNNING: cancellation requires the host execution supervisor (409)."
        )
    }

    func testCancelSuccessRefreshesFromAuthoritativeState() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route(
            "POST",
            "/v1/tasks/t-2/cancel",
            status: 200,
            body: "{\"task\":{\"task_id\":\"t-2\",\"request_id\":\"r-2\",\"intent\":\"add test\",\"state\":\"CANCELLED\",\"state_version\":4,\"created_at\":\"x\",\"updated_at\":\"y\"},\"cancelled_now\":true}"
        )
        let path = temporarySocketPath("cancel-ok")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.cancel(taskId: "t-2")
        XCTAssertEqual(store.cancellationNotice, "Task t-2 cancelled.")
    }

    func testRefreshTransitionsConnectionAndLoadsAuthoritativeState() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("refresh")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()
        XCTAssertEqual(store.connection, .connected)
        XCTAssertEqual(store.tasks?.total, 2)
        XCTAssertEqual(store.activeStatus?.productionActive, "DISABLED_BY_DESIGN")
        XCTAssertNotNil(store.providers)
        XCTAssertEqual(store.statusSummary, .blocked)

        let counts = store.taskCounts()
        XCTAssertEqual(counts.running, 1)
        XCTAssertEqual(counts.blocked, 1)
        XCTAssertEqual(counts.verified, 0)
    }

    func testDaemonOutageDiscardsStaleAuthoritativeState() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("outage")
        try daemon.start(socketPath: path)

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()
        XCTAssertEqual(store.connection, .connected)

        daemon.stop()
        unlink(path)
        await store.refreshNow()
        XCTAssertFalse(store.connection.isConnected)
        // Assumptions are discarded: no stale task/provider/ACTIVE data remains.
        XCTAssertNil(store.tasks)
        XCTAssertNil(store.providers)
        XCTAssertNil(store.activeStatus)
        XCTAssertEqual(store.statusSummary, .disconnected(.daemonNotRunning))
    }

    func testAPIVersionMismatchIsAConnectionState() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/health", body: "{\"status\":\"ok\",\"api_version\":\"v9\"}")
        let path = temporarySocketPath("version-mismatch")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()
        XCTAssertEqual(store.connection, .disconnected(reason: .apiVersionMismatch(version: "v9")))
    }
}
