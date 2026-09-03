import Foundation

import XCTest

@testable import PAOControlKit

/// B0: the task collection must be the authoritative one, and elapsed time must
/// keep advancing while a task runs.
///
/// Both defects were silent — the list showed ten plausible rows while the
/// counters described a larger store, and the elapsed reading looked like a
/// timer while sitting still. Neither surfaced as an error, so both are pinned
/// here rather than left to visual inspection.
final class TaskCollectionTests: XCTestCase {

    // MARK: - Task collection

    func testClientRequestsTheDaemonListLimit() {
        // Matching MAX_LIST_LIMIT keeps one request able to return everything the
        // daemon will serve; asking for more is clamped daemon-side anyway.
        XCTAssertEqual(OrchestratorStore.taskListLimit, 200)
    }

    func testTruncationIsDetectableWhenTheStoreHoldsMoreThanWasFetched() {
        let listed = TaskListView(tasks: makeTasks(count: 200), total: 512)
        XCTAssertLessThan(listed.tasks.count, listed.total)
    }

    func testACompleteCollectionIsNotReportedAsTruncated() {
        let listed = TaskListView(tasks: makeTasks(count: 12), total: 12)
        XCTAssertEqual(listed.tasks.count, listed.total)
    }

    func testCountsOverTheFullCollectionMatchTheCollection() {
        // The regression: counts were computed over every task while the list held
        // ten. Any state-filtered navigation then landed on a subset of its own
        // counter. With one collection behind both, they agree by construction.
        let tasks =
            makeTasks(count: 30, state: "COMPLETED")
            + makeTasks(count: 3, state: "RUNNING", prefix: "R")
            + makeTasks(count: 2, state: "BLOCKED", prefix: "B")
        let listed = TaskListView(tasks: tasks, total: tasks.count)

        let completed = listed.tasks.filter { $0.state == "COMPLETED" }
        XCTAssertEqual(completed.count, 30)
        XCTAssertGreaterThan(completed.count, 10, "a filter must reach past the old preview size")
    }

    func testFilteringOperatesOverEveryFetchedTask() {
        let tasks =
            makeTasks(count: 25, state: "COMPLETED")
            + makeTasks(count: 5, state: "RUNNING", prefix: "R")
        let matching = tasks.filter { MetricsFilter.matches(state: $0.state, filter: "COMPLETED") }
        XCTAssertEqual(matching.count, 25)
    }

    // MARK: - Elapsed time

    private let created = "2026-09-03T11:40:00Z"

    func testRunningTaskElapsedAdvancesWithWallClockNotLastUpdate() throws {
        // The defect: measuring to updated_at froze the reading at the moment the
        // task entered RUNNING, so a task running for an hour reported the seconds
        // between submission and dispatch.
        let updated = "2026-09-03T11:40:30Z"
        let now = TaskTiming.parseTimestamp("2026-09-03T12:40:00Z")!

        let elapsed = try XCTUnwrap(
            TaskTiming.elapsed(
                createdAt: created, updatedAt: updated, state: "RUNNING", now: now
            )
        )
        XCTAssertEqual(elapsed, 3600, accuracy: 0.5)
        XCTAssertNotEqual(elapsed, 30, "elapsed must not freeze at the last state change")
    }

    func testElapsedKeepsGrowingBetweenTwoObservations() {
        let updated = "2026-09-03T11:40:30Z"
        let first = TaskTiming.elapsed(
            createdAt: created, updatedAt: updated, state: "RUNNING",
            now: TaskTiming.parseTimestamp("2026-09-03T11:50:00Z")!
        )
        let second = TaskTiming.elapsed(
            createdAt: created, updatedAt: updated, state: "RUNNING",
            now: TaskTiming.parseTimestamp("2026-09-03T12:00:00Z")!
        )
        XCTAssertGreaterThan(second!, first!)
    }

    func testVerifyingIsStillLiveWork() throws {
        let now = TaskTiming.parseTimestamp("2026-09-03T12:00:00Z")!
        let elapsed = try XCTUnwrap(
            TaskTiming.elapsed(
                createdAt: created, updatedAt: "2026-09-03T11:45:00Z",
                state: "VERIFYING", now: now
            )
        )
        XCTAssertEqual(elapsed, 1200, accuracy: 0.5)
    }

    func testTerminalTaskElapsedFreezesAtTheMomentItStopped() throws {
        let now = TaskTiming.parseTimestamp("2026-09-03T18:00:00Z")!
        for state in ["VERIFIED", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"] {
            let elapsed = try XCTUnwrap(
                TaskTiming.elapsed(
                    createdAt: created, updatedAt: "2026-09-03T11:55:00Z",
                    state: state, now: now
                )
            )
            XCTAssertEqual(elapsed, 900, accuracy: 0.5, "\(state) must not keep counting")
        }
    }

    func testClockSkewNeverProducesNegativeElapsed() {
        // A daemon clock slightly ahead of the client must not render as a
        // negative age.
        let now = TaskTiming.parseTimestamp("2026-09-03T11:39:00Z")!
        let elapsed = TaskTiming.elapsed(
            createdAt: created, updatedAt: created, state: "RUNNING", now: now
        )
        XCTAssertEqual(elapsed, 0)
    }

    func testUnparseableTimestampYieldsNilRatherThanZero() {
        // Nil becomes "unknown" in the view. Zero would read as a real duration.
        XCTAssertNil(
            TaskTiming.elapsed(
                createdAt: "not-a-date", updatedAt: created, state: "RUNNING"
            )
        )
        XCTAssertNil(
            TaskTiming.elapsed(
                createdAt: created, updatedAt: "not-a-date", state: "COMPLETED"
            )
        )
    }

    func testTimestampsParseWithAndWithoutFractionalSeconds() {
        XCTAssertNotNil(TaskTiming.parseTimestamp("2026-09-03T11:40:00Z"))
        XCTAssertNotNil(TaskTiming.parseTimestamp("2026-09-03T11:40:00.123Z"))
        XCTAssertNil(TaskTiming.parseTimestamp(""))
    }

    func testTerminalStateSetMatchesTheStatesThatStopProgressing() {
        XCTAssertTrue(TaskTiming.isTerminal(state: "COMPLETED"))
        XCTAssertTrue(TaskTiming.isTerminal(state: "CANCELLED"))
        XCTAssertFalse(TaskTiming.isTerminal(state: "RUNNING"))
        XCTAssertFalse(TaskTiming.isTerminal(state: "VERIFYING"))
        XCTAssertFalse(TaskTiming.isTerminal(state: "SUBMITTED"))
    }

    // MARK: - Helpers

    private func makeTasks(
        count: Int, state: String = "COMPLETED", prefix: String = "T"
    ) -> [TaskView] {
        (0..<count).map { index in
            let json = """
                {"task_id":"\(prefix)-\(index)","request_id":"rq-\(index)",
                 "intent":"task \(index)","project_id":"p1","base_sha":null,
                 "working_subpath":null,"state":"\(state)","state_version":1,
                 "created_at":"2026-09-03T11:40:00Z","updated_at":"2026-09-03T11:41:00Z"}
                """
            // swiftlint:disable:next force_try
            return try! JSONDecoder().decode(TaskView.self, from: Data(json.utf8))
        }
    }
}

/// Store-level proof that the task collection is read from the authoritative
/// list endpoint rather than the dashboard's ten-item preview.
final class TaskCollectionStoreTests: XCTestCase {

    /// 25 tasks — more than the dashboard preview ever carried, so the two
    /// sources are distinguishable by count alone.
    private var largeTaskListBody: String {
        let rows = (0..<25).map { index in
            """
            {"task_id":"t-\(index)","request_id":"r-\(index)","intent":"task \(index)",\
            "state":"COMPLETED","state_version":1,\
            "created_at":"2026-08-31T00:00:00Z","updated_at":"2026-08-31T00:01:00Z"}
            """
        }
        return "{\"tasks\":[\(rows.joined(separator: ","))],\"total\":25}"
    }

    @MainActor
    func testTaskCollectionComesFromTheListEndpointNotTheDashboardPreview() async throws {
        let daemon = TestDaemon()
        // Registered first: TestDaemon resolves the first matching route, so this
        // shadows the standard two-task body for the limit the store requests.
        daemon.route(
            "GET", "/v1/tasks?limit=\(OrchestratorStore.taskListLimit)",
            body: largeTaskListBody
        )
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("tasklist")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        XCTAssertEqual(store.connection, .connected)
        // The dashboard fixture's recent_tasks carries two rows. Reading 25 proves
        // the collection is the authoritative list, not the preview.
        XCTAssertEqual(store.tasks?.tasks.count, 25)
        XCTAssertEqual(store.tasks?.total, 25)
        XCTAssertFalse(store.taskCollectionIsTruncated)

        // A state filter now reaches every fetched task rather than the first ten.
        let completed = (store.tasks?.tasks ?? []).filter {
            MetricsFilter.matches(state: $0.state, filter: "COMPLETED")
        }
        XCTAssertEqual(completed.count, 25)
    }

    @MainActor
    func testTruncationIsReportedWhenTheDaemonHoldsMoreThanTheLimit() async throws {
        let daemon = TestDaemon()
        let body = largeTaskListBody.replacingOccurrences(
            of: "\"total\":25", with: "\"total\":900"
        )
        daemon.route(
            "GET", "/v1/tasks?limit=\(OrchestratorStore.taskListLimit)", body: body
        )
        registerStandardRoutes(daemon)
        let path = temporarySocketPath("tasktrunc")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        // Beyond the daemon's list limit the collection really is partial. That
        // must be visible rather than inferred, so B3 can state the scope instead
        // of filtering a subset silently.
        XCTAssertTrue(store.taskCollectionIsTruncated)
        XCTAssertEqual(store.tasks?.tasks.count, 25)
        XCTAssertEqual(store.tasks?.total, 900)
    }

    @MainActor
    func testFirstLoadFallsBackToThePreviewWhenTheListEndpointIsUnavailable() async throws {
        // A daemon with no list route at all: only health and dashboard answer.
        let bare = TestDaemon()
        bare.route("GET", "/v1/health", body: healthBody)
        bare.route("GET", "/v1/dashboard", body: dashboardBody)
        let path = temporarySocketPath("tasklistdown")
        try bare.start(socketPath: path)
        defer { bare.stop() }

        let store = OrchestratorStore(socketPath: path, idFactory: { "fixed" })
        await store.refreshNow()

        // Degraded, not empty: the preview is better than a blank list, and
        // `total` keeps the shortfall detectable rather than presenting the
        // preview as the whole store.
        XCTAssertEqual(store.tasks?.tasks.count, 2)
        XCTAssertEqual(store.tasks?.total, 2)
    }
}
