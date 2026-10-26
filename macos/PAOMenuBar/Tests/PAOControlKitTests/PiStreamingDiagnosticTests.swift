import Foundation
import XCTest
@testable import PAOControlKit

final class PiStreamingDiagnosticTests: XCTestCase {
    private func run(_ result: [String: Any]) throws -> RunView {
        let data = try JSONSerialization.data(withJSONObject: [
            "run_id": "large-pi-run", "task_id": "task", "worker_id": "pi-zai-coding-plan-glm-5.3",
            "status": "FAILED", "started_at": "2026-10-22T00:00:00Z", "result": result
        ])
        return try JSONDecoder().decode(RunView.self, from: data)
    }

    func testProtocolBudgetHasLocalizedExplanationAndOutputRecovery() throws {
        let diagnostic = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
            "exit_code": 70, "worker_process_exit_code": -15,
            "pi_diagnostic_code": "PI_STREAM_LIMIT_EXCEEDED", "worker_failure_class": "PROTOCOL_ERROR"
        ])))
        XCTAssertEqual(diagnostic.code, "PI_STREAM_LIMIT_EXCEEDED")
        XCTAssertEqual(diagnostic.processExitCode, -15)
        XCTAssertEqual(diagnostic.recoveryHint, L10n.workflow("diagnostic.recoverOutput"))
        for language in ["en", "zh-Hans"] {
            XCTAssertNotNil(L10n.catalogString(key: "workflow.diagnostic.PI_STREAM_LIMIT_EXCEEDED",
                                             language: language))
        }
    }

    func testProviderReasonSurvivesOnlyWithFullStreamEvidence() throws {
        var result: [String: Any] = [
            "exit_code": 70, "worker_process_exit_code": 0,
            "pi_diagnostic_code": "PI_PROVIDER_ERROR", "pi_stream_valid": true,
            "pi_session_seen": true, "pi_agent_start_seen": true, "pi_agent_end_seen": true,
            "pi_stop_reason": "error", "output_truncated": true, "timed_out": false,
            "pi_stream_complete": true, "pi_provider_error_reason": "SERVER_OVERLOADED",
            "pi_provider_http_status": 503, "pi_provider_error_code": "overloaded_error",
            "pi_provider_error_sample_chars": 40, "pi_provider_error_sample_at_limit": false,
            "pi_provider_error_sample_sha256": String(repeating: "a", count: 64),
            "pi_error_message": "private-canary"
        ]
        let complete = try XCTUnwrap(WorkerFailureDiagnostic(run: run(result)))
        XCTAssertEqual(complete.providerReason, "SERVER_OVERLOADED")
        XCTAssertEqual(complete.providerHTTPStatus, 503)
        XCTAssertFalse(complete.explanation.contains("private-canary"))
        for value in [false, "private-canary"] as [Any] {
            result["pi_stream_complete"] = value
            XCTAssertNil(try WorkerFailureDiagnostic(run: run(result))?.providerReason)
        }
    }
}
