import SwiftUI

import PAOControlKit

/// The resource collection: what this machine can run work on, and how much of
/// it is left.
///
/// Every provider the daemon knows of appears here exactly once, whichever of
/// the three provider endpoints reported it. A provider with no quota telemetry
/// is still a resource and still renders — being unobservable is a fact about
/// the reading, not about whether the thing exists.
struct ResourceCollectionView: View {
    let state: ResourceCollectionState
    let totalCount: Int
    @Binding var query: String
    @Binding var kindFilter: ResourceKind?
    @Binding var selectedResourceId: String?
    var now: Date = Date()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor))
        .accessibilityIdentifier("workspace.collection")
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: Spacing.inner) {
            kindMenu
            Spacer(minLength: 0)
            if state.isPopulated {
                let counted = L10n.resourcesVisibleCount(
                    shown: state.resources.count, total: totalCount
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

    /// Connected, offered, discovered. Not a status filter: the three say what
    /// the owner did, and each is a legitimate thing to want to look at alone.
    private var kindMenu: some View {
        Menu {
            Button(L10n.resourcesFilterAll) { kindFilter = nil }
            Section(L10n.resourcesFilterKind) {
                ForEach(ResourceKind.allCases, id: \.rawValue) { kind in
                    Button(kind.title) { kindFilter = kind }
                }
            }
        } label: {
            Label(
                kindFilter?.title ?? L10n.resourcesFilterAll,
                systemImage: "line.3.horizontal.decrease.circle"
            )
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help(L10n.resourcesFilterKind)
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            // Loading is not empty. An empty state here would claim the daemon
            // holds no providers before it has been asked.
            VStack(spacing: Spacing.inner) {
                ProgressView().controlSize(.small)
                Text(L10n.emptyResourcesLoading)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .disconnected(let reason):
            EmptyStateView(
                title: L10n.disconnectedLabel,
                symbol: "bolt.slash",
                message: L10n.disconnectionReason(reason),
                hint: L10n.resourcesDisconnectedHint
            )

        case .empty:
            EmptyStateView(
                title: L10n.emptyResourcesNoneTitle,
                symbol: "square.stack.3d.up.slash",
                message: L10n.emptyResourcesNoneMessage
            )

        case .noSearchMatch:
            EmptyStateView(
                title: L10n.emptyResourcesSearchTitle,
                symbol: "magnifyingglass",
                message: L10n.emptyResourcesSearchMessage(
                    query.trimmingCharacters(in: .whitespacesAndNewlines)
                )
            )

        case .populated(let resources):
            List(resources, selection: $selectedResourceId) { resource in
                ResourceCollectionRow(resource: resource, now: now)
            }
            .listStyle(.inset(alternatesRowBackgrounds: false))
        }
    }
}

/// One resource: what it is, whether it is reachable, and the one quota figure
/// that can honestly stand for it.
///
/// Deliberately not every metric. The row carries identity, availability, the
/// limiting balance where one is authoritative, and the reset horizon; the rest
/// belongs to the detail, where there is room to say what each figure means.
struct ResourceCollectionRow: View {
    let resource: ResourceSnapshot
    var now: Date = Date()

    private var status: StatusPresentation { resource.connectionStatus }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.inner) {
            Image(systemName: status.symbol)
                .foregroundStyle(status.color)
                .frame(width: 16)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Spacing.inner) {
                    Text(resource.displayName)
                        .font(.body)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    quotaSummary
                }
                Text(secondaryLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibleSummary)
    }

    /// The one quota figure the row may show.
    ///
    /// Only the window the daemon named as limiting, or a lone readable binding,
    /// qualifies. Several readable bindings render as a count: averaging a
    /// 5-hour window at 12% with a monthly window at 90% would describe no
    /// window that exists.
    @ViewBuilder
    private var quotaSummary: some View {
        if let binding = resource.summaryBinding, let fraction = binding.remainingFraction {
            HStack(spacing: Spacing.tight) {
                Image(systemName: binding.status.symbol)
                    .imageScale(.small)
                    .foregroundStyle(binding.status.color)
                Text(QuotaFormat.percent(fraction))
                    .font(.callout.weight(.semibold).monospacedDigit())
            }
        } else if resource.readableBindingCount > 1 {
            Text(L10n.resourceSeveralBindings(resource.readableBindingCount))
                .font(.caption)
                .foregroundStyle(.secondary)
        } else if resource.kind == .connected {
            // Connected but unreadable. UNKNOWN gets its own tone rather than
            // the treatment "nothing to report" would get.
            ResourceChip(
                text: L10n.resourceQuotaNotObservable,
                tone: .unknown,
                symbol: "questionmark.circle"
            )
        }
    }

    /// Kind, reset horizon and execution targets, in the space of one line.
    private var secondaryLine: String {
        var parts: [String] = [resource.kind.title]
        if let reset = resetText { parts.append(reset) }
        if let freshness = freshnessText { parts.append(freshness) }
        let targets = resource.executionTargets.count
        if targets > 0 {
            parts.append(
                L10n.resourceTargetsSummary(
                    verified: resource.verifiedExecutionTargetCount, total: targets
                )
            )
        }
        return parts.joined(separator: " · ")
    }

    private var resetText: String? {
        guard let resetAt = resource.summaryBinding?.resetAt,
            let date = TaskTiming.parseTimestamp(resetAt)
        else { return nil }
        guard let horizon = QuotaFormat.timeUntil(date, now: now) else {
            // Past its own reset. Counting down into negative numbers would
            // present stale evidence as live.
            return L10n.quotaResetPassed
        }
        return L10n.quotaResetsIn(horizon)
    }

    /// Age of the quota reading. Absent when nothing was dated — "just now" is
    /// never shown without a timestamp to justify it.
    private var freshnessText: String? {
        guard let observedAt = resource.quotaObservedAt,
            let date = TaskTiming.parseTimestamp(observedAt),
            let age = QuotaFormat.age(of: date, now: now)
        else { return nil }
        return L10n.resourceObservedAge(age)
    }

    private var accessibleSummary: String {
        var parts = [resource.displayName, resource.connectionState, resource.kind.title]
        if let binding = resource.summaryBinding, let fraction = binding.remainingFraction {
            parts.append("\(L10n.quotaRemainingLabel) \(QuotaFormat.percent(fraction))")
            parts.append(binding.state)
        } else {
            parts.append(L10n.resourceQuotaNotObservable)
        }
        if let resetText { parts.append(resetText) }
        return parts.joined(separator: ", ")
    }
}
