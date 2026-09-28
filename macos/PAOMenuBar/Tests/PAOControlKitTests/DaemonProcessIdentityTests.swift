import Foundation
import XCTest

@testable import PAOControlKit

final class DaemonProcessIdentityTests: XCTestCase {
    func testHealthIdentityDecodesAndPinsPidPlusInstance() throws {
        let health = try decodeHealth(pid: 4123, instance: "0123456789abcdef0123456789abcdef")

        XCTAssertEqual(
            DaemonProcessIdentity(health: health),
            DaemonProcessIdentity(
                processId: 4123,
                instanceId: "0123456789abcdef0123456789abcdef"
            )
        )
    }

    func testDifferentInstanceWithReusedPidIsNotSameOwnership() throws {
        let first = try XCTUnwrap(DaemonProcessIdentity(
            health: decodeHealth(pid: 4123, instance: "aaaaaaaaaaaaaaaa")
        ))
        let reused = try XCTUnwrap(DaemonProcessIdentity(
            health: decodeHealth(pid: 4123, instance: "bbbbbbbbbbbbbbbb")
        ))

        XCTAssertNotEqual(first, reused)
    }

    func testDifferentPidWithSameInstanceIsNotSameOwnership() throws {
        let first = try XCTUnwrap(DaemonProcessIdentity(
            health: decodeHealth(pid: 4123, instance: "aaaaaaaaaaaaaaaa")
        ))
        let other = try XCTUnwrap(DaemonProcessIdentity(
            health: decodeHealth(pid: 4124, instance: "aaaaaaaaaaaaaaaa")
        ))

        XCTAssertNotEqual(first, other)
    }

    func testPreIdentityDaemonFailsClosed() throws {
        let json = """
        {
          "status": "ok",
          "api_version": "v1",
          "supervisor_steps": []
        }
        """
        let health = try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))

        XCTAssertNil(DaemonProcessIdentity(health: health))
    }

    func testPidOneIsNeverAcceptedAsOwnedDaemon() throws {
        let health = try decodeHealth(pid: 1, instance: "aaaaaaaaaaaaaaaa")

        XCTAssertNil(DaemonProcessIdentity(health: health))
    }

    private func decodeHealth(pid: Int32, instance: String) throws -> HealthView {
        let json = """
        {
          "status": "ok",
          "api_version": "v1",
          "process_id": \(pid),
          "process_instance_id": "\(instance)",
          "supervisor_steps": []
        }
        """
        return try JSONDecoder().decode(HealthView.self, from: Data(json.utf8))
    }
}

private extension DaemonProcessIdentity {
    init(processId: Int32, instanceId: String) {
        self.init(health: try! JSONDecoder().decode(
            HealthView.self,
            from: Data("""
            {
              "status": "ok",
              "api_version": "v1",
              "process_id": \(processId),
              "process_instance_id": "\(instanceId)",
              "supervisor_steps": []
            }
            """.utf8)
        ))!
    }
}
