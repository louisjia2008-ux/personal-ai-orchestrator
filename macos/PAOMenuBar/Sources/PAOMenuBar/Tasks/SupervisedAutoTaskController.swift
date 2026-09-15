import Foundation

import PAOControlKit

/// Ephemeral task-detail controller for supervised-auto owner actions.
///
/// It deliberately owns no durable task state. Each action constructs a client
/// for the app's current control socket, re-fetches the authoritative TaskView
/// immediately before mutation, submits the exact state version, and lets the
/// caller reload TaskDetail afterward. The daemon remains the only authority.
@MainActor
final class SupervisedAutoTaskController: ObservableObject {
    enum Action: String, Hashable {
        case ack
        case veto
        case dispatchNow
    }

    enum Notice: Equatable {
        case acknowledged
        case vetoed
        case dispatchRequested(status: String)
        case staleState
        case blocked(code: String)
        case failed(detail: String)
        case malformedResponse
    }

    @Published private(set) var inFlight: Set<Action> = []
    @Published private(set) var notice: Notice?

    func isInFlight(_ action: Action) -> Bool {
        inFlight.contains(action)
    }

    func clearNotice() {
        notice = nil
    }

    func acknowledge(taskId: String, socketPath: String) async {
        guard begin(.ack) else { return }
        defer { end(.ack) }
        let client = PAOControlClient(socketPath: socketPath)
        do {
            let task = try await client.getTask(taskId)
            _ = try await client.autoAck(
                taskId: taskId,
                taskStateVersion: task.stateVersion
            )
            notice = .acknowledged
            ClientLog.operation("auto_ack", outcome: "ok")
        } catch let error as PAOClientError {
            notice = mapped(error)
            ClientLog.operation("auto_ack", outcome: logCode(error))
        } catch {
            notice = .malformedResponse
            ClientLog.operation("auto_ack", outcome: "malformed")
        }
    }

    func veto(taskId: String, socketPath: String) async {
        guard begin(.veto) else { return }
        defer { end(.veto) }
        let client = PAOControlClient(socketPath: socketPath)
        do {
            let task = try await client.getTask(taskId)
            _ = try await client.autoVeto(
                taskId: taskId,
                requestId: "veto-\(UUID().uuidString.lowercased())",
                taskStateVersion: task.stateVersion
            )
            notice = .vetoed
            ClientLog.operation("auto_veto", outcome: "ok")
        } catch let error as PAOClientError {
            notice = mapped(error)
            ClientLog.operation("auto_veto", outcome: logCode(error))
        } catch {
            notice = .malformedResponse
            ClientLog.operation("auto_veto", outcome: "malformed")
        }
    }

    func dispatchNow(taskId: String, socketPath: String) async {
        guard begin(.dispatchNow) else { return }
        defer { end(.dispatchNow) }
        let client = PAOControlClient(socketPath: socketPath)
        do {
            let task = try await client.getTask(taskId)
            let result = try await client.autoDispatchNow(
                taskId: taskId,
                taskStateVersion: task.stateVersion
            )
            if result.accepted {
                notice = .dispatchRequested(status: result.status)
                ClientLog.operation("auto_dispatch_now", outcome: "ok")
            } else {
                notice = .blocked(code: result.failureCode ?? "BLOCKED")
                ClientLog.operation("auto_dispatch_now", outcome: "refused")
            }
        } catch let error as PAOClientError {
            notice = mapped(error)
            ClientLog.operation("auto_dispatch_now", outcome: logCode(error))
        } catch {
            notice = .malformedResponse
            ClientLog.operation("auto_dispatch_now", outcome: "malformed")
        }
    }

    private func begin(_ action: Action) -> Bool {
        guard !inFlight.contains(action) else { return false }
        inFlight.insert(action)
        return true
    }

    private func end(_ action: Action) {
        inFlight.remove(action)
    }

    private func mapped(_ error: PAOClientError) -> Notice {
        if case .httpError(409, let code) = error,
           code == "stale_task_state_version"
            || code == "task_state_not_auto"
            || code == "task_state_not_auto_grace" {
            return .staleState
        }
        if case .httpError(_, let code) = error {
            return .blocked(code: code)
        }
        return .failed(detail: error.displayDetail)
    }

    private func logCode(_ error: PAOClientError) -> String {
        switch error {
        case .socketPathTooLong(let length): return "socket_path_too_long(\(length))"
        case .daemonNotRunning: return "daemon_not_running"
        case .staleSocket: return "stale_socket"
        case .accessDenied: return "access_denied"
        case .apiVersionMismatch(let version): return "api_version_mismatch(\(version))"
        case .httpError(let status, let code): return "http_\(status)_\(code)"
        case .malformedResponse: return "malformed_response"
        case .invalidSocketPath: return "invalid_socket_path"
        case .transportFailure: return "transport_failure"
        }
    }
}
