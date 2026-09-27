import Darwin
import Foundation
import XCTest

@testable import PAOControlKit

final class DaemonProcessOwnershipTests: XCTestCase {
    func testSocketPeerPIDUsesKernelSocketOwnerIdentity() throws {
        let socketPath = temporarySocketPath("peer-pid")
        let daemon = TestDaemon()
        try daemon.start(socketPath: socketPath)
        defer {
            daemon.stop()
            unlink(socketPath)
        }

        XCTAssertEqual(
            DaemonProcessOwnership.socketPeerPID(at: socketPath),
            getpid()
        )
    }

    func testExactExecutableIdentityIsAccepted() throws {
        let identity = try DaemonProcessOwnership.validate(
            pid: 123,
            expectedHelperPath: "/tmp/pao-daemon",
            actualExecutablePath: "/tmp/pao-daemon"
        )
        XCTAssertEqual(identity.pid, 123)
        XCTAssertEqual(identity.executablePath, "/tmp/pao-daemon")
    }

    func testPIDReuseOrExecutableMismatchFailsClosed() {
        XCTAssertThrowsError(
            try DaemonProcessOwnership.validate(
                pid: 123,
                expectedHelperPath: "/Applications/PAO.app/Contents/Helpers/pao-daemon",
                actualExecutablePath: "/usr/bin/python3"
            )
        ) { error in
            XCTAssertEqual(
                error as? DaemonProcessOwnershipError,
                .executableMismatch
            )
        }
    }

    func testAlreadyDeadPIDFailsClosedWithoutGuessing() {
        XCTAssertThrowsError(
            try DaemonProcessOwnership.validate(
                pid: 999_999,
                expectedHelperPath: "/tmp/pao-daemon",
                actualExecutablePath: nil
            )
        ) { error in
            XCTAssertEqual(
                error as? DaemonProcessOwnershipError,
                .executableUnavailable(pid: 999_999)
            )
        }
    }

    func testMissingSocketDoesNotInventAnOwner() {
        XCTAssertNil(
            DaemonProcessOwnership.socketPeerPID(
                at: temporarySocketPath("missing-peer")
            )
        )
    }

    func testSignalTargetsOnlyTheValidatedPID() throws {
        let first = Process()
        first.executableURL = URL(fileURLWithPath: "/bin/sleep")
        first.arguments = ["60"]

        let second = Process()
        second.executableURL = URL(fileURLWithPath: "/bin/sleep")
        second.arguments = ["60"]

        try first.run()
        try second.run()
        defer {
            if first.isRunning { first.terminate() }
            if second.isRunning { second.terminate() }
            first.waitUntilExit()
            second.waitUntilExit()
        }

        let identity = try DaemonProcessOwnership.validate(
            pid: first.processIdentifier,
            expectedHelperPath: "/bin/sleep",
            actualExecutablePath: DaemonProcessOwnership.executablePath(
                pid: first.processIdentifier
            )
        )
        try DaemonProcessOwnership.signalTermination(pid: identity.pid)
        first.waitUntilExit()

        XCTAssertFalse(first.isRunning)
        XCTAssertTrue(second.isRunning)
    }
}
