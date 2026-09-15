import AppKit
import SwiftUI
import UserNotifications

import PAOControlKit

/// Root window for the Daily Driver.
///
/// The previous file also carried the retired Overview dashboard, the technical
/// New Task form and a second Settings implementation. Those surfaces are now
/// separate product components; this root owns only navigation, window-level
/// actions and completion notifications.
struct DashboardView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @SceneStorage("dashboard.selection") private var storedSelection = DashboardSection.overview.rawValue
    @State private var section: DashboardSection?
    @State private var taskFilter = TaskFilter()
    @State private var showsTaskInspector = false
    @State private var showsNewTaskSheet = false
    @State private var resourceQuery = ""
    @State private var resourceKindFilter: ResourceKind?
    @State private var selectedResourceId: String?
    @State private var settingsTab: SettingsTab = .general
    @State private var now = Date()

    /// Quota reset horizons and observation ages are minute-granularity UI.
    private let resourceClock = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    private var activeSection: DashboardSection {
        section ?? DashboardSectionMigration.section(forStoredValue: storedSelection)
    }

    private var taskInspectorPresentation: Binding<Bool> {
        Binding(
            get: { activeSection == .tasks && showsTaskInspector },
            set: { showsTaskInspector = $0 }
        )
    }

    /// Single navigation entry point for deep links and risk/task actions. Apply
    /// sub-surface context before switching destination so the target renders in
    /// the correct state on its first frame.
    private func navigate(to intent: NavigationIntent) {
        switch intent.context {
        case .projects:
            settingsTab = .projects
        case .clientSettings:
            settingsTab = .general
        case .providers, .quota, .verification, .none:
            break
        }

        if let taskId = intent.taskId {
            taskFilter = TaskFilter()
            store.selectedTaskId = taskId
            Task { await store.loadTaskDetail(taskId: taskId) }
        }
        section = intent.section
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(DashboardLayoutMetrics.sidebarWidth)
        } detail: {
            detail(for: activeSection)
                .navigationTitle(activeSection.title)
                .toolbar { toolbarContent }
        }
        .modifier(
            TaskInspectorPresentation(
                isPresented: taskInspectorPresentation,
                detail: store.selectedTaskDetail
            )
        )
        .frame(
            minWidth: DashboardLayoutMetrics.minimumWindowWidth,
            minHeight: DashboardLayoutMetrics.minimumWindowHeight
        )
        .sheet(isPresented: $showsNewTaskSheet) {
            DailyDriverNewTaskSheet { submittedTaskId in
                showsNewTaskSheet = false
                taskFilter = TaskFilter()
                store.selectedTaskId = submittedTaskId
                section = .tasks
                Task { await store.loadTaskDetail(taskId: submittedTaskId) }
            }
            .environmentObject(store)
        }
        .onAppear {
            store.dashboardVisible = true
            if section == nil {
                section = DashboardSectionMigration.section(forStoredValue: storedSelection)
            }
            Task { await store.refreshNow() }
        }
        .onDisappear { store.dashboardVisible = false }
        .onChange(of: section) { newValue in
            storedSelection = (newValue ?? .overview).rawValue
        }
        .overlay(alignment: .top) { completionBanner }
        .onChange(of: store.taskCompletionNotice) { notice in
            guard let notice else { return }
            deliverCompletionNotification(notice)
        }
    }

    @ViewBuilder
    private var completionBanner: some View {
        if let notice = store.taskCompletionNotice {
            HStack(spacing: Spacing.inner) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(StatusStyle.verification("VERIFIED").color)
                VStack(alignment: .leading, spacing: 1) {
                    Text(L10n.taskCompletionTitle(notice.state))
                        .font(.callout.weight(.semibold))
                    Text(notice.intent)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: Spacing.inner)
                Button(L10n.taskCompletionView) {
                    navigate(to: NavigationIntent(section: .tasks, taskId: notice.taskId))
                    store.clearTaskCompletionNotice()
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                Button {
                    store.clearTaskCompletionNotice()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(L10n.taskCompletionDismiss)
            }
            .padding(Spacing.section)
            .background(
                .regularMaterial,
                in: RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius)
            )
            .padding(Spacing.section)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityElement(children: .contain)
        }
    }

    /// Best-effort background notification. The active app already presents the
    /// in-window completion banner, so it does not emit a duplicate system alert.
    private func deliverCompletionNotification(_ notice: TaskCompletionNotice) {
        guard !NSApp.isActive else { return }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = L10n.taskCompletionTitle(notice.state)
            content.body = notice.intent
            let request = UNNotificationRequest(
                identifier: "pao-task-\(notice.taskId)",
                content: content,
                trigger: nil
            )
            center.add(request)
        }
    }

    private var sidebar: some View {
        List(
            selection: Binding(
                get: { activeSection },
                set: { section = $0 ?? .overview }
            )
        ) {
            ForEach(DashboardSection.allCases) { candidate in
                Label(candidate.title, systemImage: candidate.symbol)
                    .tag(candidate)
                    .accessibilityIdentifier("sidebar.\(candidate.rawValue)")
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
                    ProgressView().controlSize(.small)
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
            DailyDriverHome(onNavigate: { intent in
                // Navigating to the unfiltered task collection should not retain
                // an unrelated narrowing from an earlier visit.
                if intent.section == .tasks && intent.taskId == nil {
                    taskFilter = TaskFilter()
                }
                navigate(to: intent)
            })

        case .tasks:
            TasksWorkspace(
                filter: $taskFilter,
                selectedTaskId: taskSelection,
                showsInspector: $showsTaskInspector,
                onNewTask: { showsNewTaskSheet = true },
                onOpenProviders: {
                    navigate(to: NavigationIntent(section: .resources, context: .providers))
                }
            )

        case .resources:
            ResourcesWorkspace(
                query: $resourceQuery,
                kindFilter: $resourceKindFilter,
                selectedResourceId: $selectedResourceId,
                now: now
            )
            .onReceive(resourceClock) { now = $0 }

        case .activity:
            ActivityDashboard()

        case .settings:
            SettingsDashboard(tab: $settingsTab) { projectId in
                store.selectedProjectId = projectId
                showsNewTaskSheet = true
            }
        }
    }
}

/// Native inspector where the OS supports it; TaskDetailSurface exposes the same
/// metadata as an inline disclosure on macOS 13.
private struct TaskInspectorPresentation: ViewModifier {
    @Binding var isPresented: Bool
    let detail: TaskDetailView?

    func body(content: Content) -> some View {
        if #available(macOS 14.0, *) {
            content.inspector(isPresented: $isPresented) {
                TaskInspectorContent(detail: detail)
                    .inspectorColumnWidth(min: 260, ideal: 300, max: 440)
            }
        } else {
            content
        }
    }
}
