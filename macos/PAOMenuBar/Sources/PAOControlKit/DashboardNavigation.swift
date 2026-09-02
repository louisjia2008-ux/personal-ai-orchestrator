import Foundation

/// Dashboard navigation model. Lives in PAOControlKit so navigation semantics
/// (section identity, metric-to-task filtering) are unit-testable without the
/// app executable.
public enum DashboardSection: String, CaseIterable, Identifiable, Sendable {
    case overview
    case projects
    case tasks
    case agents
    case providers
    case quota
    case routing
    case verification
    case history
    case settings

    public static var allCases: [DashboardSection] {
        [
            .overview, .projects, .tasks,
            .providers, .quota,
            .routing, .verification,
            .history, .settings,
        ]
    }

    public var id: String { rawValue }

    public var title: String { L10n.dashboardSection(rawValue) }

    public var symbol: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.67percent"
        case .projects: return "folder.badge.gearshape"
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

/// Pure task-state filter semantics shared by the overview metric tiles and the
/// tasks list. A filter value matches a task state; composite dashboard counters
/// map onto their constituent task states (READY includes SUBMITTED; VERIFIED
/// includes COMPLETED) exactly like OrchestratorStore.taskCounts().
public enum MetricsFilter {
    public static func matches(state: String, filter: String) -> Bool {
        guard !filter.isEmpty else { return true }
        if state == filter { return true }
        if filter == "READY" && state == "SUBMITTED" { return true }
        if filter == "VERIFIED" && state == "COMPLETED" { return true }
        return false
    }
}
