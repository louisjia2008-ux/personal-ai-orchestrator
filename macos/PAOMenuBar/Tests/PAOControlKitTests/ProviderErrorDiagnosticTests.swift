import Foundation
import XCTest
@testable import PAOControlKit

final class ProviderErrorDiagnosticTests: XCTestCase {
    private func run(_ overrides: [String: Any] = [:], status: String = "FAILED") throws -> RunView {
        var result: [String: Any] = [
            "exit_code": 70, "worker_process_exit_code": 0,
            "pi_diagnostic_code": "PI_PROVIDER_ERROR", "runtime": "pi-json",
            "pi_stream_valid": true, "pi_session_seen": true,
            "pi_agent_start_seen": true, "pi_agent_end_seen": true,
            "pi_stop_reason": "error", "output_truncated": false, "timed_out": false,
            "pi_provider_error_reason": "SERVER_OVERLOADED", "pi_provider_http_status": 503,
            "pi_provider_error_code": "overloaded_error",
            "pi_provider_error_sample_sha256": String(repeating: "a", count: 64),
            "pi_provider_error_sample_chars": 100, "pi_provider_error_sample_at_limit": false,
            "pi_error_message": "private-provider-canary", "stdout_tail": "private-transcript-canary"
        ]
        result.merge(overrides) { _, new in new }
        let data = try JSONSerialization.data(withJSONObject: [
            "run_id": "provider-run", "task_id": "task", "worker_id": "pi-zai-coding-plan-glm-5.3",
            "status": status, "started_at": "2026-10-22T00:00:00Z", "result": result
        ])
        return try JSONDecoder().decode(RunView.self, from: data)
    }

    func testUsefulStaticProviderDetailsAreShownWithoutRawText() throws {
        let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: run()))
        XCTAssertEqual(diagnostic.providerReason, "SERVER_OVERLOADED")
        XCTAssertEqual(diagnostic.providerHTTPStatus, 503)
        XCTAssertEqual(diagnostic.providerErrorCode, "overloaded_error")
        XCTAssertEqual(diagnostic.providerSampleSHA256, String(repeating: "a", count: 64))
        XCTAssertEqual(diagnostic.providerSampleAtLimit, false)
        XCTAssertFalse(diagnostic.explanation.contains("canary"))
        XCTAssertFalse(diagnostic.recoveryHint.contains("canary"))
        XCTAssertEqual(diagnostic.processExitCode, 0)
        XCTAssertEqual(diagnostic.hostExitCode, 70)
    }

    func testMalformedProviderTupleFallsBackWithoutRenderingUntrustedValues() throws {
        let cases: [[String: Any]] = [
            ["pi_provider_error_reason": "private-canary"],
            ["pi_provider_http_status": true], ["pi_provider_http_status": 999],
            ["pi_provider_http_status": "private-canary"],
            ["pi_provider_error_code": "private-canary"],
            ["pi_provider_error_sample_sha256": "private-canary"],
            ["pi_provider_error_sample_chars": true], ["pi_provider_error_sample_chars": -1],
            ["pi_provider_error_sample_chars": 2049], ["pi_provider_error_sample_at_limit": true],
            ["pi_agent_end_seen": false], ["pi_stop_reason": "stop"], ["output_truncated": true]
        ]
        for fields in cases {
            let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: run(fields)))
            XCTAssertNil(diagnostic.providerReason, "\(fields.keys)")
            XCTAssertNil(diagnostic.providerSampleSHA256)
            XCTAssertFalse(diagnostic.explanation.contains("canary"))
        }
    }

    func testSampleLimitIsAnObservationAndNotAnExecutionResult() throws {
        let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
            "pi_provider_error_sample_chars": 2048, "pi_provider_error_sample_at_limit": true
        ])))
        XCTAssertTrue(try XCTUnwrap(diagnostic.providerSampleAtLimit))
        for status in ["RUNNING", "FINISHED", "CANCELLED"] {
            XCTAssertNil(try WorkerFailureDiagnostic(run: run(status: status)))
        }
    }

    func testEveryProviderReasonHasBothTranslationsAndSafeRecovery() throws {
        var hints = Set<String>()
        for reason in WorkerFailureDiagnostic.providerReasons where reason != "NO_ERROR_DETAIL" {
            let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
                "pi_provider_error_reason": reason
            ])))
            XCTAssertEqual(diagnostic.providerReason, reason)
            XCTAssertFalse(diagnostic.explanation.contains("canary"))
            hints.insert(diagnostic.recoveryHint)
            for language in ["en", "zh-Hans"] {
                XCTAssertNotNil(L10n.catalogString(key: "workflow.diagnostic.provider.\(reason)", language: language))
            }
        }
        XCTAssertGreaterThanOrEqual(hints.count, 6)
    }

    func testLegacyProviderFailureExplainsWhyItsCauseCannotBeReconstructed() throws {
        let legacy: [String: Any] = [
            "run_id": "legacy", "task_id": "t", "worker_id": "pi-zai-coding-plan-glm-5.3",
            "status": "FAILED", "started_at": "2026-10-22T00:00:00Z",
            "result": ["exit_code": 70, "pi_diagnostic_code": "PI_PROVIDER_ERROR",
                       "worker_failure_class": "UNCLASSIFIED"]
        ]
        let data = try JSONSerialization.data(withJSONObject: legacy)
        let value = try JSONDecoder().decode(RunView.self, from: data)
        let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: value))
        XCTAssertNil(diagnostic.providerReason)
        XCTAssertNil(diagnostic.providerSampleSHA256)
        XCTAssertEqual(diagnostic.explanation, L10n.workflow("diagnostic.provider.LEGACY_UNRETAINED"))
    }
}
