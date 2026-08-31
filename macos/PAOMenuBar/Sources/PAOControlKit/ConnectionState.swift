import Foundation

/// Sanitized client-side error taxonomy derived only from transport outcomes and the
/// daemon's sanitized error codes. No internal daemon detail is ever surfaced.
public enum PAOClientError: Error, Equatable, Sendable {
    case socketPathTooLong(Int)
    case daemonNotRunning
    case staleSocket
    case accessDenied
    case apiVersionMismatch(String)
    case httpError(status: Int, code: String)
    case malformedResponse
    case invalidSocketPath
    case transportFailure

    public var displayTitle: String {
        switch self {
        case .socketPathTooLong: return "Socket path too long"
        case .daemonNotRunning: return "Daemon not running"
        case .staleSocket: return "Socket invalid (stale)"
        case .accessDenied: return "Permission denied"
        case .apiVersionMismatch: return "API version mismatch"
        case .httpError: return "Request rejected"
        case .malformedResponse: return "Malformed daemon response"
        case .invalidSocketPath: return "Invalid socket path"
        case .transportFailure: return "Transport failure"
        }
    }

    public var displayDetail: String {
        switch self {
        case .socketPathTooLong(let length):
            return "Unix socket path is \(length) bytes; macOS allows at most 104."
        case .daemonNotRunning:
            return "No daemon is listening on the configured control socket."
        case .staleSocket:
            return "A socket file exists but nothing is serving it."
        case .accessDenied:
            return "The current user may not connect to this socket."
        case .apiVersionMismatch(let version):
            return "Daemon reports API version \(version); this client speaks v1."
        case .httpError(let status, let code):
            return "\(code) (HTTP \(status))"
        case .malformedResponse:
            return "The daemon response could not be decoded."
        case .invalidSocketPath:
            return "Configure a valid absolute socket path."
        case .transportFailure:
            return "The control socket transport failed."
        }
    }

    /// Renders the documented 409 running-task cancellation semantics verbatim.
    public var isRunningCancelConflict: Bool {
        if case .httpError(409, "running_task_cancellation_requires_execution_supervisor") = self {
            return true
        }
        return false
    }
}

public enum ConnectionState: Equatable, Sendable {
    case disconnected(reason: DisconnectionReason)
    case connected

    public enum DisconnectionReason: Equatable, Sendable {
        case daemonNotRunning
        case socketInvalid
        case socketPathTooLong(length: Int)
        case accessDenied
        case apiVersionMismatch(version: String)
        case malformedResponse
        case transportFailure
    }

    public var isConnected: Bool {
        if case .connected = self { return true }
        return false
    }
}
