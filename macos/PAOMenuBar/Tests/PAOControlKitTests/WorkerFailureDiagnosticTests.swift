import Foundation
import XCTest
@testable import PAOControlKit

final class WorkerFailureDiagnosticTests: XCTestCase {
    private func run(_ result: [String: Any], status: String = "FAILED",
                     worker: String = "pi-zai-coding-plan-glm-5.3") throws -> RunView {
        let data = try JSONSerialization.data(withJSONObject: [
            "run_id": "run-failed", "task_id": "t", "worker_id": worker,
            "status": status, "started_at": "2026-10-22T00:00:00Z", "result": result
        ])
        return try JSONDecoder().decode(RunView.self, from: data)
    }

    func testLegacyProtocolFailureExplicitlyHasNoRetainedDiagnosis() throws {
        let value = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
            "exit_code": 70, "worker_failure_class": "UNCLASSIFIED"
        ])))
        XCTAssertEqual(value.code, "PI_DIAGNOSTICS_UNAVAILABLE")
        XCTAssertEqual(value.runId, "run-failed")
        XCTAssertEqual(value.hostExitCode, 70)
        XCTAssertNil(value.processExitCode)
        XCTAssertNil(try WorkerFailureDiagnostic(run: run(["exit_code": 70], worker: "opencode")))
    }

    func testCompletedOrRunningRunCannotInheritFailedAttemptDiagnosis() throws {
        for status in ["RUNNING", "FINISHED", "CANCELLED"] {
            XCTAssertNil(try WorkerFailureDiagnostic(run: run([
                "exit_code": 70, "pi_diagnostic_code": "PI_INVALID_JSONL"
            ], status: status)))
        }
    }

    func testHostCodesProduceLocalizedRecoveryWithoutRawErrorText() throws {
        let raw = "private-provider-payload-canary"
        let value = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
            "exit_code": 70, "worker_process_exit_code": 0,
            "pi_diagnostic_code": "PI_MISSING_AGENT_END", "pi_error_message": raw,
            "worker_failure_message": raw, "worker_failure_class": "PROTOCOL_ERROR"
        ])))
        XCTAssertEqual(value.code, "PI_MISSING_AGENT_END")
        XCTAssertEqual(value.processExitCode, 0)
        XCTAssertFalse(value.explanation.contains(raw))
        XCTAssertFalse(value.recoveryHint.contains(raw))
        for code in WorkerFailureDiagnostic.protocolCodes.union(WorkerFailureDiagnostic.otherCodes) {
            for language in ["en", "zh-Hans"] {
                XCTAssertNotNil(L10n.catalogString(key: "workflow.diagnostic.\(code)", language: language))
            }
        }
    }

    func testUnrecognizedRemoteCodeIsNeverRendered() throws {
        let value = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
            "exit_code": 70, "pi_diagnostic_code": "private-provider-payload-canary"
        ])))
        XCTAssertEqual(value.code, "PI_DIAGNOSTICS_UNAVAILABLE")
        XCTAssertFalse(value.explanation.contains("canary"))
    }

    func testRecoveryHintsSeparateEstablishedProviderFailures() throws {
        var hints = Set<String>()
        for failure in ["AUTH", "QUOTA_EXHAUSTED", "RATE_LIMITED", "CONTEXT_RESTRICTED", "UNCLASSIFIED"] {
            let value = try XCTUnwrap(WorkerFailureDiagnostic(run: run([
                "exit_code": 70, "pi_diagnostic_code": "PI_PROVIDER_ERROR",
                "worker_failure_class": failure
            ])))
            hints.insert(value.recoveryHint)
        }
        XCTAssertEqual(hints.count, 5)
    }
}
