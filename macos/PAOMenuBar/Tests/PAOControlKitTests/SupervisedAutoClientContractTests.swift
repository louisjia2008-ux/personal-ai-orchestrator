import Foundation
import XCTest

@testable import PAOControlKit

/// Pins the Swift client to the current-main WP5a wire contract. These fixtures
/// intentionally mirror `tests/test_auto_endpoints.py`,
/// `tests/test_scheduling_settings.py`, and `tests/test_project_settings.py` on
/// the Python daemon side so a presentation-only PR cannot silently invent a
/// different request shape.
final class SupervisedAutoClientContractTests: XCTestCase {
    private let taskBody = """
    {"task_id":"t-auto","request_id":"r-auto","intent":"supervised task","project_id":"project-fixture","state":"AUTO_GRACE","state_version":7,"created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","auto_decision_id":"auto-t-auto-v6","auto_grace_deadline_at":null,"auto_acked_at":null,"auto_reason":"AUTO_PLANNED{auto-t-auto-v6,m3-sub}"}
    """

    private let projectBody = """
    {"project_id":"project-fixture","display_name":"Fixture","canonical_repo_root":"/repo","git_root":"/repo","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":true,"unattended_allowed":false,"grace_seconds":120}
    """

    private let updatedProjectBody = """
    {"project_id":"project-fixture","display_name":"Fixture","canonical_repo_root":"/repo","git_root":"/repo","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:02:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":true,"unattended_allowed":false,"grace_seconds":300}
    """

    private let schedulingBody = """
    {"default_scheduling_policy":"QUALITY_FIRST","selectable_policies":["BALANCED","QUALITY_FIRST"],"mode":"SUPERVISED_AUTO","selectable_modes":["MANUAL","SUPERVISED_AUTO","ACTIVE"]}
    """

    private let manualSchedulingBody = """
    {"default_scheduling_policy":"QUALITY_FIRST","selectable_policies":["BALANCED","QUALITY_FIRST"],"mode":"MANUAL","selectable_modes":["MANUAL","SUPERVISED_AUTO","ACTIVE"]}
    """

    func testAutoAckVetoAndDispatchNowUseCurrentMainPathsAndBodies() async throws {
        let daemon = TestDaemon()
        daemon.route("POST", "/v1/tasks/t-auto/auto/ack", body: taskBody)
        daemon.route("POST", "/v1/tasks/t-auto/auto/veto", body: taskBody)
        daemon.route(
            "POST",
            "/v1/tasks/t-auto/auto/dispatch-now",
            body: """
            {"dispatch_id":"auto-dispatch-1","task":\(taskBody),"request_id":"auto-auto-t-auto-v6","authority":"SUPERVISED_AUTO","execution_target_id":"m3-sub","status":"RESERVED","accepted":true,"reason":null,"failure_code":null}
            """
        )
        let path = temporarySocketPath("auto-wire")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        _ = try await client.autoAck(taskId: "t-auto", taskStateVersion: 7)
        _ = try await client.autoVeto(
            taskId: "t-auto",
            requestId: "veto-1",
            taskStateVersion: 7
        )
        let dispatch = try await client.autoDispatchNow(taskId: "t-auto", taskStateVersion: 7)

        XCTAssertTrue(dispatch.accepted)
        XCTAssertEqual(dispatch.authority, "SUPERVISED_AUTO")
        XCTAssertEqual(dispatch.executionTargetId, "m3-sub")
        XCTAssertEqual(dispatch.status, "RESERVED")

        let ack = request(daemon, method: "POST", path: "/v1/tasks/t-auto/auto/ack")
        XCTAssertEqual(json(ack?.body)["task_state_version"] as? Int, 7)

        let veto = request(daemon, method: "POST", path: "/v1/tasks/t-auto/auto/veto")
        XCTAssertEqual(json(veto?.body)["request_id"] as? String, "veto-1")
        XCTAssertEqual(json(veto?.body)["task_state_version"] as? Int, 7)

        let dispatchRequest = request(
            daemon,
            method: "POST",
            path: "/v1/tasks/t-auto/auto/dispatch-now"
        )
        XCTAssertEqual(json(dispatchRequest?.body)["task_state_version"] as? Int, 7)
    }

    func testSchedulingModeCoordinatorPreservesFreshAuthoritativePolicy() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/settings/scheduling", body: schedulingBody)
        daemon.route("PUT", "/v1/settings/scheduling", body: manualSchedulingBody)
        let path = temporarySocketPath("mode-wire")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let coordinator = AutomationModeMutationCoordinator()
        let updated = try await coordinator.setMode(socketPath: path, mode: "MANUAL")
        XCTAssertEqual(updated.mode, "MANUAL")
        XCTAssertEqual(updated.defaultSchedulingPolicy, "QUALITY_FIRST")

        let requests = daemon.receivedRequests.filter {
            $0.path == "/v1/settings/scheduling"
        }
        XCTAssertEqual(requests.map(\.method), ["GET", "PUT"])
        guard requests.count == 2 else { return }
        let body = json(requests[1].body)
        XCTAssertEqual(body["mode"] as? String, "MANUAL")
        XCTAssertEqual(body["default_scheduling_policy"] as? String, "QUALITY_FIRST")
    }

    func testProjectCoordinatorPreservesFreshSiblingFieldsInFullTuplePUT() async throws {
        let daemon = TestDaemon()
        daemon.route(
            "GET",
            "/v1/projects",
            body: "{\"projects\":[\(projectBody)]}"
        )
        daemon.route(
            "PUT",
            "/v1/projects/project-fixture/settings",
            body: updatedProjectBody
        )
        let path = temporarySocketPath("project-auto-wire")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let coordinator = ProjectAutomationMutationCoordinator()
        let updated = try await coordinator.update(
            socketPath: path,
            projectId: "project-fixture",
            graceSeconds: 300
        )
        XCTAssertEqual(updated?.graceSeconds, 300)

        let put = request(
            daemon,
            method: "PUT",
            path: "/v1/projects/project-fixture/settings"
        )
        let body = json(put?.body)
        XCTAssertEqual(body["supervised_auto_allowed"] as? Bool, true)
        XCTAssertEqual(body["unattended_allowed"] as? Bool, false)
        XCTAssertEqual(body["grace_seconds"] as? Int, 300)
    }

    func testAutoEndpointSanitizedConflictCodeIsPreserved() async throws {
        let daemon = TestDaemon()
        daemon.route(
            "POST",
            "/v1/tasks/t-auto/auto/dispatch-now",
            status: 409,
            body: "{\"error\":\"scheduling_mode_not_supervised_auto\"}"
        )
        let path = temporarySocketPath("auto-conflict")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        do {
            _ = try await client.autoDispatchNow(taskId: "t-auto", taskStateVersion: 7)
            XCTFail("expected sanitized 409")
        } catch let error as PAOClientError {
            XCTAssertEqual(
                error,
                .httpError(status: 409, code: "scheduling_mode_not_supervised_auto")
            )
        }
    }

    private func request(
        _ daemon: TestDaemon,
        method: String,
        path: String
    ) -> (method: String, path: String, body: String)? {
        daemon.receivedRequests.first { $0.method == method && $0.path == path }
    }

    private func json(_ body: String?) -> [String: Any] {
        guard let body,
              let data = body.data(using: .utf8),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return value
    }
}
