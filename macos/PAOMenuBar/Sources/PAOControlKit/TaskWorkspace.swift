import Foundation

/// Search, grouping and filtering semantics for the task workspace.
///
/// Lives in PAOControlKit rather than in the view because "which tasks is the
/// owner looking at" is a question about the collection, not about layout, and
/// it has to be testable without the app executable. The view binds a
/// `TaskFilter` and renders whatever `TaskCollection.resolve` returns; it never
/// decides membership itself.

// MARK: - Authoritative states

/// The task states the Safety Kernel can actually record.
///
/// Mirrors `personal_ai_orchestrator.safety_kernel.TaskState`. It exists so the
/// client can tell "a state we know about" from "a state a newer daemon
/// introduced" — the second must stay visible as itself rather than being
/// quietly folded into a group it was never assigned to.
public enum TaskStates {
    public static let all: [String] = [
        "SUBMITTED", "READY", "RUNNING", "WORKER_FINISHED", "VERIFYING",
        "VERIFIED", "BLOCKED", "FAILED", "CANCELLED", "COMPLETED",
    ]

    public static func isKnown(_ state: String) -> Bool {
        all.contains(state)
    }

    /// States from which the Safety Kernel permits a transition to CANCELLED.
    ///
    /// Mirrors `_ALLOWED_TRANSITIONS`. The UI offers Stop only for these, so the
    /// button is absent rather than present-and-rejected on a task the daemon
    /// would refuse to cancel. RUNNING is included because the daemon accepts the
    /// request and answers with its own conflict semantics; that answer is
    /// surfaced verbatim rather than pre-empted here.
    public static let cancellable: Set<String> = [
        "SUBMITTED", "READY", "RUNNING", "WORKER_FINISHED", "VERIFYING", "BLOCKED",
    ]

    public static func isCancellable(_ state: String) -> Bool {
        cancellable.contains(state)
    }
}

// MARK: - Product groupings

/// A product grouping over authoritative task states.
///
/// The groups answer the owner's questions — what is queued, what is running,
/// what needs me — without inventing a state the daemon does not have. Every
/// case lists exactly the machine values it contains; nothing is derived by
/// exclusion, so a state added by a future daemon lands in no group at all
/// rather than being absorbed into the nearest-looking one.
///
/// Two deliberate departures from the obvious four-label sketch:
///
/// - `needsAttention` rather than "Blocked", because BLOCKED and FAILED both
///   stop the work and both need the owner, but only one of them is BLOCKED.
///   Labelling the pair "Blocked" would misname half its contents.
/// - `cancelled` is its own group rather than part of "Completed". A cancelled
///   task did not complete; filing it under completion would report a stop as a
///   success. It is small, but it keeps the groups total: every authoritative
///   state is reachable from exactly one group.
public enum TaskStateGroup: String, CaseIterable, Identifiable, Sendable {
    /// Accepted, not yet running.
    case queued
    /// Work is in flight, including the verification the daemon runs for it.
    case active
    /// Stopped and waiting on the owner.
    case needsAttention
    /// Finished successfully.
    case completed
    /// Deliberately stopped before finishing.
    case cancelled

    public var id: String { rawValue }

    public var states: Set<String> {
        switch self {
        case .queued: return ["SUBMITTED", "READY"]
        case .active: return ["RUNNING", "WORKER_FINISHED", "VERIFYING"]
        case .needsAttention: return ["BLOCKED", "FAILED"]
        case .completed: return ["VERIFIED", "COMPLETED"]
        case .cancelled: return ["CANCELLED"]
        }
    }

    public func contains(_ state: String) -> Bool {
        states.contains(state)
    }

    public var title: String { L10n.taskStateGroup(rawValue) }

    /// Symbol for the group, so a filter menu is readable without colour.
    public var symbol: String {
        switch self {
        case .queued: return "tray"
        case .active: return "gearshape.2"
        case .needsAttention: return "exclamationmark.triangle"
        case .completed: return "checkmark.circle"
        case .cancelled: return "slash.circle"
        }
    }

    /// The group a state belongs to, or nil when this build has never seen the
    /// state. Nil is a real answer: it means "not classifiable", not "other".
    public static func group(for state: String) -> TaskStateGroup? {
        allCases.first { $0.contains(state) }
    }
}

// MARK: - Selection

/// What the state filter is currently narrowed to.
///
/// Groups and exact states are both first-class. The exact-state case is what
/// keeps the filter total: a state that belongs to no group — because a newer
/// daemon introduced it — is still selectable by its own machine value, so the
/// owner can never be left with tasks that no filter can reach.
public enum TaskStateSelection: Equatable, Hashable, Sendable {
    case all
    case group(TaskStateGroup)
    case state(String)

    public func matches(_ state: String) -> Bool {
        switch self {
        case .all: return true
        case .group(let group): return group.contains(state)
        case .state(let exact): return state == exact
        }
    }

    /// Stable representation for SceneStorage. Prefixed because a group name and
    /// a machine state must never be confused for one another.
    public var storageValue: String {
        switch self {
        case .all: return ""
        case .group(let group): return "group:\(group.rawValue)"
        case .state(let state): return "state:\(state)"
        }
    }

    public init(storageValue: String) {
        if storageValue.hasPrefix("group:"),
            let group = TaskStateGroup(rawValue: String(storageValue.dropFirst(6)))
        {
            self = .group(group)
            return
        }
        if storageValue.hasPrefix("state:") {
            let state = String(storageValue.dropFirst(6))
            self = state.isEmpty ? .all : .state(state)
            return
        }
        self = .all
    }

    /// Translate an Overview counter into the equivalent workspace selection.
    ///
    /// The Overview tiles count composite states (`READY` counts SUBMITTED too,
    /// `VERIFIED` counts COMPLETED too — see `MetricsFilter`). Those two
    /// composites are exactly the `queued` and `completed` groups, so the tile
    /// and the list agree by construction rather than by coincidence; the
    /// single-state tiles map to their own state.
    public static func fromMetricsFilter(_ filter: String) -> TaskStateSelection {
        switch filter {
        case "": return .all
        case "READY": return .group(.queued)
        case "VERIFIED": return .group(.completed)
        default: return .state(filter)
        }
    }
}

// MARK: - Filter

/// Everything narrowing the visible task collection.
///
/// Search runs over the collection already loaded from the daemon. There is no
/// server-side task search in the control API, and pretending otherwise would
/// make a local subset look like a global result.
public struct TaskFilter: Equatable, Sendable {
    public var query: String
    public var state: TaskStateSelection
    /// nil means every project. An explicit id narrows to that project only.
    public var projectId: String?

    public init(
        query: String = "",
        state: TaskStateSelection = .all,
        projectId: String? = nil
    ) {
        self.query = query
        self.state = state
        self.projectId = projectId
    }

    public var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var hasQuery: Bool { !trimmedQuery.isEmpty }

    /// True when anything other than search is narrowing the collection.
    public var hasNarrowing: Bool {
        state != .all || projectId != nil
    }

    /// Whether a task survives the state and project filters, ignoring search.
    public func matchesNarrowing(_ task: TaskView) -> Bool {
        guard state.matches(task.state) else { return false }
        if let projectId, task.projectId != projectId { return false }
        return true
    }

    /// Whether a task matches the search text.
    ///
    /// `projectName` is the resolved display name for the task's project, when
    /// the client holds one: the owner searches for "PinTrace", not for
    /// `proj-pintrace`. Provider and worker identity are deliberately absent —
    /// `TaskView` carries none, and only the selected task's detail knows which
    /// worker ran it, so matching on it would search a field most rows lack.
    public func matchesQuery(_ task: TaskView, projectName: String?) -> Bool {
        let needle = trimmedQuery
        guard !needle.isEmpty else { return true }
        let haystacks = [
            task.intent,
            task.taskId,
            task.requestId,
            task.projectId,
            projectName,
            task.state,
        ]
        return haystacks.contains { field in
            guard let field, !field.isEmpty else { return false }
            return field.localizedCaseInsensitiveContains(needle)
        }
    }

    public func matches(_ task: TaskView, projectName: String? = nil) -> Bool {
        matchesNarrowing(task) && matchesQuery(task, projectName: projectName)
    }

    public func apply(
        to tasks: [TaskView], projectNames: [String: String] = [:]
    ) -> [TaskView] {
        tasks.filter { matches($0, projectName: $0.projectId.flatMap { projectNames[$0] }) }
    }
}

// MARK: - Resolved collection

/// What the task collection column should render.
///
/// Loading, "no tasks at all", "your search matched nothing" and "your filter
/// matched nothing" are four different facts and lead to four different next
/// actions, so they are four cases rather than one empty list.
public enum TaskCollectionState: Equatable, Sendable {
    /// Connected, but the first collection has not arrived yet.
    case loading
    /// Not connected. The collection is unknown, not empty.
    case disconnected(reason: ConnectionState.DisconnectionReason)
    /// The daemon holds no tasks.
    case empty
    /// Tasks exist; the search text matched none of them.
    case noSearchMatch
    /// Tasks exist; the state/project filter matched none of them.
    case noFilterMatch
    case populated([TaskView])

    public var tasks: [TaskView] {
        if case .populated(let tasks) = self { return tasks }
        return []
    }

    public var isPopulated: Bool {
        if case .populated = self { return true }
        return false
    }
}

/// Pure resolution of store state into what the collection column shows.
public enum TaskCollection {

    public static func resolve(
        tasks: TaskListView?,
        connection: ConnectionState,
        filter: TaskFilter,
        projectNames: [String: String] = [:]
    ) -> TaskCollectionState {
        guard connection.isConnected else {
            if case .disconnected(let reason) = connection {
                return .disconnected(reason: reason)
            }
            return .disconnected(reason: .daemonNotRunning)
        }
        guard let tasks else { return .loading }
        guard !tasks.tasks.isEmpty else { return .empty }

        let visible = filter.apply(to: tasks.tasks, projectNames: projectNames)
        if !visible.isEmpty { return .populated(visible) }

        // Which narrowing emptied the list decides what the owner is told. A
        // filter that hides everything is fixed by clearing the filter; a search
        // that finds nothing is fixed by changing the words.
        if filter.hasQuery {
            let withoutQuery = tasks.tasks.filter(filter.matchesNarrowing)
            if !withoutQuery.isEmpty { return .noSearchMatch }
        }
        if filter.hasNarrowing { return .noFilterMatch }
        return .noSearchMatch
    }

    /// Distinct states present in a collection, in canonical order, with any
    /// state this build does not recognize appended verbatim.
    ///
    /// Drives the exact-state section of the filter menu, which is what makes an
    /// unrecognized state reachable at all.
    public static func presentStates(in tasks: [TaskView]) -> [String] {
        let present = Set(tasks.map(\.state))
        let known = TaskStates.all.filter(present.contains)
        let unknown = present.subtracting(TaskStates.all).sorted()
        return known + unknown
    }

    /// Projects referenced by the collection, resolved to display names where
    /// the client holds one. Tasks with no project are not invented into one.
    public static func presentProjects(
        in tasks: [TaskView], projectNames: [String: String]
    ) -> [(id: String, name: String)] {
        let ids = Set(tasks.compactMap(\.projectId))
        return ids
            .map { (id: $0, name: projectNames[$0] ?? $0) }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
}
