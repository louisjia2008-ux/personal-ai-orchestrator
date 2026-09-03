import SwiftUI

import PAOControlKit

/// The Tasks destination: the operational workspace of the app.
///
/// Shape is app sidebar → task collection → task detail. The app sidebar is the
/// one the root `NavigationSplitView` already owns; this view supplies the other
/// two through an `HSplitView`, which is the native macOS resizable divider and
/// keeps the other four destinations untouched. A second `NavigationSplitView`
/// nested inside a detail column would give the same three panes while
/// duplicating navigation chrome the app already has.
///
/// The optional inspector is the fourth pane and is never required: everything
/// the owner needs to act on is in Task Detail whether it is open or not.
struct TasksWorkspace: View {
    @EnvironmentObject private var store: OrchestratorStore
    @Binding var filter: TaskFilter
    @Binding var selectedTaskId: String?
    @Binding var showsInspector: Bool
    let onNewTask: () -> Void
    let onOpenProviders: () -> Void
    @FocusState private var searchFocused: Bool

    private var allTasks: [TaskView] { store.tasks?.tasks ?? [] }

    private var projectNames: [String: String] {
        Dictionary(
            (store.projects?.projects ?? []).map { ($0.projectId, $0.displayName) },
            uniquingKeysWith: { first, _ in first }
        )
    }

    private var collection: TaskCollectionState {
        TaskCollection.resolve(
            tasks: store.tasks,
            connection: store.connection,
            filter: filter,
            projectNames: projectNames
        )
    }

    private var selectedProjectName: String? {
        store.selectedTaskDetail?.task.projectId.flatMap { projectNames[$0] }
    }

    /// `.inspector` arrived in macOS 14; the deployment target is macOS 13.
    private var inspectorAvailable: Bool {
        if #available(macOS 14.0, *) { return true }
        return false
    }

    var body: some View {
        HSplitView {
            TaskCollectionView(
                state: collection,
                allTasks: allTasks,
                projectNames: projectNames,
                totalCount: store.tasks?.total ?? allTasks.count,
                isTruncated: store.taskCollectionIsTruncated,
                filter: $filter,
                selectedTaskId: $selectedTaskId,
                onNewTask: onNewTask
            )
            .frame(minWidth: 260, idealWidth: 320, maxWidth: 460)

            TaskDetailSurface(
                detail: store.selectedTaskDetail,
                selectedTaskId: selectedTaskId,
                projectName: selectedProjectName,
                inspectorAvailable: inspectorAvailable,
                onNewTask: onNewTask,
                onOpenProviders: onOpenProviders
            )
            .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .searchable(text: $filter.query, prompt: L10n.tasksSearchPrompt)
        .modifier(SearchFocusBinding(focus: $searchFocused))
        .toolbar { toolbarContent }
        .modifier(
            TaskInspectorPresentation(
                isPresented: $showsInspector,
                detail: store.selectedTaskDetail
            )
        )
        .background(hiddenCommands)
        .onChange(of: selectedTaskId) { newValue in
            guard let newValue else { return }
            Task { await store.loadTaskDetail(taskId: newValue) }
        }
        .onAppear {
            if let selectedTaskId {
                Task { await store.loadTaskDetail(taskId: selectedTaskId) }
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        if inspectorAvailable {
            ToolbarItem {
                Button {
                    showsInspector.toggle()
                } label: {
                    Image(systemName: "sidebar.trailing")
                }
                .help(L10n.inspectorToggle)
                .accessibilityLabel(L10n.inspectorToggle)
            }
        }
    }

    /// Keyboard-first navigation. SwiftUI on macOS only honors
    /// `.keyboardShortcut` on real views, so these stay present and hidden.
    private var hiddenCommands: some View {
        let scope = collection.tasks
        return Group {
            Button(L10n.commandFocusSearch) { searchFocused = true }
                .keyboardShortcut("f", modifiers: .command)
                .hidden()
            Button(L10n.commandPreviousTask) {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: scope, offset: -1)
            }
            .keyboardShortcut("[", modifiers: .command)
            .disabled(scope.isEmpty)
            .hidden()
            Button(L10n.commandNextTask) {
                _ = store.navigateToNeighbour(current: selectedTaskId, in: scope, offset: 1)
            }
            .keyboardShortcut("]", modifiers: .command)
            .disabled(scope.isEmpty)
            .hidden()
            Button(L10n.commandCopyTaskId) { selectedTaskId.map(Pasteboard.copy) }
                .keyboardShortcut("c", modifiers: [.command, .option])
                .disabled(selectedTaskId == nil)
                .hidden()
            Button(L10n.commandReloadDetail) {
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

/// Routes Command-F to the toolbar search field.
///
/// `searchFocused` arrived in macOS 15. On earlier systems the field is still
/// there and still works; only the shortcut into it is unavailable, which is a
/// smaller loss than dropping `.searchable` for a hand-built text field.
private struct SearchFocusBinding: ViewModifier {
    var focus: FocusState<Bool>.Binding

    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.searchFocused(focus)
        } else {
            content
        }
    }
}

/// Presents the task inspector natively where the platform has one.
///
/// macOS 13 has no `.inspector`, and refusing to build for it is not an option:
/// the same content is reachable there as a disclosure at the end of Task
/// Detail, which `TaskDetailSurface` renders when the inspector is unavailable.
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
