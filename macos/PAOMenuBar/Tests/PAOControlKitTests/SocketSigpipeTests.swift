import Darwin
import Foundation
import XCTest

/// Run with SIG_DFL in child processes: a fatal SIGPIPE becomes an assertion in
/// the parent, instead of aborting the entire XCTest suite or being ignored.
final class SocketSigpipeTests: XCTestCase {
    private static let compiledProbe: Result<URL, Error> = Result {
        let build = Bundle(for: SocketSigpipeTests.self).bundleURL.deletingLastPathComponent()
        let objects = try FileManager.default.contentsOfDirectory(
            at: build.appendingPathComponent("PAOControlKit.build"), includingPropertiesForKeys: nil
        ).filter { $0.pathExtension == "o" }.sorted { $0.path < $1.path }
        guard !objects.isEmpty else { throw NSError(domain: "SocketSigpipeTests.objects", code: 1) }
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent("pao-sigpipe-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
        let fixture = scratch.appendingPathComponent("TestDaemon.swift")
        try String(contentsOf: source.appendingPathComponent("TestDaemon.swift"), encoding: .utf8)
            .replacingOccurrences(of: "import XCTest\n", with: "")
            .write(to: fixture, atomically: true, encoding: .utf8)
        let executable = scratch.appendingPathComponent("socket-sigpipe-probe")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
        process.arguments = ["swiftc", "-parse-as-library", "-swift-version", "5", "-I", build.appendingPathComponent("Modules").path,
            source.appendingPathComponent("../Fixtures/SocketSigpipeProbe.swift").standardizedFileURL.path,
            fixture.path] + objects.map(\.path) + ["-o", executable.path]
        let output = scratch.appendingPathComponent("compile.log")
        FileManager.default.createFile(atPath: output.path, contents: nil)
        let handle = try FileHandle(forWritingTo: output)
        defer { try? handle.close() }
        process.standardOutput = handle; process.standardError = handle
        try process.run(); process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw NSError(domain: "SocketSigpipeTests.compile", code: Int(process.terminationStatus),
                          userInfo: [NSLocalizedDescriptionKey: try String(contentsOf: output, encoding: .utf8)])
        }
        return executable
    }

    private func runProbe(_ mode: String) throws {
        let executable = try Self.compiledProbe.get()
        let process = Process()
        process.executableURL = executable; process.arguments = [mode]
        let log = executable.deletingLastPathComponent().appendingPathComponent(mode + ".log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let handle = try FileHandle(forWritingTo: log)
        defer { try? handle.close() }
        process.standardOutput = handle; process.standardError = handle
        let done = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in done.signal() }
        try process.run()
        guard done.wait(timeout: .now() + 10) == .success else {
            process.terminate()
            if done.wait(timeout: .now() + 2) != .success { kill(process.processIdentifier, SIGKILL) }
            XCTFail("SIGPIPE probe timed out: \(mode)")
            return
        }
        let text = try String(contentsOf: log, encoding: .utf8)
        XCTAssertEqual(process.terminationReason, .exit, "\(mode): \(text)")
        XCTAssertEqual(process.terminationStatus, 0, "\(mode): \(text)")
        XCTAssertTrue(text.contains("SIGPIPE_PROBE_PASSED \(mode)"), text)
    }

    func testPeerClosedBeforeConnectReturnsIsAnErrorNotSIGPIPE() throws { try runProbe("client-close") }
    func testPeerClosedBeforeAcceptReturnsCannotAbortXCTest() throws { try runProbe("server-close") }
    func testClientProtectionFailurePreventsConnect() throws { try runProbe("client-option-failure") }
    func testListenerProtectionFailurePreventsBind() throws { try runProbe("server-option-failure") }
    func testProtectedSocketsStillCompleteNormalRoundTrips() throws { try runProbe("roundtrip") }
}
