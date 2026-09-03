import SwiftUI

import PAOControlKit

/// One resource's quota bindings.
///
/// A provider does not have "a quota". It has bindings — one window of one pool
/// — and they can be in different states with different reset times. Each is
/// rendered on its own, and there is deliberately no total: averaging a 5-hour
/// window at 12% with a monthly window at 90% produces a number that describes
/// no window that exists, and summing them produces one that cannot exist.
struct ResourceQuotaSection: View {
    let resource: ResourceSnapshot
    var now: Date = Date()

    private var bindings: [QuotaBindingSnapshot] { resource.quotaBindings }

    var body: some View {
        ResourceSection(L10n.resourceSectionQuota, symbol: "chart.pie") {
            if resource.kind != .connected && bindings.isEmpty {
                // Never connected, so never read. Not a failure.
                ResourceNotice(text: L10n.resourceNotConnected, symbol: "link.badge.plus")
            } else if bindings.isEmpty {
                // Connected with no quota telemetry. Still a usable resource;
                // only its balance is unknown.
                VStack(alignment: .leading, spacing: Spacing.inner) {
                    ResourceNotice(
                        text: L10n.resourceNoQuotaTelemetry,
                        symbol: "questionmark.circle",
                        tone: .unknown
                    )
                    if let reason = resource.quota?.failureReason {
                        Text(L10n.quotaFailureReason(reason))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else {
                // Bindings read side by side when the detail pane is wide
                // enough — `[FIVE_HOUR] [WEEKLY]` — and reflow into a column
                // when it is not. A grid rather than a fit-or-stack pair: with
                // four bindings the all-or-nothing form fell back to one
                // column and left the pane half empty. Each binding is still
                // rendered on its own, in full: a card per binding, never an
                // aggregate.
                LazyVGrid(
                    columns: [
                        GridItem(
                            .adaptive(minimum: DashboardLayoutMetrics.cardMinimumWidth),
                            spacing: DashboardLayoutMetrics.cardSpacing
                        )
                    ],
                    alignment: .leading,
                    spacing: DashboardLayoutMetrics.cardSpacing
                ) {
                    bindingCards
                }
            }
        }
    }

    /// One card per binding, sharing the dashboard's card geometry.
    private var bindingCards: some View {
        ForEach(bindings) { binding in
            DashboardCard {
                QuotaBindingRow(binding: binding, now: now)
            }
        }
    }
}

/// One quota binding: the window, its state, what remains, and when it resets.
struct QuotaBindingRow: View {
    let binding: QuotaBindingSnapshot
    var now: Date = Date()

    private var confidence: StatusPresentation {
        StatusStyle.quotaConfidence(binding.confidence)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            // Two rows, because three chips and a window name do not fit one
            // row in a card narrow enough to sit two-across: on one row the
            // window name wrapped mid-word and the state chip truncated to
            // "AVAIL…". The window and its state lead; the qualifiers follow.
            HStack(spacing: Spacing.inner) {
                Label(binding.windowKind, systemImage: "timer")
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                Spacer(minLength: Spacing.tight)
                ResourceChip(
                    text: binding.state,
                    tone: binding.status.tone,
                    symbol: binding.status.symbol
                )
            }
            HStack(spacing: Spacing.tight) {
                if binding.isLimiting {
                    // The daemon's own answer to "which window limits this
                    // plan". The client never elects one itself.
                    ResourceChip(
                        text: L10n.resourceBindingLimiting,
                        tone: .caution,
                        symbol: "exclamationmark.triangle"
                    )
                }
                ResourceChip(text: binding.confidence, tone: confidence.tone)
                Spacer(minLength: 0)
            }

            if binding.isReadable, let fraction = binding.remainingFraction {
                // Observed, so the meter may be filled.
                QuotaMeter(
                    fraction: fraction, level: .observed, tone: binding.status.tone
                )
                HStack(spacing: Spacing.inner) {
                    Text(
                        "\(L10n.quotaRemainingLabel) \(QuotaFormat.percent(fraction))"
                    )
                    .font(.callout.weight(.semibold).monospacedDigit())
                    if let ratio = QuotaFormat.unitRatio(
                        remaining: binding.remainingUnits,
                        total: binding.totalUnits,
                        unit: binding.unit
                    ) {
                        Text("(\(ratio))")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)
                    }
                    Spacer(minLength: 0)
                    resetLabel
                }
            } else {
                // UNKNOWN: no percentage, and an empty track rather than a bar
                // filled from nothing.
                QuotaMeter(fraction: nil, level: .observed, tone: .unknown)
                HStack(spacing: Spacing.inner) {
                    Text(L10n.quotaNoReliablePercentage)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    resetLabel
                }
            }

            freshnessLabel
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibleSummary)
    }

    private var resetDate: Date? {
        binding.resetAt.flatMap(TaskTiming.parseTimestamp)
    }

    @ViewBuilder
    private var resetLabel: some View {
        if let resetDate {
            if let horizon = QuotaFormat.timeUntil(resetDate, now: now) {
                // Scarcity and urgency side by side, never merged: 20% resetting
                // in 30 minutes and 20% resetting in six days are equally scarce
                // and not equally urgent.
                Text(L10n.quotaResetsIn(horizon))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .help(Timestamps.absolute(resetDate))
            } else {
                Text(L10n.quotaResetPassed)
                    .font(.caption)
                    .foregroundStyle(StatusTone.unknown.color)
                    .help(Timestamps.absolute(resetDate))
            }
        } else {
            Text(L10n.quotaResetUnknown)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }

    /// How old the reading is. Absent freshness is stated as absent — "just now"
    /// is never shown without a timestamp that earns it.
    @ViewBuilder
    private var freshnessLabel: some View {
        if let observedAt = binding.observedAt,
            let date = TaskTiming.parseTimestamp(observedAt)
        {
            let stale = isStale(observedAt: date)
            HStack(spacing: Spacing.tight) {
                Image(systemName: stale ? "clock.badge.exclamationmark" : "clock")
                    .imageScale(.small)
                Text(
                    QuotaFormat.age(of: date, now: now).map(L10n.quotaObservedAge)
                        ?? Timestamps.friendly(observedAt, now: now)
                )
            }
            .font(.caption2)
            .foregroundStyle(stale ? StatusTone.caution.color : Color.secondary)
            .help(stale ? L10n.resourceStaleWarning : Timestamps.absolute(date))
        } else {
            Label(L10n.resourceFreshnessUnknown, systemImage: "clock.badge.questionmark")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    /// A reading older than the reset it describes cannot be current: the window
    /// it was taken in has ended. That is the one staleness rule the data
    /// supports without inventing a freshness policy.
    private func isStale(observedAt: Date) -> Bool {
        guard let resetDate else { return false }
        return resetDate <= observedAt || resetDate <= now
    }

    private var accessibleSummary: String {
        var parts = [binding.windowKind, binding.state, binding.confidence]
        if binding.isLimiting { parts.append(L10n.resourceBindingLimiting) }
        if let fraction = binding.remainingFraction, binding.isReadable {
            parts.append("\(L10n.quotaRemainingLabel) \(QuotaFormat.percent(fraction))")
        } else {
            parts.append(L10n.quotaNoReliablePercentage)
        }
        if let resetDate, let horizon = QuotaFormat.timeUntil(resetDate, now: now) {
            parts.append(L10n.quotaResetsIn(horizon))
        }
        return parts.joined(separator: ", ")
    }
}

/// The shared subscription plan, when the resource has one.
///
/// Preserves the separations P4.2.6.5 established, which B4 does not relitigate:
/// pool membership is not a balance, per-model consumption is a contribution and
/// not an entitlement, and equivalent capacity is a task count that cannot be
/// combined with a percentage.
struct ResourcePlanSection: View {
    let plan: QuotaPlanView

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.element) {
            planHeader
            coveredModels
            if !plan.inScopeEquivalents.isEmpty { modelEquivalents }
            if !plan.outOfScopeEquivalents.isEmpty { otherWorkloadScopes }
            if !plan.modelConsumption.isEmpty { modelConsumption }
            equivalentCapacity
        }
    }

    private var planHeader: some View {
        HStack(spacing: Spacing.inner) {
            Text(plan.displayName)
                .font(.callout.weight(.semibold))
            if let level = plan.planLevel {
                ResourceChip(text: level.uppercased())
            }
            if plan.activeWorkloadScope != "UNKNOWN" {
                // Names the workload the figures describe, so an owner who knows
                // MiniMax also meters video can tell it was excluded rather than
                // lost.
                ResourceChip(text: L10n.quotaWorkloadScope(plan.activeWorkloadScope))
            }
            Spacer(minLength: 0)
        }
    }

    private var coveredModels: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            Text(L10n.quotaPlanSharedModels)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if plan.coveredModelIds.isEmpty {
                Text(L10n.quotaNoReliableData)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                // Membership only. Deliberately no meters: a model in a shared
                // pool owns no balance of its own.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Spacing.tight) {
                        ForEach(plan.coveredModelIds, id: \.self) { ResourceChip(text: $0) }
                    }
                    VStack(alignment: .leading, spacing: Spacing.tight) {
                        ForEach(plan.coveredModelIds, id: \.self) { ResourceChip(text: $0) }
                    }
                }
            }
        }
    }

    private var modelEquivalents: some View {
        DisclosureGroup(L10n.quotaPlanEquivalents) {
            VStack(alignment: .leading, spacing: Spacing.tight) {
                ForEach(plan.inScopeEquivalents) { item in
                    HStack {
                        Text(item.scopeId).font(.caption)
                        Text(item.windowId).font(.caption2).foregroundStyle(.tertiary)
                        Spacer()
                        if let fraction = item.remainingFraction {
                            Text(QuotaFormat.percent(fraction))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Text(L10n.quotaPlanEquivalentsFooter)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, Spacing.tight)
        }
        .font(.caption)
    }

    /// Balances this build observes but does not schedule against. Preserved
    /// rather than hidden — deleting a real observation because today's product
    /// ignores it would destroy evidence — but subordinate, and stated as
    /// constraining nothing here.
    private var otherWorkloadScopes: some View {
        DisclosureGroup(L10n.quotaPlanOtherScopes) {
            VStack(alignment: .leading, spacing: Spacing.tight) {
                ForEach(plan.outOfScopeEquivalents) { item in
                    HStack {
                        Text(item.scopeId).font(.caption)
                        Text(L10n.quotaWorkloadScope(item.workloadScope))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                        Spacer()
                        if let fraction = item.remainingFraction {
                            Text(QuotaFormat.percent(fraction))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
                Text(L10n.quotaPlanOtherScopesFooter)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, Spacing.tight)
        }
        .font(.caption)
    }

    private var modelConsumption: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            Text(L10n.quotaPlanModelUsage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(plan.modelConsumption) { item in
                HStack {
                    Text(item.modelId).font(.caption)
                    Spacer()
                    // A bare quantity with its unit and no bar: a bar implies a
                    // ceiling, and consumption has none.
                    Text(
                        "\(L10n.quotaPlanConsumedLabel) "
                            + QuotaFormat.units(item.consumedUnits)
                            + " " + item.unitKind.lowercased()
                    )
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
            }
            Text(L10n.quotaPlanModelUsageFooter)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Equivalent capacity: a task count for the limiting window, always
    /// ESTIMATED.
    ///
    /// Its unit is tasks, not percent, so it is never placed beside the balances
    /// above and never folded into one. The explanation is behind disclosure
    /// because the concept is genuinely technical, not because it is unimportant.
    private var equivalentCapacity: some View {
        DisclosureGroup(L10n.quotaPlanEstimatedCapacity) {
            VStack(alignment: .leading, spacing: Spacing.tight) {
                if plan.equivalentCapacity.isEmpty {
                    Text(L10n.quotaPlanInsufficientHistory)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(plan.equivalentCapacity) { item in
                    HStack(alignment: .firstTextBaseline) {
                        Text(item.modelId).font(.caption)
                        Spacer()
                        if let tasks = item.estimatedRemainingTasks {
                            VStack(alignment: .trailing, spacing: 2) {
                                HStack(spacing: Spacing.tight) {
                                    ResourceChip(
                                        text: L10n.quotaEstimatedBadge, tone: .caution
                                    )
                                    if item.smallSample {
                                        ResourceChip(text: L10n.quotaPlanSmallSample)
                                    }
                                }
                                Text(L10n.quotaCapacityTasks(Int(tasks.rounded())))
                                    .font(.caption)
                                Text(L10n.quotaCapacityBasis(item.sampleCount))
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        } else {
                            // Absence, not zero. "0 tasks remaining" and "we do
                            // not know yet" mean opposite things.
                            Text(L10n.quotaPlanInsufficientHistory)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Text(L10n.quotaEquivalentCapacityExplanation)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, Spacing.tight)
        }
        .font(.caption)
    }
}
