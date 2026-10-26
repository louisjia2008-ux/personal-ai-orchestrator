import CryptoKit
import Foundation
import XCTest
@testable import PAOControlKit

final class TaskResultPatchRefreshTests: XCTestCase {
    private func patch(_ text: String, source: String = "VERIFIED_SNAPSHOT", evidence: String? = nil) throws -> TaskPatchView {
        let data = Data(text.utf8)
        var body: [String: Any] = ["task_id": "t", "base_sha": String(repeating: "a", count: 40),
            "patch_base64": data.base64EncodedString(), "byte_count": data.count,
            "sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(),
            "source": source, "workspace_status": "CURRENT"]
        if let evidence { body["evidence_id"] = evidence }
        return try JSONDecoder().decode(TaskPatchView.self, from: JSONSerialization.data(withJSONObject: body))
    }

    private func report(_ patch: TaskPatchView?, status: String = "VERIFIED", evidence: String = "new") throws -> CrossClientResultView {
        var binding: [String: Any] = ["task_id": "t"]
        if let patch { binding["patch_sha256"] = patch.sha256; binding["patch_byte_count"] = patch.byteCount }
        return try JSONDecoder().decode(CrossClientResultView.self, from: JSONSerialization.data(withJSONObject: [
            "result_id": "r", "binding": binding, "goal": "Fix code", "goal_redacted": false,
            "completeness": "COMPLETE", "verification": ["status": status, "evidence_id": evidence],
            "changed_files": [], "tests": [], "limitations": [],
            "truncated": ["goal": false, "changed_files": false, "tests": false]
        ]))
    }

    func testAnOpenVerifyingSheetFetchesTheSnapshotWhenItsBindingArrives() throws {
        let accepted = try patch("accepted")
        XCTAssertFalse(TaskResultPatchRefresh.shouldRefresh(patch: nil, summary: try report(nil, status: "IN_PROGRESS"), taskId: "t"))
        XCTAssertTrue(TaskResultPatchRefresh.shouldRefresh(patch: nil, summary: try report(accepted), taskId: "t"))
        XCTAssertFalse(TaskResultPatchRefresh.shouldRefresh(patch: accepted, summary: try report(accepted), taskId: "t"))
    }

    func testAnUnverifiedPreviewOrEarlierSnapshotIsReplacedByAcceptedBytes() throws {
        let accepted = try patch("new")
        for earlier in [try patch("old"), try patch("new", source: "UNVERIFIED_WORKTREE")] {
            XCTAssertTrue(TaskResultPatchRefresh.shouldRefresh(patch: earlier, summary: try report(accepted), taskId: "t"))
        }
        XCTAssertFalse(TaskResultPatchRefresh.matches(try patch("old"), summary: try report(accepted), taskId: "t"))
        XCTAssertFalse(TaskResultPatchRefresh.matches(try patch("new", source: "UNVERIFIED_WORKTREE"), summary: try report(accepted), taskId: "t"))
    }

    func testHistoricalSelectionAndTaskIdentityCannotCrossBind() throws {
        let historical = try patch("old", evidence: "old")
        let summary = try report(historical, status: "HISTORICAL_VERIFIED", evidence: "old")
        XCTAssertTrue(TaskResultPatchRefresh.matches(historical, summary: summary, taskId: "t", evidenceId: "old"))
        XCTAssertFalse(TaskResultPatchRefresh.matches(historical, summary: summary, taskId: "t"))
        XCTAssertFalse(TaskResultPatchRefresh.matches(historical, summary: summary, taskId: "other", evidenceId: "old"))
        XCTAssertFalse(TaskResultPatchRefresh.shouldRefresh(patch: nil, summary: summary, taskId: "t", evidenceId: "different"))
    }

    func testFailureOrMissingProofDoesNotTriggerAcceptedPatchRequests() throws {
        let accepted = try patch("new")
        for status in ["IN_PROGRESS", "FAILED_VERIFICATION", "NOT_VERIFIED"] {
            XCTAssertFalse(TaskResultPatchRefresh.shouldRefresh(patch: nil, summary: try report(accepted, status: status), taskId: "t"))
        }
        XCTAssertFalse(TaskResultPatchRefresh.shouldRefresh(patch: nil, summary: try report(nil), taskId: "t"))
    }
}
