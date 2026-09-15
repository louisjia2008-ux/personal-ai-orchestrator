import SwiftUI

import PAOControlKit

/// The execution targets belonging to one resource.
///
/// A target is not a provider and not a model: it is the concrete thing — a
/// model SKU on a runtime — that work is dispatched to. Presenting it under its
/// provider is what makes the distinction legible: Claude the subscription,
/// Claude Code the target it runs through.
///
/// Only discovery reports targets. The connection registry's model SKU list is a
/// catalog fact and is shown separately, because a listed model is not a
/// runnable target and promoting one into the other would claim capability that
/// was never verified.
struct ResourceExecutionTargetsSection: View {
    let resource: ResourceSnapshot

    private var targets: [ExecutionTargetHealthView] { resource.executionTargets }

    /// Normal providers require an explicit connection-registry record. Pi is a
    /// local-runtime-authenticated surface and intentionally has no such record;
    /// the daemon's Pi runtime-availability projection is true only when its auth
    /// is READY and the exact model still exists in the Pi catalog.
    private var hasRoutingConnection: Bool {
        resource.kind == .connected
            || targets.contains { $0.runtimeId == "pi" && $0.runtimeAvailable == true }
    }

    /// Dispatch admission requires both routing connection and a target whose
    /// current host launch prerequisites hold. Historical demote-fallback
    /// verification remains visible below, but it is not promoted into this set.
    private var runnableTargets: [ExecutionTargetHealthView] {
        guard hasRoutingConnection else { return [] }
        return targets
            .filter(\.isLaunchableOnHost)
            .sorted {
                $0.modelSkuId.localizedCaseInsensitiveCompare($1.modelSkuId) == .orderedAscending
            }
    }

    private var unavailableTargets: [ExecutionTargetHealthView] {
        let runnableIds = Set(runnableTargets.map(\.executionTargetId))
        return targets
            .filter { !runnableIds.contains($0.executionTargetId) }
            .sorted { $0.modelSkuId.localizedCaseInsensitiveCompare($1.modelSkuId) == .orderedAscending }
    }

    var body: some View {
        ResourceSection(
            L10n.resourceSectionExecutionTargets,
            symbol: "bolt.horizontal.circle"
        ) {
            if targets.isEmpty {
                ResourceNotice(text: L10n.resourceNoExecutionTargets, symbol: "cpu")
            } else {
                VStack(alignment: .leading, spacing: Spacing.element) {
                    if runnableTargets.isEmpty {
                        ResourceNotice(
                            text: DailyDriverL10n.resourcesNoRunnableTarget,
                            symbol: "bolt.slash",
                            tone: .caution
                        )
                    } else {
                        VStack(alignment: .leading, spacing: Spacing.inner) {
                            Text(DailyDriverL10n.resourcesAvailableTargets)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            ForEach(runnableTargets) { target in
                                ExecutionTargetRow(target: target)
                            }
                        }
                    }

                    if !unavailableTargets.isEmpty {
                        DisclosureGroup(
                            DailyDriverL10n.resourcesUnavailableTargets(unavailableTargets.count)
                        ) {
                            VStack(alignment: .leading, spacing: Spacing.inner) {
                                ForEach(unavailableTargets) { target in
                                    ExecutionTargetRow(target: target)
                                }
                            }
                            .padding(.top, Spacing.inner)
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

/// One execution target: what it is, whether current execution verification
/// exists, and what the daemon last observed about runtime/quota availability.
struct ExecutionTargetRow: View {
    let target: ExecutionTargetHealthView

    /// A stale demote-fallback means an older VERIFIED row exists but the newest
    /// execution observation is non-VERIFIED. Showing that as VERIFIED would turn
    /// historical evidence into current authority, so the primary chip fails closed.
    private var hasCurrentVerification: Bool {
        target.isExecutionVerified && !target.isExecutionVerifiedStale
    }

    /// M1 WP2: SF Symbol for the tier chip. ``bolt`` (T0), ``gear``
    /// (T1), ``hare`` (T2), ``leaf`` (T3). ``questionmark.circle``
    /// for any string the table could not classify.
    private func tierSymbol(_ tier: String) -> String {
        switch tier {
        case "T0": return "bolt.fill"
        case "T1": return "gearshape.fill"
        case "T2": return "hare.fill"
        case "T3": return "leaf.fill"
        default: return "questionmark.circle"
        }
    }

    /// The observed availability, when the daemon recorded one. Absent
    /// availability is `unknown`, never healthy.
    private var availability: StatusPresentation {
        guard let observed = target.observedAvailability else {
            return StatusPresentation(tone: .unknown, symbol: "questionmark.circle")
        }
        return StatusStyle.quotaState(observed.state)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            HStack(spacing: Spacing.inner) {
                Image(systemName: availability.symbol)
                    .foregroundStyle(availability.color)
                VStack(alignment: .leading, spacing: 1) {
                    Text(target.modelSkuId)
                        .font(.callout.weight(.medium))
                    Text(target.runtimeId)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                // M1 WP2: tier chip. Always neutral tone — the tier
                // is a property of the target, not an alert.
                if let tier = target.tier {
                    ResourceChip(
                        text: L10n.tierLabel(tier),
                        tone: .neutral,
                        symbol: tierSymbol(tier)
                    )
                }
                if target.isExecutionVerifiedStale {
                    ResourceChip(
                        text: L10n.targetVerifiedStale,
                        tone: .caution,
                        symbol: "exclamationmark.triangle"
                    )
                }
                ResourceChip(
                    text: hasCurrentVerification ? "VERIFIED" : "UNVERIFIED",
                    tone: hasCurrentVerification ? .positive : .caution,
                    symbol: hasCurrentVerification ? "checkmark.seal" : "seal"
                )
            }

            DisclosureGroup(L10n.advancedDetails) {
                VStack(alignment: .leading, spacing: Spacing.tight) {
                    ResourceFieldRow(
                        label: L10n.executionTarget,
                        value: target.executionTargetId,
                        monospaced: true
                    )
                    ResourceFieldRow(
                        label: L10n.executionVerified,
                        value: hasCurrentVerification ? "TRUE" : "FALSE"
                    )
                    if let runtimeAvailable = target.runtimeAvailable {
                        ResourceFieldRow(
                            label: L10n.runtimeAvailability,
                            value: runtimeAvailable ? "AVAILABLE" : "UNAVAILABLE"
                        )
                    }
                    if let observed = target.observedAvailability {
                        ResourceFieldRow(
                            label: L10n.observedState, value: observed.state
                        )
                        ResourceFieldRow(
                            label: L10n.measurementSource, value: observed.measurementSource
                        )
                        ResourceFieldRow(
                            label: L10n.confidenceLabel, value: observed.confidence
                        )
                        ResourceFieldRow(
                            label: L10n.observedAt,
                            value: Timestamps.friendly(observed.observedAt)
                        )
                        if let reason = observed.sanitizedReasonCode {
                            ResourceFieldRow(label: L10n.reasonCode, value: reason)
                        }
                    } else {
                        ResourceNotice(
                            text: L10n.availabilityUnknown,
                            symbol: "questionmark.circle",
                            tone: .unknown
                        )
                    }
                }
                .padding(.top, Spacing.tight)
            }
            .font(.caption)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
    }
}

/// Catalog models this provider exposes.
///
/// Membership, not capability and not capacity: a model listed here has no
/// quota of its own and is not necessarily runnable. It sits below execution
/// targets for that reason.
struct ResourceModelsSection: View {
    let resource: ResourceSnapshot

    var body: some View {
        if !resource.modelSkus.isEmpty {
            ResourceSection(L10n.resourceSectionModels, symbol: "cpu") {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Spacing.tight) {
                        ForEach(resource.modelSkus, id: \.self) { ResourceChip(text: $0) }
                    }
                    VStack(alignment: .leading, spacing: Spacing.tight) {
                        ForEach(resource.modelSkus, id: \.self) { ResourceChip(text: $0) }
                    }
                }
            }
        }
    }
}
