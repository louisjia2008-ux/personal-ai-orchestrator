import Foundation
import XCTest
@testable import PAOControlKit

final class BoundedProcessOutputTests: XCTestCase {
    @MainActor
    func testSilentLiveChildReachesStartupTimeoutWithoutBlockingMainActor() async throws {
        let root = "/tmp/pao-s-" + UUID().uuidString.prefix(8)
        defer { try? FileManager.default.removeItem(atPath: root) }
        let layout = AppSupportLayout(
            appSupportRoot: root, runtimeConfigPath: root + "/runtime.json",
            stateDatabasePath: root + "/state.sqlite", runtimeStateRoot: root + "/state",
            logsRoot: root + "/logs", socketPath: root + "/control.sock"
        )
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: true)
        let helper = URL(fileURLWithPath: root + "/helper")
        try Data("#!/bin/sh\nexec /bin/sleep 0.8\n".utf8).write(to: helper)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
        let controller = DaemonLifecycleController(
            configuration: DaemonLaunchConfiguration(layout: layout, helperURL: helper),
            client: PAOControlClient(socketPath: layout.socketPath, timeoutSeconds: 0.05),
            startupTimeout: 0.15, environment: ["PATH": "/usr/bin:/bin"]
        )
        controller.ensureStarted()
        let start = Date()
        var timedOut = false
        while Date().timeIntervalSince(start) < 2 {
            if case .failed(reason: "startup_timeout") = controller.status {
                timedOut = true
                break
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(timedOut)
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
        // The bounded fixture exits on its own; no process-name based cleanup.
        try await Task.sleep(for: .seconds(1))

    }

    func testLargeStderrIsDrainedAndTailIsBounded() async throws {
        let capture = BoundedProcessOutput(limit: 1024)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", "/usr/bin/head -c 1048576 /dev/zero >&2; echo tail-marker >&2"]
        process.environment = ["PATH": "/usr/bin:/bin"]
        process.standardError = capture.pipe
        try process.run()
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning && Date() < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        if process.isRunning { process.terminate() }
        XCTAssertFalse(process.isRunning, "stderr must not fill the pipe and stall the child")
        XCTAssertEqual(process.terminationStatus, 0)
        for _ in 0..<100 {
            if capture.snapshot().suffix(12) == Data("tail-marker\n".utf8) { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertLessThanOrEqual(capture.snapshot().count, 1024)
        XCTAssertTrue(String(decoding: capture.snapshot(), as: UTF8.self).hasSuffix("tail-marker\n"))
    }
}
