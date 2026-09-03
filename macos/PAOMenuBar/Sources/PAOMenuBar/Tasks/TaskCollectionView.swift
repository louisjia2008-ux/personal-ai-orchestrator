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
            if isTruncated {
                Divider()
                truncationNotice
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: Spacing.inner) {
            stateMenu
            projectMenu
            Spacer(minLength: 0)
            if state.isPopulated {
                // How much of the *loaded* collection the filters are showing.
                // The store's total belongs to the truncation line below, where
                // it is paired with the sentence that explains the shortfall;
                // pairing it here would suggest the filters searched all of it.
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
    ///
    /// The exact-state section is what keeps the filter total: a state this
    /// build does not classify still appears there under its own machine value,
    /// so no task can end up unreachable by any filter.
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
        // Machine values are shown verbatim, as everywhere else in the client.
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
            // Loading is not empty. An empty state here would claim the store
            // holds nothing before anything has been asked.
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
            // No "New task" here: the owner is looking for something that exists
            // or does not, and creating a task answers neither question.
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
            // `List(_:selection:)` tags rows by task id, which is the
            // authoritative identity the rest of the app selects by.
            List(tasks, selection: $selectedTaskId) { task in
                TaskCollectionRow(
                    task: task,
                    projectName: task.projectId.flatMap { projectNames[$0] }
                )
            }
            .listStyle(.inset(alternatesRowBackgrounds: false))
        }
    }

    /// Low-noise, and honest about both numbers: what arrived and what exists.
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

/// One row: what it is, what state it is in, whose project it belongs to, and
/// how long it has been at it.
struct TaskCollectionRow: View {
    let task: TaskView
    let projectName: String?
    /// Live rows measure elapsed time to now; the workspace ticks this so a
    /// running task's reading advances instead of freezing at its last state
    /// change. See `TaskTiming`.
    var now: Date = Date()

    private var status: StatusPresentation { StatusStyle.task(state: task.state) }

    private var isLive: Bool { !TaskTiming.isTerminal(state: task.state) }

    private var timingText: String {
        if isLive, let elapsed = TaskTiming.elapsed(task: task, now: now) {
            return Timestamps.duration(elapsed)
        }
        return Timestamps.friendly(task.updatedAt, now: now)
    }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.inner) {
            Image(systemName: status.symbol)
                .foregroundStyle(status.color)
                .frame(width: 16)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(task.intent)
                    .font(.body)
                    .lineLimit(2)
                HStack(spacing: Spacing.tight) {
                    Text(projectName ?? task.projectId ?? L10n.tasksNoProject)
                        .lineLimit(1)
                    Text("·")
                    Text(task.state)
                        .monospaced()
                    Text("·")
                    Text(timingText)
                        .monospacedDigit()
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(task.intent), \(task.state), \(projectName ?? L10n.tasksNoProject)"
        )
    }
}
