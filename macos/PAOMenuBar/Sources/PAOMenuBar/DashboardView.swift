import SwiftUI
import AppKit
import UserNotifications

import PAOControlKit

struct DashboardView: View {
    @EnvironmentObject private var store: OrchestratorStore
    @SceneStorage("dashboard.selection") private var storedSelection = DashboardSection.overview.rawValue
    @State private var section: DashboardSection?
    /// Search, state grouping and project narrowing for the task workspace.
    /// Owned here so a trip through another destination does not discard it.
    @State private var taskFilter = TaskFilter()
    @State private var showsTaskInspector = false
    @State private var showsNewTaskSheet = false
    /// Search, kind narrowing and selection for the resource workspace, owned
    /// here so a trip through another destination does not discard them.
    @State private var resourceQuery = ""
    @State private var resourceKindFilter: ResourceKind?
    @State private var selectedResourceId: String?
    @State private var settingsTab: SettingsTab = .general
    /// Drives reset countdowns and observation ages in Resources. Without it a
    /// countdown renders once and then sits still while the window it describes
    /// closes, which is the opposite of what a countdown is for.
    @State private var now = Date()

    /// One minute, because every quota figure the clock touches is expressed in
    /// minutes or coarser. A faster tick would redraw the surface for a change
    /// no reading can show.
    private let resourceClock = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    private var activeSection: DashboardSection {
        section ?? DashboardSectionMigration.section(forStoredValue: storedSelection)
    }

    /// The task inspector, which only Tasks has, presented from the navigation
    /// container rather than from inside the destination.
    private var taskInspectorPresentation: Binding<Bool> {
        Binding(
            get: { activeSection == .tasks && showsTaskInspector },
            set: { showsTaskInspector = $0 }
        )
    }

    /// Single entry point for navigation that does not come from the sidebar:
    /// overview risks today, deep links later. Applying the sub-surface context
    /// before the destination means the destination renders already positioned.
    /// Open Tasks narrowed to an Overview counter.
    ///
    /// The tiles count composite states, and `TaskStateSelection` maps each one
    /// to the selection that contains exactly those states, so the list the tile
    /// opens holds exactly what the tile counted.
    private func openTasks(metricsFilter: String) {
        taskFilter = TaskFilter(state: .fromMetricsFilter(metricsFilter))
        section = .tasks
    }

    private func navigate(to intent: NavigationIntent) {
        switch intent.context {
        case .projects: settingsTab = .projects
        case .clientSettings: settingsTab = .general
        // Resources no longer has sub-surfaces to position to: providers,
        // execution targets and quota are one workspace, so both contexts
        // resolve to the destination itself. Selecting a resource on the
        // owner's behalf would require guessing which one the risk meant, and
        // the risk payload carries no provider identity to guess from.
        case .providers, .quota: break
        case .verification, .none: break
        }
        if let taskId = intent.taskId {
            // Navigating to a specific task clears the narrowing, so the task the
            // link names is actually visible in the collection beside it.
            taskFilter = TaskFilter()
            store.selectedTaskId = taskId
            Task { await store.loadTaskDetail(taskId: taskId) }
        }
        section = intent.section
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                // Pinned, not flexible: a resizable column let the split view
                // re-solve the sidebar per destination, which is why it changed
                // width when the owner switched pages.
                .navigationSplitViewColumnWidth(DashboardLayoutMetrics.sidebarWidth)
        } detail: {
            detail(for: activeSection)
                .navigationTitle(activeSection.title)
                .toolbar { toolbarContent }
        }
        // On the navigation split view itself. `.inspector` splits whatever it
        // is applied to across the full window, so applied to the detail — or
        // inside the Tasks page, where it started — it nested a second
        // full-window split inside the navigation split, and the destination
        // was laid out from the window's top-left instead of the pane's. The
        // sidebar and the task list slid up under the title bar as soon as the
        // window was small enough for it to show.
        .modifier(
            TaskInspectorPresentation(
                isPresented: taskInspectorPresentation,
                detail: store.selectedTaskDetail
            )
        )
        // Small enough to admit the smallest window the dashboard is designed
        // for: sidebar + the collection and detail minimums, and no more.
        .frame(
            minWidth: DashboardLayoutMetrics.minimumWindowWidth,
            minHeight: DashboardLayoutMetrics.minimumWindowHeight
        )
        .sheet(isPresented: $showsNewTaskSheet) {
            NewTaskSheet { submittedTaskId in
                showsNewTaskSheet = false
                // A new task is never hidden behind the filter that was in force
                // when it was created.
                taskFilter = TaskFilter()
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
                section = DashboardSectionMigration.section(forStoredValue: storedSelection)
            }
            Task { await store.refreshNow() }
        }
        .onDisappear { store.dashboardVisible = false }
        .onChange(of: section) { newValue in
            storedSelection = (newValue ?? .overview).rawValue
        }
        // A task that finished while the owner was elsewhere. The banner rides
        // above the workspace instead of replacing content, and its action goes
        // through the single navigation entry point so the task arrives selected.
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
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius))
            .padding(Spacing.section)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityElement(children: .contain)
        }
    }

    /// System notification for completions the owner did not watch happen.
    /// Best effort: authorization may be denied, and an active window already
    /// shows the banner, so the notification is only for the background case.
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
        List(selection: Binding(
            get: { activeSection },
            set: { section = $0 ?? .overview }
        )) {
            // Five product destinations, flat. Grouping headers existed to make nine
            // backend-shaped rows legible; five product surfaces do not need them.
            ForEach(DashboardSection.allCases) { candidate in
                sidebarItem(candidate)
            }
        }
        .listStyle(.sidebar)
    }

    private func sidebarItem(_ candidate: DashboardSection) -> some View {
        Label(candidate.title, systemImage: candidate.symbol)
            .tag(candidate)
            // Identified by destination, not by title text: since B5 every
            // page also renders its title in the content header, and a
            // text query can no longer tell a sidebar row from a page header.
            .accessibilityIdentifier("sidebar.\(candidate.rawValue)")
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
                onOpenTasks: { openTasks(metricsFilter: $0) },
                onNavigate: { navigate(to: $0) }
            )
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

/// The task inspector column.
///
/// Attached to the navigation split view's detail, not to the Tasks page
/// itself. `.inspector` re-hosts the subtree it is applied to as a split of the
/// whole window: applied inside the detail it laid the destination out from the
/// window's top edge instead of from below the toolbar, so at small window
/// sizes Tasks — and the sidebar beside it — slid up under the title bar while
/// every other destination stayed put.
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

    private static let selectablePolicies = SelectablePolicyFallback.policies + ["MANUAL"]

    /// M1 WP2: tier floor options. Order matters for the menu — T0 at
    /// the top, T3 at the bottom — so the default ``T1`` selection
    /// lives in the middle of the list.
    private static let selectableTiers = ["T0", "T1", "T2", "T3"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.newTaskTitle)
                .font(.headline)
            Text(L10n.newTaskSubtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Picker(L10n.newTaskProject, selection: $store.selectedProjectId) {
                Text(L10n.newTaskSelectProject).tag(String?.none)
                ForEach(onlineProjects) { project in
                    Text(project.displayName).tag(String?.some(project.projectId))
                }
            }
            .pickerStyle(.menu)

            Picker(L10n.newTaskExecution, selection: $executionTargetId) {
                Text(L10n.newTaskExecutionAuto).tag("")
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

            // M1 WP2: tier floor picker. Defaults to T1 (workhorse) so
            // existing user behaviour is preserved; an owner who wants
            // to pin a flagship-only task picks T0.
            Picker(L10n.newTaskMinTier, selection: $store.selectedMinTier) {
                ForEach(Self.selectableTiers, id: \.self) { tier in
                    Text(L10n.tierLabel(tier)).tag(tier)
                }
            }
            .pickerStyle(.menu)
            Text(L10n.newTaskMinTierHelp)
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

/// Projects, unchanged, rendered inside Settings.
///
/// Only the navigation location moves in B2. Folder picking still produces a
/// security-scoped bookmark, and registration still goes through the
/// resolveProject preview/confirmation step — the capability migration and its
/// acceptance belong to B7.
private struct ProjectsSection: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var selectedURL: URL?
    @State private var bookmarkData: Data?
    let onNewTask: (String) -> Void

    private var projects: [ProjectView] {
        store.projects?.projects ?? []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.section) {
            HStack {
                Text(L10n.sectionProjects)
                    .font(.headline)
                Spacer()
                Button {
                    pickProjectFolder()
                } label: {
                    Label(L10n.projectsAdd, systemImage: "folder.badge.plus")
                }
                .buttonStyle(.borderless)
            }

            if let preview = store.pendingProjectPreview {
                DashboardCard(title: L10n.projectsDetectedRepository, symbol: "checkmark.seal") {
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
                            Label(L10n.projectsRegister, systemImage: "plus.circle")
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            }

            if projects.isEmpty {
                EmptyStateView(
                    title: L10n.sectionProjects,
                    symbol: "folder.badge.gearshape",
                    message: L10n.projectsEmpty
                )
            } else {
                ForEach(projects) { project in
                    ProjectCard(project: project, onNewTask: onNewTask)
                }
            }
        }
        // A form measure, centered like System Settings' content column: a
        // wide window gains symmetric margins, not a dead region on the right.
        .frame(maxWidth: DashboardLayoutMetrics.formMaximumWidth)
        .frame(maxWidth: .infinity)
    }

    private func pickProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = L10n.projectsAdd
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
                    Label(L10n.projectsRevealInFinder, systemImage: "finder")
                }
                .controlSize(.small)

                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: project.canonicalRepoRoot))
                    Task { await store.markProjectOpened(projectId: project.projectId) }
                } label: {
                    Label(L10n.projectsOpen, systemImage: "arrow.up.forward.app")
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
                    Label(L10n.projectsRemove, systemImage: "minus.circle")
                }
                .controlSize(.small)
            }
            ProjectAutoSettingsCard(project: project)
        }
    }
}

/// M1 WP5b §18: per-project supervised-auto policy. Editing one field
/// submits the complete tuple with the other two resolved from the
/// authoritative current view, so no sibling is ever silently reset.
/// Turning supervised auto off asks for confirmation first, because the
/// daemon aborts that project's waiting/planned AUTO lifecycles.
private struct ProjectAutoSettingsCard: View {
    @EnvironmentObject private var store: OrchestratorStore
    let project: ProjectView
    @State private var confirmingDisable = false
    @State private var graceDraft: String = ""

    private var graceValue: Int {
        Int(graceDraft) ?? project.graceSeconds
    }

    private var graceIsValid: Bool {
        (1...86_400).contains(graceValue)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            Divider()
            Text(L10n.projectAutoTitle)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Toggle(L10n.projectAutoAllowSupervisedAuto, isOn: Binding(
                get: { project.supervisedAutoAllowed },
                set: { newValue in
                    if newValue {
                        Task {
                            await store.setProjectAutoSettings(
                                projectId: project.projectId,
                                supervisedAutoAllowed: true
                            )
                        }
                    } else if project.supervisedAutoAllowed {
                        // The daemon aborts this project's AUTO lifecycles on
                        // revoke: confirm before the destructive commit.
                        confirmingDisable = true
                    }
                }
            ))
            Toggle(L10n.projectAutoAllowUnattended, isOn: Binding(
                get: { project.unattendedAllowed },
                set: { newValue in
                    Task {
                        await store.setProjectAutoSettings(
                            projectId: project.projectId,
                            unattendedAllowed: newValue
                        )
                    }
                }
            ))
            .disabled(!project.supervisedAutoAllowed)
            HStack {
                Text(L10n.projectAutoGraceSeconds)
                Spacer()
                TextField(
                    "",
                    text: $graceDraft,
                    prompt: Text(verbatim: "\(project.graceSeconds)")
                )
                .textFieldStyle(.roundedBorder)
                .frame(width: 90)
                .multilineTextAlignment(.trailing)
                .disabled(!project.supervisedAutoAllowed)
                .onSubmit(commitGrace)
                Stepper(
                    "",
                    onIncrement: { setGrace(graceValue + 30) },
                    onDecrement: { setGrace(graceValue - 30) }
                )
                .fixedSize()
                .disabled(!project.supervisedAutoAllowed || !graceIsValid)
                .accessibilityLabel(L10n.projectAutoGraceSeconds)
            }
            if !graceDraft.isEmpty && !graceIsValid {
                // Convenience validation only: the full legal range stays
                // enterable and the daemon's invalid_grace_seconds remains
                // the authority surfaced on refusal.
                Text(L10n.projectAutoGraceInvalid)
                    .font(.caption2)
                    .foregroundStyle(StatusTone.caution.color)
            }
            Text(L10n.projectAutoGraceHelp)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            // Only this project's outcomes render here; another
            // project's invalid_grace_seconds must not leak in.
            if let notice = store.autoControlNotice,
               case .projectAutoSettingsSaved = notice,
               notice.applies(to: .project(project.projectId)) {
                Text(L10n.autoControlNoticeText(notice))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if let notice = store.autoControlNotice,
               case .blocked(_, let code) = notice,
               notice.applies(to: .project(project.projectId)) {
                Text(L10n.autoControlNoticeText(notice))
                    .font(.caption2)
                    .foregroundStyle(code == "invalid_grace_seconds"
                        ? StatusTone.caution.color : StatusTone.neutral.color)
            }
        }
        .confirmationDialog(
            L10n.projectAutoDisableWarning,
            isPresented: $confirmingDisable,
            titleVisibility: .visible
        ) {
            Button(L10n.projectAutoDisableConfirm, role: .destructive) {
                Task {
                    await store.setProjectAutoSettings(
                        projectId: project.projectId,
                        supervisedAutoAllowed: false
                    )
                }
            }
            Button(L10n.cancel, role: .cancel) {}
        }
    }

    private func setGrace(_ value: Int) {
        graceDraft = "\(min(max(value, 1), 86_400))"
        commitGrace()
    }

    private func commitGrace() {
        guard graceIsValid else { return }
        Task {
            await store.setProjectAutoSettings(
                projectId: project.projectId,
                graceSeconds: graceValue
            )
        }
    }
}

private struct ProjectFacts: View {
    let project: ProjectView

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent(L10n.projectsGitRoot, value: project.gitRoot)
            if let workingSubpath = project.workingSubpath {
                LabeledContent(L10n.projectsWorkingSubpath, value: workingSubpath)
            }
            LabeledContent(L10n.projectsBranch, value: project.currentBranch ?? project.defaultBranch)
            LabeledContent("HEAD", value: short(project.lastKnownHead))
            LabeledContent(L10n.projectsRecentTasks, value: "\(project.recentTaskCount)")
            LabeledContent(L10n.projectsLastUsed, value: project.lastOpenedAt ?? L10n.never)
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
    /// Hands the tasks workspace the composite counter the owner clicked, so the
    /// list it opens contains exactly the tasks the tile counted.
    let onOpenTasks: (String) -> Void
    let onNavigate: (NavigationIntent) -> Void

    var body: some View {
        DashboardPageContainer {
            let counts = store.dashboard?.counts
            // Equal-width flexible columns plus a shared tile height: the verification
            // tile aggregates two states and previously wrapped its title, which grew
            // that one card and broke the row's alignment.
            LazyVGrid(
                columns: Array(
                    repeating: GridItem(.flexible(), spacing: DashboardLayoutMetrics.cardSpacing),
                    count: 5
                ),
                spacing: DashboardLayoutMetrics.cardSpacing
            ) {
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

            // Grid cells rather than a card wrapping a card: the group is a
            // heading plus tiles, aligned to the same left and right edges as
            // the KPI row above it.
            if let info = store.dashboard?.basicInfo {
                OverviewGroup(
                    L10n.overviewBasicInfo,
                    symbol: "info.circle",
                    detail: L10n.overviewLastRefresh(Timestamps.friendly(info.lastRefreshSync))
                ) {
                    LazyVGrid(
                        columns: [
                            GridItem(
                                .adaptive(minimum: 170),
                                spacing: DashboardLayoutMetrics.cardSpacing
                            )
                        ],
                        spacing: DashboardLayoutMetrics.cardSpacing
                    ) {
                        BasicInfoCell(L10n.overviewConnection, info.daemonConnection)
                        BasicInfoCell(L10n.overviewProjects, "\(info.registeredProjects)")
                        BasicInfoCell(L10n.overviewProviders, "\(info.discoveredProviders)")
                        BasicInfoCell(L10n.overviewRunnableTargets, "\(info.availableExecutionTargets)")
                        BasicInfoCell(L10n.overviewRunningTasks, "\(info.runningTasks)")
                        BasicInfoCell(L10n.overviewTasksToday, "\(info.tasksToday)")
                        BasicInfoCell(L10n.overviewRoutingToday, "\(info.routingDecisionsToday)")
                        BasicInfoCell(L10n.overviewQuotaWarnings, "\(info.quotaWarningCount)")
                    }
                }
            }

            // Both charts reserve the same plot height, so the two cards in
            // this row end at the same baseline however wide the window is.
            HStack(alignment: .top, spacing: DashboardLayoutMetrics.cardSpacing) {
                DashboardCard(title: L10n.overviewTaskTrend, symbol: "chart.xyaxis.line") {
                    if let trend = store.dashboard?.taskTrend, trend.count >= 2 {
                        TaskTrendChart(buckets: trend)
                    } else {
                        EmptyChartState(L10n.overviewTaskTrendEmpty)
                    }
                }
                DashboardCard(title: L10n.overviewTaskStates, symbol: "chart.pie") {
                    if let slices = store.dashboard?.taskStateDistribution, !slices.isEmpty {
                        StateDistributionChart(slices: slices)
                    } else {
                        EmptyChartState(L10n.overviewTaskStatesEmpty)
                    }
                }
            }
            .fixedSize(horizontal: false, vertical: true)

            OverviewGroup(L10n.overviewRisks, symbol: "exclamationmark.triangle") {
                if let risks = store.dashboard?.risks, !risks.isEmpty {
                    VStack(alignment: .leading, spacing: Spacing.inner) {
                        ForEach(risks) { risk in
                            RiskRow(risk: risk, onNavigate: onNavigate)
                        }
                        DisclosureGroup(L10n.advancedDetails) {
                            ForEach(store.dashboard?.importantBlockers ?? [], id: \.self) { raw in
                                Text(raw)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                        .font(.caption)
                    }
                } else {
                    Text(L10n.noBlockers)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            OverviewGroup(L10n.overviewRecentActivity, symbol: "clock") {
                EventList(events: store.dashboard?.recentEvents ?? [])
            }
        }
    }

    /// Navigation only: opens the tasks list narrowed to what the counter
    /// counted. Never mutates authoritative task state.
    private func navigateToTasks(filter: String) {
        onOpenTasks(filter)
    }

    private var disconnectedText: String {
        if case .disconnected(let reason) = store.connection {
            return L10n.disconnectionReason(reason)
        }
        return ""
    }
}

// MARK: - Activity

/// Activity absorbs History, and is where verification risks land when the risk
/// carries no authoritative task identity to select a task with.
private struct ActivityDashboard: View {
    @EnvironmentObject private var store: OrchestratorStore

    var body: some View {
        DashboardPageContainer {
            // The events themselves, as one day-grouped table across the main
            // pane. No card: a chronological list is not a summary panel, and
            // wrapping it in one is what left Activity as a grey slab. The
            // table uses the pane's full width — the old measure cap was the
            // left-heavy narrow column the visual round removed.
            EventList(events: store.dashboard?.recentEvents ?? [], groupsByDay: true)
        }
    }
}

// MARK: - Settings

/// Which settings surface the Settings destination is showing.
///
/// Projects stopped being a first-level destination in B2 and lives here. B7 does
/// the real Projects-into-Settings migration; this is the navigation entry only.
enum SettingsTab: String, CaseIterable, Identifiable {
    case general
    case projects

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return L10n.settingsTabGeneral
        case .projects: return L10n.sectionProjects
        }
    }
}

private struct SettingsDashboard: View {
    @Binding var tab: SettingsTab
    let onNewTask: (String) -> Void

    var body: some View {
        DashboardPageContainer {
            switch tab {
            case .general:
                ClientSettingsSection()
            case .projects:
                ProjectsSection(onNewTask: onNewTask)
            }
        }
        // The page's own control belongs in the page's toolbar, beside every
        // other page's controls, rather than in a second header band.
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Picker(L10n.settingsPickerTitle, selection: $tab) {
                    ForEach(SettingsTab.allCases) { candidate in
                        Text(candidate.title).tag(candidate)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }
        }
    }
}

/// The client-settings page, still reachable as its own window from the menu bar
/// (`PAOMenuBarApp`'s Settings scene) as well as inside the Settings destination.
struct ClientSettingsDashboard: View {
    var body: some View {
        DashboardPageContainer {
            ClientSettingsSection()
        }
    }
}

private struct ClientSettingsSection: View {
    @AppStorage("pao.launchAtLogin") private var launchAtLogin = false
    @AppStorage("pao.autoStartDaemon") private var autoStartDaemon = true
    @EnvironmentObject private var store: OrchestratorStore
    private let layout = AppSupportLayout.resolve()

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.section) {
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
                    ?? SelectablePolicyFallback.policies
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
            // Moved here from the Routing destination, which no longer exists: the
            // full mode list is global scheduling configuration, not task detail.
            AutomationModeCard()
            ActiveSchedulingPolicyCard()
        }
        // The same centered form measure as Projects: one Settings grid, not
        // two alignments inside one destination.
        .frame(maxWidth: DashboardLayoutMetrics.formMaximumWidth)
        .frame(maxWidth: .infinity)
        .onAppear {
            Task { await store.loadSchedulingSettings() }
        }
    }
}

/// M1 WP5b §17: the automation-mode card. MANUAL and SUPERVISED_AUTO
/// are owner-selectable; ACTIVE is displayed for truth with its gate
/// explanation but is deliberately not offered as an ordinary enable
/// action — production activation remains separately gated.
private struct AutomationModeCard: View {
    @EnvironmentObject private var store: OrchestratorStore

    /// Authoritative mode or `nil` when unknown. Missing settings are
    /// rendered as unknown — never coerced to MANUAL, which would
    /// fabricate truth the client does not own.
    private var currentMode: String? {
        AutomationModePresentation.currentMode(from: store.schedulingSettings)
    }

    var body: some View {
        DashboardCard(title: L10n.autoModeTitle, symbol: "sparkles") {
            if let currentMode {
                Text(L10n.autoModeName(currentMode))
                    .font(.title3.weight(.semibold))
                Text(L10n.autoModeDetail(currentMode))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(L10n.autoModeUnknown)
                    .font(.title3.weight(.semibold))
                Text(L10n.autoModeUnknownDetail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            Text(L10n.autoModeChoices)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(AutomationModeCatalog.selectable, id: \.self) { mode in
                SchedulingModeRow(
                    mode: mode,
                    isActive: mode == currentMode,
                    isSelectable: AutomationModePresentation.canSelectModes(
                        currentMode: currentMode
                    ),
                    onSelect: {
                        Task { await store.setSchedulingMode(mode) }
                    }
                )
                .accessibilityLabel(L10n.autoModeName(mode))
            }
            // ACTIVE: authoritative existence, not an ordinary enable.
            // A future daemon that reports ACTIVE still renders truth
            // here; no activation flow is implemented in WP5b.
            HStack(alignment: .top, spacing: 10) {
                Image(
                    systemName: currentMode == "ACTIVE"
                        ? "largecircle.fill.circle" : "circle.dashed"
                )
                .foregroundStyle(Color.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L10n.autoModeName("ACTIVE"))
                        .font(.callout.weight(currentMode == "ACTIVE" ? .semibold : .regular))
                    Text(L10n.autoModeGatedDetail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if currentMode == "ACTIVE" {
                        Text(L10n.autoModeActiveReported)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                Spacer()
                Image(systemName: "lock")
                    .foregroundStyle(Color.secondary)
                    .accessibilityLabel(L10n.autoModeGatedSymbol)
            }
            Text(L10n.autoModeFooter)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
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
/// Badge semantics, resolved through the shared `StatusTone` vocabulary so a
/// badge cannot drift away from the same status rendered elsewhere.
private enum BadgeKind {
    case good
    case warn
    case bad
    case neutral

    var tone: StatusTone {
        switch self {
        case .good: return .positive
        case .warn: return .caution
        case .bad: return .critical
        case .neutral: return .neutral
        }
    }

    var color: Color { tone.color }
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

    /// Every KPI tile reserves the shared tile height regardless of whether it
    /// carries a secondary line, so five tiles in a row stay geometrically
    /// identical. The height is a layout token, not a local number: the row's
    /// alignment contract is shared with the rest of the dashboard.
    private static let tileHeight = DashboardLayoutMetrics.kpiTileHeight

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
                RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius)
                    .fill(hovering ? Color.accentColor.opacity(0.12) : .clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: DashboardLayoutMetrics.cardCornerRadius)
                    .stroke(hovering ? Color.accentColor.opacity(0.45) : .clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(L10n.metricTileHelp)
        .accessibilityIdentifier("overview.kpiTile")
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
        DashboardCard {
            VStack(alignment: .leading, spacing: 2) {
                Text(value)
                    .font(.title3.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct TaskTrendChart: View {
    let buckets: [TaskTrendBucketView]

    private var maxValue: Int {
        max(1, buckets.map { $0.submitted + $0.completed + $0.blocked }.max() ?? 1)
    }

    /// A bar is a bar, not a panel. Columns flex to share the plot, but stop
    /// growing at a width a bar can still be read as one: four buckets in a
    /// wide card previously rendered as four rectangles the size of cards.
    private static let barMaximumWidth: CGFloat = 36
    private static let labelHeight: CGFloat = 16

    private var plotHeight: CGFloat {
        DashboardLayoutMetrics.chartHeight - Self.labelHeight - Spacing.tight
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: Spacing.inner) {
            ForEach(buckets.suffix(12)) { bucket in
                VStack(spacing: Spacing.tight) {
                    stackedBar(bucket)
                    Text(hour(bucket.bucketStart))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .frame(height: Self.labelHeight)
                }
                // The column shares the plot evenly; the bar inside it stops
                // at a bar's width. Without the cap, four buckets in a wide
                // card rendered as four rectangles the size of cards.
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: DashboardLayoutMetrics.chartHeight, alignment: .bottom)
    }

    private func stackedBar(_ bucket: TaskTrendBucketView) -> some View {
        let submitted = CGFloat(bucket.submitted) / CGFloat(maxValue)
        let completed = CGFloat(bucket.completed) / CGFloat(maxValue)
        let blocked = CGFloat(bucket.blocked) / CGFloat(maxValue)
        // Series colours come from the states they represent, so the trend reads
        // with the same vocabulary as the rest of the dashboard. Bars flex to
        // fill the card: fixed-width columns left two slivers stranded in a wide
        // card.
        return VStack(spacing: 0) {
            Rectangle().fill(StatusStyle.task(state: "BLOCKED").tone.fillColor)
                .frame(height: max(0, blocked * plotHeight))
            Rectangle().fill(StatusStyle.task(state: "COMPLETED").tone.fillColor)
                .frame(height: max(0, completed * plotHeight))
            Rectangle().fill(StatusStyle.task(state: "SUBMITTED").tone.fillColor)
                .frame(height: max(2, submitted * plotHeight))
        }
        .frame(height: plotHeight, alignment: .bottom)
        .frame(maxWidth: Self.barMaximumWidth)
        .clipShape(RoundedRectangle(cornerRadius: 3))
        .help(L10n.taskTrendHelp(submitted: bucket.submitted, completed: bucket.completed, blocked: bucket.blocked))
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
        .frame(minHeight: DashboardLayoutMetrics.chartHeight, alignment: .center)
    }

    /// Chart marks share the task-state vocabulary: the same state must not be
    /// one colour in a chart and another in a badge.
    private func color(for state: String) -> Color {
        StatusStyle.task(state: state).tone.fillColor
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
            .frame(maxWidth: .infinity, minHeight: DashboardLayoutMetrics.chartHeight)
    }
}

private struct RiskRow: View {
    let risk: RiskItemView
    let onNavigate: (NavigationIntent) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                Text(L10n.riskTitle(rawCode: risk.rawCode, count: risk.count, fallback: risk.title))
                    .font(.body.weight(.medium))
                Text(L10n.riskDetail(rawCode: risk.rawCode, fallback: risk.detail))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let destination = risk.destination,
               let intent = RiskDestination.intent(
                   for: destination,
                   // RiskItemView carries no task identity, so a verification risk
                   // resolves to Activity rather than guessing a task to select.
                   taskId: nil
               ) {
                Button {
                    onNavigate(intent)
                } label: {
                    Image(systemName: "arrow.right.circle")
                }
                .buttonStyle(.borderless)
                .help(intent.section.title)
            }
        }
        .padding(.vertical, 4)
    }

    private var presentation: StatusPresentation { StatusStyle.severity(risk.severity) }
    private var color: Color { presentation.color }
    private var symbol: String { presentation.symbol }
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

/// One Overview group: the shared section heading plus its content.
///
/// Overview is four groups, not four cards. The heading carries the rank and
/// the content sits directly beneath it at the page's leading edge, so the KPI
/// row, the basic-information grid and the risk list all start from the same
/// left edge.
private struct OverviewGroup<Content: View>: View {
    let title: String
    let symbol: String
    let detail: String?
    @ViewBuilder var content: Content

    init(
        _ title: String,
        symbol: String,
        detail: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.symbol = symbol
        self.detail = detail
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            DashboardSectionHeader(title, symbol: symbol, detail: detail)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
