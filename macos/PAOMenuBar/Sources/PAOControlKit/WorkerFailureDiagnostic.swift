import Foundation

/// Static presentation from bounded host codes; never render provider error text.
public struct WorkerFailureDiagnostic: Equatable, Sendable {
    public let code: String
    public let runId: String
    public let processExitCode: Int?
    public let hostExitCode: Int?
    public let providerReason: String?
    public let providerHTTPStatus: Int?
    public let providerErrorCode: String?
    public let providerSampleSHA256: String?
    public let providerSampleAtLimit: Bool?
    private let failureClass: String?

    public static let protocolCodes: Set<String> = [
        "PI_INVALID_UTF8", "PI_INVALID_JSONL", "PI_INVALID_EVENT_SHAPE",
        "PI_MISSING_SESSION_HEADER", "PI_INVALID_TOOL_LIFECYCLE",
        "PI_INCOMPLETE_TOOL_LIFECYCLE", "PI_INVALID_AGENT_LIFECYCLE", "PI_STREAM_LIMIT_EXCEEDED",
        "PI_MISSING_SESSION", "PI_MISSING_AGENT_START", "PI_MISSING_AGENT_END",
        "PI_EXTENSION_ERROR", "PI_MISSING_ASSISTANT_RESULT", "PI_UNEXPECTED_STOP_REASON"
    ]
    public static let otherCodes: Set<String> = [
        "PI_TIMEOUT", "PI_OUTPUT_TRUNCATED", "PI_PROCESS_EXIT", "PI_PROVIDER_ERROR",
        "PI_ABORTED", "PI_MODEL_MISMATCH", "PI_EXPECTED_MODEL_UNAVAILABLE",
        "PI_DIAGNOSTICS_UNAVAILABLE"
    ]
    public static let providerReasons: Set<String> = [
        "AUTH", "QUOTA_EXHAUSTED", "RATE_LIMITED", "SERVER_OVERLOADED",
        "CONTEXT_RESTRICTED", "REQUEST_TIMEOUT", "CONNECTION_FAILED",
        "STREAM_INCOMPLETE", "HTTP_ERROR", "CONFLICTING_ERROR", "UNCLASSIFIED",
        "NO_ERROR_DETAIL"
    ]
    private static let providerCodes: Set<String> = [
        "insufficient_quota", "quota_exceeded", "quota_exhausted", "usage_limit_reached",
        "rate_limit_exceeded", "rate_limit_error", "too_many_requests", "overloaded_error",
        "server_overloaded", "service_unavailable", "context_length_exceeded",
        "context_window_exceeded", "prompt_too_long", "authentication_error",
        "authorization_error", "invalid_api_key", "unauthorized"
    ]

    public init?(run: RunView) {
        guard run.status == "FAILED" else { return nil }
        let rawCode = run.result?["pi_diagnostic_code"]?.stringValue
        if let rawCode, Self.protocolCodes.union(Self.otherCodes).contains(rawCode) {
            code = rawCode
        } else if run.exitCode == 70 && (run.workerId.hasPrefix("pi-")
                    || run.result?["runtime"]?.stringValue == "pi-json") {
            code = "PI_DIAGNOSTICS_UNAVAILABLE"
        } else { return nil }
        runId = run.runId
        hostExitCode = run.exitCode
        processExitCode = run.result?["worker_process_exit_code"]?.intValue
        failureClass = run.result?["worker_failure_class"]?.stringValue
        let provider = Self.providerDetails(run.result, code: code)
        providerReason = provider?.reason
        providerHTTPStatus = provider?.status
        providerErrorCode = provider?.errorCode
        providerSampleSHA256 = provider?.digest
        providerSampleAtLimit = provider?.atLimit
    }

    private static func providerDetails(_ result: [String: RunResultValue]?, code: String)
        -> (reason: String, status: Int?, errorCode: String?, digest: String?, atLimit: Bool)? {
        guard code == "PI_PROVIDER_ERROR", let result,
              result["pi_stream_valid"]?.boolValue == true,
              result["pi_session_seen"]?.boolValue == true,
              result["pi_agent_start_seen"]?.boolValue == true,
              result["pi_agent_end_seen"]?.boolValue == true,
              result["pi_stop_reason"]?.stringValue == "error",
              (result["output_truncated"]?.boolValue == false
                || result["pi_stream_complete"]?.boolValue == true),
              result["timed_out"]?.boolValue == false,
              let reason = result["pi_provider_error_reason"]?.stringValue,
              providerReasons.contains(reason),
              let count = result["pi_provider_error_sample_chars"]?.intValue,
              (0...2048).contains(count),
              let atLimit = result["pi_provider_error_sample_at_limit"]?.boolValue,
              atLimit == (count == 2048) else { return nil }
        let digest = result["pi_provider_error_sample_sha256"]?.stringValue
        if count > 0 {
            guard reason != "NO_ERROR_DETAIL", let digest, digest.count == 64,
                  digest.unicodeScalars.allSatisfy({
                      (48...57).contains(Int($0.value)) || (97...102).contains(Int($0.value))
                  }) else { return nil }
        } else if reason != "NO_ERROR_DETAIL" || digest != nil { return nil }
        let status = result["pi_provider_http_status"]?.intValue
        if result["pi_provider_http_status"] != nil {
            guard let status, (400...599).contains(status), count > 0 else { return nil }
        }
        let errorCode = result["pi_provider_error_code"]?.stringValue
        if result["pi_provider_error_code"] != nil {
            guard let errorCode, providerCodes.contains(errorCode), count > 0 else { return nil }
        }
        return (reason, status, errorCode, digest, atLimit)
    }

    public var explanation: String {
        if code == "PI_PROVIDER_ERROR" {
            return L10n.workflow("diagnostic.provider.\(providerReason ?? "LEGACY_UNRETAINED")")
        }
        return L10n.workflow("diagnostic.\(code)")
    }

    public var recoveryHint: String {
        if let providerReason {
            switch providerReason {
            case "AUTH": return L10n.workflow("diagnostic.providerRecoverAuth")
            case "QUOTA_EXHAUSTED": return L10n.workflow("diagnostic.recoverQuota")
            case "RATE_LIMITED", "SERVER_OVERLOADED":
                return L10n.workflow("diagnostic.recoverTransient")
            case "CONTEXT_RESTRICTED": return L10n.workflow("diagnostic.recoverContext")
            case "REQUEST_TIMEOUT", "CONNECTION_FAILED", "STREAM_INCOMPLETE":
                return L10n.workflow("diagnostic.providerRecoverConnection")
            default: return L10n.workflow("diagnostic.providerRecoverUnknown")
            }
        }
        switch failureClass {
        case "AUTH": return L10n.workflow("diagnostic.recoverAuth")
        case "QUOTA_EXHAUSTED": return L10n.workflow("diagnostic.recoverQuota")
        case "RATE_LIMITED", "SERVER_OVERLOADED":
            return L10n.workflow("diagnostic.recoverTransient")
        case "CONTEXT_RESTRICTED": return L10n.workflow("diagnostic.recoverContext")
        default: break
        }
        switch code {
        case "PI_MODEL_MISMATCH", "PI_EXPECTED_MODEL_UNAVAILABLE":
            return L10n.workflow("diagnostic.recoverModel")
        case "PI_OUTPUT_TRUNCATED", "PI_STREAM_LIMIT_EXCEEDED":
            return L10n.workflow("diagnostic.recoverOutput")
        default: return L10n.workflow("diagnostic.recoverRetry")
        }
    }
}
