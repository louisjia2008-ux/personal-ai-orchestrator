import Foundation
import XCTest

@testable import PAOControlKit

final class SupervisedAutoControlClientTests: XCTestCase {
    func testSchedulingAndProjectMutationBodiesPreserveFullAuthoritativeTuple() async throws {
        let daemon = TestDaemon()
        daemon.route(
            "PUT",
            "/v1/settings/scheduling",
            body: """
            {"default_scheduling_policy":"QUALITY_FIRST","selectable_policies":["BALANCED","QUALITY_FIRST"],"mode":"SUPERVISED_AUTO","selectable_modes":["MANUAL","SUPERVISED_AUTO","ACTIVE"]}
            """
        )
        daemon.route(
            "PUT",
            "/v1/projects/project-1/settings",
            body: projectResponseBody
        )

        let path = temporarySocketPath("supervised-settings")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)

        let scheduling = try await client.setSchedulingMode(
            "SUPERVISED_AUTO",
            defaultSchedulingPolicy: "QUALITY_FIRST"
        )
        XCTAssertEqual(scheduling.mode, "SUPERVISED_AUTO")
        XCTAssertEqual(scheduling.defaultSchedulingPolicy, "QUALITY_FIRST")

        let project = try await client.setProjectSupervisedAutoSettings(
            projectId: "project-1",
            supervisedAutoAllowed: true,
            unattendedAllowed: false,
            graceSeconds: 180
        )
        XCTAssertTrue(project.supervisedAutoAllowed)
        XCTAssertFalse(project.unattendedAllowed)
        XCTAssertEqual(project.graceSeconds, 180)

        let schedulingRequest = try XCTUnwrap(
            daemon.receivedRequests.first {
                $0.method == "PUT" && $0.path == "/v1/settings/scheduling"
            }
        )
        let schedulingJSON = try jsonObject(schedulingRequest.body)
        XCTAssertEqual(schedulingJSON["mode"] as? String, "SUPERVISED_AUTO")
        XCTAssertEqual(
            schedulingJSON["default_scheduling_policy"] as? String,
            "QUALITY_FIRST"
        )

        let projectRequest = try XCTUnwrap(
            daemon.receivedRequests.first {
                $0.method == "PUT" && $0.path == "/v1/projects/project-1/settings"
            }
        )
        let projectJSON = try jsonObject(projectRequest.body)
        XCTAssertEqual(projectJSON["supervised_auto_allowed"] as? Bool, true)
        XCTAssertEqual(projectJSON["unattended_allowed"] as? Bool, false)
        XCTAssertEqual(projectJSON["grace_seconds"] as? Int, 180)
        XCTAssertEqual(projectJSON.count, 3, "project update must submit the complete three-field tuple only")
    }

    func testAckVetoAndDispatchNowCarryExactTaskStateVersion() async throws {
        let daemon = TestDaemon()
        daemon.route(
            "POST",
            "/v1/tasks/task-1/auto/ack",
            body: taskResponse(state: "AUTO_GRACE", stateVersion: 8)
        )
        daemon.route(
            "POST",
            "/v1/tasks/task-1/auto/veto",
            body: taskResponse(state: "CANCELLED", stateVersion: 9)
        )
        daemon.route(
            "POST",
            "/v1/tasks/task-1/auto/dispatch-now",
            body: dispatchResponseBody
        )

        let path = temporarySocketPath("supervised-task-actions")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)

        let acked = try await client.autoAck(taskId: "task-1", taskStateVersion: 7)
        XCTAssertEqual(acked.state, "AUTO_GRACE")
        XCTAssertEqual(acked.stateVersion, 8)

        let vetoed = try await client.autoVeto(
            taskId: "task-1",
            requestId: "veto-request-1",
            taskStateVersion: 8
        )
        XCTAssertEqual(vetoed.state, "CANCELLED")

        let dispatch = try await client.autoDispatchNow(
            taskId: "task-1",
            taskStateVersion: 8
        )
        XCTAssertEqual(dispatch.task.taskId, "task-1")
        XCTAssertEqual(dispatch.status, "RESERVED")

        let ackRequest = try XCTUnwrap(
            daemon.receivedRequests.first {
                $0.method == "POST" && $0.path == "/v1/tasks/task-1/auto/ack"
            }
        )
        XCTAssertEqual(try jsonObject(ackRequest.body)["task_state_version"] as? Int, 7)

        let vetoRequest = try XCTUnwrap(
            daemon.receivedRequests.first {
                $0.method == "POST" && $0.path == "/v1/tasks/task-1/auto/veto"
            }
        )
        let vetoJSON = try jsonObject(vetoRequest.body)
        XCTAssertEqual(vetoJSON["task_state_version"] as? Int, 8)
        XCTAssertEqual(vetoJSON["request_id"] as? String, "veto-request-1")

        let dispatchRequest = try XCTUnwrap(
            daemon.receivedRequests.first {
                $0.method == "POST" && $0.path == "/v1/tasks/task-1/auto/dispatch-now"
            }
        )
        XCTAssertEqual(try jsonObject(dispatchRequest.body)["task_state_version"] as? Int, 8)
    }

    private func jsonObject(_ body: String) throws -> [String: Any] {
        try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any]
        )
    }

    private func taskResponse(state: String, stateVersion: Int) -> String {
        """
        {"task_id":"task-1","request_id":"request-1","intent":"fix issue","project_id":"project-1","state":"\(state)","state_version":\(stateVersion),"created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","scheduling_policy":"BALANCED","min_tier":"T1"}
        """
    }

    private var projectResponseBody: String {
        """
        {"project_id":"project-1","display_name":"Project One","canonical_repo_root":"/tmp/project","git_root":"/tmp/project","default_branch":"main","last_known_head":"abc123","created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:01:00Z","working_subpath":null,"remote_url":null,"last_opened_at":null,"storage_availability":"ONLINE","recent_task_count":0,"current_branch":"main","supervised_auto_allowed":true,"unattended_allowed":false,"grace_seconds":180}
        """
    }

    private var dispatchResponseBody: String {
        """
        {"dispatch_id":"dispatch-1","task":{"task_id":"task-1","request_id":"request-1","intent":"fix issue","project_id":"project-1","state":"RUNNING","state_version":9,"created_at":"2026-09-15T00:00:00Z","updated_at":"2026-09-15T00:02:00Z"},"request_id":"dispatch-now-1","authority":"SUPERVISED_AUTO","execution_target_id":"minimax-cn-coding-plan-MiniMax-M3","status":"RESERVED","accepted":true,"reason":"supervised auto dispatch reserved","failure_code":null}
        """
    }
}
