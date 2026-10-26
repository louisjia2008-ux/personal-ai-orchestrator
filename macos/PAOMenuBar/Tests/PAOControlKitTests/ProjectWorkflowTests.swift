import Foundation
import XCTest
@testable import PAOControlKit

final class ProjectWorkflowTests: XCTestCase {
    private func resultResponse(taskId: String = "t", completeness: String = "COMPLETE") -> String {
        """
        {"store_id":"store-one","process_epoch":"epoch-one","payload":{"result":{
          "result_id":"result-host","binding":{"task_id":"\(taskId)","run_id":"run-one",
            "verification_evidence_sha256":"digest"},"goal":"Fix src/auth/session.py",
          "goal_redacted":false,"completeness":"\(completeness)",
          "verification":{"status":"VERIFIED","evidence_id":"verify-host"},
          "changed_files":["src/personal_ai_orchestrator/result_delivery.py","src/auth/session.py"],
          "tests":[{"name":"host check","status":"PASSED"}],
          "limitations":["HOST_REPORT_ONLY"],
          "truncated":{"goal":false,"changed_files":false,"tests":false}}}}
        """
    }

    func testCrossClientResultUsesTheCanonicalReadonlyDetail() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("safe-result")
        daemon.route("POST", "/v2/local-client", body: resultResponse())
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let result = try await PAOControlClient(socketPath: path).taskCrossClientResult("t")
        XCTAssertEqual(result.completeness, "COMPLETE")
        XCTAssertEqual(result.verification.status, "VERIFIED")
        XCTAssertEqual(result.changedFiles, ["src/personal_ai_orchestrator/result_delivery.py", "src/auth/session.py"])
        XCTAssertFalse(result.goalRedacted)
        XCTAssertFalse(result.truncated.any)
        XCTAssertFalse(result.hasOmissions)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(daemon.receivedRequests[0].body.utf8)) as? [String: Any])
        XCTAssertEqual(body["operation"] as? String, "DETAIL")
        XCTAssertEqual(body["task_id"] as? String, "t")
        XCTAssertEqual(daemon.receivedRequests.count, 1)
    }

    func testCrossClientResultKeepsTruncationAndRejectsAnotherTasksBinding() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("safe-result-binding")
        daemon.route("POST", "/v2/local-client", body: resultResponse(completeness: "TRUNCATED"))
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let client = PAOControlClient(socketPath: path)
        let result = try await client.taskCrossClientResult("t")
        XCTAssertEqual(result.completeness, "TRUNCATED")
        XCTAssertTrue(result.hasOmissions)
        do {
            _ = try await client.taskCrossClientResult("another-task")
            XCTFail("A result from a different task must not be presented")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .malformedResponse)
        }
    }

    func testHostTestOutputSurvivesTheReportAndSummary() throws {
        let data = Data("""
        {"task_id":"t","task_state":"VERIFIED","status":"VERIFIED","result":{
          "profile":"p","passed":true,"changed_paths":[],"unexpected_paths":[],
          "stages":[{"name":"pytest","returncode":0,"stdout":"67 passed\\n","stderr":"",
                     "argv":["python","-m","pytest"]}]}}
        """.utf8)
        let report = try JSONDecoder().decode(VerificationReportView.self, from: data)
        let stage = try XCTUnwrap(TaskVerificationSummary(report: report).stages.first)
        XCTAssertEqual(stage.stdout, "67 passed\n")
        XCTAssertEqual(stage.argv, ["python", "-m", "pytest"])
        XCTAssertTrue(stage.passed)
    }

    func testSameCommitDifferentBuildCannotAdoptOldDaemon() {
        let sha = String(repeating: "a", count: 40)
        let app = BuildIdentity(commitSHA: sha, configuration: "Release", builtAt: "2026-10-20T12:00:00Z")
        XCTAssertTrue(BuildCompatibility.compare(
            app: app, daemonCommitSHA: sha, daemonBuiltAt: "2026-10-20T11:00:00Z"
        ).isMismatch)
        XCTAssertEqual(BuildCompatibility.compare(
            app: app, daemonCommitSHA: sha, daemonBuiltAt: app.builtAt
        ), .matched)
    }

    func testProjectConfigurationRoundTripUsesOwnerEndpoint() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("project-checks")
        let response = """
        {"project_id":"p-1","configured":true,"command_lines":["test -f result.md"],
         "allowed_paths":["docs"],"timeout_seconds":120}
        """
        daemon.route("GET", "/v1/projects/p-1/verification", body: response)
        daemon.route("PUT", "/v1/projects/p-1/verification", body: response)
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let client = PAOControlClient(socketPath: path)
        let configured = try await client.saveProjectVerification(
            "p-1", commands: ["test -f result.md"], allowedPaths: ["docs"], timeout: 120,
            preparationCommands: ["uv sync --offline"]
        )
        let loaded = try await client.projectVerification("p-1")
        XCTAssertEqual(configured, loaded)
        let sent = try XCTUnwrap(daemon.receivedRequests.first)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.body.utf8)) as? [String: Any])
        XCTAssertEqual(body["command_lines"] as? [String], ["test -f result.md"])
        XCTAssertEqual(body["allowed_paths"] as? [String], ["docs"])
        XCTAssertEqual(body["preparation_command_lines"] as? [String], ["uv sync --offline"])
    }

    func testRetryBindsVisibleGenerationAndHostIdentityWithoutStarting() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("manual-retry")
        daemon.route("POST", "/v2/local-client", body: """
        {"store_id":"store-one","process_epoch":"epoch-one","payload":{}}
        """)
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let task = try JSONDecoder().decode(TaskView.self, from: Data(submitResponseBody.utf8))
        try await PAOControlClient(socketPath: path).retryManualTask(
            task: task, priorDispatchId: "owner-dispatch-prior"
        )
        let bodies = try daemon.receivedRequests.map {
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data($0.body.utf8)) as? [String: Any])
        }
        XCTAssertEqual(bodies.count, 2)
        XCTAssertEqual(bodies[0]["operation"] as? String, "CONTEXT")
        XCTAssertEqual(bodies[1]["operation"] as? String, "RETRY")
        XCTAssertEqual(bodies[1]["task_state_version"] as? Int, task.stateVersion)
        XCTAssertEqual(bodies[1]["expected_store_id"] as? String, "store-one")
        XCTAssertEqual(bodies[1]["expected_process_epoch"] as? String, "epoch-one")
        XCTAssertEqual(bodies[1]["prior_dispatch_id"] as? String, "owner-dispatch-prior")
    }

    func testModelProbeUsesFixedEmptyRequest() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("model-probe")
        daemon.route("POST", "/v1/execution-targets/pi-target/probe", body:
            "{\"result\":\"UNKNOWN\",\"reason_code\":\"PI_PROBE_PROVIDER_ERROR\"}")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let result = try await PAOControlClient(socketPath: path).probeExecutionTarget("pi-target")
        XCTAssertEqual(result.result, "UNKNOWN")
        XCTAssertEqual(result.reasonCode, "PI_PROBE_PROVIDER_ERROR")
        XCTAssertEqual(daemon.receivedRequests.first?.body, "{}")
    }
}
