import Foundation
import SwiftUI

/// Structured quick-submit outcome. Daemon values (task id, state, sanitized detail)
/// are kept verbatim; the presentation layer turns them into localized text.
public enum SubmitNotice: Equatable, Sendable {
    case submitted(taskId: String, state: String)
    case duplicateBlocked(windowSeconds: Int)
    case failed(detail: String)
    case malformedResponse
}

/// Structured cancellation outcome with daemon semantics preserved verbatim.
public enum CancelNotice: Equatable, Sendable {
    case cancelled(taskId: String)
    case alreadyCancelled(taskId: String)
    case runningConflict(taskId: String)
    case failed(detail: String)
    case malformedResponse
}

/// Client-side display state. Authoritative state is always reloaded from the daemon;
/// this store keeps no durable task database of its own.
@MainActor
public final class OrchestratorStore: ObservableObject {
    @Published public private(set) var connection: ConnectionState = .disconnected(reason: .daemonNotRunning)
    @Published public private(set) var tasks: TaskListView?
    @Published public private(set) var dashboard: DashboardSummaryView?
    @Published public private(set) var selectedTaskDetail: TaskDetailView?
    @Published public private(set) var providers: ProviderHealthListView?
    @Published public private(set) var activeStatus: ActiveStatusView?
    @Published public private(set) var lastError: PAOClientError?
    @Published public private(set) var lastSubmittedTaskId: String?
    @Published public private(set) var submitNotice: SubmitNotice?
    @Published public private(set) var cancellationNotice: CancelNotice?
    @Published public var menuVisible: Bool = false
    @Published public var dashboardVisible: Bool = false

    public let socketPath: String
    private let client: PAOControlClient
    private var refreshTask: Task<Void, Never>?
    private var backoffSeconds: Double = 2.0
    private var lastSubmit: (intent: String, at: Date)?
    private let idFactory: () -> String

    public static let menuOpenInterval: TimeInterval = 2.0
    public static let backgroundInterval: TimeInterval = 15.0
    public static let maximumBackoff: TimeInterval = 60.0
    public static let duplicateSubmitWindow: TimeInterval = 5.0

    public init(socketPath: String,
                idFactory: @escaping () -> String = { UUID().uuidString.prefix(12).lowercased() }) {
        self.socketPath = socketPath
        self.client = PAOControlClient(socketPath: socketPath)
        self.idFactory = idFactory
        startRefreshing()
    }

    deinit {
        refreshTask?.cancel()
    }

    public var statusSummary: StatusSummary {
        StatusSummary.derive(connection: connection, tasks: tasks?.tasks ?? [], providers: providers)
    }

    public func taskCounts() -> (running: Int, ready: Int, blocked: Int, verified: Int) {
        let states = (tasks?.tasks ?? []).map(\.state)
        return (
            running: states.filter { $0 == "RUNNING" }.count,
            ready: states.filter { $0 == "READY" || $0 == "SUBMITTED" }.count,
            blocked: states.filter { $0 == "BLOCKED" }.count,
            verified: states.filter { $0 == "VERIFIED" || $0 == "COMPLETED" }.count
        )
    }

    // MARK: - Refresh policy

    private func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while let self, !Task.isCancelled {
                await self.refreshOnce()
                if Task.isCancelled { break }
                let interval = self.nextInterval()
                try? await Task.sleep(for: .seconds(interval))
            }
        }
    }

    private func nextInterval() -> TimeInterval {
        if connection.isConnected {
            backoffSeconds = 2.0
            return (menuVisible || dashboardVisible) ? Self.menuOpenInterval : Self.backgroundInterval
        }
        let current = backoffSeconds
        backoffSeconds = min(backoffSeconds * 2, Self.maximumBackoff)
        return current
    }

    public func refreshNow() async {
        await refreshOnce()
    }

    private func refreshOnce() async {
        switch SocketDiscovery.validate(path: socketPath) {
        case .tooLong(let length):
            transition(.disconnected(reason: .socketPathTooLong(length: length)))
            return
        case .notAbsolute:
            transition(.disconnected(reason: .socketInvalid))
            return
        case .valid:
            break
        }

        do {
            let health = try await client.health()
            guard health.isCompatible else {
                transition(.disconnected(reason: .apiVersionMismatch(version: health.apiVersion)))
                return
            }
            transition(.connected)
            let dashboard = try await client.dashboard()
            self.dashboard = dashboard
            self.tasks = TaskListView(tasks: dashboard.recentTasks, total: dashboard.counts.total)
            self.providers = dashboard.providers
            self.activeStatus = dashboard.activeStatus
            self.lastError = nil
            let counts = taskCounts()
            ClientLog.taskCounts(running: counts.running, ready: counts.ready,
                                 blocked: counts.blocked, verified: counts.verified)
        } catch let error as PAOClientError {
            self.lastError = error
            transition(.disconnected(reason: Self.disconnectionReason(for: error)))
            ClientLog.operation("refresh", outcome: error.logCode)
        } catch {
            transition(.disconnected(reason: .malformedResponse))
            ClientLog.operation("refresh", outcome: "malformed")
        }
    }

    private static func disconnectionReason(for error: PAOClientError) -> ConnectionState.DisconnectionReason {
        switch error {
        case .daemonNotRunning: return .daemonNotRunning
        case .staleSocket, .invalidSocketPath: return .socketInvalid
        case .socketPathTooLong(let length): return .socketPathTooLong(length: length)
        case .accessDenied: return .accessDenied
        case .apiVersionMismatch(let version): return .apiVersionMismatch(version: version)
        case .malformedResponse: return .malformedResponse
        case .httpError, .transportFailure: return .transportFailure
        }
    }

    private func transition(_ newState: ConnectionState) {
        let changed = newState != connection
        connection = newState
        if changed {
            ClientLog.connection(newState)
        }
        if !newState.isConnected {
            tasks = nil
            dashboard = nil
            selectedTaskDetail = nil
            providers = nil
            activeStatus = nil
            // Ephemeral operation success state claims daemon authority; once the
            // connection is gone it must not linger as if still authoritative.
            lastSubmittedTaskId = nil
            submitNotice = nil
            cancellationNotice = nil
        }
    }

    // MARK: - Operations

    /// Quick submit: input becomes a structured task intent through POST /v1/tasks.
    /// It is never interpreted as a shell command.
    public func quickSubmit(intent: String) async {
        let trimmed = intent.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if let last = lastSubmit,
           last.intent == trimmed,
           Date().timeIntervalSince(last.at) < Self.duplicateSubmitWindow {
            submitNotice = .duplicateBlocked(windowSeconds: Int(Self.duplicateSubmitWindow))
            return
        }
        let suffix = idFactory()
        let request = SubmitRequest(
            taskId: "menubar-\(suffix)",
            requestId: "menubar-req-\(suffix)",
            intent: trimmed
        )
        do {
            let task = try await client.submit(request)
            lastSubmit = (trimmed, Date())
            lastSubmittedTaskId = task.taskId
            submitNotice = .submitted(taskId: task.taskId, state: task.state)
            ClientLog.operation("submit", outcome: "ok")
            await refreshOnce()
        } catch let error as PAOClientError {
            submitNotice = .failed(detail: error.displayDetail)
            ClientLog.operation("submit", outcome: error.logCode)
        } catch {
            submitNotice = .malformedResponse
            ClientLog.operation("submit", outcome: "malformed")
        }
    }

    /// Cancel respects daemon semantics exactly; RUNNING conflicts surface verbatim.
    public func cancel(taskId: String) async {
        do {
            let result = try await client.cancel(taskId: taskId)
            cancellationNotice = result.cancelledNow
                ? .cancelled(taskId: taskId)
                : .alreadyCancelled(taskId: taskId)
            ClientLog.operation("cancel", outcome: "ok")
            await refreshOnce()
        } catch let error as PAOClientError {
            if error.isRunningCancelConflict {
                cancellationNotice = .runningConflict(taskId: taskId)
            } else {
                cancellationNotice = .failed(detail: error.displayDetail)
            }
            ClientLog.operation("cancel", outcome: error.logCode)
        } catch {
            cancellationNotice = .malformedResponse
            ClientLog.operation("cancel", outcome: "malformed")
        }
    }

    public func loadTaskDetail(taskId: String) async {
        do {
            selectedTaskDetail = try await client.taskDetail(taskId)
            lastError = nil
        } catch let error as PAOClientError {
            lastError = error
            ClientLog.operation("task-detail", outcome: error.logCode)
        } catch {
            lastError = .malformedResponse
            ClientLog.operation("task-detail", outcome: "malformed")
        }
    }
}

extension PAOClientError {
    var logCode: String {
        switch self {
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
