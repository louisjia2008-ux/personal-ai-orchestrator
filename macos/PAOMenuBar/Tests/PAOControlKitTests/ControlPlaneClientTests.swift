import Foundation

import XCTest

@testable import PAOControlKit

final class ControlPlaneClientTests: XCTestCase {
    func testRequestEncodingIncludesUDSContractHeaders() throws {
        let body = Data("{\"task_id\":\"t\"}".utf8)
        let request = PAOHTTP.encodeRequest(method: "POST", path: "/v1/tasks", body: body)
        let text = String(data: request, encoding: .utf8) ?? ""
        XCTAssertTrue(text.hasPrefix("POST /v1/tasks HTTP/1.1\r\n"))
        XCTAssertTrue(text.contains("Host: localhost\r\n"))
        XCTAssertTrue(text.contains("Content-Type: application/json\r\n"))
        XCTAssertTrue(text.contains("Content-Length: 15\r\n"))
        XCTAssertTrue(text.contains("Connection: close\r\n"))
        XCTAssertTrue(text.hasSuffix("\r\n\r\n{\"task_id\":\"t\"}"))
    }

    func testResponseParsingExtractsStatusBodyAndErrorCode() throws {
        let payload = "HTTP/1.0 409 X\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{}"
        let parsed = PAOHTTP.parseResponse(Data(payload.utf8))
        XCTAssertNotNil(parsed)
        XCTAssertEqual(parsed?.status, 409)

        let errorPayload = "HTTP/1.0 409 X\r\nContent-Length: 78\r\n\r\n"
            + "{\"error\":\"running_task_cancellation_requires_execution_supervisor\"}"
        let errorParsed = PAOHTTP.parseResponse(Data(errorPayload.utf8))
        XCTAssertEqual(errorParsed?.status, 409)
        XCTAssertEqual(errorParsed?.errorCode, "running_task_cancellation_requires_execution_supervisor")

        XCTAssertNil(PAOHTTP.parseResponse(Data("garbage".utf8)))
    }

    func testDaemonUnavailableMapsToDaemonNotRunning() async {
        let path = temporarySocketPath("missing")
        let client = PAOControlClient(socketPath: path, timeoutSeconds: 2)
        do {
            _ = try await client.health()
            XCTFail("expected daemonNotRunning")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .daemonNotRunning)
            XCTAssertEqual(error.displayTitle, "Daemon not running")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testSocketPathTooLongFailsClosed() async {
        let longPath = "/" + String(repeating: "a", count: 200) + ".sock"
        let client = PAOControlClient(socketPath: longPath, timeoutSeconds: 2)
        do {
            _ = try await client.health()
            XCTFail("expected socketPathTooLong")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .socketPathTooLong(longPath.utf8.count))
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testStaleSocketMapsToStaleSocketState() async throws {
        let path = temporarySocketPath("stale")
        // Create a socket file without a listener: bind then close without unlink.
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(path.utf8)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: pathBytes) }
        _ = withUnsafePointer(to: &address) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.bind(fd, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        close(fd)

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 2)
        do {
            _ = try await client.health()
            XCTFail("expected staleSocket")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .staleSocket)
        } catch {
            XCTFail("unexpected error \(error)")
        }
        unlink(path)
    }

    func testFullRoundTripOverRealUDS() async throws {
        let daemon = TestDaemon()
        registerStandardRoutes(daemon)
        daemon.route("POST", "/v1/tasks", status: 201, body: submitResponseBody)
        let path = temporarySocketPath("roundtrip")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        let health = try await client.health()
        XCTAssertEqual(health.apiVersion, "v1")
        XCTAssertTrue(health.isCompatible)

        let tasks = try await client.listTasks(limit: 20)
        XCTAssertEqual(tasks.total, 2)
        XCTAssertEqual(tasks.tasks.first?.state, "RUNNING")

        let providers = try await client.providers()
        XCTAssertEqual(providers.providers.first?.displayName, "MiniMax CN")

        let active = try await client.activeStatus()
        XCTAssertEqual(active.productionActive, "DISABLED_BY_DESIGN")

        let submitted = try await client.submit(
            SubmitRequest(taskId: "menubar-abc", requestId: "menubar-req-abc", intent: "demo")
        )
        XCTAssertEqual(submitted.taskId, "menubar-abc")

        let posted = daemon.receivedRequests.first { $0.method == "POST" && $0.path == "/v1/tasks" }
        XCTAssertNotNil(posted)
        XCTAssertTrue(posted!.body.contains("\"task_id\":\"menubar-abc\""))
        XCTAssertTrue(posted!.body.contains("\"request_id\":\"menubar-req-abc\""))
    }

    func testHTTPErrorSurfacesSanitizedCode() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/health", body: healthBody)
        daemon.route("GET", "/v1/tasks/missing/verification",
                     status: 404,
                     body: "{\"error\":\"task_not_found\"}")
        let path = temporarySocketPath("httperror")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        do {
            _ = try await client.verificationReport(taskId: "missing")
            XCTFail("expected httpError")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .httpError(status: 404, code: "task_not_found"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testMalformedResponseFailsClosed() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/health", body: "not-json-at-all")
        let path = temporarySocketPath("malformed")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        do {
            _ = try await client.health()
            XCTFail("expected malformedResponse")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .malformedResponse)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testAPIVersionMismatchDetection() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/health", body: "{\"status\":\"ok\",\"api_version\":\"v2\"}")
        let path = temporarySocketPath("version")
        try daemon.start(socketPath: path)
        defer { daemon.stop() }

        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        let health = try await client.health()
        XCTAssertFalse(health.isCompatible)
        XCTAssertEqual(health.apiVersion, "v2")
    }

    func testConnectionRecoveryAcrossDaemonRestart() async throws {
        let path = temporarySocketPath("recovery")
        let daemonA = TestDaemon()
        registerStandardRoutes(daemonA)
        try daemonA.start(socketPath: path)
        let client = PAOControlClient(socketPath: path, timeoutSeconds: 3)
        _ = try await client.health()

        daemonA.stop()
        unlink(path)
        do {
            _ = try await client.health()
            XCTFail("expected failure while daemon down")
        } catch {
            // expected: transport failure of some sanitized kind
        }

        let daemonB = TestDaemon()
        registerStandardRoutes(daemonB)
        try daemonB.start(socketPath: path)
        defer { daemonB.stop() }
        let recovered = try await client.health()
        XCTAssertTrue(recovered.isCompatible)
    }
}
