import SwiftUI

import PAOControlKit

/// Burn rate and forecast for one resource's quota bindings.
///
/// This is the only place on the surface where a number is not a fact, and the
/// presentation carries that all the way down: every value goes through
/// `MetricPresentation` with its evidence level attached, so a forecast arrives
/// already marked `≈`, already barred from filling a meter, and already labelled
/// as a projection.
///
/// It is advisory. Nothing here reaches the scheduler: the daemon owns routing
/// and quota policy, and this explains what the daemon's own readings imply
/// without becoming a second opinion the system acts on.
struct QuotaProjectionSection: View {
    let outlooks: [QuotaOutlook]
    var displayNames: [String: String] = [:]
    var now: Date = Date()

    var body: some View {
        ResourceSection(
            L10n.resourceSectionProjection,
            symbol: "chart.line.uptrend.xyaxis",
            footnote: L10n.quotaProjectionFooter
        ) {
            if outlooks.isEmpty {
                ResourceNotice(
                    text: QuotaProjectionUnavailable.noHistory.explanation,
                    symbol: "questionmark.circle",
                    tone: .unknown
                )
            } else {
                VStack(alignment: .leading, spacing: Spacing.element) {
                    ForEach(outlooks, id: \.series) { outlook in
                        QuotaOutlookRow(
                            outlook: outlook,
                            title: QuotaSeriesStyle.label(
                                for: outlook.series, displayNames: displayNames
                            ),
                            now: now
                        )
                    }
                    scarcityDisclosure
                }
            }
        }
    }

    /// Where a scarcity verdict would go, and why there is none.
    ///
    /// The daemon classifies scarcity from a pace built on the window's start
    /// and duration. The control API publishes neither those fields nor the
    /// resulting class, so the client cannot reproduce the verdict — and must
    /// not invent a second one with thresholds of its own, because a routing
    /// recommendation the scheduler does not share is worse than none. The
    /// evidence the verdict would rest on is directly above; the gap is stated
    /// rather than left silent.
    private var scarcityDisclosure: some View {
        Text(L10n.quotaScarcityUnavailable)
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// One binding's rate and forecast, or the named reason there is neither.
struct QuotaOutlookRow: View {
    let outlook: QuotaOutlook
    let title: String
    var now: Date = Date()

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            Text(title)
                .font(.callout.weight(.medium))
            burnRateRow
            projectionRows
        }
        .padding(.vertical, 2)
    }

    // MARK: Burn rate

    @ViewBuilder
    private var burnRateRow: some View {
        if let rate = outlook.burnRate {
            ResourceMetric(
                label: L10n.quotaBurnRateTitle,
                metric: MetricPresentation(
                    text: rate.isFlat
                        ? L10n.quotaBurnRateFlat
                        : QuotaFormat.ratePerHour(rate.fractionPerHour),
                    level: .derived,
                    observedAt: nil
                ),
                caption: L10n.quotaBurnRateBasis(
                    samples: rate.sampleCount,
                    span: QuotaFormat.horizon(rate.spanSeconds)
                )
            )
        } else if let reason = outlook.burnRateUnavailable {
            ResourceMetric(
                label: L10n.quotaBurnRateTitle,
                metric: .unavailable(reason: reason.explanation, level: .derived)
            )
        }
    }

    // MARK: Projection

    @ViewBuilder
    private var projectionRows: some View {
        if let projection = outlook.projection {
            ResourceMetric(
                label: L10n.quotaProjectionRemainingAtReset,
                metric: MetricPresentation(
                    text: QuotaFormat.percent(projection.projectedRemainingAtReset),
                    level: .forecast,
                    observedAt: nil
                ),
                caption: QuotaFormat.timeUntil(projection.resetAt, now: now)
                    .map(L10n.quotaResetsIn)
            )
            if let exhaustionAt = projection.exhaustionAt {
                ResourceMetric(
                    label: L10n.quotaProjectionExhaustionAt,
                    metric: MetricPresentation(
                        text: Timestamps.friendly(
                            ISO8601DateFormatter().string(from: exhaustionAt), now: now
                        ),
                        level: .forecast
                    )
                )
            } else {
                ResourceNotice(
                    text: L10n.quotaProjectionNoExhaustion, symbol: "checkmark.circle"
                )
                .font(.caption)
            }
            if let unused = projection.likelyUnusedFraction {
                // The harvest question, stated as an estimate: quota expected to
                // still be there when the window resets is quota expected to
                // expire unspent.
                ResourceMetric(
                    label: L10n.quotaProjectionLikelyUnused,
                    metric: MetricPresentation(
                        text: QuotaFormat.percent(unused), level: .forecast
                    )
                )
            }
        } else if let reason = outlook.projectionUnavailable,
            reason != outlook.burnRateUnavailable
        {
            // Only when the projection failed for its own reason. Repeating the
            // rate's reason would report one fault as two.
            ResourceMetric(
                label: L10n.quotaProjectionTitle,
                metric: .unavailable(reason: reason.explanation, level: .forecast)
            )
        }
    }
}
