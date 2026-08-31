import SwiftUI

import PAOControlKit

enum DashboardSection: String, CaseIterable, Identifiable {
    case overview
    case tasks
    case agents
    case providers
    case quota
    case routing
    case verification
    case history
    case settings

    var id: String { rawValue }

    var title: String { L10n.dashboardSection(rawValue) }

    var symbol: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.67percent"
        case .tasks: return "checklist"
        case .agents: return "cpu"
        case .providers: return "network"
        case .quota: return "chart.pie"
        case .routing: return "point.topleft.down.curvedto.point.bottomright.up"
        case .verification: return "checkmark.seal"
        case .history: return "clock.arrow.circlepath"
        case .settings: return "gearshape"
        }
    }
}

struct DashboardView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @SceneStorage("dashboard.selection") private var selectedRaw = DashboardSection.overview.rawValue
    @State private var selectedTaskId: String?
    @State private var query = ""
    @State private var stateFilter = ""

    private var selection: Binding<DashboardSection> {
        Binding(
            get: { DashboardSection(rawValue: selectedRaw) ?? .overview },
            set: { selectedRaw = $0.rawValue }
        )
    }

    var body: some View {
        NavigationSplitView {
            List(DashboardSection.allCases, selection: selection) { section in
                Label(section.title, systemImage: section.symbol)
            }
            .navigationSplitViewColumnWidth(min: 210, ideal: 230)
        } detail: {
            detail
                .navigationTitle(selection.wrappedValue.title)
                .toolbar {
                    ToolbarItem {
                        Button {
                            Task { await store.refreshNow() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .help(L10n.refresh)
                    }
                }
        }
        .frame(minWidth: 960, minHeight: 620)
        .onAppear {
            store.dashboardVisible = true
            Task { await store.refreshNow() }
        }
        .onDisappear { store.dashboardVisible = false }
    }

    @ViewBuilder
    private var detail: some View {
        switch selection.wrappedValue {
        case .overview:
            OverviewDashboard()
        case .tasks:
            TasksDashboard(query: $query, stateFilter: $stateFilter, selectedTaskId: $selectedTaskId)
        case .agents:
            ExecutionTargetsDashboard()
        case .providers:
            ProvidersDashboard()
        case .quota:
            QuotaDashboard()
        case .routing:
            SelectedTaskDetailDashboard(selectedTaskId: $selectedTaskId, mode: .routing)
        case .verification:
            SelectedTaskDetailDashboard(selectedTaskId: $selectedTaskId, mode: .verification)
        case .history:
            HistoryDashboard()
        case .settings:
            ClientSettingsDashboard()
        }
    }
}

private struct OverviewDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                DashboardCard(title: L10n.connectionLabel, symbol: store.statusSummary.systemImage) {
                    HStack {
                        StatusBadge(text: L10n.statusTitle(store.statusSummary), kind: store.connection.isConnected ? .good : .bad)
                        Text(store.connection.isConnected ? L10n.connectedLabel : disconnectedText)
                            .foregroundStyle(.secondary)
                    }
                    Text(store.socketPath)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }

                let counts = store.dashboard?.counts
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 5), spacing: 12) {
                    MetricTile("RUNNING", counts?.running ?? 0, symbol: "gearshape.2")
                    MetricTile("READY", counts?.ready ?? 0, symbol: "tray")
                    MetricTile("BLOCKED", counts?.blocked ?? 0, symbol: "exclamationmark.octagon")
                    MetricTile("VERIFIED", counts?.verified ?? 0, symbol: "checkmark.seal")
                    MetricTile("COMPLETED", counts?.completed ?? 0, symbol: "checkmark.circle")
                }

                DashboardCard(title: L10n.blockers, symbol: "exclamationmark.triangle") {
                    if let blockers = store.dashboard?.importantBlockers, !blockers.isEmpty {
                        ForEach(blockers, id: \.self) { Text($0).foregroundStyle(.secondary) }
                    } else {
                        Text(L10n.unsupportedEmptyState).foregroundStyle(.secondary)
                    }
                    StatusBadge(text: store.activeStatus?.productionActive ?? "UNKNOWN", kind: .neutral)
                }

                DashboardCard(title: L10n.events, symbol: "clock") {
                    EventList(events: store.dashboard?.recentEvents ?? [])
                }
            }
            .padding(20)
        }
    }

    private var disconnectedText: String {
        if case .disconnected(let reason) = store.connection {
            return L10n.disconnectionReason(reason)
        }
        return ""
    }
}

private struct TasksDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var query: String
    @Binding var stateFilter: String
    @Binding var selectedTaskId: String?

    private var tasks: [TaskView] {
        let source = store.tasks?.tasks ?? []
        return source.filter { task in
            let matchesQuery = query.isEmpty
                || task.taskId.localizedCaseInsensitiveContains(query)
                || task.intent.localizedCaseInsensitiveContains(query)
            let matchesState = stateFilter.isEmpty || task.state == stateFilter
            return matchesQuery && matchesState
        }
    }

    private var states: [String] {
        Array(Set((store.tasks?.tasks ?? []).map(\.state))).sorted()
    }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                HStack {
                    TextField(L10n.search, text: $query)
                        .textFieldStyle(.roundedBorder)
                    Picker(L10n.stateFilter, selection: $stateFilter) {
                        Text(L10n.allStates).tag("")
                        ForEach(states, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.menu)
                    .frame(width: 170)
                }
                .padding()

                List(tasks, selection: $selectedTaskId) { task in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(task.taskId)
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
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
                    .padding(.vertical, 4)
                }
                .onChange(of: selectedTaskId) { taskId in
                    guard let taskId else { return }
                    Task { await store.loadTaskDetail(taskId: taskId) }
                }
            }
            .frame(minWidth: 420)

            TaskDetailPanel(detail: store.selectedTaskDetail)
                .frame(minWidth: 430)
        }
    }
}

private enum DetailMode {
    case routing
    case verification
}

private struct SelectedTaskDetailDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var selectedTaskId: String?
    let mode: DetailMode

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail = store.selectedTaskDetail {
                    if mode == .routing {
                        RoutingPanel(detail: detail)
                    } else {
                        VerificationPanel(detail: detail)
                    }
                } else {
                    EmptyStateView(title: L10n.taskDetail, symbol: "sidebar.left",
                                   message: L10n.selectTaskEmptyState)
                }
            }
            .padding(20)
        }
        .onAppear {
            if let selectedTaskId {
                Task { await store.loadTaskDetail(taskId: selectedTaskId) }
            }
        }
    }
}

private struct ExecutionTargetsDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(store.providers?.providers ?? []) { provider in
                    DashboardCard(title: provider.displayName, symbol: "cpu") {
                        ForEach(provider.executionTargets) { target in
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(target.executionTargetId).font(.system(.body, design: .monospaced))
                                    Spacer()
                                    StatusBadge(text: target.enabled ? "ENABLED" : "DISABLED", kind: target.enabled ? .good : .neutral)
                                }
                                Label(target.modelSkuId, systemImage: "shippingbox")
                                Label(target.runtimeId, systemImage: "terminal")
                                if let observed = target.observedAvailability {
                                    Text("\(observed.state) | \(observed.measurementSource) | \(observed.confidence)")
                                        .foregroundStyle(.secondary)
                                } else {
                                    Text("availability UNKNOWN").foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
            .padding(20)
        }
    }
}

private struct ProvidersDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(store.providers?.providers ?? []) { provider in
                    DashboardCard(title: provider.displayName, symbol: "network") {
                        Text("\(provider.providerId) | \(L10n.accountsCount(provider.accountCount))")
                            .foregroundStyle(.secondary)
                        ForEach(provider.quotaPools) { pool in
                            Text("\(pool.name): \(pool.state) | \(pool.confidence) | \(pool.measurementSourceType)")
                        }
                        ForEach(provider.executionTargets) { target in
                            Text("\(L10n.providerTargetLabel) \(target.executionTargetId)")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .padding(20)
        }
    }
}

private struct QuotaDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(store.providers?.providers ?? []) { provider in
                    ForEach(provider.quotaPools) { pool in
                        DashboardCard(title: "\(provider.displayName) / \(pool.name)", symbol: "chart.pie") {
                            HStack {
                                StatusBadge(text: pool.confidence, kind: pool.confidence == "EXACT" ? .good : .neutral)
                                StatusBadge(text: pool.measurementSourceType, kind: .neutral)
                                StatusBadge(text: pool.state, kind: pool.state == "UNKNOWN" ? .warn : .good)
                            }
                            ForEach(pool.windows, id: \.windowId) { window in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("\(window.windowKind) / \(window.state)")
                                    if window.confidence == "EXACT", let fraction = window.remainingFraction {
                                        ProgressView(value: fraction)
                                        Text(L10n.quotaRemaining(fraction: fraction, confidence: window.confidence))
                                    } else {
                                        Text(L10n.quotaRemaining(fraction: window.remainingFraction, confidence: window.confidence))
                                            .foregroundStyle(.secondary)
                                    }
                                    Text(window.resetAt ?? "reset UNKNOWN")
                                        .font(.caption)
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                }
            }
            .padding(20)
        }
    }
}

private struct HistoryDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        ScrollView {
            DashboardCard(title: L10n.history, symbol: "clock.arrow.circlepath") {
                EventList(events: store.dashboard?.recentEvents ?? [])
            }
            .padding(20)
        }
    }
}

struct ClientSettingsDashboard: View {
    @AppStorage("pao.launchAtLogin") private var launchAtLogin = false
    @AppStorage("pao.autoStartDaemon") private var autoStartDaemon = true
    @EnvironmentObject private var store: OrchestratorStore
    private let layout = AppSupportLayout.resolve()

    var body: some View {
        Form {
            Section(L10n.daemonLifecycle) {
                Toggle(L10n.launchAtLogin, isOn: $launchAtLogin)
                Toggle(L10n.autoStartDaemon, isOn: $autoStartDaemon)
                LabeledContent(L10n.socket, value: store.socketPath)
                LabeledContent(L10n.runtimeConfig, value: layout.runtimeConfigPath)
            }
            Section(L10n.productionActive) {
                LabeledContent("ACTIVE", value: store.activeStatus?.productionActive ?? "UNKNOWN")
                Text("ACTIVE is read-only in P4.2.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(20)
    }
}

private struct TaskDetailPanel: View {
    let detail: TaskDetailView?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail {
                    DashboardCard(title: L10n.taskDetail, symbol: "doc.text.magnifyingglass") {
                        Text(detail.task.taskId).font(.system(.headline, design: .monospaced))
                        Text(detail.task.intent)
                        StatusBadge(text: detail.task.state, kind: detail.task.state == "BLOCKED" ? .bad : .neutral)
                        Text("\(detail.task.createdAt) -> \(detail.task.updatedAt)")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                    RoutingPanel(detail: detail)
                    VerificationPanel(detail: detail)
                    DashboardCard(title: L10n.history, symbol: "clock") {
                        EventList(events: detail.events)
                    }
                } else {
                    EmptyStateView(title: L10n.taskDetail, symbol: "doc.text",
                                   message: L10n.selectTaskEmptyState)
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
                Text(L10n.unsupportedEmptyState).foregroundStyle(.secondary)
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
            LabeledContent("evidence", value: detail.verification.evidenceId ?? "none")
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
            Text(L10n.unsupportedEmptyState).foregroundStyle(.secondary)
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

    init(_ label: String, _ value: Int, symbol: String) {
        self.label = label
        self.value = value
        self.symbol = symbol
    }

    var body: some View {
        DashboardCard(title: label, symbol: symbol) {
            Text("\(value)")
                .font(.system(size: 28, weight: .semibold, design: .rounded))
        }
    }
}

private struct EmptyStateView: View {
    let title: String
    let symbol: String
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 38))
                .foregroundStyle(.secondary)
            Text(title).font(.headline)
            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(30)
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
