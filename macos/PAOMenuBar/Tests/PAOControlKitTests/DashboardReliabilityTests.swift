import Foundation
import XCTest

@testable import PAOControlKit

@MainActor
final class DashboardReliabilityTests: XCTestCase {
    func testChangingSelectionClearsThePreviousInspectorImmediately() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("selection-clear")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        await store.loadTaskDetail(taskId: "t-1")
        XCTAssertEqual(store.selectedTaskDetail?.task.taskId, "t-1")

        store.selectedTaskId = "t-2"
        XCTAssertNil(store.selectedTaskDetail)
        // A Task queued by the old selection must not even fetch its detail.
        let requestsBefore = daemon.receivedRequests.count
        await store.loadTaskDetail(taskId: "t-1")
        XCTAssertEqual(daemon.receivedRequests.count, requestsBefore)
        XCTAssertNil(store.selectedTaskDetail)
    }

    func testOlderDetailResponseCannotOverwriteNewSelection() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "old detail request started")
        let release = DispatchSemaphore(value: 0)
        daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        daemon.route("GET", "/v1/tasks/t-2/detail", body: secondTaskDetailBody)
        let path = temporarySocketPath("selection-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        let oldRequest = Task { await store.loadTaskDetail(taskId: "t-1") }
        await fulfillment(of: [started], timeout: 2)
        store.selectedTaskId = "t-2"
        await store.loadTaskDetail(taskId: "t-2")
        XCTAssertEqual(store.selectedTaskDetail?.task.taskId, "t-2")

        release.signal()
        await oldRequest.value
        XCTAssertEqual(store.selectedTaskDetail?.task.taskId, "t-2")
    }

    func testOlderSameTaskResponseCannotOverwriteNewerDetail() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "older same-task request started")
        let release = DispatchSemaphore(value: 0)
        daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        let path = temporarySocketPath("same-task-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        let oldRequest = Task { await store.loadTaskDetail(taskId: "t-1") }
        await fulfillment(of: [started], timeout: 2)
        daemon.route(
            "GET", "/v1/tasks/t-1/detail",
            body: taskDetailBody.replacingOccurrences(of: "\"state_version\":2", with: "\"state_version\":3"),
            replaceExisting: true
        )
        await store.loadTaskDetail(taskId: "t-1")
        XCTAssertEqual(store.selectedTaskDetail?.task.stateVersion, 3)

        release.signal()
        await oldRequest.value
        XCTAssertEqual(store.selectedTaskDetail?.task.stateVersion, 3)
    }

    func testOldDetailErrorCannotReplaceNewSelectionSuccess() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "old failing request started")
        let release = DispatchSemaphore(value: 0)
        daemon.route(
            "GET", "/v1/tasks/t-1/detail", status: 404, body: "{\"error\":\"not_found\"}"
        ) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        daemon.route("GET", "/v1/tasks/t-2/detail", body: secondTaskDetailBody)
        let path = temporarySocketPath("detail-error-race")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        let oldRequest = Task { await store.loadTaskDetail(taskId: "t-1") }
        await fulfillment(of: [started], timeout: 2)
        store.selectedTaskId = "t-2"
        await store.loadTaskDetail(taskId: "t-2")
        release.signal()
        await oldRequest.value

        XCTAssertEqual(store.selectedTaskDetail?.task.taskId, "t-2")
        XCTAssertNil(store.lastError)
    }

    func testDeselectionInvalidatesAnInFlightDetail() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "detail request started before deselection")
        let release = DispatchSemaphore(value: 0)
        daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        let path = temporarySocketPath("detail-deselection")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        let request = Task { await store.loadTaskDetail(taskId: "t-1") }
        await fulfillment(of: [started], timeout: 2)
        store.selectedTaskId = nil
        release.signal()
        await request.value

        XCTAssertNil(store.selectedTaskDetail)
        XCTAssertNil(store.lastError)
    }

    func testDisconnectInvalidatesAnInFlightDetail() async throws {
        let daemon = TestDaemon()
        let started = expectation(description: "detail request started before disconnect")
        let release = DispatchSemaphore(value: 0)
        daemon.route("GET", "/v1/tasks/t-1/detail", body: taskDetailBody) {
            started.fulfill()
            _ = release.wait(timeout: .now() + 5)
        }
        daemon.route("GET", "/v1/health", status: 503, body: "{\"error\":\"unavailable\"}")
        let path = temporarySocketPath("detail-disconnect")
        try daemon.start(socketPath: path)
        defer { release.signal(); daemon.stop() }
        let store = manualStore(path)

        store.selectedTaskId = "t-1"
        let request = Task { await store.loadTaskDetail(taskId: "t-1") }
        await fulfillment(of: [started], timeout: 2)
        await store.refreshNow()
        let disconnectError = store.lastError
        release.signal()
        await request.value

        XCTAssertFalse(store.connection.isConnected)
        XCTAssertNil(store.selectedTaskDetail)
        XCTAssertNotNil(disconnectError)
        XCTAssertEqual(store.lastError, disconnectError)
    }

    func testQuotaRefreshRetainsLastProjectionWhenFallbackAlsoFails() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, quotaBody: quotaObservedBody)
        let path = temporarySocketPath("quota-double-failure")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)
        await store.refreshNow()
        let observed = try XCTUnwrap(store.quota)

        daemon.route(
            "POST", "/v1/quota/refresh", status: 500,
            body: "{\"error\":\"collector_failed\"}", replaceExisting: true
        )
        daemon.route(
            "GET", "/v1/quota", status: 503,
            body: "{\"error\":\"unavailable\"}", replaceExisting: true
        )
        await store.refreshQuota()

        XCTAssertEqual(store.quota, observed)
        XCTAssertEqual(store.lastQuotaRefreshError, "http_500_collector_failed")
    }

    func testMenuBarCountsAndStatusCoverTasksOutsideTheFetchedList() async throws {
        let daemon = TestDaemon()
        let rows = (0..<OrchestratorStore.taskListLimit).map { index in
            """
            {"task_id":"done-\(index)","request_id":"r-\(index)","intent":"done",\
            "state":"COMPLETED","state_version":4,"created_at":"x","updated_at":"y"}
            """
        }
        daemon.route(
            "GET", "/v1/tasks?limit=\(OrchestratorStore.taskListLimit)",
            body: "{\"tasks\":[\(rows.joined(separator: ","))],\"total\":209}"
        )
        var dashboard = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(dashboardBody.utf8)) as? [String: Any]
        )
        dashboard["counts"] = [
            "running": 2, "ready": 3, "blocked": 1, "verifying": 1,
            "verified": 2, "completed": 200, "total": 209
        ]
        daemon.route(
            "GET", "/v1/dashboard",
            body: String(decoding: try JSONSerialization.data(withJSONObject: dashboard), as: UTF8.self)
        )
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("global-counts")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = manualStore(path)
        await store.refreshNow()

        XCTAssertTrue(store.taskCollectionIsTruncated)
        XCTAssertEqual(store.tasks?.tasks.count, 200)
        let counts = store.taskCounts()
        XCTAssertEqual(counts.running, 2)
        XCTAssertEqual(counts.ready, 3)
        XCTAssertEqual(counts.blocked, 1)
        XCTAssertEqual(counts.verified, 202)
        XCTAssertEqual(store.statusSummary, .blocked)
    }

    func testGlobalCountsDriveWorkingStatusWithoutAFetchedRunningTask() {
        let counts = DashboardCountsView(
            running: 1, ready: 0, blocked: 0, verifying: 0,
            verified: 0, completed: 200, total: 201
        )
        XCTAssertEqual(
            StatusSummary.derive(
                connection: .connected, tasks: [], providers: nil, dashboardCounts: counts
            ),
            .working
        )
        XCTAssertEqual(
            StatusSummary.derive(
                connection: .disconnected(reason: .daemonNotRunning), tasks: [],
                providers: nil, dashboardCounts: counts
            ),
            .disconnected(.daemonNotRunning)
        )
    }

    private func manualStore(_ path: String) -> OrchestratorStore {
        OrchestratorStore(socketPath: path, autoStartDaemon: false, autoRefresh: false)
    }

    private var secondTaskDetailBody: String {
        taskDetailBody.replacingOccurrences(of: "t-1", with: "t-2")
    }
}
