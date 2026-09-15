import SwiftUI

import PAOControlKit

/// The task collection: what exists, what state it is in, and which project it
/// belongs to.
///
/// The collection is the authoritative one from the list endpoint, never the
/// dashboard's ten-item preview, and when the daemon could not serve all of it
/// the column says so rather than presenting a subset as the whole.
struct TaskCollectionView: View {
    let state: TaskCollectionState
    let allTasks: [TaskView]
    let projectNames: [String: String]
    let totalCount: Int
    let isTruncated: Bool
    @Binding var filter: TaskFilter
    @Binding var selectedTaskId: String?
    let onNewTask: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if isTruncated {
                Divider()
                truncationNotice
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor))
        .accessibilityIdentifier("workspace.collection")
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: Spacing.inner) {
            stateMenu
            projectMenu
            Spacer(minLength: 0)
            if state.isPopulated {
                let counted = L10n.tasksVisibleCount(
                    shown: state.tasks.count, total: allTasks.count
                )
                Text(counted)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
                    .accessibilityLabel(counted)
            }
        }
        .padding(.horizontal, Spacing.element)
        .padding(.vertical, Spacing.inner)
    }

    /// Groups first, then every state actually present in the collection.
    /// Exact machine states remain available in the filter even though rows use
    /// owner-readable labels.
    private var stateMenu: some View {
        Menu {
            Button(L10n.tasksFilterAllTasks) { filter.state = .all }
            Section(L10n.tasksFilterGroups) {
                ForEach(TaskStateGroup.allCases) { group in
                    Button {
                        filter.state = .group(group)
                    } label: {
                        Label(group.title, systemImage: group.symbol)
                    }
                }
            }
            let states = TaskCollection.presentStates(in: allTasks)
            if !states.isEmpty {
                Section(L10n.tasksFilterStates) {
                    ForEach(states, id: \.self) { state in
                        Button(state) { filter.state = .state(state) }
                    }
                }
            }
        } label: {
            Label(stateMenuTitle, systemImage: "line.3.horizontal.decrease.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help(L10n.stateFilter)
    }

    private var stateMenuTitle: String {
        switch filter.state {
        case .all: return L10n.tasksFilterAllTasks
        case .group(let group): return group.title
        case .state(let state): return state
        }
    }

    /// Project identity did not survive Projects leaving the sidebar by
    /// accident; it is carried here and on every row.
    private var projectMenu: some View {
        let projects = TaskCollection.presentProjects(in: allTasks, projectNames: projectNames)
        return Menu {
            Button(L10n.tasksFilterAllProjects) { filter.projectId = nil }
            if !projects.isEmpty {
                Section(L10n.tasksFilterProject) {
                    ForEach(projects, id: \.id) { project in
                        Button(project.name) { filter.projectId = project.id }
                    }
                }
            }
        } label: {
            Label(projectMenuTitle, systemImage: "folder")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help(L10n.tasksFilterProject)
        .disabled(projects.isEmpty)
    }

    private var projectMenuTitle: String {
        guard let projectId = filter.projectId else { return L10n.tasksFilterAllProjects }
        return projectNames[projectId] ?? projectId
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            VStack(spacing: Spacing.inner) {
                ProgressView()
                    .controlSize(.small)
                Text(L10n.emptyTasksLoading)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .disconnected(let reason):
            EmptyStateView(
                title: L10n.disconnectedLabel,
                symbol: "bolt.slash",
                message: L10n.disconnectionReason(reason),
                hint: L10n.tasksDisconnectedHint
            )

        case .empty:
            EmptyStateView(
                title: L10n.emptyTasksNoneTitle,
                symbol: "tray",
                message: L10n.emptyTasksNoneMessage,
                actionTitle: L10n.newTask,
                action: onNewTask
            )

        case .noSearchMatch:
            EmptyStateView(
                title: L10n.emptyTasksSearchTitle,
                symbol: "magnifyingglass",
                message: L10n.emptyTasksSearchMessage(filter.trimmedQuery),
                hint: L10n.emptyTasksSearchHint
            )

        case .noFilterMatch:
            EmptyStateView(
                title: L10n.emptyTasksFilterTitle,
                symbol: "line.3.horizontal.decrease.circle",
                message: L10n.emptyTasksFilterMessage,
                actionTitle: L10n.tasksFilterClear,
                action: {
                    filter.state = .all
                    filter.projectId = nil
                }
            )

        case .populated(let tasks):
            List(tasks, selection: $selectedTaskId) { task in
                TaskCollectionRow(
                    task: task,
                    projectName: task.projectId.flatMap { projectNames[$0] }
                )
            }
            .listStyle(.inset(alternatesRowBackgrounds: false))
        }
    }

    private var truncationNotice: some View {
        Label {
            Text(L10n.tasksTruncated(shown: allTasks.count, total: totalCount))
        } icon: {
            Image(systemName: "info.circle")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, Spacing.element)
        .padding(.vertical, Spacing.inner)
        .frame(maxWidth: .infinity, alignment: .leading)
        .help(L10n.tasksTruncatedHelp(limit: OrchestratorStore.taskListLimit))
    }
}

/// Compact owner-facing row. Raw machine state remains available from Task
/// Detail/Inspector and the exact-state filter; the collection prioritizes the
/// question "what needs my attention?" over backend vocabulary.
struct TaskCollectionRow: View {
    let task: TaskView
    let projectName: String?
    var now: Date = Date()

    private var status: StatusPresentation { StatusStyle.task(state: task.state) }
    private var isLive: Bool { !TaskTiming.isTerminal(state: task.state) }

    private var timingText: String {
        if isLive, let elapsed = TaskTiming.elapsed(task: task, now: now) {
            return Timestamps.duration(elapsed)
        }
        return Timestamps.friendly(task.updatedAt, now: now)
    }

    private var displayState: String {
        switch task.state {
        case "AUTO_PLANNED": return DailyDriverL10n.taskAwaitingApproval
        case "AUTO_GRACE": return DailyDriverL10n.taskGraceWindow
        case "SUBMITTED": return DailyDriverL10n.taskReadyToRoute
        case "READY": return DailyDriverL10n.taskReady
        case "RUNNING": return DailyDriverL10n.taskRunning
        case "VERIFYING": return DailyDriverL10n.taskVerifying
        case "VERIFIED": return DailyDriverL10n.taskVerified
        case "COMPLETED": return DailyDriverL10n.taskCompleted
        case "BLOCKED": return DailyDriverL10n.taskBlocked
        case "FAILED": return DailyDriverL10n.taskFailed
        case "CANCELLED": return DailyDriverL10n.taskCancelled
        default: return task.state.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.inner) {
            Image(systemName: status.symbol)
                .foregroundStyle(status.color)
                .frame(width: 16)
                .padding(.top, 3)

            VStack(alignment: .leading, spacing: 4) {
                Text(task.intent)
                    .font(.body)
                    .lineLimit(2)

                HStack(spacing: Spacing.tight) {
                    Text(displayState)
                        .fontWeight(task.state == "BLOCKED" || task.state == "FAILED" ? .semibold : .regular)
                    Text("·")
                    Text(timingText)
                        .monospacedDigit()
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                Text(projectName ?? task.projectId ?? L10n.tasksNoProject)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(task.intent), \(displayState), \(projectName ?? L10n.tasksNoProject)"
        )
        .help(task.state)
    }
}
