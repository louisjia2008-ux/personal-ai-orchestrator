import Foundation
import XCTest
@testable import PAOControlKit

@MainActor
final class ProjectBaselineTests: XCTestCase {
    func testWorkingSnapshotSelectionReachesDaemonWithPreviewBinding() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("baseline-submit")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = OrchestratorStore(socketPath: path, idFactory: { "baseline" })
        let token = String(repeating: "a", count: 64)
        await store.quickSubmit(
            projectId: "project-fixture", intent: "fix code",
            baseline: TaskBaselineSelection(
                projectId: "project-fixture", mode: .workingTreeSnapshot, fingerprint: token
            )
        )
        let request = try XCTUnwrap(daemon.receivedRequests.first { $0.path == "/v1/tasks" && $0.method == "POST" })
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(request.body.utf8)) as? [String: Any])
        XCTAssertEqual(body["baseline_mode"] as? String, "WORKING_TREE_SNAPSHOT")
        XCTAssertEqual(body["expected_baseline_fingerprint"] as? String, token)
        XCTAssertEqual(body["project_id"] as? String, "project-fixture")
    }

    func testPreviewForAnotherProjectNeverSubmits() async throws {
        let daemon = TestDaemon()
        let path = temporarySocketPath("baseline-project")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }
        let store = OrchestratorStore(socketPath: path)
        let notice = await store.quickSubmit(
            projectId: "project-new", intent: "keep this draft",
            baseline: TaskBaselineSelection(
                projectId: "project-old", mode: .committedHead, fingerprint: String(repeating: "b", count: 64)
            )
        )
        guard case .failed = notice else { return XCTFail("Mismatched preview must fail") }
        XCTAssertTrue(daemon.receivedRequests.isEmpty)
    }

    func testReceiptDecodesSourceAndTaskBaselinesSeparatelyAndLegacyIsOptional() throws {
        let source = String(repeating: "1", count: 40)
        let snapshot = String(repeating: "2", count: 40)
        var body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(submitResponseBody.utf8)) as? [String: Any])
        body["base_sha"] = snapshot
        body["baseline"] = [
            "mode": "WORKING_TREE_SNAPSHOT", "source_head": source, "source_branch": "main",
            "source_fingerprint": String(repeating: "a", count: 64), "base_sha": snapshot,
            "snapshot_ref": "refs/pao/task-baselines/local"
        ]
        let task = try JSONDecoder().decode(TaskView.self, from: JSONSerialization.data(withJSONObject: body))
        XCTAssertEqual(task.baseline?.sourceHead, source)
        XCTAssertEqual(task.baseline?.baseSha, task.baseSha)
        XCTAssertEqual(task.baseline?.mode, .workingTreeSnapshot)
        let legacy = try JSONDecoder().decode(TaskView.self, from: Data(submitResponseBody.utf8))
        XCTAssertNil(legacy.baseline)
    }
}
