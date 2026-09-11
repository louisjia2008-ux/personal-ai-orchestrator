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

        XCTAssertEqual(store.autoControlNotice, .blocked(code: "invalid_grace_seconds"))
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
}
