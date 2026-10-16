import SwiftUI
import PAOControlKit

/// Prioritizes decisions without claiming that a bounded recent-task sample is
/// the full queue. Global task counters remain below and still open all tasks.
struct OverviewActionQueue: View {
    @EnvironmentObject private var store: OrchestratorStore
    let onNavigate: (NavigationIntent) -> Void

    let onOpenAllTasks: () -> Void

    private var recentTasks: [TaskView] { Array((store.dashboard?.recentTasks ?? []).prefix(10)) }
    private var missingObservations: Bool {
        recentTasks.contains { task in
            guard let observed = store.recentSafeDetails[task.taskId] else { return true }
            return !observed.recoveryStatusAvailable || observed.task.stateVersion != task.stateVersion
                || observed.task.state != task.state
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            HStack {
                Text(L10n.controlText("overview.attention")).font(.title3.weight(.semibold))
                Spacer()
                Button(L10n.controlText("overview.allTasks")) {
                    onOpenAllTasks()
                }
            }
            Text(L10n.controlText("overview.sample"))
                .font(.caption).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: Spacing.element)],
                      alignment: .leading, spacing: Spacing.element) {
                ForEach(TaskActionKind.allCases) { kind in
                    actionGroup(kind)
                }
            }
            if missingObservations {
                Label(L10n.controlText("overview.partial"), systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let observed = store.recentActionsObservedAt {
                Text(L10n.controlText("overview.observed") + " " + observed.formatted(date: .omitted, time: .standard))
                    .font(.caption).foregroundStyle(.tertiary)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("overview.actionQueue")
    }

    private func actionGroup(_ kind: TaskActionKind) -> some View {
        let tasks = recentTasks.filter {
            TaskActionKind.classify($0, detail: store.recentSafeDetails[$0.taskId]) == kind
        }
        return DashboardCard(title: L10n.controlText("overview.group.\(kind.rawValue)"), symbol: symbol(kind)) {
            if tasks.isEmpty {
                Text(L10n.controlText(missingObservations && (kind == .approval || kind == .recovery)
                                      ? "overview.unavailable" : "overview.noneInSample"))
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                ForEach(tasks.prefix(3)) { task in
                    Button {
                        onNavigate(NavigationIntent(section: .tasks, taskId: task.taskId))
                    } label: {
                        HStack(alignment: .top, spacing: Spacing.inner) {
                            VStack(alignment: .leading, spacing: Spacing.tight) {
                                Text(task.intent).font(.callout.weight(.medium)).lineLimit(2)
                                Text(task.state).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right").font(.caption)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("overview.task.\(task.taskId)")
                }
                if tasks.count > 3 {
                    Text(L10n.controlText("overview.moreInSample"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func symbol(_ kind: TaskActionKind) -> String {
        switch kind {
        case .approval: return "person.crop.circle.badge.exclamationmark"
        case .blocked: return "exclamationmark.octagon"
        case .recovery: return "arrow.counterclockwise.circle"
        case .running: return "gearshape.2"
        }
    }
}

struct OverviewPolicyResources: View {
    @EnvironmentObject private var store: OrchestratorStore
    let onNavigate: (NavigationIntent) -> Void

    var body: some View {
        DashboardCard(title: L10n.controlText("overview.policyResources"), symbol: "slider.horizontal.3") {
            HStack(alignment: .top, spacing: Spacing.section) {
                VStack(alignment: .leading, spacing: Spacing.tight) {
                    Text(store.schedulingSettings.map { L10n.schedulingPolicyName($0.defaultSchedulingPolicy) }
                         ?? L10n.valueUnknown).font(.headline)
                    Text(L10n.policyGlobalDefault).font(.caption).foregroundStyle(.secondary)
                    Button(L10n.policyChange) { onNavigate(NavigationIntent(section: .settings)) }
                }
                Spacer()
                VStack(alignment: .leading, spacing: Spacing.tight) {
                    if let info = store.dashboard?.basicInfo {
                        LabeledContent(L10n.overviewRunnableTargets, value: "\(info.availableExecutionTargets)")
                        LabeledContent(L10n.overviewQuotaWarnings, value: "\(info.quotaWarningCount)")
                    } else {
                        Text(L10n.valueUnknown).foregroundStyle(.secondary)
                    }
                    Button(L10n.controlText("overview.openResources")) {
                        onNavigate(NavigationIntent(section: .resources))
                    }
                }
                .font(.callout)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("overview.policyResources")
    }
}
