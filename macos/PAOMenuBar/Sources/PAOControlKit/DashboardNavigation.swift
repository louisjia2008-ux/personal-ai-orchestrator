import Foundation

/// Dashboard navigation model. Lives in PAOControlKit so navigation semantics
/// (destination identity, stored-value migration, risk deep links, metric-to-task
/// filtering) are unit-testable without the app executable.
///
/// The five destinations are the frozen product information architecture. They are
/// product surfaces, not backend concepts: the former Projects / Providers / Quota /
/// Routing / Verification / History destinations still exist as capabilities, but
/// they are reached *inside* one of these five.
public enum DashboardSection: String, CaseIterable, Identifiable, Sendable {
    case overview
    case tasks
    case resources
    case activity
    case settings

    public var id: String { rawValue }

    public var title: String { L10n.dashboardSection(rawValue) }

    public var symbol: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.67percent"
        case .tasks: return "checklist"
        case .resources: return "square.stack.3d.up"
        case .activity: return "clock.arrow.circlepath"
        case .settings: return "gearshape"
        }
    }
}

/// Migration for navigation selections persisted before the five-destination IA.
///
/// A build shipped before this change stored raw values such as `"providers"` or
/// `"routing"` in SceneStorage. Letting `DashboardSection(rawValue:)` simply fail
/// and fall back to Overview would silently discard where the owner was — and would
/// do so differently for values that have an obvious new home. The mapping below is
/// therefore explicit and total: every value the old enum could store resolves to the
/// destination that now owns that capability.
public enum DashboardSectionMigration {
    /// Every raw value the pre-B2 `DashboardSection` could persist, including the
    /// legacy `agents` case that was excluded from `allCases` but still storable.
    public static let legacyStoredValues: [String] = [
        "overview", "projects", "tasks", "agents", "providers",
        "quota", "routing", "verification", "history", "settings",
    ]

    /// Resolve a persisted navigation value to a canonical destination.
    ///
    /// Canonical values pass through unchanged. Legacy values map to the destination
    /// that absorbed them. Anything unrecognized — including an empty string — is
    /// Overview, which is always safe because it never depends on prior selection.
    public static func section(forStoredValue stored: String) -> DashboardSection {
        if let canonical = DashboardSection(rawValue: stored) {
            return canonical
        }
        switch stored {
        case "projects": return .settings
        case "agents", "providers", "quota": return .resources
        case "routing": return .tasks
        case "verification", "history": return .activity
        default: return .overview
        }
    }
}

/// Sub-surface a destination should open on when navigation is driven by something
/// other than the sidebar. Contexts are deliberately coarse: they name a surface the
/// destination already owns, never a filter or selection the data model cannot back.
public enum NavigationContext: String, Equatable, Sendable {
    case projects
    case clientSettings
    case providers
    case quota
    case verification
}

/// A resolved navigation request: which destination, which sub-surface, and — only
/// when the source supplied authoritative identity — which task.
public struct NavigationIntent: Equatable, Sendable {
    public let section: DashboardSection
    public let context: NavigationContext?
    public let taskId: String?

    public init(
        section: DashboardSection,
        context: NavigationContext? = nil,
        taskId: String? = nil
    ) {
        self.section = section
        self.context = context
        self.taskId = taskId
    }
}

/// Translation from the daemon's semantic risk destinations to navigation intents.
///
/// The daemon emits product-language destinations (`projects`, `models_providers`,
/// `quota`, `settings`, `verification`) that describe *what is wrong*, not where the
/// macOS sidebar happens to put it. Binding them straight to `DashboardSection` would
/// make every future IA change a daemon-compatibility problem, so the coupling runs
/// through this layer instead.
public enum RiskDestination {
    /// `taskId` must come from authoritative task identity in the risk payload.
    /// Today `RiskItemView` carries none, so verification risks always resolve to
    /// Activity; the task-specific branch exists for when the payload gains identity.
    public static func intent(for destination: String, taskId: String? = nil) -> NavigationIntent? {
        switch destination {
        case "projects":
            return NavigationIntent(section: .settings, context: .projects)
        case "models_providers":
            return NavigationIntent(section: .resources, context: .providers)
        case "quota":
            return NavigationIntent(section: .resources, context: .quota)
        case "settings":
            return NavigationIntent(section: .settings, context: .clientSettings)
        case "verification":
            guard let taskId, !taskId.isEmpty else {
                // No authoritative task identity: never guess which task the risk
                // meant. Activity is the non-task-specific verification surface.
                return NavigationIntent(section: .activity)
            }
            return NavigationIntent(section: .tasks, context: .verification, taskId: taskId)
        default:
            return nil
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
