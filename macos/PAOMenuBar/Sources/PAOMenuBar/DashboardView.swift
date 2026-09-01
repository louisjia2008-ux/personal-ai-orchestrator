import SwiftUI

import PAOControlKit

struct DashboardView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @SceneStorage("dashboard.selection") private var storedSelection = DashboardSection.overview.rawValue
    @State private var section: DashboardSection?
    @State private var query = ""
    @State private var stateFilter = ""
    @State private var showsNewTaskSheet = false

    private var activeSection: DashboardSection {
        section ?? DashboardSection(rawValue: storedSelection) ?? .overview
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 210, ideal: 230)
        } detail: {
            detail(for: activeSection)
                .navigationTitle(activeSection.title)
                .toolbar { toolbarContent }
        }
        .frame(minWidth: 1080, minHeight: 640)
        .sheet(isPresented: $showsNewTaskSheet) {
            NewTaskSheet { submittedTaskId in
                showsNewTaskSheet = false
                stateFilter = ""
                query = ""
                store.selectedTaskId = submittedTaskId
                section = .tasks
                Task { await store.loadTaskDetail(taskId: submittedTaskId) }
            }
            .environmentObject(store)
            .frame(minWidth: 520, minHeight: 300)
        }
        .onAppear {
            store.dashboardVisible = true
            if section == nil {
                section = DashboardSection(rawValue: storedSelection) ?? .overview
            }
            Task { await store.refreshNow() }
        }
        .onDisappear { store.dashboardVisible = false }
        .onChange(of: section) { newValue in
            storedSelection = (newValue ?? .overview).rawValue
        }
    }

    private var sidebar: some View {
        List(selection: Binding(
            get: { activeSection },
            set: { section = $0 ?? .overview }
        )) {
            ForEach(DashboardSection.allCases) { candidate in
                Label(candidate.title, systemImage: candidate.symbol)
                    .tag(candidate)
            }
        }
        .listStyle(.sidebar)
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button {
                showsNewTaskSheet = true
            } label: {
                Label(L10n.newTask, systemImage: "plus.rectangle")
            }
            .keyboardShortcut("n", modifiers: .command)
            .help(L10n.newTaskHelp)
        }
        ToolbarItem {
            Button {
                Task { await store.refreshNow() }
            } label: {
                if store.isRefreshing {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .disabled(store.isRefreshing)
            .keyboardShortcut("r", modifiers: .command)
            .help(L10n.refresh)
        }
    }

    @ViewBuilder
    private func detail(for section: DashboardSection) -> some View {
        let taskSelection = Binding(
            get: { store.selectedTaskId },
            set: { store.selectedTaskId = $0 }
        )
        switch section {
        case .overview:
            OverviewDashboard(
                section: $section,
                stateFilter: $stateFilter
            )
        case .tasks:
            TasksDashboard(
                query: $query,
                stateFilter: $stateFilter,
                selectedTaskId: taskSelection,
                onNewTask: { showsNewTaskSheet = true }
            )
        case .agents:
            ExecutionTargetsDashboard()
        case .providers:
            ProvidersDashboard()
        case .quota:
            QuotaDashboard()
        case .routing:
            SelectedTaskDetailDashboard(selectedTaskId: taskSelection, mode: .routing, onNewTask: { showsNewTaskSheet = true })
        case .verification:
            SelectedTaskDetailDashboard(selectedTaskId: taskSelection, mode: .verification, onNewTask: { showsNewTaskSheet = true })
        case .history:
            HistoryDashboard()
        case .settings:
            ClientSettingsDashboard()
        }
    }
}

// MARK: - New task sheet

private struct NewTaskSheet: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Environment(\.dismiss) private var dismiss
    @State private var intent: String = ""
    @State private var submitting: Bool = false
    let onSubmitted: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.newTaskTitle)
                .font(.headline)
            Text(L10n.newTaskSubtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            TextEditor(text: $intent)
                .font(.body)
                .frame(minHeight: 110)
                .border(Color(nsColor: .separatorColor), width: 1)
                .scrollContentBackground(.hidden)
                .background(Color(nsColor: .textBackgroundColor))
            if let notice = store.submitNotice, case .failed = notice {
                Text(L10n.submitNotice(notice))
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button(L10n.cancel, role: .cancel) {
                    dismiss()
                }
                .keyboardShortcut(.cancelAction)
                Button(submitting ? L10n.submitting : L10n.submit, action: submit)
                    .keyboardShortcut(.defaultAction)
                    .disabled(submitting || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(18)
    }

    private func submit() {
        let value = intent
        submitting = true
        Task {
            await store.quickSubmit(intent: value)
            submitting = false
            if let taskId = store.lastSubmittedTaskId {
                onSubmitted(taskId)
            }
        }
    }
}

// MARK: - Overview

private struct OverviewDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var section: DashboardSection?
    @Binding var stateFilter: String

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.overview.rawValue),
            symbol: DashboardSection.overview.symbol
        ) {
            DashboardCard(title: L10n.connectionLabel, symbol: store.statusSummary.systemImage) {
                HStack {
                    StatusBadge(text: L10n.statusTitle(store.statusSummary), kind: store.connection.isConnected ? .good : .bad)
                    Text(store.connection.isConnected ? L10n.connectedLabel : disconnectedText)
                        .foregroundStyle(.secondary)
                }
                DaemonLifecycleLabel(lifecycle: store.daemonLifecycle)
                Text(store.socketPath)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            let counts = store.dashboard?.counts
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 5), spacing: 12) {
                MetricTile("RUNNING", counts?.running ?? 0, symbol: "gearshape.2") {
                    navigateToTasks(filter: "RUNNING")
                }
                MetricTile("READY", counts?.ready ?? 0, symbol: "tray") {
                    navigateToTasks(filter: "READY")
                }
                MetricTile("BLOCKED", counts?.blocked ?? 0, symbol: "exclamationmark.octagon") {
                    navigateToTasks(filter: "BLOCKED")
                }
                MetricTile("VERIFIED", counts?.verified ?? 0, symbol: "checkmark.seal") {
                    navigateToTasks(filter: "VERIFIED")
                }
                MetricTile("COMPLETED", counts?.completed ?? 0, symbol: "checkmark.circle") {
                    navigateToTasks(filter: "VERIFIED")
                }
            }
            Text(L10n.metricTileHelp)
                .font(.caption)
                .foregroundStyle(.tertiary)

            DashboardCard(title: L10n.blockers, symbol: "exclamationmark.triangle") {
                if let blockers = store.dashboard?.importantBlockers, !blockers.isEmpty {
                    ForEach(blockers, id: \.self) { Text($0).foregroundStyle(.secondary) }
                } else {
                    Text(L10n.noBlockers).foregroundStyle(.secondary)
                }
                StatusBadge(text: store.activeStatus?.productionActive ?? "UNKNOWN", kind: .neutral)
            }

            DashboardCard(title: L10n.events, symbol: "clock") {
                EventList(events: store.dashboard?.recentEvents ?? [])
            }
        }
    }

    /// Navigation only: opens the tasks list filtered to a state. Never mutates
    /// authoritative task state.
    private func navigateToTasks(filter: String) {
        stateFilter = filter
        section = .tasks
    }

    private var disconnectedText: String {
        if case .disconnected(let reason) = store.connection {
            return L10n.disconnectionReason(reason)
        }
        return ""
    }
}

// MARK: - Tasks

private struct TasksDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var query: String
    @Binding var stateFilter: String
    @Binding var selectedTaskId: String?
    let onNewTask: () -> Void
    @FocusState private var searchFocused: Bool

    private var allTasks: [TaskView] {
        store.tasks?.tasks ?? []
    }

    private var tasks: [TaskView] {
        allTasks.filter { task in
            let matchesQuery = query.isEmpty
                || task.taskId.localizedCaseInsensitiveContains(query)
                || task.intent.localizedCaseInsensitiveContains(query)
            let matchesState = MetricsFilter.matches(state: task.state, filter: stateFilter)
            return matchesQuery && matchesState
        }
    }

    private var states: [String] {
        Array(Set(allTasks.map(\.state))).sorted()
    }

    var body: some View {
        DashboardPageContainer(
            title: L10n.taskBrowserTitle,
            symbol: DashboardSection.tasks.symbol,
            trailing: {
                AnyView(
                    Button {
                        onNewTask()
                    } label: {
                        Label(L10n.newTask, systemImage: "plus")
                    }
                    .buttonStyle(.borderless)
                    .keyboardShortcut("n", modifiers: .command)
                    .help(L10n.newTaskHelp)
                )
            }
        ) {
            GeometryReader { proxy in
                let compact = proxy.size.width < 760
                HStack(alignment: .top, spacing: 12) {
                    DashboardBrowserPanel(title: L10n.taskBrowserTitle) {
                        browserContent
                    }
                    .frame(
                        minWidth: compact ? 280 : 300,
                        idealWidth: compact ? 320 : 340,
                        maxWidth: 420
                    )
                    Divider()
                    DashboardDetailPanel {
                        detailContent
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .padding(.horizontal, 4)
            }
            .frame(minHeight: 360)
        }
        .background(hiddenCommands)
    }

    @ViewBuilder
    private var browserContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                TextField(L10n.search, text: $query)
                    .textFieldStyle(.roundedBorder)
                    .focused($searchFocused)
                Picker(L10n.stateFilter, selection: $stateFilter) {
                    Text(L10n.allStates).tag("")
                    ForEach(states, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.menu)
                .labelsHidden()
            }
            .padding(10)

            Divider()

            if tasks.isEmpty {
                Text(L10n.tasksNoMatch)
                    .foregroundStyle(.secondary)
                    .padding(12)
                Spacer()
            } else {
                List(selection: $selectedTaskId) {
                    ForEach(tasks) { task in
                        TaskRow(task: task, isSelected: task.taskId == selectedTaskId)
                            .tag(task.taskId)
                            .padding(.vertical, 2)
                    }
                }
                .listStyle(.inset)
            }
        }
    }

    @ViewBuilder
    private var detailContent: some View {
        if allTasks.isEmpty {
            if store.connection.isConnected {
                EmptyStateView(
                    title: L10n.taskDetail,
                    symbol: "doc.text",
                    message: L10n.noTasksHint,
                    hint: L10n.tasksEmptyHint,
                    action: onNewTask
                )
            } else {
                EmptyStateView(
                    title: L10n.disconnectedLabel,
                    symbol: "bolt.slash",
                    message: L10n.tasksDisconnectedHint
                )
            }
        } else if let selectedTaskId {
            TaskDetailPanel(
                detail: store.selectedTaskDetail,
                selectedTaskId: selectedTaskId,
                onCopyTaskId: { Pasteboard.copy(selectedTaskId) },
                onReload: {
                    Task { await store.loadTaskDetail(taskId: selectedTaskId) }
                }
            )
        } else {
            EmptyStateView(
                title: L10n.taskDetailCanvasTitle,
                symbol: "doc.text",
                message: L10n.selectTaskForDetails,
                hint: L10n.taskBrowserHint
            )
        }
    }

    /// Hidden commands for keyboard-first navigation. SwiftUI on macOS only
    /// honors `.keyboardShortcut` on real views; an empty `Group` works for
    /// invisible but routable shortcuts.
    private var hiddenCommands: some View {
        Group {
            Button("Focus search") { searchFocused = true }
                .keyboardShortcut("f", modifiers: .command)
                .hidden()
            Button("Previous task") {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: tasks, offset: -1)
            }
            .keyboardShortcut("[", modifiers: .command)
            .disabled(tasks.isEmpty)
            .hidden()
            Button("Next task") {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: tasks, offset: 1)
            }
            .keyboardShortcut("]", modifiers: .command)
            .disabled(tasks.isEmpty)
            .hidden()
            Button("Copy task ID") { selectedTaskId.map(Pasteboard.copy) }
                .keyboardShortcut("c", modifiers: [.command, .option])
                .disabled(selectedTaskId == nil)
                .hidden()
            Button("Reload detail") {
                if let selectedTaskId {
                    Task { await store.loadTaskDetail(taskId: selectedTaskId) }
                }
            }
            .keyboardShortcut("r", modifiers: [.command, .option])
            .disabled(selectedTaskId == nil)
            .hidden()
        }
    }
}

private struct TaskRow: View {
    let task: TaskView
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(isSelected ? Color.accentColor : .clear)
                .frame(width: 3)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(task.taskId)
                        .font(.system(.body, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    StatusBadge(text: task.state, kind: task.state == "BLOCKED" ? .bad : .neutral)
                }
                Text(task.intent)
                    .lineLimit(2)
                    .foregroundStyle(.secondary)
                Text(task.updatedAt)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .padding(.leading, 6)
        }
    }
}

// MARK: - Routing / verification sections

private enum DetailMode {
    case routing
    case verification
}

private struct SelectedTaskDetailDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var selectedTaskId: String?
    let mode: DetailMode
    let onNewTask: () -> Void

    private var tasks: [TaskView] {
        store.tasks?.tasks ?? []
    }

    var body: some View {
        DashboardPageContainer(
            title: mode == .routing
                ? L10n.dashboardSection(DashboardSection.routing.rawValue)
                : L10n.dashboardSection(DashboardSection.verification.rawValue),
            symbol: mode == .routing
                ? DashboardSection.routing.symbol
                : DashboardSection.verification.symbol
        ) {
            taskPicker

            if let detail = store.selectedTaskDetail {
                taskHeader(detail: detail)
                if mode == .routing {
                    RoutingPanel(detail: detail)
                } else {
                    VerificationPanel(detail: detail)
                }
            } else {
                noSelectionState
            }
        }
        .background(hiddenCommands)
        .onAppear {
            if let selectedTaskId {
                Task { await store.loadTaskDetail(taskId: selectedTaskId) }
            }
        }
        .onChange(of: selectedTaskId) { taskId in
            guard let taskId else { return }
            Task { await store.loadTaskDetail(taskId: taskId) }
        }
    }

    private var taskPicker: some View {
        DashboardCard(title: L10n.taskContext, symbol: "sidebar.left") {
            Picker(L10n.taskContext, selection: $selectedTaskId) {
                Text(L10n.pickerNoSelection).tag(String?.none)
                ForEach(tasks) { task in
                    Text("\(task.taskId) · \(task.state)")
                        .tag(String?.some(task.taskId))
                }
            }
            .pickerStyle(.menu)
            if let selectedTaskId {
                Text(L10n.pickerChangeHint)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    @ViewBuilder
    private func taskHeader(detail: TaskDetailView) -> some View {
        DashboardCard(title: L10n.taskDetail, symbol: "doc.text.magnifyingglass") {
            HStack(alignment: .firstTextBaseline) {
                Text(detail.task.taskId)
                    .font(.system(.headline, design: .monospaced))
                    .textSelection(.enabled)
                Spacer()
                StatusBadge(text: detail.task.state, kind: detail.task.state == "BLOCKED" ? .bad : .neutral)
            }
            Text(detail.task.intent)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(detail.task.createdAt) -> \(detail.task.updatedAt)")
                .font(.caption)
                .foregroundStyle(.tertiary)
            HStack(spacing: 8) {
                Button {
                    Pasteboard.copy(selectedTaskId ?? "")
                } label: {
                    Label(L10n.copyTaskId, systemImage: "doc.on.doc")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("c", modifiers: [.command, .option])
                .help(L10n.copyTaskIdHint)

                Button {
                    if let selectedTaskId {
                        Task { await store.loadTaskDetail(taskId: selectedTaskId) }
                    }
                } label: {
                    Label(L10n.reload, systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("r", modifiers: [.command, .option])
                .help(L10n.reloadHint)
            }
        }
    }

    @ViewBuilder
    private var noSelectionState: some View {
        if tasks.isEmpty {
            EmptyStateView(
                title: mode == .routing ? L10n.routingTitle : L10n.verificationTitle,
                symbol: mode == .routing ? "point.topleft.down.curvedto.point.bottomright.up" : "checkmark.seal",
                message: mode == .routing ? L10n.routingNeedsTask : L10n.verificationNeedsTask,
                hint: L10n.taskContextEmptyHint
            ) {
                Button(L10n.newTask, action: onNewTask)
            }
        } else {
            EmptyStateView(
                title: mode == .routing ? L10n.routingTitle : L10n.verificationTitle,
                symbol: "sidebar.left",
                message: mode == .routing ? L10n.routingNoSelection : L10n.verificationNoSelection
            )
        }
    }

    private var hiddenCommands: some View {
        Group {
            Button("Previous task") {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: tasks, offset: -1)
            }
            .keyboardShortcut("[", modifiers: .command)
            .disabled(tasks.isEmpty)
            .hidden()
            Button("Next task") {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: tasks, offset: 1)
            }
            .keyboardShortcut("]", modifiers: .command)
            .disabled(tasks.isEmpty)
            .hidden()
        }
    }
}

// MARK: - Agents / execution targets

private struct ExecutionTargetsDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.agents.rawValue),
            symbol: DashboardSection.agents.symbol,
            trailing: {
                AnyView(
                    Button {
                        Task { await store.refreshProviders() }
                    } label: {
                        if store.isRefreshingProviders {
                            ProgressView().controlSize(.small)
                        } else {
                            Label(L10n.refreshProviders, systemImage: "arrow.clockwise")
                        }
                    }
                    .buttonStyle(.borderless)
                    .disabled(store.isRefreshingProviders)
                    .help(L10n.refreshProviders)
                )
            }
        ) {
            ProviderDiscoveryStatusCard()
            let providers = store.providers?.providers ?? []
            if providers.isEmpty {
                EmptyStateView(
                    title: L10n.agentsTitle,
                    symbol: "cpu",
                    message: L10n.noProvidersHint
                )
            } else {
                ForEach(providers) { provider in
                    DashboardCard(title: provider.displayName, symbol: "cpu") {
                        if provider.executionTargets.isEmpty {
                            Text(L10n.noProvidersHint)
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(provider.executionTargets) { target in
                                ExecutionTargetDisclosure(target: target)
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct ExecutionTargetDisclosure: View {
    let target: ExecutionTargetHealthView
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent(L10n.modelSku, value: target.modelSkuId)
                LabeledContent(L10n.runtimeIdLabel, value: target.runtimeId)
                if let runtimeAvailable = target.runtimeAvailable {
                    LabeledContent(
                        L10n.runtimeAvailability,
                        value: runtimeAvailable ? "AVAILABLE" : "UNAVAILABLE"
                    )
                }
                if let observed = target.observedAvailability {
                    LabeledContent(L10n.observedState, value: observed.state)
                    LabeledContent(L10n.measurementSource, value: observed.measurementSource)
                    LabeledContent(L10n.confidenceLabel, value: observed.confidence)
                    LabeledContent(L10n.observedAt, value: observed.observedAt)
                    if let reason = observed.sanitizedReasonCode {
                        LabeledContent(L10n.reasonCode, value: reason)
                    }
                } else {
                    Text(L10n.availabilityUnknown)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.top, 4)
            .padding(.leading, 4)
        } label: {
            HStack {
                Text(target.executionTargetId)
                    .font(.system(.body, design: .monospaced))
                Spacer()
                StatusBadge(text: target.enabled ? "ENABLED" : "DISABLED", kind: target.enabled ? .good : .neutral)
            }
        }
    }
}

// MARK: - Providers

private struct ProvidersDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.providers.rawValue),
            symbol: DashboardSection.providers.symbol,
            trailing: {
                AnyView(
                    Button {
                        Task { await store.refreshProviders() }
                    } label: {
                        if store.isRefreshingProviders {
                            ProgressView().controlSize(.small)
                        } else {
                            Label(L10n.refreshProviders, systemImage: "arrow.clockwise")
                        }
                    }
                    .buttonStyle(.borderless)
                    .disabled(store.isRefreshingProviders)
                    .help(L10n.refreshProviders)
                )
            }
        ) {
            ProviderDiscoveryStatusCard()
            let providers = store.providers?.providers ?? []
            if providers.isEmpty {
                EmptyStateView(
                    title: L10n.providersTitle,
                    symbol: "network",
                    message: store.providerDiscoveryStatus?.discoveryState == "PENDING"
                        ? L10n.discoveryEmpty
                        : L10n.noProvidersHint
                )
            } else {
                ForEach(providers) { provider in
                    ProviderCard(provider: provider)
                }
            }
        }
    }
}

private struct ProviderCard: View {
    let provider: ProviderHealthView

    var body: some View {
        DashboardCard(title: provider.displayName, symbol: "network") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    StatusBadge(text: provider.providerId, kind: .neutral)
                    Text(L10n.accountsCount(provider.accountCount))
                        .foregroundStyle(.secondary)
                    if let evidence = provider.evidenceSource {
                        StatusBadge(text: evidence, kind: evidenceBadgeKind(evidence))
                    }
                }
                if let authStatus = provider.authStatus {
                    LabeledContent(L10n.providerAuthStatus, value: authStatus)
                        .foregroundStyle(.secondary)
                }
                if let executionStatus = provider.executionStatus {
                    LabeledContent(L10n.providerExecutionStatus, value: executionStatus)
                        .foregroundStyle(.secondary)
                }
                if !provider.executionTargets.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L10n.providerExecutionTargets)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ForEach(provider.executionTargets) { target in
                            HStack {
                                Text(target.executionTargetId)
                                    .font(.system(.caption, design: .monospaced))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                                if let available = target.runtimeAvailable {
                                    StatusBadge(
                                        text: available ? "AVAILABLE" : "UNAVAILABLE",
                                        kind: available ? .good : .bad
                                    )
                                }
                            }
                        }
                    }
                }
                if !provider.quotaPools.isEmpty {
                    Divider()
                    Text("Quota pools")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(provider.quotaPools) { pool in
                        QuotaPoolDisclosure(pool: pool)
                    }
                }
                if let lastChecked = provider.lastChecked {
                    Text(L10n.quotaObservedAt(lastChecked))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    private func evidenceBadgeKind(_ source: String) -> BadgeKind {
        switch source {
        case "EXECUTION_PROBE_RUN": return .good
        case "AUTH_FROM_ENV_PRESENCE": return .warn
        case "DISCOVERED_FROM_CATALOG": return .neutral
        default: return .neutral
        }
    }
}

// MARK: - Quota

private struct QuotaPoolDisclosure: View {
    let pool: QuotaPoolHealthView
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 5) {
                Text(L10n.quotaConfidenceExplanation(pool.confidence))
                    .foregroundStyle(.secondary)
                Text(L10n.quotaSourceExplanation(pool.measurementSourceType))
                    .foregroundStyle(.secondary)
                if let observedAt = pool.observedAt {
                    Text(L10n.quotaObservedAt(observedAt))
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.leading, 4)
        } label: {
            HStack {
                Text("\(pool.name): \(pool.state)")
                Spacer()
                StatusBadge(text: pool.confidence, kind: pool.confidence == "EXACT" ? .good : .neutral)
            }
        }
    }
}

private struct QuotaDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.quota.rawValue),
            symbol: DashboardSection.quota.symbol,
            trailing: {
                AnyView(
                    Button {
                        Task { await store.refreshProviders() }
                    } label: {
                        if store.isRefreshingProviders {
                            ProgressView().controlSize(.small)
                        } else {
                            Label(L10n.refreshProviders, systemImage: "arrow.clockwise")
                        }
                    }
                    .buttonStyle(.borderless)
                    .disabled(store.isRefreshingProviders)
                    .help(L10n.refreshProviders)
                )
            }
        ) {
            ProviderDiscoveryStatusCard()
            let providers = store.providers?.providers ?? []
            if providers.isEmpty {
                EmptyStateView(
                    title: L10n.quotaTitle,
                    symbol: "chart.pie",
                    message: store.providerDiscoveryStatus?.discoveryState == "PENDING"
                        ? L10n.discoveryEmpty
                        : L10n.noProvidersHint
                )
            } else if providers.allSatisfy({ $0.quotaPools.isEmpty }) {
                EmptyStateView(
                    title: L10n.quotaTitle,
                    symbol: "chart.pie",
                    message: L10n.noProvidersHint
                )
            } else {
                ForEach(providers) { provider in
                    ForEach(provider.quotaPools) { pool in
                        QuotaPoolCard(providerName: provider.displayName, pool: pool)
                    }
                }
            }
        }
    }
}

private struct QuotaPoolCard: View {
    let providerName: String
    let pool: QuotaPoolHealthView

    var body: some View {
        DashboardCard(title: "\(providerName) / \(pool.name)", symbol: "chart.pie") {
            HStack {
                StatusBadge(text: pool.confidence, kind: pool.confidence == "EXACT" ? .good : .neutral)
                StatusBadge(text: pool.measurementSourceType, kind: .neutral)
                StatusBadge(text: pool.state, kind: badgeKind(for: pool.state))
            }
            QuotaExplanation(pool: pool)
            ForEach(pool.windows, id: \.windowId) { window in
                QuotaWindowDisclosure(window: window)
            }
        }
    }

    private func badgeKind(for state: String) -> BadgeKind {
        switch state {
        case "EXHAUSTED": return .bad
        case "RECOVERED": return .good
        default: return .warn
        }
    }
}

/// Human-readable explanation of quota confidence/measurement semantics.
/// UNKNOWN never renders a fabricated numeric percentage.
private struct QuotaExplanation: View {
    let pool: QuotaPoolHealthView

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(L10n.quotaConfidenceExplanation(pool.confidence), systemImage: "info.circle")
                .foregroundStyle(.secondary)
            Label(L10n.quotaSourceExplanation(pool.measurementSourceType), systemImage: "dot.radiowaves.left.and.right")
                .foregroundStyle(.secondary)
            Label(L10n.quotaStateNote(pool.state), systemImage: L10n.quotaStateSymbol(pool.state))
                .foregroundStyle(pool.state == "EXHAUSTED" ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
            if let observedAt = pool.observedAt {
                Label(L10n.quotaObservedAt(observedAt), systemImage: "clock")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

private struct QuotaWindowDisclosure: View {
    let window: QuotaWindowHealthView
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent(L10n.quotaConfidenceLabel, value: window.confidence)
                LabeledContent(L10n.quotaSourceLabel, value: window.windowKind)
                if window.confidence == "EXACT", let fraction = window.remainingFraction {
                    ProgressView(value: fraction)
                    Text(L10n.quotaRemaining(fraction: fraction, confidence: window.confidence))
                } else {
                    Text(L10n.quotaRemaining(fraction: window.remainingFraction, confidence: window.confidence))
                        .foregroundStyle(.secondary)
                }
                Text(window.resetAt ?? L10n.quotaResetUnknown)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .padding(.leading, 4)
        } label: {
            HStack {
                Text("\(window.windowKind) / \(window.state)")
                Spacer()
                StatusBadge(text: window.confidence, kind: window.confidence == "EXACT" ? .good : .neutral)
            }
        }
    }
}

/// Diagnostic card that surfaces the discovery lifecycle so the user can
/// tell at a glance whether the Providers/Agents/Quota pages reflect a
/// live discovery cycle or just the persisted registry from a prior
/// launch. Hidden when nothing has been discovered yet, so the cards
/// stay quiet on a cold start with no providers.
private struct ProviderDiscoveryStatusCard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        if let status = store.providerDiscoveryStatus {
            DashboardCard(title: L10n.providerDiscoveryState, symbol: "antenna.radiowaves.left.and.right") {
                HStack(spacing: 8) {
                    StatusBadge(text: status.discoveryState, kind: badgeKind(for: status.discoveryState))
                    if let snapshot = status.catalogSnapshotId {
                        StatusBadge(text: snapshot, kind: .neutral)
                    }
                    if let method = status.sourceMethod {
                        StatusBadge(text: method, kind: .neutral)
                    }
                }
                if let lastDiscoveredAt = status.lastDiscoveredAt {
                    Text(L10n.providerLastDiscovered(lastDiscoveredAt))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let errorCode = status.lastErrorCode {
                    Text(L10n.discoveryError(errorCode))
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }
        }
    }

    private func badgeKind(for state: String) -> BadgeKind {
        switch state {
        case "DISCOVERED": return .good
        case "PENDING": return .warn
        case "EMPTY": return .neutral
        case "FAILED": return .bad
        default: return .neutral
        }
    }
}

// MARK: - History

private struct HistoryDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.history.rawValue),
            symbol: DashboardSection.history.symbol
        ) {
            DashboardCard(title: L10n.history, symbol: "clock.arrow.circlepath") {
                EventList(events: store.dashboard?.recentEvents ?? [])
            }
        }
    }
}

// MARK: - Settings

struct ClientSettingsDashboard: View {
    @AppStorage("pao.launchAtLogin") private var launchAtLogin = false
    @AppStorage("pao.autoStartDaemon") private var autoStartDaemon = true
    @EnvironmentObject private var store: OrchestratorStore
    private let layout = AppSupportLayout.resolve()

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.settings.rawValue),
            symbol: DashboardSection.settings.symbol
        ) {
            DashboardCard(title: L10n.daemonLifecycle, symbol: "gearshape") {
                Toggle(L10n.launchAtLogin, isOn: $launchAtLogin)
                Text(L10n.launchAtLoginFooter)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle(L10n.autoStartDaemon, isOn: $autoStartDaemon)
                Text(L10n.autoStartDaemonFooter)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent(L10n.socket, value: store.socketPath)
                LabeledContent(L10n.runtimeConfig, value: layout.runtimeConfigPath)
                DaemonLifecycleLabel(lifecycle: store.daemonLifecycle)
            }
            DashboardCard(title: L10n.productionActive, symbol: "lock") {
                LabeledContent("ACTIVE", value: store.activeStatus?.productionActive ?? "UNKNOWN")
                if let active = store.activeStatus {
                    Label(
                        active.authorized ? L10n.activeAuthorized : L10n.activeNotAuthorized,
                        systemImage: active.authorized ? "lock.open" : "lock"
                    )
                    .foregroundStyle(.secondary)
                    ForEach(active.blockingReasons, id: \.self) { reason in
                        Label(reason, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.secondary)
                    }
                    if !active.gate.isEmpty {
                        DisclosureGroup(L10n.activeGateLabel) {
                            ForEach(active.gate.keys.sorted(), id: \.self) { key in
                                let value = active.gate[key] ?? false
                                LabeledContent(key, value: value ? L10n.activeGateOpen : L10n.activeGateClosed)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Text(L10n.activeReadOnlyNote)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - Shared presentation components

private struct DaemonLifecycleLabel: View {
    @ObservedObject var lifecycle: DaemonLifecycleController

    var body: some View {
        LabeledContent(L10n.daemonLifecycle, value: lifecycle.status.displayValue)
    }
}

private struct TaskDetailPanel: View {
    let detail: TaskDetailView?
    let selectedTaskId: String?
    let onCopyTaskId: () -> Void
    let onReload: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail {
                    DashboardCard(title: L10n.taskDetail, symbol: "doc.text.magnifyingglass") {
                        HStack(alignment: .firstTextBaseline) {
                            Text(detail.task.taskId)
                                .font(.system(.headline, design: .monospaced))
                                .textSelection(.enabled)
                            Spacer()
                            StatusBadge(text: detail.task.state, kind: detail.task.state == "BLOCKED" ? .bad : .neutral)
                        }
                        Text(detail.task.intent)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(detail.task.createdAt) -> \(detail.task.updatedAt)")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                        HStack(spacing: 8) {
                            Button {
                                onCopyTaskId()
                            } label: {
                                Label(L10n.copyTaskId, systemImage: "doc.on.doc")
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                            .keyboardShortcut("c", modifiers: [.command, .option])
                            .help(L10n.copyTaskIdHint)

                            Button {
                                onReload()
                            } label: {
                                Label(L10n.reload, systemImage: "arrow.clockwise")
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                            .keyboardShortcut("r", modifiers: [.command, .option])
                            .help(L10n.reloadHint)
                        }
                    }
                    RoutingPanel(detail: detail)
                    VerificationPanel(detail: detail)
                    DashboardCard(title: L10n.history, symbol: "clock") {
                        EventList(events: detail.events)
                    }
                } else {
                    EmptyStateView(
                        title: L10n.taskDetail,
                        symbol: "doc.text",
                        message: L10n.selectTaskEmptyState,
                        hint: L10n.taskDetailEmptyHint
                    )
                }
            }
            .padding(20)
        }
    }
}

private struct RoutingPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: L10n.routingExplanation, symbol: "point.topleft.down.curvedto.point.bottomright.up") {
            if let routing = detail.routing {
                Text(routing.decisionId).font(.system(.caption, design: .monospaced))
                StatusBadge(text: routing.mode ?? "UNKNOWN", kind: .neutral)
                LabeledContent(L10n.actualExecutionTarget, value: routing.selectedExecutionTargetId ?? "none")
                LabeledContent(L10n.wouldSelect, value: routing.mode == "SHADOW" ? (routing.selectedExecutionTargetId ?? "none") : "not shadow")
                if let reason = routing.fallbackReason {
                    Text(reason).foregroundStyle(.secondary)
                }
            } else {
                Label(L10n.routingNotYetDecided, systemImage: "clock")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct VerificationPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: L10n.verification, symbol: "checkmark.seal") {
            StatusBadge(text: detail.verification.status, kind: detail.verification.status == "VERIFIED" ? .good : .neutral)
            LabeledContent("task", value: detail.verification.taskId)
            LabeledContent(L10n.evidenceLabel, value: detail.verification.evidenceId ?? "none")
            if let failure = detail.verification.failureReason {
                Text(failure).foregroundStyle(.red)
            }
            ForEach(detail.approvals.approvals) { approval in
                Text("\(approval.kind): \(approval.status)")
            }
        }
    }
}

private struct EventList: View {
    let events: [ActivityEventView]

    var body: some View {
        if events.isEmpty {
            Label(L10n.noEvents, systemImage: "tray")
                .foregroundStyle(.secondary)
        } else {
            ForEach(events) { event in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(event.eventType).font(.system(.caption, design: .monospaced))
                        Spacer()
                        Text(event.createdAt).font(.caption).foregroundStyle(.tertiary)
                    }
                    Text(event.summary).foregroundStyle(.secondary)
                }
                Divider()
            }
        }
    }
}

private enum BadgeKind {
    case good
    case warn
    case bad
    case neutral

    var color: Color {
        switch self {
        case .good: return .green
        case .warn: return .orange
        case .bad: return .red
        case .neutral: return .secondary
        }
    }
}

private struct StatusBadge: View {
    let text: String
    let kind: BadgeKind

    var body: some View {
        Text(text)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(kind.color.opacity(0.14), in: Capsule())
            .foregroundStyle(kind.color)
            .lineLimit(1)
    }
}

private struct MetricTile: View {
    let label: String
    let value: Int
    let symbol: String
    let action: () -> Void
    @State private var hovering = false

    init(_ label: String, _ value: Int, symbol: String, action: @escaping () -> Void) {
        self.label = label
        self.value = value
        self.symbol = symbol
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            DashboardCard(title: label, symbol: symbol) {
                Text("\(value)")
                    .font(.system(size: 28, weight: .semibold, design: .rounded))
            }
            .padding(2)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(hovering ? Color.accentColor.opacity(0.12) : .clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(hovering ? Color.accentColor.opacity(0.45) : .clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(L10n.metricTileHelp)
    }
}

private struct EmptyStateView: View {
    let title: String
    let symbol: String
    let message: String
    var hint: String? = nil
    var action: (() -> Void)? = nil

    init(title: String, symbol: String, message: String, hint: String? = nil, action: (() -> Void)? = nil) {
        self.title = title
        self.symbol = symbol
        self.message = message
        self.hint = hint
        self.action = action
    }

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(.secondary)
            Text(title).font(.title3.weight(.semibold))
            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let action {
                Button(L10n.newTask, action: action)
                    .controlSize(.regular)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(30)
    }
}

/// Sanitized clipboard helper. Plain NSPasteboard writes do not require TCC
/// accessibility or screen-recording permissions, but only one application
/// command at a time targets it — exactly the model we need for the
/// "copy authoritative task ID" surface.
enum Pasteboard {
    static func copy(_ value: String) {
        guard !value.isEmpty else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(value, forType: .string)
        ClientLog.operation("clipboard", outcome: "copy")
    }
}

private struct DashboardCard<Content: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: symbol)
                .font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
