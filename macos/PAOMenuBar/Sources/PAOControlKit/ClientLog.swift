import Foundation

import OSLog

/// Privacy-conscious client logging: connection transitions, operation categories and
/// sanitized status/error codes only. Task intent text and any credential-shaped value are
/// never logged. Set PAO_MENUBAR_STDERR_LOG=1 to mirror transitions to stderr for local
/// acceptance evidence.
public enum ClientLog {
    static let logger = Logger(subsystem: "com.pao.menubar", category: "controlplane")
    static let stderrEnabled = ProcessInfo.processInfo.environment["PAO_MENUBAR_STDERR_LOG"] == "1"

    public static func connection(_ state: ConnectionState) {
        let rendered: String
        switch state {
        case .connected: rendered = "CONNECTED"
        case .disconnected(let reason): rendered = "DISCONNECTED:\(describe(reason))"
        }
        logger.info("connection=\(rendered, privacy: .public)")
        mirrorToStderr("connection=\(rendered)")
    }

    public static func operation(_ category: String, outcome: String) {
        logger.info("op=\(category, privacy: .public) outcome=\(outcome, privacy: .public)")
        mirrorToStderr("op=\(category) outcome=\(outcome)")
    }

    public static func taskCounts(running: Int, ready: Int, blocked: Int, verified: Int) {
        let line = "tasks running=\(running) ready=\(ready) blocked=\(blocked) verified=\(verified)"
        logger.info("\(line, privacy: .public)")
        mirrorToStderr(line)
    }

    static func describe(_ reason: ConnectionState.DisconnectionReason) -> String {
        switch reason {
        case .daemonNotRunning: return "DAEMON_NOT_RUNNING"
        case .socketInvalid: return "SOCKET_INVALID"
        case .socketPathTooLong(let length): return "SOCKET_PATH_TOO_LONG(\(length))"
        case .accessDenied: return "ACCESS_DENIED"
        case .apiVersionMismatch(let version): return "API_VERSION_MISMATCH(\(version))"
        case .malformedResponse: return "MALFORMED_RESPONSE"
        case .transportFailure: return "TRANSPORT_FAILURE"
        }
    }

    static func mirrorToStderr(_ line: String) {
        if stderrEnabled {
            FileHandle.standardError.write(Data(("pao-menubar \(line)\n").utf8))
        }
    }
}
