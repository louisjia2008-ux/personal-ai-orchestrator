import Foundation
import XCTest

@testable import PAOControlKit

final class HTTPResponseFramingTests: XCTestCase {
    private func response(_ headers: String, body: String = "{}", status: String = "HTTP/1.0 200 OK") -> Data {
        Data("\(status)\r\n\(headers)\r\n\r\n\(body)".utf8)
    }

    func testRejectsPrematureEOFDespiteValidJSON() {
        XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: 100")))
    }

    func testRejectsBytesBeyondDeclaredLength() {
        XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: 1")))
        XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: 0")))
    }

    func testRejectsInvalidContentLengthValues() {
        for value in ["", "-2", "+2", "2.0", "0x2", "two", "999999999999999999999999999999999"] {
            XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: \(value)")), value)
        }
    }

    func testRejectsAmbiguousOrUnsupportedFraming() {
        for headers in ["Content-Length: 2\r\ncontent-length: 3", "Content-Length: 2, 3",
                        "Transfer-Encoding: chunked", "Transfer-Encoding: identity",
                        "Content-Length: 2\r\nTransfer-Encoding: chunked"] {
            XCTAssertNil(PAOHTTP.parseResponse(response(headers)), headers)
        }
    }

    func testAllowsRepeatedIdenticalLengths() {
        XCTAssertNotNil(PAOHTTP.parseResponse(response("Content-Length: 2\r\ncontent-length: 2")))
        XCTAssertNotNil(PAOHTTP.parseResponse(response("Content-Length: 2, 2")))
    }

    func testLengthUsesUTF8BytesAndCaseInsensitiveHeaderName() {
        let body = "{\"message\":\"修复🧪\"}"
        XCTAssertNotNil(PAOHTTP.parseResponse(response("cOnTeNt-LeNgTh:\t\(body.utf8.count) \t", body: body)))
        XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: \(body.count)", body: body)))
    }

    func testAllowsCloseDelimitedResponsesAndSanitizedErrorCodes() {
        let body = "{\"error\":\"task_not_found\"}"
        let parsed = PAOHTTP.parseResponse(response("Content-Type: application/json", body: body,
                                                    status: "HTTP/1.0 404 Not Found"))
        XCTAssertEqual(parsed?.status, 404)
        XCTAssertEqual(parsed?.errorCode, "task_not_found")
    }

    func testRejectsMalformedStatusAndHeaderLines() {
        for status in ["garbage 200 OK", "HTTP/2.0 200 OK", "HTTP/1.0 20 OK", "HTTP/1.1 600 BAD"] {
            XCTAssertNil(PAOHTTP.parseResponse(response("Content-Length: 2", status: status)), status)
        }
        for headers in ["Content-Length : 2", " Content-Length: 2", "BrokenHeader", "Content-Length: 2\r\n folded"] {
            XCTAssertNil(PAOHTTP.parseResponse(response(headers)), headers)
        }
    }

    func testRealUDSRejectsIncompleteSuccessResponse() async throws {
        let daemon = TestDaemon()
        let raw = response("Content-Length: 999", body: healthBody)
        daemon.route("GET", "/v1/health", body: healthBody, rawResponse: raw)
        let path = temporarySocketPath("framing")
        try daemon.start(socketPath: path)
        defer { daemon.stop(); unlink(path) }
        do {
            _ = try await PAOControlClient(socketPath: path, timeoutSeconds: 2).health()
            XCTFail("incomplete HTTP response must not produce a healthy daemon")
        } catch let error as PAOClientError {
            XCTAssertEqual(error, .malformedResponse)
        }
    }

    func testRealUDSAcceptsValidResponse() async throws {
        let daemon = TestDaemon()
        daemon.route("GET", "/v1/health", body: healthBody)
        let path = temporarySocketPath("validframing")
        try daemon.start(socketPath: path)
        defer { daemon.stop(); unlink(path) }
        let health = try await PAOControlClient(socketPath: path, timeoutSeconds: 2).health()
        XCTAssertEqual(health.status, "ok")
    }
}
