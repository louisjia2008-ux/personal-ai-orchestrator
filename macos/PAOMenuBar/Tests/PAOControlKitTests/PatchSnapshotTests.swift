import CryptoKit
import Foundation
import XCTest
@testable import PAOControlKit

final class PatchSnapshotTests: XCTestCase {
    private func payload(bytes: Data) -> [String: Any] {
        ["task_id": "t", "base_sha": String(repeating: "a", count: 40),
         "patch_base64": bytes.base64EncodedString(), "byte_count": bytes.count,
         "sha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
         "source": "VERIFIED_SNAPSHOT", "workspace_status": "CURRENT"]
    }

    func testNonUTF8PatchExportsOriginalBytesRatherThanPreview() throws {
        let bytes = Data([0x61, 0xff, 0xfe, 0x0a, 0x00])
        let patch = try JSONDecoder().decode(TaskPatchView.self, from: JSONSerialization.data(withJSONObject: payload(bytes: bytes)))
        XCTAssertEqual(patch.patchData, bytes)
        XCTAssertNotEqual(Data(patch.patch.utf8), bytes)
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        try patch.patchData.write(to: url, options: .atomic)
        XCTAssertEqual(try Data(contentsOf: url), bytes)
    }

    func testCorruptDigestCountEncodingAndMissingBytesAreRejected() throws {
        let valid = payload(bytes: Data([0xff, 0xfe]))
        for (key, value) in [("sha256", "wrong" as Any), ("byte_count", 1 as Any),
                             ("patch_base64", "invalid%%%" as Any), ("patch_base64", NSNull() as Any)] {
            var body = valid
            body[key] = value
            XCTAssertThrowsError(try JSONDecoder().decode(TaskPatchView.self, from: JSONSerialization.data(withJSONObject: body)))
        }
    }

    func testStaleAndUnavailableAreNeverPositiveOrCompleted() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "plan_primary_reviewer", withExtension: "json", subdirectory: "Fixtures/routing"))
        let original = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        for raw in ["VERIFICATION_STALE", "WORKSPACE_UNAVAILABLE"] {
            var body = original
            var task = try XCTUnwrap(body["task"] as? [String: Any])
            task["state"] = "VERIFIED"
            task["current_verification_status"] = raw
            body["task"] = task
            body["verification"] = ["task_id": task["task_id"]!, "task_state": "VERIFIED", "status": raw]
            let detail = try JSONDecoder().decode(TaskDetailView.self, from: JSONSerialization.data(withJSONObject: body))
            XCTAssertEqual(detail.task.displayState, raw)
            XCTAssertNotEqual(StatusStyle.verification(raw).tone, .positive)
            let steps = TaskLifecycle.steps(for: detail)
            XCTAssertEqual(steps.first { $0.stage == .verification }?.status, .unknown)
            XCTAssertEqual(steps.first { $0.stage == .completion }?.status, .unknown)
        }
    }
}
