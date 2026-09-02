import SwiftUI
import AppKit

import PAOControlKit

struct DashboardView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @SceneStorage("dashboard.selection") private var storedSelection = DashboardSection.overview.rawValue
    @State private var section: DashboardSection?
    @State private var query = ""
    @State private var stateFilter = ""
    @State private var showsNewTaskSheet = false

    private var activeSection: DashboardSection {
        let resolved = section ?? DashboardSection(rawValue: storedSelection) ?? .overview
        return resolved == .agents ? .providers : resolved
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
            Section("Work") {
                sidebarItem(.overview)
                sidebarItem(.projects)
                sidebarItem(.tasks)
            }
            Section("AI Resources") {
                sidebarItem(.providers)
                sidebarItem(.quota)
            }
            Section("Execution") {
                sidebarItem(.routing)
                sidebarItem(.verification)
            }
            Section("System") {
                sidebarItem(.history)
                sidebarItem(.settings)
            }
        }
        .listStyle(.sidebar)
    }

    private func sidebarItem(_ candidate: DashboardSection) -> some View {
        Label(candidate.title, systemImage: candidate.symbol)
            .tag(candidate)
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
        case .projects:
            ProjectsDashboard { projectId in
                store.selectedProjectId = projectId
                showsNewTaskSheet = true
            }
        case .tasks:
            TasksDashboard(
                query: $query,
                stateFilter: $stateFilter,
                selectedTaskId: taskSelection,
                onNewTask: { showsNewTaskSheet = true }
            )
        case .agents:
            ProvidersDashboard()
        case .providers:
            ProvidersDashboard()
        case .quota:
            QuotaDashboard()
        case .routing:
            SelectedTaskDetailDashboard(
                selectedTaskId: taskSelection,
                section: $section,
                mode: .routing,
                onNewTask: { showsNewTaskSheet = true }
            )
        case .verification:
            SelectedTaskDetailDashboard(
                selectedTaskId: taskSelection,
                section: $section,
                mode: .verification,
                onNewTask: { showsNewTaskSheet = true }
            )
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
    @State private var executionTargetId: String = ""
    @State private var submitting: Bool = false
    let onSubmitted: (String) -> Void

    private var onlineProjects: [ProjectView] {
        store.projects?.projects.filter(\.isOnline) ?? []
    }

    private var verifiedTargets: [ExecutionTargetHealthView] {
        (store.providers?.providers ?? [])
            .flatMap(\.executionTargets)
            .filter { $0.enabled && $0.isExecutionVerified }
    }

    /// MANUAL may only name a target on a connected provider — scheduling never
    /// reaches catalog-only or merely importable surfaces.
    private var connectedTargets: [ExecutionTargetHealthView] {
        let connectedIds = Set(
            (store.providerConnections?.connected ?? []).map(\.providerId)
        )
        return (store.providers?.providers ?? [])
            .filter { connectedIds.contains($0.providerId) }
            .flatMap(\.executionTargets)
    }

    private static let selectablePolicies = [
        "BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "MANUAL",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.newTaskTitle)
                .font(.headline)
            Text(L10n.newTaskSubtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Picker("Project", selection: $store.selectedProjectId) {
                Text("Select project").tag(String?.none)
                ForEach(onlineProjects) { project in
                    Text(project.displayName).tag(String?.some(project.projectId))
                }
            }
            .pickerStyle(.menu)

            Picker("Execution", selection: $executionTargetId) {
                Text("Auto").tag("")
                ForEach(verifiedTargets) { target in
                    Text(target.modelSkuId).tag(target.executionTargetId)
                }
            }
            .pickerStyle(.menu)

            // This selection is submitted with the task and resolved daemon-side.
            Picker(L10n.newTaskSchedulingPolicy, selection: $store.selectedSchedulingPolicy) {
                ForEach(Self.selectablePolicies, id: \.self) { policy in
                    Text(L10n.schedulingPolicyName(policy)).tag(policy)
                }
            }
            .pickerStyle(.menu)
            Text(L10n.schedulingPolicyDetail(store.selectedSchedulingPolicy))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if store.selectedSchedulingPolicy == "MANUAL" {
                Picker(L10n.newTaskManualModel, selection: $store.selectedManualExecutionTargetId) {
                    Text(L10n.newTaskChooseModel).tag(String?.none)
                    ForEach(connectedTargets) { target in
                        Text(target.modelSkuId).tag(String?.some(target.executionTargetId))
                    }
                }
                .pickerStyle(.menu)
            }

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
                    .disabled(
                        submitting
                            || store.selectedProjectId == nil
                            || intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || (store.selectedSchedulingPolicy == "MANUAL"
                                && store.selectedManualExecutionTargetId == nil)
                    )
            }
        }
        .padding(18)
        .onAppear {
            if store.selectedProjectId == nil {
                store.selectedProjectId = onlineProjects.first?.projectId
            }
        }
    }

    private func submit() {
        let value = intent
        let projectId = store.selectedProjectId
        let target = executionTargetId
        submitting = true
        Task {
            await store.quickSubmit(projectId: projectId, intent: value)
            submitting = false
            if let taskId = store.lastSubmittedTaskId {
                if !target.isEmpty {
                    await store.dispatch(taskId: taskId, executionTargetId: target)
                }
                onSubmitted(taskId)
            }
        }
    }
}

// MARK: - Projects

private struct ProjectsDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var selectedURL: URL?
    @State private var bookmarkData: Data?
    let onNewTask: (String) -> Void

    private var projects: [ProjectView] {
        store.projects?.projects ?? []
    }

    var body: some View {
        DashboardPageContainer(
            title: L10n.dashboardSection(DashboardSection.projects.rawValue),
            symbol: DashboardSection.projects.symbol,
            trailing: {
                AnyView(
                    Button {
                        pickProjectFolder()
                    } label: {
                        Label("Add Project", systemImage: "folder.badge.plus")
                    }
                    .buttonStyle(.borderless)
                )
            }
        ) {
            if let preview = store.pendingProjectPreview {
                DashboardCard(title: "Detected Repository", symbol: "checkmark.seal") {
                    ProjectFacts(project: preview)
                    HStack {
                        Button {
                            selectedURL = nil
                            bookmarkData = nil
                        } label: {
                            Label(L10n.cancel, systemImage: "xmark")
                        }
                        Button {
                            guard let selectedURL else { return }
                            Task {
                                await store.registerProject(
                                    path: selectedURL.path,
                                    displayName: preview.displayName,
                                    bookmarkData: bookmarkData
                                )
                            }
                        } label: {
                            Label("Register", systemImage: "plus.circle")
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            }

            if projects.isEmpty {
                EmptyStateView(
                    title: "Projects",
                    symbol: DashboardSection.projects.symbol,
                    message: "No registered projects."
                )
            } else {
                ForEach(projects) { project in
                    ProjectCard(project: project, onNewTask: onNewTask)
                }
            }
        }
    }

    private func pickProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = "Add Project"
        let response = panel.runModal()
        guard response == .OK, let url = panel.url else { return }
        selectedURL = url
        bookmarkData = try? url.bookmarkData(
            options: [.withSecurityScope],
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        Task { await store.resolveProject(path: url.path) }
    }
}

private struct ProjectCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let project: ProjectView
    let onNewTask: (String) -> Void

    var body: some View {
        DashboardCard(title: project.displayName, symbol: "folder") {
            HStack {
                StatusBadge(
                    text: project.storageAvailability,
                    kind: project.storageAvailability == "ONLINE" ? .good : .bad
                )
                Text(project.canonicalRepoRoot)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            ProjectFacts(project: project)
            HStack(spacing: 8) {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: project.gitRoot)])
                    Task { await store.markProjectOpened(projectId: project.projectId) }
                } label: {
                    Label("Reveal in Finder", systemImage: "finder")
                }
                .controlSize(.small)

                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: project.canonicalRepoRoot))
                    Task { await store.markProjectOpened(projectId: project.projectId) }
                } label: {
                    Label("Open project", systemImage: "arrow.up.forward.app")
                }
                .controlSize(.small)
                .disabled(project.storageAvailability != "ONLINE")

                Button {
                    onNewTask(project.projectId)
                } label: {
                    Label(L10n.newTask, systemImage: "plus")
                }
                .controlSize(.small)
                .disabled(project.storageAvailability != "ONLINE")

                Button(role: .destructive) {
                    Task { await store.removeProject(projectId: project.projectId) }
                } label: {
                    Label("Remove from Orchestrator", systemImage: "minus.circle")
                }
                .controlSize(.small)
            }
        }
    }
}

private struct ProjectFacts: View {
    let project: ProjectView

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent("Git root", value: project.gitRoot)
            if let workingSubpath = project.workingSubpath {
                LabeledContent("Working subpath", value: workingSubpath)
            }
            LabeledContent("Branch", value: project.currentBranch ?? project.defaultBranch)
            LabeledContent("HEAD", value: short(project.lastKnownHead))
            LabeledContent("Recent tasks", value: "\(project.recentTaskCount)")
            LabeledContent("Last used", value: project.lastOpenedAt ?? "Never")
        }
        .font(.caption)
    }

    private func short(_ sha: String) -> String {
        sha.count > 12 ? String(sha.prefix(12)) : sha
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
            let counts = store.dashboard?.counts
            // Equal-width flexible columns plus a shared tile height: the verification
            // tile aggregates two states and previously wrapped its title, which grew
            // that one card and broke the row's alignment.
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 5), spacing: 12) {
                MetricTile(L10n.kpiRunning, counts?.running ?? 0, symbol: "gearshape.2") {
                    navigateToTasks(filter: "RUNNING")
                }
                MetricTile(L10n.kpiReady, counts?.ready ?? 0, symbol: "tray") {
                    navigateToTasks(filter: "READY")
                }
                MetricTile(L10n.kpiBlocked, counts?.blocked ?? 0, symbol: "exclamationmark.octagon") {
                    navigateToTasks(filter: "BLOCKED")
                }
                MetricTile(
                    L10n.kpiVerification,
                    (counts?.verifying ?? 0) + (counts?.verified ?? 0),
                    symbol: "checkmark.seal",
                    detail: L10n.kpiVerificationDetail(
                        verifying: counts?.verifying ?? 0,
                        verified: counts?.verified ?? 0
                    )
                ) {
                    navigateToTasks(filter: "VERIFIED")
                }
                MetricTile(L10n.kpiCompleted, counts?.completed ?? 0, symbol: "checkmark.circle") {
                    navigateToTasks(filter: "COMPLETED")
                }
            }
            .fixedSize(horizontal: false, vertical: true)

            if let info = store.dashboard?.basicInfo {
                DashboardCard(title: "Basic Information", symbol: "info.circle") {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 10)], spacing: 10) {
                        BasicInfoCell("Connection", info.daemonConnection)
                        BasicInfoCell("Projects", "\(info.registeredProjects)")
                        BasicInfoCell("Providers", "\(info.discoveredProviders)")
                        BasicInfoCell("Runnable targets", "\(info.availableExecutionTargets)")
                        BasicInfoCell("Running", "\(info.runningTasks)")
                        BasicInfoCell("Tasks today", "\(info.tasksToday)")
                        BasicInfoCell("Routing today", "\(info.routingDecisionsToday)")
                        BasicInfoCell("Quota warnings", "\(info.quotaWarningCount)")
                    }
                    Text("Last refresh: \(info.lastRefreshSync)")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }

            HStack(alignment: .top, spacing: 12) {
                DashboardCard(title: "Task Trend", symbol: "chart.xyaxis.line") {
                    if let trend = store.dashboard?.taskTrend, trend.count >= 2 {
                        TaskTrendChart(buckets: trend)
                    } else {
                        EmptyChartState("Historical data is still being collected.")
                    }
                }
                DashboardCard(title: "Task States", symbol: "chart.pie") {
                    if let slices = store.dashboard?.taskStateDistribution, !slices.isEmpty {
                        StateDistributionChart(slices: slices)
                    } else {
                        EmptyChartState("No task state data yet.")
                    }
                }
            }

            DashboardCard(title: "Risks", symbol: "exclamationmark.triangle") {
                if let risks = store.dashboard?.risks, !risks.isEmpty {
                    ForEach(risks) { risk in
                        RiskRow(risk: risk, section: $section)
                    }
                    DisclosureGroup("Advanced Details") {
                        ForEach(store.dashboard?.importantBlockers ?? [], id: \.self) { raw in
                            Text(raw)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                } else {
                    Text(L10n.noBlockers).foregroundStyle(.secondary)
                }
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
                },
                onStop: {
                    Task { await store.cancel(taskId: selectedTaskId) }
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
                    Text(task.intent)
                        .font(.body.weight(.medium))
                        .lineLimit(2)
                    Spacer()
                    StatusBadge(text: task.state, kind: task.state == "BLOCKED" ? .bad : .neutral)
                }
                Text(relativeTime(task.updatedAt))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .padding(.leading, 6)
        }
    }

    private func relativeTime(_ value: String) -> String {
        guard let date = parseAPIDate(value) else { return value }
        let seconds = max(0, Int(Date().timeIntervalSince(date)))
        if seconds < 60 { return "Updated just now" }
        let minutes = seconds / 60
        if minutes < 60 { return "Updated \(minutes)m ago" }
        let hours = minutes / 60
        if hours < 24 { return "Updated \(hours)h ago" }
        return "Updated \(hours / 24)d ago"
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
    @Binding var section: DashboardSection?
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
            // Scheduling must never be a black box: the active policy and the full set
            // of modes are visible whether or not a task is selected.
            if mode == .routing {
                ActiveSchedulingPolicyCard()
            }

            taskPicker

            if let detail = store.selectedTaskDetail {
                taskHeader(detail: detail)
                if mode == .routing {
                    RoutingPanel(detail: detail)
                } else {
                    VerificationPanel(detail: detail)
                }
            } else if mode == .routing {
                RoutingNoSelectionCard(
                    hasTasks: !tasks.isEmpty,
                    onNewTask: onNewTask,
                    onOpenProviders: { section = .providers }
                )
            } else {
                noSelectionState
            }
        }
        .background(hiddenCommands)
        .onAppear {
            Task { await store.loadSchedulingSettings() }
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
            if selectedTaskId != nil {
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
                hint: L10n.taskContextEmptyHint,
                action: onNewTask
            )
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
                LabeledContent(
                    L10n.executionVerified,
                    value: target.isExecutionVerified ? "TRUE" : "FALSE"
                )
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
                StatusBadge(
                    text: target.enabled && target.isExecutionVerified ? "VERIFIED" : "UNVERIFIED",
                    kind: target.enabled && target.isExecutionVerified ? .good : .neutral
                )
            }
        }
    }
}

// MARK: - Providers

private struct ProvidersDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var tab: ModelsProvidersTab = .connected

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
            Picker("Models & Providers", selection: $tab) {
                ForEach(ModelsProvidersTab.allCases) { tab in
                    Text(tab.title).tag(tab)
                }
            }
            .pickerStyle(.segmented)

            let connections = store.providerConnections
            let providers = store.providers?.providers ?? []
            let candidates = connections?.importCandidates ?? []
            if providers.isEmpty && (connections?.connected.isEmpty ?? true)
                && (connections?.availableToAdd.isEmpty ?? true) && candidates.isEmpty {
                EmptyStateView(
                    title: L10n.providersTitle,
                    symbol: "network",
                    message: store.providerDiscoveryStatus?.discoveryState == "PENDING"
                        ? L10n.discoveryEmpty
                        : L10n.noProvidersHint
                )
            } else {
                switch tab {
                case .connected:
                    let connected = connections?.connected ?? []
                    if connected.isEmpty {
                        // A strict connection registry legitimately starts empty, but the
                        // owner must never be left guessing where to go next.
                        ConnectedEmptyState(
                            hasImportCandidates: !candidates.isEmpty,
                            onAdd: { tab = .available },
                            onImport: { tab = .available }
                        )
                    } else {
                        ForEach(connected) { connection in
                            ConnectedProviderCard(
                                connection: connection,
                                provider: providers.first { $0.providerId == connection.providerId }
                            )
                        }
                    }
                    if !candidates.isEmpty {
                        ImportCandidatesCard(candidates: candidates)
                    }
                case .available:
                    if !candidates.isEmpty {
                        ImportCandidatesCard(candidates: candidates)
                    }
                    let available = connections?.availableToAdd ?? []
                    if available.isEmpty {
                        EmptyStateView(
                            title: L10n.providersTabAvailable,
                            symbol: "plus.circle",
                            message: L10n.providersAvailableEmptyMessage
                        )
                    } else {
                        ForEach(available) { provider in
                            AvailableProviderCard(provider: provider)
                        }
                    }
                }
            }
        }
    }
}

private enum ModelsProvidersTab: String, CaseIterable, Identifiable {
    case connected
    case available

    var id: String { rawValue }

    var title: String {
        switch self {
        case .connected: return L10n.providersTabConnected
        case .available: return L10n.providersTabAvailable
        }
    }
}

/// Recovery UI for an empty Connected tab.
///
/// The tab being empty is a legitimate state, not a failure — but a blank page with
/// no next action reads as breakage. Both routes out are offered explicitly.
private struct ConnectedEmptyState: View {
    let hasImportCandidates: Bool
    let onAdd: () -> Void
    let onImport: () -> Void

    var body: some View {
        DashboardCard(title: L10n.providersConnectedEmptyTitle, symbol: "link.badge.plus") {
            Text(L10n.providersConnectedEmptyMessage)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button(action: onAdd) {
                    Label(L10n.providersAddProvider, systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
                if hasImportCandidates {
                    Button(action: onImport) {
                        Label(L10n.providersImportExisting, systemImage: "square.and.arrow.down")
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
    }
}

/// Owner-gated import of surfaces that already carry strong, scoped evidence.
///
/// Selection is explicit and starts empty: reconciliation proposes, the owner decides.
private struct ImportCandidatesCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let candidates: [ProviderImportCandidateView]
    @State private var selected: Set<String> = []
    @State private var importing = false

    var body: some View {
        DashboardCard(title: L10n.providersImportTitle, symbol: "square.and.arrow.down") {
            ForEach(candidates) { candidate in
                VStack(alignment: .leading, spacing: 4) {
                    Toggle(isOn: binding(for: candidate.providerId)) {
                        HStack(spacing: 8) {
                            Text(candidate.displayName)
                                .font(.body.weight(.medium))
                            if let region = candidate.region {
                                StatusBadge(text: region, kind: .neutral)
                            }
                            if let plan = candidate.planSurface {
                                StatusBadge(text: plan, kind: .neutral)
                            }
                        }
                    }
                    // Every line here is structured scheduler/discovery evidence.
                    ForEach(candidate.evidence) { item in
                        Label(item.detail, systemImage: "checkmark.circle")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
            Text(L10n.providersImportFooter)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button {
                    importing = true
                    let ids = Array(selected)
                    Task {
                        await store.importProviderConnections(providerIds: ids)
                        selected = []
                        importing = false
                    }
                } label: {
                    if importing {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(L10n.providersImportExisting, systemImage: "square.and.arrow.down")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(selected.isEmpty || importing)
            }
        }
    }

    private func binding(for providerId: String) -> Binding<Bool> {
        Binding(
            get: { selected.contains(providerId) },
            set: { isOn in
                if isOn {
                    selected.insert(providerId)
                } else {
                    selected.remove(providerId)
                }
            }
        )
    }
}

private struct ConnectedProviderCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let connection: ProviderConnectionView
    let provider: ProviderHealthView?

    var body: some View {
        DashboardCard(title: connection.displayName, symbol: "link") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    StatusBadge(text: readableConnection(connection.connectionState), kind: .good)
                    StatusBadge(text: readableAuth(connection.authState), kind: authBadge(connection.authState))
                    StatusBadge(
                        text: connection.executionVerified
                            ? L10n.providerExecutionVerified
                            : L10n.providerNotExecutionVerified,
                        kind: connection.executionVerified ? .good : .warn
                    )
                }
                if let plan = connection.planSurface {
                    LabeledContent("Plan", value: plan)
                        .foregroundStyle(.secondary)
                }
                if let region = connection.region {
                    LabeledContent("Region", value: region)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 8) {
                    Label("\(connection.modelSkus.count) models", systemImage: "cpu")
                    if let poolCount = provider?.quotaPools.count {
                        Label("\(poolCount) quota pools", systemImage: "chart.pie")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                DisclosureGroup("Advanced Details") {
                    LabeledContent("Provider ID", value: connection.providerId)
                    LabeledContent("Credential reference", value: connection.credentialReferenceType)
                    LabeledContent("Runtime", value: readableRuntime(connection.runtimeState))
                    if let reason = connection.lastReasonCode {
                        LabeledContent("Reason", value: reason)
                    }
                    if let provider {
                        LabeledContent("Accounts", value: "\(provider.accountCount)")
                        LabeledContent("Execution targets", value: "\(provider.executionTargets.count)")
                        LabeledContent("Quota pools", value: "\(provider.quotaPools.count)")
                    }
                }
                HStack {
                    if let checked = connection.lastValidatedAt {
                        Text(L10n.quotaObservedAt(checked))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    Button(role: .destructive) {
                        Task { await store.disconnectProvider(providerId: connection.providerId) }
                    } label: {
                        Label(L10n.providerDisconnect, systemImage: "link.badge.minus")
                    }
                    .buttonStyle(.borderless)
                }
            }
        }
    }
}

private struct AvailableProviderCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let provider: AvailableProviderView

    var body: some View {
        DashboardCard(title: provider.displayName, symbol: "plus.circle") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    StatusBadge(text: "Not Connected", kind: .neutral)
                    StatusBadge(text: readableAuth(provider.authState), kind: authBadge(provider.authState))
                    StatusBadge(
                        text: provider.executionVerified
                            ? L10n.providerExecutionVerified
                            : L10n.providerNotExecutionVerified,
                        kind: provider.executionVerified ? .good : .warn
                    )
                }
                if let plan = provider.planSurface {
                    LabeledContent("Surface", value: plan)
                        .foregroundStyle(.secondary)
                }
                if let region = provider.region {
                    LabeledContent("Region", value: region)
                        .foregroundStyle(.secondary)
                }
                Label("\(provider.modelSkus.count) catalog models", systemImage: "list.bullet")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                DisclosureGroup("Advanced Details") {
                    LabeledContent("Provider ID", value: provider.providerId)
                    LabeledContent("Connection", value: provider.connectionState)
                    LabeledContent("Runtime", value: readableRuntime(provider.runtimeState))
                }
                HStack {
                    if let checked = provider.lastChecked {
                        Text(L10n.quotaObservedAt(checked))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    Button {
                        Task { await store.connectProvider(providerId: provider.providerId) }
                    } label: {
                        Label(L10n.providersAddProvider, systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
    }
}

private func readableAuth(_ value: String) -> String {
    switch value {
    case "AUTHENTICATED": return "已确认登录"
    case "AUTH_REQUIRED": return "需要登录"
    case "AUTH_UNKNOWN": return "尚未确认登录状态"
    default: return value
    }
}

private func readableConnection(_ value: String) -> String {
    switch value {
    case "CONNECTED": return "已连接"
    case "DISCONNECTED": return "已断开"
    case "DISCOVERED": return "可添加"
    default: return value
    }
}

private func readableRuntime(_ value: String) -> String {
    switch value {
    case "AVAILABLE": return "当前运行环境可用"
    case "UNAVAILABLE": return "当前运行环境不可用"
    case "UNKNOWN": return "运行环境未知"
    default: return value
    }
}

private func authBadge(_ value: String) -> BadgeKind {
    switch value {
    case "AUTHENTICATED": return .good
    case "AUTH_REQUIRED": return .bad
    default: return .warn
    }
}

private struct ProviderCard: View {
    let provider: ProviderHealthView

    var body: some View {
        DashboardCard(title: provider.displayName, symbol: "network") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
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
                HStack(spacing: 8) {
                    Label("\(provider.executionTargets.count) models", systemImage: "cpu")
                    Label("\(provider.quotaPools.count) quota pools", systemImage: "chart.pie")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                DisclosureGroup("Advanced Details") {
                    LabeledContent("Provider ID", value: provider.providerId)
                    if !provider.executionTargets.isEmpty {
                        Divider()
                        Text(L10n.providerExecutionTargets)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ForEach(provider.executionTargets) { target in
                            ExecutionTargetDisclosure(target: target)
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

private struct ModelsSection: View {
    let providers: [ProviderHealthView]

    private var targets: [ProviderTargetContext] {
        providers.flatMap { provider in
            provider.executionTargets.map { ProviderTargetContext(provider: provider, target: $0) }
        }
    }

    var body: some View {
        if targets.isEmpty {
            EmptyStateView(title: "Models", symbol: "cpu", message: "No runnable models have been discovered yet.")
        } else {
            DashboardCard(title: "Models", symbol: "cpu") {
                ForEach(targets) { item in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.target.modelSkuId)
                                .font(.body.weight(.medium))
                            Text(item.provider.displayName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        StatusBadge(
                            text: item.target.enabled && item.target.isExecutionVerified ? "Ready" : "Needs verification",
                            kind: item.target.enabled && item.target.isExecutionVerified ? .good : .warn
                        )
                    }
                    .padding(.vertical, 4)
                    Divider()
                }
            }
        }
    }
}

private struct ProviderTargetContext: Identifiable {
    let provider: ProviderHealthView
    let target: ExecutionTargetHealthView

    var id: String { target.executionTargetId }
}

private struct ExecutionTargetsSection: View {
    let providers: [ProviderHealthView]

    var body: some View {
        ForEach(providers) { provider in
            DashboardCard(title: provider.displayName, symbol: "bolt.horizontal.circle") {
                if provider.executionTargets.isEmpty {
                    Text("No execution targets discovered.")
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

    private var providers: [ProviderHealthView] {
        store.providers?.providers ?? []
    }

    private var pools: [QuotaPoolContext] {
        providers.flatMap { provider in
            provider.quotaPools.map { QuotaPoolContext(provider: provider, pool: $0) }
        }
    }

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
            if providers.isEmpty {
                EmptyStateView(
                    title: L10n.quotaTitle,
                    symbol: "chart.pie",
                    message: store.providerDiscoveryStatus?.discoveryState == "PENDING"
                        ? L10n.discoveryEmpty
                        : L10n.noProvidersHint
                )
            } else if pools.isEmpty {
                EmptyStateView(
                    title: L10n.quotaTitle,
                    symbol: "chart.pie",
                    message: L10n.noProvidersHint
                )
            } else {
                QuotaSummaryCard(providers: providers)
                QuotaComparisonCard(pools: pools)
                LimitingQuotaWindowCard(pools: pools)
                QuotaHistoryCard(history: store.dashboard?.quotaHistory)
            }
        }
    }
}

private struct QuotaSummaryCard: View {
    let providers: [ProviderHealthView]

    private var pools: [QuotaPoolHealthView] {
        providers.flatMap(\.quotaPools)
    }

    private var nearLimit: Int {
        pools.flatMap(\.windows).filter { window in
            window.confidence != "UNKNOWN"
                && window.remainingFraction.map { $0 <= 0.15 } == true
        }.count
    }

    private var exhausted: Int {
        pools.filter { ["EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN"].contains($0.state) }.count
    }

    private var schedulableTargets: Int {
        providers.flatMap(\.executionTargets).filter {
            $0.enabled && $0.isExecutionVerified && $0.runtimeAvailable == true
        }.count
    }

    var body: some View {
        DashboardCard(title: "Quota Summary", symbol: "chart.pie") {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 10)], spacing: 10) {
                BasicInfoCell("Available providers", "\(providers.filter { !$0.quotaPools.isEmpty || !$0.executionTargets.isEmpty }.count)")
                BasicInfoCell("Near limit", "\(nearLimit)")
                BasicInfoCell("Exhausted or blocked", "\(exhausted)")
                BasicInfoCell("Schedulable targets", "\(schedulableTargets)")
            }
        }
    }
}

private struct QuotaPoolContext: Identifiable {
    let provider: ProviderHealthView
    let pool: QuotaPoolHealthView

    var id: String { pool.quotaPoolId }
}

private struct QuotaComparisonCard: View {
    let pools: [QuotaPoolContext]

    var body: some View {
        DashboardCard(title: "Provider Comparison", symbol: "list.bullet.rectangle") {
            ForEach(pools) { item in
                QuotaPoolComparisonRow(provider: item.provider, pool: item.pool)
                Divider()
            }
        }
    }
}

private struct QuotaPoolComparisonRow: View {
    let provider: ProviderHealthView
    let pool: QuotaPoolHealthView

    private var limitingWindow: QuotaWindowHealthView? {
        pool.windows
            .filter { $0.confidence != "UNKNOWN" && $0.remainingFraction != nil }
            .min { ($0.remainingFraction ?? 1) < ($1.remainingFraction ?? 1) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(provider.displayName)
                        .font(.body.weight(.medium))
                    Text("\(pool.name) / \(pool.planId)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                StatusBadge(text: pool.state, kind: badgeKind(for: pool.state))
                StatusBadge(text: pool.confidence, kind: pool.confidence == "UNKNOWN" ? .neutral : .good)
            }

            if let limitingWindow {
                HStack(spacing: 10) {
                    Label(limitingWindow.windowKind, systemImage: "timer")
                    Text(L10n.quotaRemaining(fraction: limitingWindow.remainingFraction, confidence: limitingWindow.confidence))
                    Text(limitingWindow.resetAt ?? L10n.quotaResetUnknown)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            } else {
                Text("No reliable remaining percentage is available for this quota pool.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            DisclosureGroup("Window Details") {
                ForEach(pool.windows, id: \.windowId) { window in
                    QuotaWindowDisclosure(window: window)
                }
                Divider()
                LabeledContent(L10n.measurementSource, value: pool.measurementSourceType)
                if let observedAt = pool.observedAt {
                    LabeledContent(L10n.observedAt, value: observedAt)
                }
                LabeledContent("Quota pool ID", value: pool.quotaPoolId)
                    .font(.system(.caption, design: .monospaced))
            }
        }
        .padding(.vertical, 4)
    }

    private func badgeKind(for state: String) -> BadgeKind {
        switch state {
        case "EXHAUSTED", "EXHAUSTED_OBSERVED", "COOLDOWN": return .bad
        case "RECOVERED", "AVAILABLE": return .good
        default: return .warn
        }
    }
}

private struct LimitingQuotaWindowCard: View {
    let pools: [QuotaPoolContext]

    private var limiting: (ProviderHealthView, QuotaPoolHealthView, QuotaWindowHealthView)? {
        pools.compactMap { item -> (ProviderHealthView, QuotaPoolHealthView, QuotaWindowHealthView)? in
            guard let window = item.pool.windows
                .filter({ $0.confidence != "UNKNOWN" && $0.remainingFraction != nil })
                .min(by: { ($0.remainingFraction ?? 1) < ($1.remainingFraction ?? 1) })
            else { return nil }
            return (item.provider, item.pool, window)
        }
        .min { ($0.2.remainingFraction ?? 1) < ($1.2.remainingFraction ?? 1) }
    }

    var body: some View {
        DashboardCard(title: "Limiting Window", symbol: "hourglass") {
            if let limiting {
                LabeledContent("Provider", value: limiting.0.displayName)
                LabeledContent("Plan", value: limiting.1.planId)
                LabeledContent("Window", value: limiting.2.windowKind)
                LabeledContent("Remaining", value: L10n.quotaRemaining(fraction: limiting.2.remainingFraction, confidence: limiting.2.confidence))
                LabeledContent("Confidence", value: limiting.2.confidence)
                LabeledContent("Reset", value: limiting.2.resetAt ?? L10n.quotaResetUnknown)
            } else {
                EmptyChartState("No limiting window can be derived from current observations.")
            }
        }
    }
}

private struct QuotaHistoryCard: View {
    let history: QuotaHistoryView?

    private var observations: [QuotaObservationView] {
        Array((history?.observations ?? [])
            .filter { $0.confidence != "UNKNOWN" && $0.remainingFraction != nil }
            .suffix(24))
    }

    var body: some View {
        DashboardCard(title: "Quota History", symbol: "chart.xyaxis.line") {
            if observations.count >= 2 {
                QuotaHistoryChart(observations: observations)
                Text("Showing recorded quota observations only. Retention limit: \(history?.retentionLimit ?? 0).")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else {
                EmptyChartState("Historical quota data is still being collected.")
            }
        }
    }
}

private struct QuotaHistoryChart: View {
    let observations: [QuotaObservationView]

    var body: some View {
        HStack(alignment: .bottom, spacing: 5) {
            ForEach(observations) { observation in
                let value = observation.remainingFraction ?? 0
                RoundedRectangle(cornerRadius: 3)
                    .fill(value <= 0.15 ? Color.red.opacity(0.8) : Color.blue.opacity(0.7))
                    .frame(width: 10, height: max(3, CGFloat(value) * 120))
                    .help("\(observation.providerId) \(observation.windowId): \(L10n.quotaRemaining(fraction: observation.remainingFraction, confidence: observation.confidence))")
            }
        }
        .frame(maxWidth: .infinity, minHeight: 130, alignment: .bottomLeading)
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
                if window.confidence != "UNKNOWN", let fraction = window.remainingFraction {
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
            BuildInformationCard(daemonBuild: store.daemonBuild,
                                 compatibility: store.buildCompatibility)
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
            // Global default lives daemon-side; AppStorage would make it a per-client
            // preference that routing could not honour.
            DashboardCard(title: L10n.settingsDefaultSchedulingPolicy, symbol: "slider.horizontal.3") {
                let selectable = store.schedulingSettings?.selectablePolicies
                    ?? ["BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST"]
                Picker(
                    L10n.settingsDefaultSchedulingPolicy,
                    selection: Binding(
                        get: { store.schedulingSettings?.defaultSchedulingPolicy ?? "BALANCED" },
                        set: { newValue in
                            Task { await store.setDefaultSchedulingPolicy(newValue) }
                        }
                    )
                ) {
                    ForEach(selectable, id: \.self) { policy in
                        Text(L10n.schedulingPolicyName(policy)).tag(policy)
                    }
                }
                .pickerStyle(.menu)
                .disabled(store.schedulingSettings == nil)
                Text(
                    L10n.schedulingPolicyDetail(
                        store.schedulingSettings?.defaultSchedulingPolicy ?? "BALANCED"
                    )
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                Text(L10n.settingsDefaultSchedulingPolicyFooter)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            DashboardCard(title: L10n.ownerExecutionSetting, symbol: "person.badge.key") {
                let enabled = store.ownerExecutionSettings?.ownerInitiatedExecutionEnabled ?? false
                Toggle(L10n.ownerExecutionToggle, isOn: Binding(
                    get: { enabled },
                    set: { newValue in
                        Task { await store.setOwnerExecution(enabled: newValue) }
                    }
                ))
                .disabled(store.ownerExecutionSettings == nil)
                Text(L10n.ownerExecutionFooter)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent(L10n.ownerExecutionState, value: enabled ? "ON" : "OFF")
                Text(L10n.ownerExecutionMeaning)
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
        .onAppear {
            Task { await store.loadSchedulingSettings() }
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
    let onStop: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail {
                    TaskSummaryPanel(
                        detail: detail,
                        onCopyTaskId: onCopyTaskId,
                        onReload: onReload,
                        onStop: onStop
                    )
                    ProgressPanel(detail: detail)
                    LiveActivityPanel(detail: detail)
                    ChangesPanel(detail: detail)
                    TestsVerificationPanel(detail: detail)
                    AdvancedDetailsPanel(detail: detail)
                    RawWorkerConsolePanel(detail: detail)
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

private struct TaskSummaryPanel: View {
    let detail: TaskDetailView
    let onCopyTaskId: () -> Void
    let onReload: () -> Void
    let onStop: () -> Void

    private var currentPhase: String {
        ExecutionPhase.derive(from: detail).first(where: { $0.isCurrent })?.title ?? "Preparing"
    }

    private var providerModel: String {
        detail.runs.last?.workerId
            ?? detail.routing?.selectedExecutionTargetId
            ?? "Auto"
    }

    var body: some View {
        DashboardCard(title: "Summary", symbol: "doc.text.magnifyingglass") {
            HStack(alignment: .firstTextBaseline) {
                Text(detail.task.intent)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                StatusBadge(
                    text: detail.task.state,
                    kind: detail.task.state == "BLOCKED" ? .bad : (detail.task.state == "VERIFIED" ? .good : .neutral)
                )
            }
            LabeledContent(L10n.providerModel, value: providerModel)
            LabeledContent("Current phase", value: currentPhase)
            LabeledContent("Elapsed", value: elapsedText(start: detail.task.createdAt, end: detail.task.updatedAt))
            HStack(spacing: 8) {
                if detail.task.state == "RUNNING" || detail.task.state == "VERIFYING" {
                    Button(role: .destructive) {
                        onStop()
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .help("Cancel through the authoritative host execution path.")
                }
                Button {
                    onReload()
                } label: {
                    Label(L10n.reload, systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("r", modifiers: [.command, .option])
                .help(L10n.reloadHint)

                Button {
                    onCopyTaskId()
                } label: {
                    Label(L10n.copyTaskId, systemImage: "doc.on.doc")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("c", modifiers: [.command, .option])
                .help(L10n.copyTaskIdHint)
            }
        }
    }
}

private struct ExecutionPhase: Identifiable {
    let title: String
    let isComplete: Bool
    let isCurrent: Bool

    var id: String { title }

    static func derive(from detail: TaskDetailView) -> [ExecutionPhase] {
        let events = Set(detail.events.map(\.eventType))
        let hasWorkspace = detail.workspace != nil || events.contains("WORKSPACE_REGISTERED")
        let hasRouting = detail.routing != nil || events.contains("ROUTING_DECISION_RECORDED")
        let hasQuota = events.contains("QUOTA_ADMITTED")
        let hasRun = !detail.runs.isEmpty || events.contains("RUN_STARTED")
        let editingDone = detail.runs.contains { $0.status != "RUNNING" } || events.contains("RUN_FINISHED")
        let verifying = detail.task.state == "VERIFYING"
            || detail.task.state == "VERIFIED"
            || detail.verification.status != "NOT_VERIFIED"
        let finished = ["VERIFIED", "BLOCKED", "FAILED", "CANCELLED", "COMPLETED"].contains(detail.task.state)
        let states: [(String, Bool)] = [
            ("Preparing", true),
            ("Routing", hasRouting),
            ("Workspace", hasWorkspace),
            ("Quota", hasQuota),
            ("Starting worker", hasRun),
            ("Editing", editingDone),
            ("Testing", editingDone),
            ("Verifying", verifying),
            ("Finished", finished),
        ]
        let currentIndex = states.firstIndex { !$0.1 } ?? states.count - 1
        return states.enumerated().map { index, item in
            ExecutionPhase(title: item.0, isComplete: item.1, isCurrent: index == currentIndex)
        }
    }
}

private struct ProgressPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: "Progress", symbol: "checklist") {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
                ForEach(ExecutionPhase.derive(from: detail)) { phase in
                    HStack(spacing: 6) {
                        Image(systemName: phase.isComplete ? "checkmark.circle.fill" : (phase.isCurrent ? "circle.dotted" : "circle"))
                            .foregroundStyle(phase.isComplete ? .green : (phase.isCurrent ? .accentColor : .secondary))
                        Text(phase.title)
                            .lineLimit(1)
                    }
                    .font(.caption)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }
}

private struct LiveActivityPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: "Live Activity", symbol: "waveform.path.ecg") {
            EventList(events: detail.events)
        }
    }
}

private struct ChangesPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: "Changes", symbol: "doc.on.clipboard") {
            if let workspace = detail.workspace {
                LabeledContent(L10n.worktree, value: workspace.worktreePath)
                    .font(.system(.caption, design: .monospaced))
                LabeledContent(L10n.branch, value: workspace.branch)
                LabeledContent("Files changed", value: changedFileText)
            } else {
                Label("No task worktree has been registered yet.", systemImage: "tray")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var changedFileText: String {
        guard let result = detail.verification.result else { return "not verified yet" }
        let count = result.changedPaths.count
        return "\(count)"
    }
}

private struct TestsVerificationPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: "Tests / Verification", symbol: "checkmark.seal") {
            StatusBadge(text: detail.verification.status, kind: detail.verification.status == "VERIFIED" ? .good : .neutral)
            LabeledContent(L10n.evidenceLabel, value: detail.verification.evidenceId ?? "none")
            if let result = detail.verification.result {
                LabeledContent("Verifier profile", value: result.profile)
                ForEach(result.stages) { stage in
                    LabeledContent(stage.name, value: stage.passed ? "passed" : "failed")
                }
            }
            if let failure = detail.verification.failureReason {
                Text(failure).foregroundStyle(.red)
            }
            ForEach(detail.approvals.approvals) { approval in
                Text("\(approval.kind): \(approval.status)")
            }
        }
    }
}

/// All five owner-facing scheduling modes, plus which one is currently in force.
///
/// Shown on the Routing page even with no task selected, so the owner can always see
/// and change how work is being routed.
private struct ActiveSchedulingPolicyCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var expanded = false

    private static let modes = [
        "BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "MANUAL",
    ]

    private var activePolicy: String {
        store.schedulingSettings?.defaultSchedulingPolicy ?? "BALANCED"
    }

    var body: some View {
        DashboardCard(title: L10n.policyTitle, symbol: "slider.horizontal.3") {
            Text(L10n.schedulingPolicyName(activePolicy))
                .font(.title3.weight(.semibold))
            Text(L10n.schedulingPolicyDetail(activePolicy))
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Text(L10n.policyGlobalDefault)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                Spacer()
                Button {
                    expanded.toggle()
                } label: {
                    Label(L10n.policyChange, systemImage: "pencil")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            if expanded {
                Divider()
                Text(L10n.routingModes)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ForEach(Self.modes, id: \.self) { mode in
                    SchedulingModeRow(
                        mode: mode,
                        isActive: mode == activePolicy,
                        // MANUAL names a per-task target, so it is not a global default.
                        isSelectable: mode != "MANUAL",
                        onSelect: {
                            Task { await store.setDefaultSchedulingPolicy(mode) }
                        }
                    )
                }
            }
        }
    }
}

private struct SchedulingModeRow: View {
    let mode: String
    let isActive: Bool
    let isSelectable: Bool
    let onSelect: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: isActive ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(isActive ? Color.accentColor : Color.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(L10n.schedulingPolicyName(mode))
                    .font(.callout.weight(isActive ? .semibold : .regular))
                Text(L10n.schedulingPolicyDetail(mode))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if isSelectable && !isActive {
                Button(action: onSelect) {
                    Text(L10n.policyChange)
                }
                .buttonStyle(.borderless)
                .controlSize(.small)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Routing page state when no task is selected: explain what the scheduler weighs,
/// and surface the provider problem when there is nothing eligible to weigh.
private struct RoutingNoSelectionCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let hasTasks: Bool
    let onNewTask: () -> Void
    let onOpenProviders: () -> Void

    private var connectedCount: Int {
        store.providerConnections?.connected.count ?? 0
    }

    private var importCandidateCount: Int {
        store.providerConnections?.importCandidates.count ?? 0
    }

    var body: some View {
        DashboardCard(title: L10n.routingTitle, symbol: "point.topleft.down.curvedto.point.bottomright.up") {
            if connectedCount == 0 {
                Label(L10n.routingNoConnectedProviders, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    Button(action: onOpenProviders) {
                        Label(L10n.providersAddProvider, systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                    if importCandidateCount > 0 {
                        Button(action: onOpenProviders) {
                            Label(L10n.providersImportExisting, systemImage: "square.and.arrow.down")
                        }
                        .buttonStyle(.bordered)
                    }
                }
            } else {
                Text(hasTasks ? L10n.routingNoTaskSelected : L10n.routingNeedsTask)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(L10n.routingConsiders)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if !hasTasks {
                    Button(action: onNewTask) {
                        Label(L10n.newTask, systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
    }
}

private struct RoutingPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DashboardCard(title: L10n.routingExplanation, symbol: "point.topleft.down.curvedto.point.bottomright.up") {
            if let routing = detail.routing {
                StatusBadge(text: routing.mode ?? "UNKNOWN", kind: .neutral)
                // The resolved policy and where it came from are read from the frozen
                // decision record, not from current Settings.
                if let resolved = routing.resolvedPolicy {
                    LabeledContent(
                        L10n.routingResolvedPolicy,
                        value: L10n.schedulingPolicyName(resolved)
                    )
                } else if let policy = routing.policyId {
                    LabeledContent(L10n.routingResolvedPolicy, value: L10n.schedulingPolicyName(policy))
                }
                if let source = routing.policyResolutionSource {
                    LabeledContent(
                        L10n.policyResolutionSourceLabel,
                        value: L10n.policyResolutionSource(source)
                    )
                }
                LabeledContent(L10n.actualExecutionTarget, value: routing.selectedExecutionTargetId ?? "none")
                LabeledContent(L10n.wouldSelect, value: routing.mode == "SHADOW" ? (routing.selectedExecutionTargetId ?? "none") : "not shadow")
                if let why = routing.whySelected {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L10n.routingWhySelected)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(why)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !routing.candidates.isEmpty {
                    Divider()
                    Text(L10n.routingCandidates)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(Array(routing.candidates.enumerated()), id: \.offset) { _, candidate in
                        RoutingCandidateRow(candidate: candidate)
                    }
                }
                if let reason = routing.fallbackReason {
                    Text(reason).foregroundStyle(.secondary)
                }
                DisclosureGroup("Advanced Details") {
                    LabeledContent("Routing decision", value: routing.decisionId)
                    LabeledContent("Routing request", value: routing.requestId)
                }
            } else {
                Label(L10n.routingNotYetDecided, systemImage: "clock")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct RoutingCandidateRow: View {
    let candidate: [String: RoutingDecisionView.StringValue]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(candidate["model_display_name"]?.value
                    ?? candidate["model_sku_id"]?.value
                    ?? "Unknown model")
                    .font(.caption.weight(.semibold))
                Spacer()
                StatusBadge(text: statusText, kind: statusKind)
                if let score = candidate["score"]?.value {
                    Text(score)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            if let provider = candidate["provider_display_name"]?.value {
                Text(provider)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if let whyNot = candidate["why_not_selected"]?.value {
                // Structured scheduler evidence, verbatim — never model-generated prose.
                VStack(alignment: .leading, spacing: 2) {
                    Text(L10n.routingWhyNotSelected)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tertiary)
                    Text(whyNot)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            DisclosureGroup("Advanced Details") {
                if let target = candidate["execution_target_id"]?.value {
                    LabeledContent("Execution target", value: target)
                }
                if let providerId = candidate["provider_id"]?.value {
                    LabeledContent("Provider ID", value: providerId)
                }
                if let quota = candidate["quota_snapshot_id"]?.value {
                    LabeledContent("Quota evidence", value: quota)
                }
            }
        }
        .padding(.vertical, 6)
    }

    private var statusText: String {
        if candidate["selected"]?.boolValue == true { return "Selected" }
        if candidate["eligible"]?.boolValue == true && candidate["admitted"]?.boolValue == true {
            return "Eligible"
        }
        return "Ineligible"
    }

    private var statusKind: BadgeKind {
        if candidate["selected"]?.boolValue == true { return .good }
        if candidate["eligible"]?.boolValue == true && candidate["admitted"]?.boolValue == true {
            return .neutral
        }
        return .warn
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

/// Owner-initiated execution panel. Dispatch is only enabled when the
/// safety gates hold: the owner setting is ON, the task is dispatchable,
/// and a verified execution target is selected. Nothing here implies
/// autonomous Production ACTIVE.
private struct OwnerDispatchPanel: View {
    @EnvironmentObject private var store: OrchestratorStore
    let detail: TaskDetailView
    @State private var selectedTargetId: String = ""

    private var verifiedTargets: [ExecutionTargetHealthView] {
        (store.providers?.providers ?? [])
            .flatMap(\.executionTargets)
            .filter { $0.enabled && $0.isExecutionVerified }
    }

    /// MANUAL may only name a target on a connected provider — scheduling never
    /// reaches catalog-only or merely importable surfaces.
    private var connectedTargets: [ExecutionTargetHealthView] {
        let connectedIds = Set(
            (store.providerConnections?.connected ?? []).map(\.providerId)
        )
        return (store.providers?.providers ?? [])
            .filter { connectedIds.contains($0.providerId) }
            .flatMap(\.executionTargets)
    }

    private static let selectablePolicies = [
        "BALANCED", "QUALITY_FIRST", "QUOTA_SAVER", "SPEED_FIRST", "MANUAL",
    ]

    private var ownerSettingEnabled: Bool {
        store.ownerExecutionSettings?.ownerInitiatedExecutionEnabled ?? false
    }

    private var taskDispatchable: Bool {
        detail.task.state == "SUBMITTED" || detail.task.state == "READY"
    }

    private var canDispatch: Bool {
        ownerSettingEnabled && taskDispatchable && !verifiedTargets.isEmpty && !selectedTargetId.isEmpty
    }

    var body: some View {
        DashboardCard(title: L10n.ownerDispatch, symbol: "person.badge.key") {
            if !ownerSettingEnabled {
                Label(L10n.ownerExecutionDisabled, systemImage: "lock")
                    .foregroundStyle(.secondary)
            }
            if !taskDispatchable {
                Label(
                    L10n.taskNotDispatchable(detail.task.state),
                    systemImage: "exclamationmark.circle"
                )
                .foregroundStyle(.secondary)
            }
            if verifiedTargets.isEmpty {
                Label(L10n.noVerifiedTargets, systemImage: "xmark.shield")
                    .foregroundStyle(.secondary)
            } else {
                Picker(L10n.executionTarget, selection: $selectedTargetId) {
                    ForEach(verifiedTargets) { target in
                        Text(target.executionTargetId).tag(target.executionTargetId)
                    }
                }
                if let target = verifiedTargets.first(where: { $0.executionTargetId == selectedTargetId }) {
                    LabeledContent(L10n.providerModel, value: target.modelSkuId)
                }
            }
            Button {
                Task {
                    await store.dispatch(
                        taskId: detail.task.taskId,
                        executionTargetId: selectedTargetId
                    )
                }
            } label: {
                Label(L10n.dispatch, systemImage: "play")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!canDispatch)
            .help(canDispatch ? L10n.dispatchHelp : L10n.dispatchBlockedHint)

            if let dispatch = store.lastDispatch, dispatch.task.taskId == detail.task.taskId {
                LabeledContent(L10n.dispatchStatus, value: dispatch.status)
                if let code = dispatch.failureCode {
                    LabeledContent(L10n.failureCode, value: code).foregroundStyle(.red)
                }
                if let reason = dispatch.reason {
                    Text(reason)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let workspace = detail.workspace {
                LabeledContent(L10n.worktree, value: workspace.worktreePath)
                    .font(.system(.caption, design: .monospaced))
                LabeledContent(L10n.branch, value: workspace.branch)
                LabeledContent(L10n.writerLock, value: workspace.writerLocked ? "HELD" : "RELEASED")
            }
            ForEach(detail.runs) { run in
                VStack(alignment: .leading, spacing: 2) {
                    LabeledContent(L10n.workerRun, value: run.status)
                    LabeledContent("PID", value: run.pid.map(String.init) ?? "-")
                }
            }
            Text(L10n.ownerDispatchFooter)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .onAppear {
            if selectedTargetId.isEmpty {
                selectedTargetId = verifiedTargets.first?.executionTargetId ?? ""
            }
        }
    }
}

private struct AdvancedDetailsPanel: View {
    let detail: TaskDetailView

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 8) {
                LabeledContent("Task ID", value: detail.task.taskId)
                LabeledContent("Request ID", value: detail.task.requestId)
                if let routing = detail.routing {
                    LabeledContent("Routing decision", value: routing.decisionId)
                    LabeledContent("Routing request", value: routing.requestId)
                }
                if let workspace = detail.workspace {
                    LabeledContent(L10n.worktree, value: workspace.worktreePath)
                        .font(.system(.caption, design: .monospaced))
                    LabeledContent("Base SHA", value: workspace.baseSha)
                    LabeledContent(L10n.writerLock, value: workspace.writerLocked ? "HELD" : "RELEASED")
                }
                ForEach(detail.runs) { run in
                    VStack(alignment: .leading, spacing: 4) {
                        LabeledContent("Run ID", value: run.runId)
                        LabeledContent("Execution target", value: run.workerId)
                        LabeledContent("PID", value: run.pid.map(String.init) ?? "-")
                        LabeledContent("Run status", value: run.status)
                    }
                    .padding(.vertical, 4)
                    Divider()
                }
            }
        } label: {
            Label("Advanced Details", systemImage: "gearshape.2")
                .font(.headline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct RawWorkerConsolePanel: View {
    let detail: TaskDetailView

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                if detail.runs.isEmpty {
                    Label("No worker run has started.", systemImage: "tray")
                        .foregroundStyle(.secondary)
                }
                ForEach(detail.runs) { run in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("worker=\(run.workerId) status=\(run.status)")
                        if let exitCode = run.exitCode {
                            Text("exit_code=\(exitCode)")
                        }
                        Text("stdout_bytes=\(run.stdoutBytes ?? 0) stderr_bytes=\(run.stderrBytes ?? 0)")
                        if let stdout = run.stdoutSHA256 {
                            Text("stdout_sha256=\(stdout)")
                        }
                        if let stderr = run.stderrSHA256 {
                            Text("stderr_sha256=\(stderr)")
                        }
                        if run.outputTruncated == true {
                            Text("output_truncated=true")
                        }
                        if run.timedOut == true {
                            Text("timed_out=true")
                        }
                    }
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(Color.black.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
                }
                ForEach(detail.events) { event in
                    Text("[\(event.createdAt)] \(event.eventType): \(event.summary)")
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        } label: {
            Label("Raw Worker Output", systemImage: "terminal")
                .font(.headline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
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

private func elapsedText(start: String, end: String) -> String {
    guard let startDate = parseAPIDate(start),
          let endDate = parseAPIDate(end) else {
        return "unknown"
    }
    let seconds = max(0, Int(endDate.timeIntervalSince(startDate)))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)m \(seconds % 60)s" }
    return "\(minutes / 60)h \(minutes % 60)m"
}

private func parseAPIDate(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) {
        return date
    }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: value)
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
    let detail: String?
    let action: () -> Void
    @State private var hovering = false

    /// Every KPI tile reserves the same height regardless of whether it carries a
    /// secondary line, so five tiles in a row stay geometrically identical.
    private static let tileHeight: CGFloat = 108

    init(
        _ label: String,
        _ value: Int,
        symbol: String,
        detail: String? = nil,
        action: @escaping () -> Void
    ) {
        self.label = label
        self.value = value
        self.symbol = symbol
        self.detail = detail
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            DashboardCard(title: label, symbol: symbol, titleLineLimit: 1) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(value)")
                        .font(.system(size: 28, weight: .semibold, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    // The secondary line keeps the aggregate honest: an owner can see
                    // the verifying/verified split without a second, taller card.
                    Text(detail ?? " ")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .opacity(detail == nil ? 0 : 1)
                        .accessibilityHidden(detail == nil)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: Self.tileHeight, alignment: .top)
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

private struct BasicInfoCell: View {
    let label: String
    let value: String

    init(_ label: String, _ value: String) {
        self.label = label
        self.value = value
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(value)
                .font(.title3.weight(.semibold))
                .lineLimit(1)
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct TaskTrendChart: View {
    let buckets: [TaskTrendBucketView]

    private var maxValue: Int {
        max(1, buckets.map { $0.submitted + $0.completed + $0.blocked }.max() ?? 1)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(buckets.suffix(12)) { bucket in
                VStack(spacing: 2) {
                    stackedBar(bucket)
                    Text(hour(bucket.bucketStart))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: 150)
    }

    private func stackedBar(_ bucket: TaskTrendBucketView) -> some View {
        let submitted = CGFloat(bucket.submitted) / CGFloat(maxValue)
        let completed = CGFloat(bucket.completed) / CGFloat(maxValue)
        let blocked = CGFloat(bucket.blocked) / CGFloat(maxValue)
        return VStack(spacing: 0) {
            Rectangle().fill(Color.red).frame(height: max(0, blocked * 120))
            Rectangle().fill(Color.green).frame(height: max(0, completed * 120))
            Rectangle().fill(Color.blue).frame(height: max(2, submitted * 120))
        }
        .frame(width: 12, height: 120, alignment: .bottom)
        .clipShape(RoundedRectangle(cornerRadius: 3))
        .help("Submitted \(bucket.submitted), completed \(bucket.completed), blocked \(bucket.blocked)")
    }

    private func hour(_ value: String) -> String {
        guard value.count >= 13 else { return value }
        return String(value.dropFirst(11).prefix(2))
    }
}

private struct StateDistributionChart: View {
    let slices: [TaskStateSliceView]

    private var total: Int {
        max(1, slices.map(\.count).reduce(0, +))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(slices) { slice in
                HStack {
                    Text(slice.state)
                        .font(.caption)
                        .frame(width: 92, alignment: .leading)
                    GeometryReader { proxy in
                        RoundedRectangle(cornerRadius: 4)
                            .fill(color(for: slice.state).opacity(0.75))
                            .frame(width: max(4, proxy.size.width * CGFloat(slice.count) / CGFloat(total)))
                    }
                    .frame(height: 8)
                    Text("\(slice.count)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(width: 30, alignment: .trailing)
                }
            }
        }
        .frame(minHeight: 150, alignment: .center)
    }

    private func color(for state: String) -> Color {
        switch state {
        case "RUNNING", "VERIFYING": return .blue
        case "VERIFIED", "COMPLETED": return .green
        case "BLOCKED", "FAILED": return .red
        case "READY", "SUBMITTED": return .orange
        default: return .secondary
        }
    }
}

private struct EmptyChartState: View {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var body: some View {
        Label(message, systemImage: "chart.bar.xaxis")
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, minHeight: 150)
    }
}

private struct RiskRow: View {
    let risk: RiskItemView
    @Binding var section: DashboardSection?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                Text(risk.title)
                    .font(.body.weight(.medium))
                Text(risk.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let destination = risk.destination,
               let target = sectionForDestination(destination) {
                Button {
                    section = target
                } label: {
                    Image(systemName: "arrow.right.circle")
                }
                .buttonStyle(.borderless)
                .help(target.title)
            }
        }
        .padding(.vertical, 4)
    }

    private var color: Color {
        switch risk.severity {
        case "BLOCKED": return .red
        case "WARNING": return .orange
        case "UNKNOWN": return .secondary
        default: return .blue
        }
    }

    private var symbol: String {
        switch risk.severity {
        case "BLOCKED": return "xmark.octagon.fill"
        case "WARNING": return "exclamationmark.triangle.fill"
        case "UNKNOWN": return "questionmark.circle.fill"
        default: return "info.circle.fill"
        }
    }

    private func sectionForDestination(_ destination: String) -> DashboardSection? {
        switch destination {
        case "projects": return .projects
        case "models_providers": return .providers
        case "quota": return .quota
        case "settings": return .settings
        case "verification": return .verification
        default: return nil
        }
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

/// Which commit produced the running app, and whether the daemon agrees.
///
/// This exists because a stale binary is otherwise indistinguishable from a
/// current one: every copy of the app shares a bundle identifier and version
/// string, so only the commit tells them apart.
private struct BuildInformationCard: View {
    let daemonBuild: BuildView?
    let compatibility: BuildCompatibility

    private var app: BuildIdentity { BuildIdentity.current }

    var body: some View {
        DashboardCard(title: L10n.buildTitle, symbol: "hammer") {
            if compatibility.isMismatch {
                mismatchBanner
            }
            LabeledContent(L10n.buildVersion, value: appVersion)
            LabeledContent(L10n.buildShort, value: display(app.shortSHA))
            LabeledContent(L10n.buildConfiguration, value: display(app.configuration))
            LabeledContent(L10n.buildTimestamp, value: display(app.builtAt))
            LabeledContent(L10n.buildDaemon, value: display(daemonBuild?.shortSHA))
            statusLabel
            DisclosureGroup(L10n.buildAdvanced) {
                VStack(alignment: .leading, spacing: 6) {
                    LabeledContent(L10n.buildCommit, value: display(app.commitSHA))
                    LabeledContent(
                        "\(L10n.buildDaemon) · \(L10n.buildCommit)",
                        value: display(daemonBuild?.commitSHA)
                    )
                }
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 4)
            }
            .font(.caption)
        }
    }

    private var mismatchBanner: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(L10n.buildMismatchTitle, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline.bold())
                .foregroundStyle(.orange)
            LabeledContent(L10n.buildMismatchApp, value: display(app.shortSHA))
            LabeledContent(L10n.buildMismatchDaemon, value: display(daemonBuild?.shortSHA))
            Text(L10n.buildMismatchFooter)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder
    private var statusLabel: some View {
        switch compatibility {
        case .matched:
            Label(L10n.buildMatched, systemImage: "checkmark.seal")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .indeterminate:
            Label(L10n.buildIndeterminate, systemImage: "questionmark.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .mismatched:
            EmptyView()
        }
    }

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }

    /// An unresolved value is shown as UNKNOWN, never blank and never guessed.
    private func display(_ value: String?) -> String {
        guard let value,
              !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              value != BuildIdentity.unknownValue
        else {
            return L10n.buildUnknown
        }
        return value
    }
}

private struct DashboardCard<Content: View>: View {
    let title: String
    let symbol: String
    /// KPI tiles pin this to 1 so a longer title can never grow one card in a row.
    var titleLineLimit: Int? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: symbol)
                .font(.headline)
                .lineLimit(titleLineLimit)
                .minimumScaleFactor(titleLineLimit == nil ? 1.0 : 0.7)
                .frame(maxWidth: .infinity, alignment: .leading)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
