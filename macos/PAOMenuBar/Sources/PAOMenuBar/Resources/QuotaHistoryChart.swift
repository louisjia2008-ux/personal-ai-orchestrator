import Charts
import SwiftUI

import PAOControlKit

/// Observed quota over time, one line per series, never joined across a reset.
///
/// The contract, stated once so the renderer can be read against it:
///
/// - **Series identity** — (provider, quota pool, window). Two providers that
///   both call a window `5h` are two series and are never joined.
/// - **x** — real observation time. Not an index: the previous chart drew evenly
///   spaced bars, so a six-hour gap and a one-minute gap looked identical.
/// - **y** — remaining fraction, fixed to 0...1. A free y-scale would make a
///   window that fell from 100% to 95% look like one that emptied.
/// - **Source** — `QuotaHistoryView.observations`, filtered to readings the
///   daemon gave a usable percentage for. Nothing is interpolated and nothing is
///   synthesized; a gap is empty space because nothing was observed in it.
/// - **Reset boundaries** — a dashed rule, and a break in the line. Quota that
///   went 20% then 95% was replenished, not recovered.
/// - **Selection** — none. `chartXSelection` is macOS 14 and the deployment
///   target is 13; a hand-rolled hit-test would be a worse answer than the point
///   marks and the accessible summary, which give the same facts on every
///   system.
/// - **Empty** — states which of "never observed", "one observation" and "not
///   enough for a trend" applies, because they lead to different actions.
/// - **Accessibility** — every series is summarised in text below the plot, so
///   the chart is never the only way to reach its content.
///
/// This file draws. It computes nothing: the grouping, the segmentation and the
/// rates all arrive from `PAOControlKit`, where they are tested.
struct QuotaHistoryChart: View {
    let series: [QuotaSeries]
    /// Provider display names, so the legend reads "GLM Coding Plan" rather than
    /// `glm-coding`.
    var displayNames: [String: String] = [:]

    private var identities: [QuotaSeriesIdentity] { series.map(\.identity).sorted() }

    /// One drawable point. `segmentKey` is what keeps a line from crossing a
    /// reset: marks are grouped by it, so two reset periods of one series are
    /// two lines that share a colour rather than one line through the boundary.
    private struct Point: Identifiable {
        let id: String
        let seriesKey: String
        let seriesLabel: String
        let segmentKey: String
        let observedAt: Date
        let remaining: Double
    }

    private var points: [Point] {
        series.flatMap { one -> [Point] in
            let label = QuotaSeriesStyle.label(for: one.identity, displayNames: displayNames)
            return one.segments.enumerated().flatMap { index, segment in
                segment.readings.map { reading in
                    Point(
                        id: "\(one.identity.key)#\(index)@\(reading.observedAt.timeIntervalSince1970)",
                        seriesKey: one.identity.key,
                        seriesLabel: label,
                        segmentKey: "\(one.identity.key)#\(index)",
                        observedAt: reading.observedAt,
                        remaining: reading.remainingFraction
                    )
                }
            }
        }
    }

    /// Where one reset period ends and the next begins, per series.
    ///
    /// Only boundaries between two observed periods are drawn. A trailing reset
    /// that has not happened yet is a future instant, not a discontinuity in the
    /// data, and marking it would claim an event that has not occurred.
    private var resetBoundaries: [Date] {
        series.flatMap { one in
            one.segments.dropLast().compactMap(\.resetAt)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            chart
            legend
            Text(L10n.quotaHistoryGapNote)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            accessibleSummaries
        }
    }

    private var chart: some View {
        Chart {
            ForEach(resetBoundaries, id: \.self) { boundary in
                RuleMark(x: .value(L10n.quotaHistoryResetBoundary, boundary))
                    .foregroundStyle(Color(nsColor: .tertiaryLabelColor))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .annotation(position: .top, alignment: .leading) {
                        Text(L10n.quotaHistoryResetBoundary)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
            }
            ForEach(points) { point in
                // Grouped by segment, so no line is drawn across a reset.
                LineMark(
                    x: .value(L10n.quotaHistoryAxisTime, point.observedAt),
                    y: .value(L10n.quotaHistoryAxisRemaining, point.remaining),
                    series: .value(L10n.quotaHistorySeriesLegend, point.segmentKey)
                )
                .foregroundStyle(by: .value(L10n.quotaHistorySeriesLegend, point.seriesLabel))
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                // Every observation is marked. A line alone would leave the
                // reader unable to tell six readings from two.
                PointMark(
                    x: .value(L10n.quotaHistoryAxisTime, point.observedAt),
                    y: .value(L10n.quotaHistoryAxisRemaining, point.remaining)
                )
                .foregroundStyle(by: .value(L10n.quotaHistorySeriesLegend, point.seriesLabel))
                .symbol(by: .value(L10n.quotaHistorySeriesLegend, point.seriesLabel))
                .symbolSize(28)
            }
        }
        // Fixed to the range a percentage occupies. Autoscaling would magnify a
        // 5% dip into a cliff.
        .chartYScale(domain: 0...1)
        .chartYAxis {
            AxisMarks(values: [0, 0.25, 0.5, 0.75, 1]) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let fraction = value.as(Double.self) {
                        Text(QuotaFormat.percent(fraction))
                    }
                }
            }
        }
        // Both scales are pinned to an explicit domain rather than left to be
        // inferred from the data. An inferred domain is ordered by first
        // appearance, which happens to match the sorted order today — and would
        // stop matching the moment a series' first observation moved. The
        // legend below indexes the same sorted list, so pinning is what keeps
        // the swatch beside a name and the line on the plot the same colour.
        .chartForegroundStyleScale(domain: seriesLabels, range: seriesColors)
        .chartSymbolScale(domain: seriesLabels, range: seriesSymbols)
        // The legend is rendered below with the series' own names; the built-in
        // one would duplicate it without the accessible summaries.
        .chartLegend(.hidden)
        .frame(height: 180)
        .accessibilityHidden(true)
    }

    /// The chart's scale domain: every series' label, in the same stable order
    /// the legend uses.
    private var seriesLabels: [String] {
        identities.map { QuotaSeriesStyle.label(for: $0, displayNames: displayNames) }
    }

    private var seriesColors: [Color] {
        identities.indices.map(QuotaSeriesStyle.color(at:))
    }

    /// Shape is the second channel: with two providers on one plot, colour
    /// alone would separate them, and colour is never allowed to be the only
    /// thing that does.
    private var seriesSymbols: [BasicChartSymbolShape] {
        let shapes: [BasicChartSymbolShape] = [.circle, .square, .triangle, .diamond]
        return identities.indices.map { shapes[QuotaSeriesStyle.symbolIndex(at: $0)] }
    }

    /// Names each series in text with its colour and symbol beside it.
    ///
    /// A legend is not optional here: with two providers on one plot, colour is
    /// the only thing separating them, and colour is never allowed to be the
    /// only channel.
    private var legend: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: Spacing.element) { legendEntries }
            VStack(alignment: .leading, spacing: Spacing.tight) { legendEntries }
        }
    }

    @ViewBuilder
    private var legendEntries: some View {
        ForEach(Array(identities.enumerated()), id: \.element) { index, identity in
            HStack(spacing: Spacing.tight) {
                Image(systemName: legendSymbol(at: index))
                    .imageScale(.small)
                    .foregroundStyle(QuotaSeriesStyle.color(at: index))
                Text(QuotaSeriesStyle.label(for: identity, displayNames: displayNames))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
    }

    /// SF Symbols standing in for the chart's plotting symbols. They differ in
    /// shape, not only in fill, so the legend survives greyscale too.
    private func legendSymbol(at index: Int) -> String {
        let symbols = ["circle.fill", "square.fill", "triangle.fill", "diamond.fill"]
        return symbols[QuotaSeriesStyle.symbolIndex(at: index)]
    }

    /// The chart's content in words.
    ///
    /// A reader who cannot interpret the plot still gets the three things it
    /// shows: which series, how many observations, and where the latest reading
    /// sits. Hidden from sighted layout, present to VoiceOver.
    private var accessibleSummaries: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(series, id: \.identity) { one in
                let readings = one.allReadings
                if let latest = readings.last, let earliest = readings.first {
                    Text(
                        L10n.quotaHistoryAccessibleSummary(
                            series: QuotaSeriesStyle.label(
                                for: one.identity, displayNames: displayNames
                            ),
                            count: readings.count,
                            latest: QuotaFormat.percent(latest.remainingFraction),
                            span: QuotaFormat.horizon(
                                latest.observedAt.timeIntervalSince(earliest.observedAt)
                            )
                        )
                    )
                }
            }
        }
        .frame(width: 0, height: 0)
        .accessibilityElement(children: .combine)
    }
}

/// The Usage section's chart, plus the states where there is no chart to draw.
///
/// "Never observed", "one observation" and "several observations" are three
/// different situations, and only the last one has a trend in it.
struct QuotaHistoryPanel: View {
    let series: [QuotaSeries]
    var displayNames: [String: String] = [:]
    let retentionLimit: Int

    private var observationCount: Int {
        series.reduce(0) { $0 + $1.allReadings.count }
    }

    var body: some View {
        if observationCount == 0 {
            ResourceNotice(
                text: QuotaProjectionUnavailable.noHistory.explanation,
                symbol: "questionmark.circle",
                tone: .unknown
            )
        } else if observationCount == 1 {
            // One reading is a position, not a trend. Drawing a single dot on a
            // time axis would suggest a line the data cannot support.
            ResourceNotice(text: L10n.quotaHistorySingleObservation, symbol: "circle")
        } else {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                QuotaHistoryChart(series: series, displayNames: displayNames)
                HStack(spacing: Spacing.tight) {
                    Text(L10n.quotaHistoryObservationCount(observationCount))
                    if retentionLimit > 0 {
                        Text("·")
                        Text(L10n.quotaHistoryFooter(retentionLimit: retentionLimit))
                    }
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
    }
}
