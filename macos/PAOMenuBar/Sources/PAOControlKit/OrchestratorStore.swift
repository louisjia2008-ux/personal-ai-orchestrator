import Foundation
import SwiftUI

/// Structured quick-submit outcome. Daemon values (task id, state, sanitized detail)
/// are kept verbatim; the presentation layer turns them into localized text.
public enum SubmitNotice: Equatable, Sendable {
    case submitted(taskId: String, state: String)
    case duplicateBlocked(windowSeconds: Int)
    case projectRequired
    case manualTargetRequired
    case failed(detail: String)
    case malformedResponse
}

public enum ProjectNotice: Equatable, Sendable {
    case resolved(projectId: String)
    case registered(projectId: String)
    case removed(projectId: String)
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
    @Published public private(set) var projects: ProjectListView?
    @Published public private(set) var pendingProjectPreview: ProjectView?
    @Published public private(set) var selectedTaskDetail: TaskDetailView?
    @Published public private(set) var providers: ProviderHealthListView?
    @Published public private(set) var providerConnections: ProviderConnectionListView?
    @Published public private(set) var providerDiscoveryStatus: ProviderDiscoveryStatusView?
    @Published public private(set) var isRefreshingProviders: Bool = false
    /// Connection-based quota projection. Separate from `providers` because a
    /// connected provider must stay visible here even with zero quota evidence.
    @Published public private(set) var quota: QuotaOverviewView?
    @Published public private(set) var isRefreshingQuota: Bool = false
    /// Why the last quota refresh failed, or nil when it succeeded (or has not
    /// run). A failed refresh keeps the previous projection on screen; without
    /// this the failure would be indistinguishable from "nothing happened".
    @Published public private(set) var lastQuotaRefreshError: String?
    @Published public private(set) var activeStatus: ActiveStatusView?
    @Published public private(set) var ownerExecutionSettings: OwnerExecutionSettingsView?
    @Published public private(set) var schedulingSettings: SchedulingSettingsView?
    @Published public private(set) var lastDispatch: DispatchTaskView?
    @Published public private(set) var dispatchNotice: DispatchNotice?
    /// A task that just reached a terminal state, for the completion banner
    /// and the system notification. Owner-initiated cancellations are not
    /// completion events: the owner already knows, they caused it.
    @Published public private(set) var taskCompletionNotice: TaskCompletionNotice?
    @Published public private(set) var daemonBuild: BuildView?
    @Published public private(set) var lastError: PAOClientError?
    @Published public private(set) var lastSubmittedTaskId: String?
    @Published public private(set) var submitNotice: SubmitNotice?
    @Published public private(set) var projectNotice: ProjectNotice?
    @Published public private(set) var cancellationNotice: CancelNotice?
    @Published public var menuVisible: Bool = false
    @Published public var dashboardVisible: Bool = false
    @Published public private(set) var isRefreshing: Bool = false
    @Published public var selectedTaskId: String?
    @Published public var selectedProjectId: String?
    @Published public var selectedSchedulingPolicy: String = "BALANCED"
    @Published public var selectedManualExecutionTargetId: String?

    /// Whether the dashboard binary and the connected daemon came from the same
    /// commit. Surfaced in Settings so stale-stack truth is never presented as live.
    public var buildCompatibility: BuildCompatibility {
        BuildCompatibility.compare(app: BuildIdentity.current, daemonCommitSHA: daemonBuild?.commitSHA)
    }

    public let socketPath: String
    public let daemonLifecycle: DaemonLifecycleController
    public let widgetSnapshotBridge: WidgetSnapshotBridge
    public let widgetSnapshotWriter: WidgetSnapshotWriter
    private let client: PAOControlClient
    private var refreshTask: Task<Void, Never>?
    private var quotaAutoRefreshTask: Task<Void, Never>?
    private var backoffSeconds: Double = 2.0
    private var previousTaskStates: [String: String] = [:]
    private var lastSubmit: (intent: String, at: Date)?
    private let idFactory: () -> String

    /// How many tasks the dashboard asks for.
    ///
    /// Matches the daemon's MAX_LIST_LIMIT. The daemon clamps anything larger, so
    /// this is the most a single request can return; beyond it the collection is
    /// genuinely truncated and `taskCollectionIsTruncated` says so rather than the
    /// UI quietly showing a subset.
    public static let taskListLimit: Int = 200

    public static let menuOpenInterval: TimeInterval = 2.0
    public static let activeTaskInterval: TimeInterval = 1.0
    public static let backgroundInterval: TimeInterval = 15.0
    public static let maximumBackoff: TimeInterval = 60.0
    public static let duplicateSubmitWindow: TimeInterval = 5.0

    /// How often the client asks the daemon to re-collect quota from the
    /// providers' own read-only endpoints. The projection is re-read on every
    /// dashboard refresh, but daemon-side collection only happens when
    /// something asks for it; ten minutes keeps readings current without
    /// hammering provider APIs.
    public static let quotaAutoRefreshInterval: TimeInterval = 600

    /// The quota-refresh endpoint answers only after reading every connected
    /// provider sequentially, so it gets a budget of its own rather than the
    /// 10-second default every other call shares.
    private static let quotaRefreshTimeoutSeconds: Double = 30

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
        startQuotaAutoRefresh()
    }

    deinit {
        refreshTask?.cancel()
        quotaAutoRefreshTask?.cancel()
    }

    public var statusSummary: StatusSummary {
        StatusSummary.derive(connection: connection, tasks: tasks?.tasks ?? [], providers: providers)
    }

    /// True when the daemon holds more tasks than this client fetched.
    ///
    /// Task counts are computed over the whole store while the list is bounded, so
    /// a truncated collection must be visible rather than inferred: a filter that
    /// silently searches a subset is indistinguishable from one that found nothing.
    public var taskCollectionIsTruncated: Bool {
        guard let tasks else { return false }
        return tasks.tasks.count < tasks.total
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

    /// Terminal states a task never leaves. A transition into one of these
    /// from a live state is the completion event the dashboard announces.
    private static let terminalTaskStates: Set<String> = [
        "VERIFIED", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED"
    ]

    /// States worth interrupting the owner for. Cancellation is excluded:
    /// it is owner-initiated feedback, not news.
    private static let announcedTaskStates: Set<String> = [
        "VERIFIED", "COMPLETED", "FAILED", "BLOCKED"
    ]

    private func detectTaskCompletions(in tasks: [TaskView]) {
        var next: [String: String] = [:]
        for task in tasks {
            next[task.taskId] = task.state
            guard let previous = previousTaskStates[task.taskId],
                  Self.terminalTaskStates.contains(task.state),
                  !Self.terminalTaskStates.contains(previous),
                  Self.announcedTaskStates.contains(task.state)
            else { continue }
            taskCompletionNotice = TaskCompletionNotice(
                taskId: task.taskId,
                state: task.state,
                intent: task.intent
            )
            ClientLog.operation(
                "task_completed",
                outcome: "\(task.taskId)=\(task.state)"
            )
        }
        previousTaskStates = next
    }

    /// Dismiss the completion banner. The underlying task stays selectable.
    public func clearTaskCompletionNotice() {
        taskCompletionNotice = nil
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
            if let detail = selectedTaskDetail,
               detail.task.state == "RUNNING" || detail.task.state == "VERIFYING" {
                return Self.activeTaskInterval
            }
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
            // A daemon predating this endpoint simply reports nothing; the UI
            // then shows the comparison as indeterminate rather than matched.
            self.daemonBuild = try? await client.build()
            let dashboard = try await client.dashboard()
            self.dashboard = dashboard
            // The task collection comes from the authoritative list endpoint, not
            // from the dashboard's ten-item recent_tasks preview. The dashboard
            // counts every task in the store, so driving the list from that preview
            // made search, filtering and every count-to-list navigation operate on
            // a silently truncated subset.
            if let listed = try? await client.listTasks(limit: Self.taskListLimit) {
                self.tasks = listed
                detectTaskCompletions(in: listed.tasks)
            } else if self.tasks == nil {
                // First load with the list endpoint unavailable: the preview is
                // better than nothing, and `total` keeps the shortfall detectable.
                self.tasks = TaskListView(
                    tasks: dashboard.recentTasks,
                    total: dashboard.counts.total
                )
            }
            self.projects = dashboard.projects
            if selectedProjectId == nil {
                selectedProjectId = dashboard.projects.projects.first(where: \.isOnline)?.projectId
            }
            self.providers = dashboard.providers
            if let providerConnections = try? await client.providerConnections() {
                self.providerConnections = providerConnections
            }
            // Read-only projection; it never triggers provider collection.
            if let quota = try? await client.quota() {
                self.quota = quota
            }
            self.activeStatus = dashboard.activeStatus
            if let settings = try? await client.ownerExecutionSettings() {
                self.ownerExecutionSettings = settings
            }
            if let scheduling = try? await client.schedulingSettings() {
                self.schedulingSettings = scheduling
            }
            self.lastError = nil
            await refreshProviderStatusSilently()
            if let selectedTaskId {
                await loadTaskDetail(taskId: selectedTaskId)
            }
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
            projects = nil
            pendingProjectPreview = nil
            selectedTaskDetail = nil
            providers = nil
            providerConnections = nil
            activeStatus = nil
            ownerExecutionSettings = nil
            schedulingSettings = nil
            lastDispatch = nil
            // Ephemeral operation success state claims daemon authority; once the
            // connection is gone it must not linger as if still authoritative.
            lastSubmittedTaskId = nil
            submitNotice = nil
            projectNotice = nil
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
    public func resolveProject(path: String) async {
        do {
            pendingProjectPreview = try await client.resolveProject(path: path)
            if let preview = pendingProjectPreview {
                projectNotice = .resolved(projectId: preview.projectId)
            }
            ClientLog.operation("project-resolve", outcome: "ok")
        } catch let error as PAOClientError {
            projectNotice = .failed(detail: error.displayDetail)
            ClientLog.operation("project-resolve", outcome: error.logCode)
        } catch {
            projectNotice = .malformedResponse
            ClientLog.operation("project-resolve", outcome: "malformed")
        }
    }

    public func registerProject(path: String, displayName: String? = nil, bookmarkData: Data? = nil) async {
        do {
            let bookmark = bookmarkData?.base64EncodedString()
            let project = try await client.registerProject(
                path: path,
                displayName: displayName,
                securityBookmarkB64: bookmark
            )
            selectedProjectId = project.projectId
            pendingProjectPreview = nil
            projectNotice = .registered(projectId: project.projectId)
            ClientLog.operation("project-register", outcome: "ok")
            await refreshOnce()
        } catch let error as PAOClientError {
            projectNotice = .failed(detail: error.displayDetail)
            ClientLog.operation("project-register", outcome: error.logCode)
        } catch {
            projectNotice = .malformedResponse
            ClientLog.operation("project-register", outcome: "malformed")
        }
    }

    public func removeProject(projectId: String) async {
        do {
            _ = try await client.removeProject(projectId)
            if selectedProjectId == projectId {
                selectedProjectId = nil
            }
            projectNotice = .removed(projectId: projectId)
            ClientLog.operation("project-remove", outcome: "ok")
            await refreshOnce()
        } catch let error as PAOClientError {
            projectNotice = .failed(detail: error.displayDetail)
            ClientLog.operation("project-remove", outcome: error.logCode)
        } catch {
            projectNotice = .malformedResponse
            ClientLog.operation("project-remove", outcome: "malformed")
        }
    }

    public func markProjectOpened(projectId: String) async {
        do {
            _ = try await client.markProjectOpened(projectId)
            ClientLog.operation("project-opened", outcome: "ok")
            await refreshOnce()
        } catch let error as PAOClientError {
            projectNotice = .failed(detail: error.displayDetail)
            ClientLog.operation("project-opened", outcome: error.logCode)
        } catch {
            projectNotice = .malformedResponse
            ClientLog.operation("project-opened", outcome: "malformed")
        }
    }

    public func quickSubmit(projectId: String?, intent: String) async {
        let trimmed = intent.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let projectId, !projectId.isEmpty else {
            submitNotice = .projectRequired
            return
        }
        if let last = lastSubmit,
           last.intent == trimmed,
           Date().timeIntervalSince(last.at) < Self.duplicateSubmitWindow {
            submitNotice = .duplicateBlocked(windowSeconds: Int(Self.duplicateSubmitWindow))
            return
        }
        let suffix = idFactory()
        // The picked policy must reach the daemon: an App-local selection that never
        // leaves SwiftUI state is indistinguishable from not choosing at all.
        let policy = selectedSchedulingPolicy
        let manualTarget = policy == "MANUAL" ? selectedManualExecutionTargetId : nil
        if policy == "MANUAL", manualTarget == nil {
            submitNotice = .manualTargetRequired
            return
        }
        let request = SubmitRequest(
            taskId: "menubar-\(suffix)",
            requestId: "menubar-req-\(suffix)",
            projectId: projectId,
            intent: trimmed,
            schedulingPolicy: policy,
            manualExecutionTargetId: manualTarget
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

    /// Drop the ephemeral cancellation result.
    ///
    /// The notice describes one interaction. Its `failed` and `malformedResponse`
    /// cases carry no task identity, so a notice left standing would attach a
    /// failure the owner caused on one task to whichever task they look at next.
    public func clearCancellationNotice() {
        cancellationNotice = nil
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
            let providerConnections = try? await client.providerConnections()
            if let providerConnections { self.providerConnections = providerConnections }
            // Provider discovery reads no quota; this only re-reads the
            // projection so newly visible connections get a card.
            if let quota = try? await client.quota() { self.quota = quota }
            ClientLog.operation("refresh_providers", outcome: status.discoveryState.lowercased())
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("refresh_providers", outcome: error.logCode)
        } catch {
            ClientLog.operation("refresh_providers", outcome: "malformed")
        }
    }

    /// Owner-triggered read-only quota collection.
    ///
    /// Deliberately separate from ``refreshProviders()``: that re-runs catalog
    /// and credential discovery and reads no quota at all. This contacts each
    /// connected provider's documented read-only quota endpoint. No model
    /// generation is ever issued to discover quota.
    public func refreshQuota(providerId: String? = nil) async {
        guard !isRefreshingQuota else { return }
        isRefreshingQuota = true
        defer { isRefreshingQuota = false }
        do {
            let result = try await client.refreshQuota(
                providerId: providerId,
                timeoutSeconds: Self.quotaRefreshTimeoutSeconds
            )
            self.quota = result.overview
            self.lastQuotaRefreshError = nil
            ClientLog.operation("refresh_quota", outcome: result.overview.state.lowercased())
        } catch let error as PAOClientError {
            self.lastError = error
            self.lastQuotaRefreshError = error.logCode
            // A failed refresh must not blank the page: keep the last
            // projection and let the card report the failure.
            self.quota = try? await client.quota()
            ClientLog.operation("refresh_quota", outcome: error.logCode)
        } catch {
            self.lastQuotaRefreshError = "malformed"
            ClientLog.operation("refresh_quota", outcome: "malformed")
        }
    }

    /// Daemon-side quota collection on a fixed cadence, so readings stay
    /// current without the owner clicking anything. Reuses
    /// ``refreshQuota()`` — and therefore its coalescing guard — so an
    /// automatic cycle never races a manual click, and stays quiet while the
    /// daemon is unreachable.
    private func startQuotaAutoRefresh() {
        quotaAutoRefreshTask?.cancel()
        quotaAutoRefreshTask = Task { [weak self] in
            while let self, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.quotaAutoRefreshInterval))
                guard !Task.isCancelled else { break }
                guard self.connection.isConnected else { continue }
                await self.refreshQuota()
            }
        }
    }

    public func connectProvider(providerId: String) async {
        do {
            _ = try await client.connectProvider(providerId: providerId)
            providerConnections = try? await client.providerConnections()
            providers = try? await client.providers()
            quota = try? await client.quota()
            ClientLog.operation("connect_provider", outcome: "connected")
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("connect_provider", outcome: error.logCode)
        } catch {
            ClientLog.operation("connect_provider", outcome: "malformed")
        }
    }

    public func disconnectProvider(providerId: String) async {
        do {
            _ = try await client.disconnectProvider(providerId: providerId)
            providerConnections = try? await client.providerConnections()
            providers = try? await client.providers()
            quota = try? await client.quota()
            ClientLog.operation("disconnect_provider", outcome: "disconnected")
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("disconnect_provider", outcome: error.logCode)
        } catch {
            ClientLog.operation("disconnect_provider", outcome: "malformed")
        }
    }

    /// Import owner-approved existing connections.
    ///
    /// Explicitly owner-triggered: nothing here runs on refresh or startup.
    public func importProviderConnections(providerIds: [String]) async {
        guard !providerIds.isEmpty else { return }
        do {
            _ = try await client.importProviderConnections(providerIds: providerIds)
            providerConnections = try? await client.providerConnections()
            providers = try? await client.providers()
            quota = try? await client.quota()
            ClientLog.operation("import_provider_connections", outcome: "imported")
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("import_provider_connections", outcome: error.logCode)
        } catch {
            ClientLog.operation("import_provider_connections", outcome: "malformed")
        }
    }

    public func loadSchedulingSettings() async {
        schedulingSettings = try? await client.schedulingSettings()
    }

    public func setDefaultSchedulingPolicy(_ policy: String) async {
        do {
            schedulingSettings = try await client.setDefaultSchedulingPolicy(policy)
            ClientLog.operation("set_scheduling_policy", outcome: "ok")
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("set_scheduling_policy", outcome: error.logCode)
        } catch {
            ClientLog.operation("set_scheduling_policy", outcome: "malformed")
        }
    }

    public func setProjectSchedulingPolicy(projectId: String, policy: String?) async {
        do {
            _ = try await client.setProjectSchedulingPolicy(
                projectId: projectId,
                schedulingPolicy: policy
            )
            projects = try? await client.projects()
            ClientLog.operation("set_project_scheduling_policy", outcome: "ok")
        } catch let error as PAOClientError {
            self.lastError = error
            ClientLog.operation("set_project_scheduling_policy", outcome: error.logCode)
        } catch {
            ClientLog.operation("set_project_scheduling_policy", outcome: "malformed")
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

/// A task that reached a terminal state while the dashboard was watching.
public struct TaskCompletionNotice: Equatable, Sendable {
    public let taskId: String
    public let state: String
    public let intent: String

    public init(taskId: String, state: String, intent: String) {
        self.taskId = taskId
        self.state = state
        self.intent = intent
    }
}
