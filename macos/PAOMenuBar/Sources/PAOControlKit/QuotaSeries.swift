import Foundation

/// Quota history semantics: what a *series* is, and where one reset window ends.
///
/// The previous history chart drew every stored observation into one bar row,
/// left to right, regardless of which provider, which pool, which window or
/// which reset period produced it — and it read the history newest-first while
/// drawing it oldest-first. Four different providers depleting four different
/// pools therefore appeared as one sequence that rose and fell for reasons that
/// existed nowhere in the data.
///
/// Nothing here draws anything. Grouping and segmentation are questions about
/// the observations, so they are answered where they can be tested, and the
/// chart consumes the answer.

// MARK: - Series identity

/// What makes two observations part of the same measurable sequence.
///
/// All three components are required. A pool is only meaningful inside its
/// provider, and a window is only meaningful inside its pool: GLM and MiniMax
/// both report a window called `5h`, and joining them would produce a line
/// describing neither. This mirrors the daemon's own history key
/// (`provider_id`, `quota_pool_id`, `window_id`) rather than inventing a
/// client-side notion of sameness.
public struct QuotaSeriesIdentity: Hashable, Comparable, Sendable {
    public let providerId: String
    public let quotaPoolId: String
    public let windowId: String

    public init(providerId: String, quotaPoolId: String, windowId: String) {
        self.providerId = providerId
        self.quotaPoolId = quotaPoolId
        self.windowId = windowId
    }

    public init(observation: QuotaObservationView) {
        self.init(
            providerId: observation.providerId,
            quotaPoolId: observation.quotaPoolId,
            windowId: observation.windowId
        )
    }

    /// Stable ordering, so series indices — and therefore the colours and
    /// symbols derived from them — do not shuffle between renders.
    public static func < (lhs: Self, rhs: Self) -> Bool {
        if lhs.providerId != rhs.providerId { return lhs.providerId < rhs.providerId }
        if lhs.quotaPoolId != rhs.quotaPoolId { return lhs.quotaPoolId < rhs.quotaPoolId }
        return lhs.windowId < rhs.windowId
    }

    /// Machine identity for chart scales and accessibility, joined with a
    /// separator that cannot occur in the parts.
    public var key: String { "\(providerId)\u{001F}\(quotaPoolId)\u{001F}\(windowId)" }
}

// MARK: - A single usable reading

/// One observation that carries a real, usable percentage.
///
/// Parsed and range-checked at construction, so nothing downstream has to
/// re-decide whether a reading is trustworthy. An observation the daemon
/// recorded with UNKNOWN confidence, no fraction, an unparseable timestamp or an
/// out-of-range value never becomes one of these — it is dropped from the series
/// rather than repaired into a plausible-looking point.
public struct QuotaReading: Equatable, Sendable {
    public let observedAt: Date
    public let remainingFraction: Double
    public let resetAt: Date?
    public let state: String
    public let confidence: String

    public init(
        observedAt: Date,
        remainingFraction: Double,
        resetAt: Date? = nil,
        state: String = "UNKNOWN",
        confidence: String = "EXACT"
    ) {
        self.observedAt = observedAt
        self.remainingFraction = remainingFraction
        self.resetAt = resetAt
        self.state = state
        self.confidence = confidence
    }

    /// A percentage outside 0...1 is not a quota reading, whatever produced it.
    /// Clamping would invent a value the provider never reported.
    public static func isPlausible(fraction: Double) -> Bool {
        fraction.isFinite && fraction >= 0 && fraction <= 1
    }

    /// Promotes an API observation, or returns nil when it carries no usable
    /// reading. Nil is a real answer: it means "no observation", never "zero".
    public init?(observation: QuotaObservationView) {
        guard observation.confidence != "UNKNOWN",
            let fraction = observation.remainingFraction,
            Self.isPlausible(fraction: fraction),
            let observedAt = TaskTiming.parseTimestamp(observation.observedAt)
        else { return nil }
        self.observedAt = observedAt
        self.remainingFraction = fraction
        self.resetAt = observation.resetAt.flatMap(TaskTiming.parseTimestamp)
        self.state = observation.state
        self.confidence = observation.confidence
    }
}

// MARK: - Reset windows

/// A run of readings belonging to one reset period of one series.
///
/// A reset is a discontinuity, not a data point: quota that went 80% → 20% → 95%
/// did not recover, it was replenished, and a trend drawn through that turn is a
/// trend of nothing. Segments are the unit every derived figure is computed
/// over, so no burn rate or projection can accidentally span a reset.
public struct QuotaWindowSegment: Equatable, Sendable {
    public let series: QuotaSeriesIdentity
    /// Ascending by time, deduplicated, guaranteed non-empty.
    public let readings: [QuotaReading]
    /// The reset instant this period ends at, when the daemon reported one.
    public let resetAt: Date?

    public var first: QuotaReading { readings[0] }
    public var last: QuotaReading { readings[readings.count - 1] }
    public var count: Int { readings.count }

    /// Wall-clock span actually covered by observations, which is not the span
    /// of the reset window: six readings in one hour of a five-hour window
    /// describe one hour.
    public var observedSpan: TimeInterval {
        last.observedAt.timeIntervalSince(first.observedAt)
    }

    init(series: QuotaSeriesIdentity, readings: [QuotaReading], resetAt: Date?) {
        self.series = series
        self.readings = readings
        self.resetAt = resetAt
    }
}

/// One series and the reset periods it decomposes into.
public struct QuotaSeries: Equatable, Sendable {
    public let identity: QuotaSeriesIdentity
    /// Ascending by time; each is one reset period.
    public let segments: [QuotaWindowSegment]

    /// The period in progress — the last one — which is the only one a forecast
    /// may be built from. Earlier periods are history, not evidence about now.
    public var currentSegment: QuotaWindowSegment? { segments.last }

    /// Every reading of the series in time order, across periods. For rendering
    /// marks only: the points are real, but the sequence crosses resets and must
    /// never be treated as one continuous decline.
    public var allReadings: [QuotaReading] { segments.flatMap(\.readings) }
}

public enum QuotaHistory {

    // MARK: Grouping

    /// Groups raw observations into series, ordered deterministically.
    ///
    /// Everything the daemon could not give a usable reading for is dropped
    /// here, once, rather than being filtered again at each call site with a
    /// slightly different rule.
    public static func series(from observations: [QuotaObservationView]) -> [QuotaSeries] {
        var grouped: [QuotaSeriesIdentity: [QuotaReading]] = [:]
        for observation in observations {
            guard let reading = QuotaReading(observation: observation) else { continue }
            grouped[QuotaSeriesIdentity(observation: observation), default: []].append(reading)
        }
        return grouped.keys.sorted().map { identity in
            QuotaSeries(
                identity: identity,
                segments: segments(of: identity, readings: grouped[identity] ?? [])
            )
        }
    }

    public static func series(from history: QuotaHistoryView?) -> [QuotaSeries] {
        series(from: history?.observations ?? [])
    }

    /// The series for one provider, in the same deterministic order.
    public static func series(
        from history: QuotaHistoryView?, providerId: String
    ) -> [QuotaSeries] {
        series(from: history).filter { $0.identity.providerId == providerId }
    }

    // MARK: Normalization

    /// Sorts ascending and collapses readings that claim the same instant.
    ///
    /// The daemon stores a row per distinct value tuple, so one instant can
    /// legitimately carry two different percentages — a collector retry that
    /// disagrees with itself, or two windows written under one observation time.
    /// The lower reading wins. That is deterministic regardless of the order the
    /// rows arrived in, and it is the direction a quota surface should err in:
    /// never report more headroom than the evidence supports.
    static func normalize(_ readings: [QuotaReading]) -> [QuotaReading] {
        var byInstant: [Date: QuotaReading] = [:]
        for reading in readings {
            guard let existing = byInstant[reading.observedAt] else {
                byInstant[reading.observedAt] = reading
                continue
            }
            byInstant[reading.observedAt] = preferred(existing, reading)
        }
        return byInstant.values.sorted { $0.observedAt < $1.observedAt }
    }

    /// Total, order-independent tiebreak between two readings of one instant.
    private static func preferred(_ lhs: QuotaReading, _ rhs: QuotaReading) -> QuotaReading {
        if lhs.remainingFraction != rhs.remainingFraction {
            return lhs.remainingFraction < rhs.remainingFraction ? lhs : rhs
        }
        // Same instant, same value: order by reset instant so the choice cannot
        // depend on which row the database happened to return first. A reading
        // with no reset sorts last — a known horizon is worth more than none.
        switch (lhs.resetAt, rhs.resetAt) {
        case (nil, nil): return lhs
        case (nil, _): return rhs
        case (_, nil): return lhs
        case (let left?, let right?): return left <= right ? lhs : rhs
        }
    }

    // MARK: Segmentation

    /// Splits one series' readings at reset boundaries.
    ///
    /// A boundary is decided from reset identity and elapsed time only, never
    /// from the quota value. Using the value would make segmentation circular
    /// with the burn rate computed over the segments — a rise would create a
    /// boundary, and the boundary would then hide the rise.
    ///
    /// Two consecutive readings begin a new period when either:
    ///
    /// 1. their reported reset instants differ, including known → unknown. A
    ///    different reset instant is a different window by definition; or
    /// 2. the earlier reading's reset instant has been reached by the time of
    ///    the later one. The window ended, whether or not the daemon has yet
    ///    observed the replacement.
    static func segments(
        of identity: QuotaSeriesIdentity, readings: [QuotaReading]
    ) -> [QuotaWindowSegment] {
        let ordered = normalize(readings)
        guard !ordered.isEmpty else { return [] }

        var result: [QuotaWindowSegment] = []
        var current: [QuotaReading] = [ordered[0]]
        for reading in ordered.dropFirst() {
            let previous = current[current.count - 1]
            if isResetBoundary(from: previous, to: reading) {
                result.append(
                    QuotaWindowSegment(
                        series: identity, readings: current, resetAt: previous.resetAt
                    )
                )
                current = [reading]
            } else {
                current.append(reading)
            }
        }
        result.append(
            QuotaWindowSegment(
                series: identity,
                readings: current,
                resetAt: current[current.count - 1].resetAt
            )
        )
        return result
    }

    static func isResetBoundary(from previous: QuotaReading, to next: QuotaReading) -> Bool {
        if previous.resetAt != next.resetAt { return true }
        if let resetAt = previous.resetAt, next.observedAt >= resetAt { return true }
        return false
    }
}
