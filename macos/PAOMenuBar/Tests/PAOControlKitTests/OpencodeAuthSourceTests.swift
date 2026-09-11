import XCTest

@testable import PAOControlKit

/// opencode's auth.json is the only place the bundled daemon can reach
/// for provider credentials in a GUI launch context. The mapping is
/// the single bridge between opencode's per-provider identifiers and
/// the environment variables the daemon's quota collectors expect.
final class OpencodeAuthSourceTests: XCTestCase {
    func testReadsRealAuthFileWhenPresent() throws {
        // The standard opencode auth path may not exist on a developer
        // workstation without opencode installed; skip rather than fail.
        let home = NSHomeDirectory() as NSString
        let real = home.appendingPathComponent(".local/share/opencode/auth.json")
        if !FileManager.default.isReadableFile(atPath: real) { return }

        let entries = OpencodeAuthSource.read(from: real)
        XCTAssertFalse(entries.isEmpty)
        // Every entry carries a provider id the daemon can recognise and
        // an environment variable name the collector reads.
        for entry in entries {
            XCTAssertFalse(entry.providerId.isEmpty)
            XCTAssertFalse(entry.environmentName.isEmpty)
            XCTAssertFalse(entry.key.isEmpty)
        }
    }

    func testEmptyResultForMissingFile() {
        let entries = OpencodeAuthSource.read(from: "/nonexistent/auth.json")
        XCTAssertTrue(entries.isEmpty)
    }

    func testEmptyResultForMalformedJson() throws {
        let tmp = NSTemporaryDirectory() + "auth-malformed-\(UUID().uuidString).json"
        try Data("not json".utf8).write(to: URL(fileURLWithPath: tmp))
        defer { try? FileManager.default.removeItem(atPath: tmp) }
        XCTAssertTrue(OpencodeAuthSource.read(from: tmp).isEmpty)
    }

    func testIgnoresUnrecognisedProviders() throws {
        let tmp = NSTemporaryDirectory() + "auth-mixed-\(UUID().uuidString).json"
        let payload = """
        {
          "minimax-cn-coding-plan": {"type": "api", "key": "sk-real"},
          "anthropic": {"type": "api", "key": "sk-other"},
          "malformed-entry": "not an object"
        }
        """
        try Data(payload.utf8).write(to: URL(fileURLWithPath: tmp))
        defer { try? FileManager.default.removeItem(atPath: tmp) }
        let entries = OpencodeAuthSource.read(from: tmp)
        // Only the daemon-recognised providers project; others drop.
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries.first?.providerId, "minimax-cn-coding-plan")
        XCTAssertEqual(entries.first?.environmentName, "MINIMAX_API_KEY")
        XCTAssertEqual(entries.first?.key, "sk-real")
    }

    func testProjectsZaiTokenToZaiApiKey() throws {
        let tmp = NSTemporaryDirectory() + "auth-zai-\(UUID().uuidString).json"
        let payload = """
        { "zai-coding-plan": {"type": "api", "key": "zai-token-xyz"} }
        """
        try Data(payload.utf8).write(to: URL(fileURLWithPath: tmp))
        defer { try? FileManager.default.removeItem(atPath: tmp) }
        let entries = OpencodeAuthSource.read(from: tmp)
        XCTAssertEqual(entries, [
            OpencodeAuthSource.Entry(
                providerId: "zai-coding-plan",
                environmentName: "ZAI_API_KEY",
                key: "zai-token-xyz"
            )
        ])
    }

    func testEmptyKeyValueIsDropped() throws {
        let tmp = NSTemporaryDirectory() + "auth-empty-\(UUID().uuidString).json"
        let payload = """
        {
          "minimax-cn-coding-plan": {"type": "api", "key": ""},
          "zai-coding-plan": {"type": "api"}
        }
        """
        try Data(payload.utf8).write(to: URL(fileURLWithPath: tmp))
        defer { try? FileManager.default.removeItem(atPath: tmp) }
        XCTAssertTrue(OpencodeAuthSource.read(from: tmp).isEmpty)
    }
}
