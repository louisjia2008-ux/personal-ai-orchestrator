import SwiftUI

import PAOControlKit

/// The execution targets belonging to one resource.
///
/// A target is not a provider and not a model: it is the runnable thing —
/// a model SKU on a runtime — that work is actually dispatched to. Presenting it
/// under its provider is what makes the distinction legible: Claude the
/// subscription, Claude Code the target it runs through.
///
/// Only discovery reports targets. The connection registry's model SKU list is a
/// catalog fact and is shown separately, because a listed model is not a
/// runnable target and promoting one into the other would claim capability that
/// was never verified.
struct ResourceExecutionTargetsSection: View {
    let resource: ResourceSnapshot

    private var targets: [ExecutionTargetHealthView] { resource.executionTargets }

    var body: some View {
        ResourceSection(
            L10n.resourceSectionExecutionTargets,
            symbol: "bolt.horizontal.circle"
        ) {
            if targets.isEmpty {
                ResourceNotice(text: L10n.resourceNoExecutionTargets, symbol: "cpu")
            } else {
                VStack(alignment: .leading, spacing: Spacing.inner) {
                    ForEach(targets) { target in
                        ExecutionTargetRow(target: target)
                    }
                }
            }
        }
    }
}

/// One execution target: what it is, whether it is runnable, and what the
/// daemon last observed about it.
struct ExecutionTargetRow: View {
    let target: ExecutionTargetHealthView

    /// Enabled *and* execution-verified. Either alone is not a runnable target,
    /// and reporting one as the other would offer the owner a target the
    /// scheduler would refuse.
    private var isRunnable: Bool { target.enabled && target.isExecutionVerified }

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
                // WP5b: stale-verification chip. Demote-fallback
                // semantics — the latest evidence is non-VERIFIED
                // while an older VERIFIED row exists. Renders a
                // caution chip so the owner can spot a target whose
                // history disagrees with its most recent run.
                if target.isExecutionVerifiedStale {
                    ResourceChip(
                        text: L10n.targetVerifiedStale,
                        tone: .caution,
                        symbol: "exclamationmark.triangle"
                    )
                }
                ResourceChip(
                    text: isRunnable ? "VERIFIED" : "UNVERIFIED",
                    tone: isRunnable ? .positive : .caution,
                    symbol: isRunnable ? "checkmark.seal" : "seal"
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
                        value: target.isExecutionVerified ? "TRUE" : "FALSE"
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
