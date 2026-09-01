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

/// Structured owner-dispatch outcome; sanitized daemon codes surface verbatim.
public enum DispatchNotice: Equatable, Sendable {
    case dispatched(taskId: String, dispatchId: String, status: String)
    case blocked(taskId: String, code: String)
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
    @Published public private(set) var providerDiscoveryStatus: ProviderDiscoveryStatusView?
    @Published public private(set) var isRefreshingProviders: Bool = false
    @Published public private(set) var activeStatus: ActiveStatusView?
    @Published public private(set) var ownerExecutionSettings: OwnerExecutionSettingsView?
    @Published public private(set) var lastDispatch: DispatchTaskView?
    @Published public private(set) var dispatchNotice: DispatchNotice?
    @Published public private(set) var lastError: PAOClientError?
    @Published public private(set) var lastSubmittedTaskId: String?
    @Published public private(set) var submitNotice: SubmitNotice?
    @Published public private(set) var cancellationNotice: CancelNotice?
    @Published public var menuVisible: Bool = false
    @Published public var dashboardVisible: Bool = false
    @Published public private(set) var isRefreshing: Bool = false
    @Published public var selectedTaskId: String?

    public let socketPath: String
    public let daemonLifecycle: DaemonLifecycleController
    public let widgetSnapshotBridge: WidgetSnapshotBridge
    public let widgetSnapshotWriter: WidgetSnapshotWriter
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
                daemonConfiguration: DaemonLaunchConfiguration? = nil,
                widgetSnapshotBridge: WidgetSnapshotBridge? = nil,
                autoStartDaemon: Bool = true,
                idFactory: @escaping () -> String = { UUID().uuidString.prefix(12).lowercased() }) {
        self.socketPath = socketPath
        let client = PAOControlClient(socketPath: socketPath)
        self.client = client
        let configuration = daemonConfiguration ?? DaemonLaunchConfiguration()
        self.daemonLifecycle = DaemonLifecycleController(
            configuration: configuration,
            client: client
        )
        self.widgetSnapshotBridge = widgetSnapshotBridge ?? .appOwned(layout: configuration.layout)
        self.widgetSnapshotWriter = WidgetSnapshotWriter(
            primary: self.widgetSnapshotBridge,
            fallback: .appOwned(layout: configuration.layout)
        )
        self.idFactory = idFactory
        if autoStartDaemon {
            daemonLifecycle.ensureStarted()
        }
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
        switch daemonLifecycle.status {
        case .alreadyRunning, .starting, .startedByApp, .healthyStartedByApp, .healthyPreexisting:
            return 1.0
        default:
            break
        }
        let current = backoffSeconds
        backoffSeconds = min(backoffSeconds * 2, Self.maximumBackoff)
        return current
    }

    /// User-triggered refresh. Re-entrant clicks coalesce into one underlying
    /// refresh so rapid toolbar clicks cannot spawn concurrent polls.
    public func refreshNow() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
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
            if let settings = try? await client.ownerExecutionSettings() {
                self.ownerExecutionSettings = settings
            }
            self.lastError = nil
            await refreshProviderStatusSilently()
            let counts = taskCounts()
            ClientLog.taskCounts(running: counts.running, ready: counts.ready,
                                 blocked: counts.blocked, verified: counts.verified)
            writeWidgetSnapshot()
        } catch let error as PAOClientError {
            self.lastError = error
            transition(.disconnected(reason: Self.disconnectionReason(for: error)))
            writeWidgetSnapshot()
            ClientLog.operation("refresh", outcome: error.logCode)
        } catch {
            transition(.disconnected(reason: .malformedResponse))
            writeWidgetSnapshot()
            ClientLog.operation("refresh", outcome: "malformed")
        }
    }

    private func writeWidgetSnapshot() {
        let snapshot = WidgetSnapshot(
            generatedAt: Date(),
            connection: connection,
            daemonLifecycle: daemonLifecycle.status,
            dashboard: dashboard
        )
        widgetSnapshotWriter.write(snapshot)
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
            ownerExecutionSettings = nil
            lastDispatch = nil
            // Ephemeral operation success state claims daemon authority; once the
            // connection is gone it must not linger as if still authoritative.
            lastSubmittedTaskId = nil
            submitNotice = nil
            cancellationNotice = nil
            dispatchNotice = nil
        }
    }

    // MARK: - Operations

    /// Owner-initiated dispatch: explicit, one task at a time, through
    /// the Safety Kernel gates. Client supplies no authority field; the
    /// daemon derives OWNER_INITIATED_EXECUTION itself.
    public func dispatch(taskId: String, executionTargetId: String) async {
        let suffix = idFactory()
        do {
            // Fetch the authoritative state version right before dispatch;
            // stale versions are rejected by the daemon with 409.
            let task = try await client.getTask(taskId)
            let request = DispatchRequest(
                requestId: "dispatch-\(suffix)",
                taskStateVersion: task.stateVersion,
                executionTargetId: executionTargetId
            )
            let result = try await client.dispatch(taskId: taskId, request: request)
            lastDispatch = result
            dispatchNotice = result.accepted
                ? .dispatched(taskId: taskId, dispatchId: result.dispatchId, status: result.status)
                : .blocked(taskId: taskId, code: result.failureCode ?? "BLOCKED")
            ClientLog.operation("dispatch", outcome: "ok")
            await loadTaskDetail(taskId: taskId)
            await refreshOnce()
        } catch let error as PAOClientError {
            dispatchNotice = .blocked(taskId: taskId, code: error.displayDetail)
            ClientLog.operation("dispatch", outcome: error.logCode)
        } catch {
            dispatchNotice = .malformedResponse
            ClientLog.operation("dispatch", outcome: "malformed")
        }
    }

    /// Toggle the persisted Owner-Initiated Execution setting. This is a
    /// separate concept from Production ACTIVE (disabled by design).
    public func setOwnerExecution(enabled: Bool) async {
        do {
            ownerExecutionSettings = try await client.setOwnerExecutionEnabled(enabled)
            ClientLog.operation("owner-execution", outcome: "ok")
        } catch let error as PAOClientError {
            lastError = error
            ClientLog.operation("owner-execution", outcome: error.logCode)
        } catch {
            ClientLog.operation("owner-execution", outcome: "malformed")
        }
    }

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

    /// Convenience: returns the cached task list (does not refresh).
    public var cachedTasks: [TaskView] {
        tasks?.tasks ?? []
    }

    /// Move `selectedTaskId` to the neighbour of the current selection in the
    /// supplied list (filtered/searched scope). Returns the new selection or
    /// `nil` when there is nothing navigable. Pure presentation navigation:
    /// authoritative state is never mutated.
    @discardableResult
    public func navigateToNeighbour(current: String?, in scope: [TaskView], offset: Int) -> String? {
        guard !scope.isEmpty else { return current }
        let ids = scope.map(\.taskId)
        let next: String?
        if let current, let idx = ids.firstIndex(of: current) {
            let count = ids.count
            let target = ((idx + offset) % count + count) % count
            next = ids[target]
        } else {
            next = offset >= 0 ? ids.first : ids.last
        }
        selectedTaskId = next
        if let next {
            Task { await loadTaskDetail(taskId: next) }
        } else {
            selectedTaskDetail = nil
        }
        return next
    }

    /// User-triggered provider-discovery refresh. Coalesces rapid clicks into a
    /// single bounded call against `/v1/providers/refresh`.
    public func refreshProviders() async {
        guard !isRefreshingProviders else { return }
        isRefreshingProviders = true
        defer { isRefreshingProviders = false }
        do {
            let status = try await client.refreshProviders()
            providerDiscoveryStatus = status
            let providers = try? await client.providers()
            if let providers { self.providers = providers }
            ClientLog.operation("refresh_providers", outcome: status.discoveryState.lowercased())
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("refresh_providers", outcome: error.logCode)
        } catch {
            ClientLog.operation("refresh_providers", outcome: "malformed")
        }
    }

    /// Internal: fetch provider-discovery status without surfacing a
    /// spinner. Called after each successful dashboard refresh so the
    /// Providers/Agents/Quota cards reflect the most recent discovery
    /// cycle even when the user has not clicked the explicit refresh.
    private func refreshProviderStatusSilently() async {
        do {
            let status = try await client.providerDiscoveryStatus()
            providerDiscoveryStatus = status
        } catch {
            // Silent: dashboard already reports connection health.
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
