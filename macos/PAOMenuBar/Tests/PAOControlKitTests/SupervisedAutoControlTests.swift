import Foundation

import XCTest

@testable import PAOControlKit

/// M1 WP5b §24 CLIENT / MODE / PROJECT / RACE matrix. Every case pins
/// the wire body the typed client actually sends against the frozen
/// backend contract, through the same UDS transport the app uses.
@MainActor
final class SupervisedAutoControlTests: XCTestCase {

    private func makeStore(_ daemon: TestDaemon, _ name: String) async throws -> OrchestratorStore {
        let path = temporarySocketPath(name)
        try daemon.start(socketPath: path)
        return OrchestratorStore(socketPath: path, idFactory: { "fixed" })
    }

    private func bodies(
        _ daemon: TestDaemon, method: String, path: String
    ) -> [String] {
        daemon.receivedRequests
            .filter { $0.method == method && $0.path == path }
            .map(\.body)
    }

    /// Parse a request body as JSON so assertions are structural, not
    /// substring matches.
    private func json(_ body: String) throws -> [String: Any] {
        let object = try JSONSerialization.jsonObject(with: Data(body.utf8))
        return try XCTUnwrap(object as? [String: Any])
    }

    // MARK: - CLIENT

    // CLIENT-1
    func testAutoAckEncodesExactStateVersion() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "auto-ack")
        defer { daemon.stop() }

        await store.autoAck(taskId: "t-1")

        let body = try XCTUnwrap(bodies(daemon, method: "POST", path: "/v1/tasks/t-1/auto/ack").last)
        let payload = try json(body)
        XCTAssertEqual(payload["task_state_version"] as? Int, 5)
        XCTAssertEqual(payload.count, 1, "ACK carries exactly the version, nothing else")
        XCTAssertEqual(store.autoControlNotice, .acknowledged(taskId: "t-1"))
    }

    // CLIENT-2
    func testAutoVetoEncodesRequestIdAndStateVersion() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "auto-veto")
        defer { daemon.stop() }

        await store.autoVeto(taskId: "t-1")

        let body = try XCTUnwrap(bodies(daemon, method: "POST", path: "/v1/tasks/t-1/auto/veto").last)
        let payload = try json(body)
        XCTAssertEqual(payload["request_id"] as? String, "veto-fixed")
        XCTAssertEqual(payload["task_state_version"] as? Int, 5)
        XCTAssertEqual(payload.count, 2)
        XCTAssertEqual(store.autoControlNotice, .vetoed(taskId: "t-1"))
    }

    // CLIENT-3
    func testAutoDispatchNowEncodesStateVersion() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "auto-dispatch")
        defer { daemon.stop() }

        await store.autoDispatchNow(taskId: "t-1")

        let body = try XCTUnwrap(
            bodies(daemon, method: "POST", path: "/v1/tasks/t-1/auto/dispatch-now").last
        )
        let payload = try json(body)
        XCTAssertEqual(payload["task_state_version"] as? Int, 5)
        XCTAssertEqual(payload.count, 1)
        XCTAssertEqual(
            store.autoControlNotice,
            .dispatchRequested(
                taskId: "t-1",
                dispatchId: "supervised-auto-dispatch-auto-t-1-v4",
                status: "DISPATCHING"
            )
        )
    }

    // MARK: - MODE

    // MODE-1
    func testSetSchedulingModePreservesAuthoritativeDefaultPolicy() async throws {
        let daemon = TestDaemon()
        // The daemon's authoritative policy is QUALITY_FIRST — any PUT
        // that rewrites it to a client default (e.g. BALANCED) fails here.
        registerStandardRoutes(daemon, schedulingBody: schedulingSettingsSupervisedAutoBody)
        let store = try await makeStore(daemon, "mode-preserve")
        defer { daemon.stop() }

        await store.setSchedulingMode("MANUAL")

        let body = try XCTUnwrap(
            bodies(daemon, method: "PUT", path: "/v1/settings/scheduling").last
        )
        let payload = try json(body)
        XCTAssertEqual(payload["mode"] as? String, "MANUAL")
        XCTAssertEqual(payload["default_scheduling_policy"] as? String, "QUALITY_FIRST")
        XCTAssertEqual(payload.count, 2)
        // Success truth comes from the PUT's returned settings (the
        // canned daemon's GET does not echo the change).
        XCTAssertEqual(store.autoControlNotice, .schedulingModeChanged(mode: "MANUAL"))
    }

    // MODE-2
    func testEmergencyStopSendsDaemonModePutToManual() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, schedulingBody: schedulingSettingsSupervisedAutoBody)
        let store = try await makeStore(daemon, "emergency-stop")
        defer { daemon.stop() }

        await store.emergencyStopSupervisedAuto()

        let body = try XCTUnwrap(
            bodies(daemon, method: "PUT", path: "/v1/settings/scheduling").last
        )
        let payload = try json(body)
        XCTAssertEqual(payload["mode"] as? String, "MANUAL")
        XCTAssertEqual(payload["default_scheduling_policy"] as? String, "QUALITY_FIRST")
        // Success is reported only because the daemon's returned
        // settings actually confirmed MANUAL.
        XCTAssertEqual(store.autoControlNotice, .supervisedAutoStopped)
    }

    func testEmergencyStopFailureIsNotReportedAsSuccess() async throws {
        let daemon = TestDaemon()
        // Refuse the mode PUT: production gate or any daemon-side denial.
        daemon.route(
            "PUT", "/v1/settings/scheduling",
            status: 409, body: "{\"error\":\"production_active_not_authorized\"}"
        )
        registerStandardRoutes(daemon, schedulingBody: schedulingSettingsSupervisedAutoBody)
        let store = try await makeStore(daemon, "emergency-stop-fail")
        defer { daemon.stop() }

        await store.emergencyStopSupervisedAuto()

        XCTAssertNotEqual(store.autoControlNotice, .supervisedAutoStopped)
        guard case .failed = store.autoControlNotice else {
            return XCTFail("expected a sanitized failure, got \(String(describing: store.autoControlNotice))")
        }
    }

    // MARK: - PROJECT

    // PROJECT-1
    func testChangingSupervisedAutoAllowedPreservesSiblings() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, projectsListBody: projectAutoSettingsListBody)
        let store = try await makeStore(daemon, "project-allow")
        defer { daemon.stop() }

        await store.setProjectAutoSettings(projectId: "project-fixture", supervisedAutoAllowed: false)

        let body = try XCTUnwrap(
            bodies(daemon, method: "PUT", path: "/v1/projects/project-fixture/settings").last
        )
        let payload = try json(body)
        XCTAssertEqual(payload["supervised_auto_allowed"] as? Bool, false)
        XCTAssertEqual(payload["unattended_allowed"] as? Bool, true)
        XCTAssertEqual(payload["grace_seconds"] as? Int, 300)
        XCTAssertEqual(payload.count, 3, "the tuple is complete; no extra client fields")
        XCTAssertEqual(
            store.autoControlNotice, .projectAutoSettingsSaved(projectId: "project-fixture")
        )
    }

    // PROJECT-2
    func testChangingUnattendedAllowedPreservesSiblings() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, projectsListBody: projectAutoSettingsListBody)
        let store = try await makeStore(daemon, "project-unattended")
        defer { daemon.stop() }

        await store.setProjectAutoSettings(projectId: "project-fixture", unattendedAllowed: false)

        let payload = try json(try XCTUnwrap(
            bodies(daemon, method: "PUT", path: "/v1/projects/project-fixture/settings").last
        ))
        XCTAssertEqual(payload["supervised_auto_allowed"] as? Bool, true)
        XCTAssertEqual(payload["unattended_allowed"] as? Bool, false)
        XCTAssertEqual(payload["grace_seconds"] as? Int, 300)
    }

    // PROJECT-3
    func testChangingGraceSecondsPreservesSiblings() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon, projectsListBody: projectAutoSettingsListBody)
        let store = try await makeStore(daemon, "project-grace")
        defer { daemon.stop() }

        await store.setProjectAutoSettings(projectId: "project-fixture", graceSeconds: 600)

        let payload = try json(try XCTUnwrap(
            bodies(daemon, method: "PUT", path: "/v1/projects/project-fixture/settings").last
        ))
        XCTAssertEqual(payload["supervised_auto_allowed"] as? Bool, true)
        XCTAssertEqual(payload["unattended_allowed"] as? Bool, true)
        XCTAssertEqual(payload["grace_seconds"] as? Int, 600)
    }

    func testInvalidGraceSecondsSurfacesSanitizedDaemonCode() async throws {
        let daemon = TestDaemon()
        // First-registered wins: the canned 400 must shadow the
        // standard 200 settings route.
        daemon.route(
            "PUT", "/v1/projects/project-fixture/settings",
            status: 400, body: "{\"error\":\"invalid_grace_seconds\"}"
        )
        registerStandardRoutes(daemon, projectsListBody: projectAutoSettingsListBody)
        let store = try await makeStore(daemon, "project-grace-invalid")
        defer { daemon.stop() }

        await store.setProjectAutoSettings(projectId: "project-fixture", graceSeconds: 99_999)

        XCTAssertEqual(
            store.autoControlNotice,
            .blocked(scope: .project("project-fixture"), code: "invalid_grace_seconds")
        )
    }

    // MARK: - RACE

    // RACE-1
    func testStale409ProducesNoFalseSuccessAndTriggersRefresh() async throws {
        let daemon = TestDaemon()
        // First-registered wins: the canned 409 must shadow the
        // standard 200 ack route.
        daemon.route(
            "POST", "/v1/tasks/t-1/auto/ack",
            status: 409, body: staleAutoStateBody()
        )
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "race-stale")
        defer { daemon.stop() }

        await store.autoAck(taskId: "t-1")

        // No false success...
        XCTAssertNotEqual(store.autoControlNotice, .acknowledged(taskId: "t-1"))
        guard case .staleState(let taskId) = store.autoControlNotice else {
            return XCTFail(
                "expected staleState, got \(String(describing: store.autoControlNotice))"
            )
        }
        XCTAssertEqual(taskId, "t-1")

        // ...and an authoritative reload after the refusal: the failed
        // POST is followed by a task-detail GET.
        let requests = daemon.receivedRequests
        let refused = try XCTUnwrap(
            requests.lastIndex { $0.method == "POST" && $0.path == "/v1/tasks/t-1/auto/ack" }
        )
        let refreshed = requests[refused...].contains {
            $0.method == "GET" && $0.path == "/v1/tasks/t-1/detail"
        }
        XCTAssertTrue(refreshed, "a stale conflict must be answered with an authoritative reload")
    }

    func testInvalidAutoState409IsAlsoStaleStateNotCrash() async throws {
        let daemon = TestDaemon()
        daemon.route(
            "POST", "/v1/tasks/t-1/auto/dispatch-now",
            status: 409, body: invalidAutoStateBody()
        )
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "race-invalid-state")
        defer { daemon.stop() }

        await store.autoDispatchNow(taskId: "t-1")

        guard case .staleState = store.autoControlNotice else {
            return XCTFail(
                "expected staleState, got \(String(describing: store.autoControlNotice))"
            )
        }
    }

    func testDuplicateAutoAcksCoalesceIntoOneRequest() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        let store = try await makeStore(daemon, "ack-coalesce")
        defer { daemon.stop() }

        // Two clicks before the first answers: the in-flight guard must
        // drop the second rather than racing it.
        async let first: Void = store.autoAck(taskId: "t-1")
        async let second: Void = store.autoAck(taskId: "t-1")
        _ = await (first, second)

        XCTAssertEqual(
            bodies(daemon, method: "POST", path: "/v1/tasks/t-1/auto/ack").count, 1
        )
    }

    // MARK: - EMERGENCY-STOP-RACE (closeout P0)

    /// EMERGENCY-STOP-RACE-1 / MODE-4: an old successful MANUAL mode
    /// notice plus a scheduling mutation parked in-flight must not let
    /// a coalesced emergency stop fabricate success.
    func testCoalescedEmergencyStopCannotFabricateSuccessFromStaleNotice() async throws {
        let daemon = TestDaemon()
        // The holdable PUT shadows the standard route (first match wins)
        // but answers with the same canned manual settings.
        daemon.route(
            "PUT", "/v1/settings/scheduling",
            status: 200, body: schedulingSettingsManualBody, hold: true
        )
        registerStandardRoutes(daemon, schedulingBody: schedulingSettingsSupervisedAutoBody)
        let store = try await makeStore(daemon, "stop-race")
        defer { daemon.stop() }

        // Phase 1: a real, completed MANUAL mode change leaves a
        // successful scheduling notice behind — the stale trap.
        await store.setSchedulingMode("MANUAL")
        XCTAssertEqual(store.autoControlNotice, .schedulingModeChanged(mode: "MANUAL"))

        // Phase 2: a new scheduling mutation acquires the in-flight
        // key and parks inside its PUT.
        daemon.armHold("PUT", "/v1/settings/scheduling")
        let inFlight = Task { await store.setSchedulingMode("MANUAL") }
        // Deterministic phase boundary: the parked mutation is provably
        // inside its own PUT (the 2nd this daemon has seen), so the
        // in-flight key is certainly held before the stop is invoked.
        await daemon.waitForRequestCount("PUT", "/v1/settings/scheduling", count: 2)
        let putsBeforeStop = daemon.requestCount("PUT", "/v1/settings/scheduling")
        XCTAssertEqual(putsBeforeStop, 2)  // phase-1 + the parked mutation

        // Phase 3: the stop coalesces. It must send no PUT of its own
        // and report busy — never a stop — despite the stale MANUAL
        // notice sitting in shared state.
        await store.emergencyStopSupervisedAuto()
        XCTAssertEqual(daemon.requestCount("PUT", "/v1/settings/scheduling"), putsBeforeStop)
        XCTAssertEqual(store.autoControlNotice, .schedulingModeBusy)
        XCTAssertNotEqual(store.autoControlNotice, .supervisedAutoStopped)

        // Phase 4: releasing the parked PUT lets the ORIGINAL mutation
        // finish under its own authority; its MANUAL result stays a
        // mode-change outcome, never a stop confirmation.
        daemon.release("PUT", "/v1/settings/scheduling")
        _ = await inFlight.value
        XCTAssertEqual(store.autoControlNotice, .schedulingModeChanged(mode: "MANUAL"))
        XCTAssertNotEqual(store.autoControlNotice, .supervisedAutoStopped)
    }

    // MARK: - NOTICE-SCOPE (closeout P1)

    /// NOTICE-SCOPE-1: task success notices apply only to same task.
    func testTaskOutcomeNoticesApplyOnlyToSameTask() {
        XCTAssertTrue(AutoControlNotice.acknowledged(taskId: "t-1").applies(to: .task("t-1")))
        XCTAssertTrue(AutoControlNotice.vetoed(taskId: "t-1").applies(to: .task("t-1")))
        XCTAssertTrue(
            AutoControlNotice.dispatchRequested(taskId: "t-1", dispatchId: "d", status: "DISPATCHING")
                .applies(to: .task("t-1"))
        )
        XCTAssertFalse(AutoControlNotice.acknowledged(taskId: "t-1").applies(to: .task("t-2")))
        XCTAssertFalse(AutoControlNotice.vetoed(taskId: "t-1").applies(to: .task("t-2")))
    }

    /// NOTICE-SCOPE-2: generic task failures stay scoped to the task.
    func testTaskGenericFailuresApplyOnlyToSameTask() {
        XCTAssertTrue(
            AutoControlNotice.blocked(scope: .task("t-1"), code: "task_state_not_auto")
                .applies(to: .task("t-1"))
        )
        XCTAssertTrue(
            AutoControlNotice.failed(scope: .task("t-1"), action: .ack, detail: "x")
                .applies(to: .task("t-1"))
        )
        XCTAssertTrue(
            AutoControlNotice.malformedResponse(scope: .task("t-1")).applies(to: .task("t-1"))
        )
        XCTAssertFalse(
            AutoControlNotice.blocked(scope: .task("t-1"), code: "task_state_not_auto")
                .applies(to: .task("t-2"))
        )
        XCTAssertFalse(
            AutoControlNotice.failed(scope: .task("t-1"), action: .ack, detail: "x")
                .applies(to: .task("t-2"))
        )
    }

    /// NOTICE-SCOPE-3: project refusals apply only to that project.
    func testProjectRefusalAppliesOnlyToThatProject() {
        let notice = AutoControlNotice.blocked(
            scope: .project("p-a"), code: "invalid_grace_seconds"
        )
        XCTAssertTrue(notice.applies(to: .project("p-a")))
        XCTAssertFalse(notice.applies(to: .project("p-b")))
        XCTAssertFalse(notice.applies(to: .task("t-1")))
        XCTAssertFalse(notice.applies(to: .schedulingMode))
        XCTAssertFalse(
            AutoControlNotice.projectAutoSettingsSaved(projectId: "p-a").applies(to: .project("p-b"))
        )
    }

    /// NOTICE-SCOPE-4: scheduling notices apply only to scheduling.
    func testSchedulingNoticesApplyOnlyToSchedulingScope() {
        XCTAssertTrue(AutoControlNotice.schedulingModeChanged(mode: "MANUAL").applies(to: .schedulingMode))
        XCTAssertTrue(AutoControlNotice.supervisedAutoStopped.applies(to: .schedulingMode))
        XCTAssertTrue(AutoControlNotice.schedulingModeBusy.applies(to: .schedulingMode))
        XCTAssertFalse(AutoControlNotice.supervisedAutoStopped.applies(to: .task("t-1")))
        XCTAssertFalse(AutoControlNotice.schedulingModeChanged(mode: "MANUAL").applies(to: .project("p-a")))
    }

    /// NOTICE-SCOPE-5: the menu-bar scheduling filter cannot accept
    /// task or project notices.
    func testMenuSchedulingFilterRejectsTaskAndProjectNotices() {
        let candidates: [AutoControlNotice] = [
            .acknowledged(taskId: "t-1"),
            .vetoed(taskId: "t-1"),
            .staleState(taskId: "t-1"),
            .dispatchRequested(taskId: "t-1", dispatchId: "d", status: "DISPATCHING"),
            .blocked(scope: .task("t-1"), code: "x"),
            .projectAutoSettingsSaved(projectId: "p-a"),
            .blocked(scope: .project("p-a"), code: "invalid_grace_seconds"),
        ]
        for notice in candidates {
            XCTAssertFalse(
                notice.applies(to: .schedulingMode),
                "\(notice) leaked into the scheduling surface"
            )
        }
    }

    /// NOTICE-SCOPE-6: Task A's notice never applies to Task B.
    func testTaskANoticeDoesNotApplyToTaskB() {
        let notices: [AutoControlNotice] = [
            .acknowledged(taskId: "task-a"),
            .vetoed(taskId: "task-a"),
            .staleState(taskId: "task-a"),
            .dispatchRequested(taskId: "task-a", dispatchId: "d", status: "DISPATCHING"),
            .blocked(scope: .task("task-a"), code: "x"),
            .failed(scope: .task("task-a"), action: .veto, detail: "x"),
            .malformedResponse(scope: .task("task-a")),
        ]
        for notice in notices {
            XCTAssertFalse(
                notice.applies(to: .task("task-b")),
                "\(notice) leaked into another task's surface"
            )
        }
    }
}
