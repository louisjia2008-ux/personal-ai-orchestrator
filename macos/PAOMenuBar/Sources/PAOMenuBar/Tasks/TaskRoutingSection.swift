import SwiftUI

import PAOControlKit

/// Which AI worker was selected for this task, and why.
///
/// Everything here comes from the normalized `TaskRoutingSummary`; this view
/// reconstructs no routing semantics of its own. The single rule it enforces
/// visually is the contract's central one: **only declared roles are shown**.
/// A plan that declares PRIMARY alone must not display "Reviewer — not
/// assigned", because that sentence claims a reviewer is expected when the plan
/// says none is.
struct TaskRoutingSection: View {
    let summary: TaskRoutingSummary?

    var body: some View {
        TaskDetailSection(L10n.routingTitle, symbol: "point.topleft.down.curvedto.point.bottomright.up") {
            if let summary {
                if summary.roles.isEmpty {
                    // A plan exists but declares nothing. Saying so is truthful;
                    // inventing a PRIMARY lane would not be.
                    TaskSectionNotice(text: L10n.routingNoDetails, symbol: "questionmark.circle", tone: .unknown)
                } else {
                    VStack(alignment: .leading, spacing: Spacing.element) {
                        ForEach(summary.roles) { role in
                            TaskRoutingRoleView(role: role)
                        }
                        provenance(summary)
                    }
                }
            } else {
                TaskSectionNotice(text: L10n.routingNotYetDecided, symbol: "clock")
            }
        }
    }

    /// Where the routing facts came from.
    ///
    /// Kept to two quiet lines. Legacy synthesis is a compatibility detail, not
    /// a safety problem, so it does not get a warning banner; the plan id and
    /// revision live in the inspector, where verbatim identity belongs.
    @ViewBuilder
    private func provenance(_ summary: TaskRoutingSummary) -> some View {
        if summary.isSuperseded {
            TaskSectionNotice(
                text: L10n.routingSuperseded,
                symbol: "arrow.triangle.branch",
                tone: .caution
            )
        }
        if summary.isLegacySynthesized {
            Text(L10n.routingLegacySynthesized)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}

/// One declared role: where it stands, who is doing it, and why they were picked.
struct TaskRoutingRoleView: View {
    let role: RoutingRoleSummary
    @State private var showsHistory = false

    private var presentation: StatusPresentation {
        StatusStyle.routingRole(status: role.status, outcome: role.outcome)
    }

    /// Status and outcome are separate axes, so they are shown as separate
    /// facts. A completed reviewer that rejected the work reads as "Completed —
    /// Rejected the work", never as a generic failure.
    private var statusText: String {
        let status = L10n.routingRoleStatusName(role.status)
        guard role.status == .completed, role.outcome != .none else { return status }
        return "\(status) — \(L10n.routingOutcomeName(role.outcome))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            HStack(alignment: .firstTextBaseline) {
                Text(L10n.routingRoleName(role.role))
                    .font(.callout.weight(.semibold))
                Spacer()
                TaskStatusLabel(presentation: presentation, text: statusText, font: .callout)
            }

            if let decision = role.activeDecision {
                assignment(decision)
            } else {
                // Declared and not yet assigned is an explicit state. It is only
                // ever rendered for a role the plan actually declared.
                TaskStatusLabel(
                    presentation: StatusStyle.routingStatus(.unassigned),
                    text: L10n.routingRoleStatusName(.unassigned),
                    font: .caption
                )
                .foregroundStyle(.secondary)
            }

            if role.rerouteCount > 0 {
                Text(L10n.routingRerouteCount(role.rerouteCount))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // History is secondary: the current assignment is what the owner
            // acts on, so previous decisions stay folded away.
            if role.history.count > 1 {
                DisclosureGroup(isExpanded: $showsHistory) {
                    VStack(alignment: .leading, spacing: Spacing.inner) {
                        ForEach(role.history.dropFirst()) { decision in
                            TaskRoutingDecisionRow(decision: decision)
                        }
                    }
                    .padding(.top, Spacing.tight)
                } label: {
                    Text(L10n.routingPreviousDecisions)
                        .font(.caption)
                }
            }
        }
        .padding(.vertical, Spacing.tight)
    }

    @ViewBuilder
    private func assignment(_ decision: RoutingDecisionRecordView) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: Spacing.tight) {
                Image(systemName: "cpu")
                    .imageScale(.small)
                    .foregroundStyle(.secondary)
                Text(
                    decision.modelDisplayName
                        ?? decision.selectedExecutionTargetId
                        ?? L10n.candidateUnknownModel
                )
                .font(.callout)
                .textSelection(.enabled)
                if let provider = decision.providerId {
                    Text(provider)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let why = decision.whySelected {
                // Structured scheduler evidence, verbatim.
                Text(why)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let reason = decision.rerouteReason {
                Text("\(L10n.routingRerouteReason): \(reason)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A superseded selection for one role.
struct TaskRoutingDecisionRow: View {
    let decision: RoutingDecisionRecordView

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: Spacing.tight) {
                Text(
                    decision.modelDisplayName
                        ?? decision.selectedExecutionTargetId
                        ?? L10n.candidateUnknownModel
                )
                .font(.caption.weight(.medium))
                Spacer()
                Text(Timestamps.friendly(decision.createdAt))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            if let reason = decision.rerouteReason ?? decision.whySelected {
                Text(reason)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
