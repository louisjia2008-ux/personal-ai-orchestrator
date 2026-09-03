import Foundation

/// Deterministic burn rate and quota forecast.
///
/// Everything here is arithmetic over observations the daemon reported. There is
/// no model, no smoothing and no randomness: the same history produces the same
/// answer on every machine, and the answer can be explained to the owner in one
/// sentence. The daemon remains the sole owner of scheduling and quota policy —
/// nothing computed here is fed back into routing. This is explanation.
///
/// The refusals matter more than the arithmetic. Almost every input this can be
/// handed is one it must decline to forecast from, and each refusal names
/// itself, so the surface says *why* there is no estimate instead of showing a
/// number that happens to be computable.

// MARK: - Why there is no figure

/// Reasons a burn rate or a projection could not be produced.
///
/// Each is a distinct thing to tell the owner. Collapsing them into one
/// "unavailable" would leave "we have never observed this pool" and "the window
/// already reset" reading identically, and only one of those is fixed by
/// waiting.
public enum QuotaProjectionUnavailable: String, Error, Equatable, Sendable, CaseIterable {
    /// Nothing usable was ever recorded for this series.
    case noHistory = "NO_HISTORY"
    /// Fewer than two readings in the current reset window. One reading is a
    /// position, not a rate.
    case insufficientObservations = "INSUFFICIENT_OBSERVATIONS"
    /// Every reading in the window shares one instant, so no time elapsed
    /// between them. A rate would divide by zero.
    case noElapsedTime = "NO_ELAPSED_TIME"
    /// The balance rose. Something replenished it inside a window this client
    /// cannot see the cause of, and extrapolating a rising balance forward
    /// would forecast a surplus that no evidence supports.
    case remainingIncreased = "REMAINING_INCREASED"
    /// No reset instant, so there is no horizon to project to. The burn rate is
    /// still valid; only the projection is not.
    case resetUnknown = "RESET_UNKNOWN"
    /// The reset instant has already passed. A new window has begun that has
    /// not been observed, so the readings describe a window that no longer
    /// exists.
    case resetAlreadyPassed = "RESET_ALREADY_PASSED"

    public var explanation: String { L10n.quotaProjectionUnavailable(rawValue) }
}

// MARK: - Burn rate

/// How fast a quota window is being consumed, in fraction of the window's
/// capacity per second.
///
/// DERIVED, never observed: no provider reports a burn rate, and this one is an
/// average across the observed span of the current reset window, not an
/// instantaneous reading.
public struct QuotaBurnRate: Equatable, Sendable {
    /// Fraction of total capacity consumed per second. Positive means being
    /// consumed; zero means flat; never negative — a rising balance is refused
    /// as a rate rather than reported as one.
    public let fractionPerSecond: Double
    /// Readings the rate was measured from.
    public let sampleCount: Int
    /// Seconds between the first and last reading used.
    public let spanSeconds: TimeInterval
    /// The reading the rate was measured to, which is also the freshness of
    /// everything derived from it.
    public let observedAt: Date
    /// Remaining fraction at `observedAt`.
    public let remainingFraction: Double

    public var isFlat: Bool { fractionPerSecond == 0 }

    /// Per hour, which is the unit the owner reads in. Per-second figures for a
    /// multi-hour window are all leading zeroes.
    public var fractionPerHour: Double { fractionPerSecond * 3_600 }

    /// Seconds until the balance would reach zero at this rate, or nil when it
    /// never would.
    public var secondsToExhaustion: TimeInterval? {
        guard fractionPerSecond > 0 else { return nil }
        return remainingFraction / fractionPerSecond
    }
}

// MARK: - Projection

/// A forecast about one reset window.
///
/// Every field is FORECAST. `EvidenceLevel.forecast` refuses to fill a meter and
/// prefixes its values with `≈`, which is what keeps these from being read as
/// the provider's own figures.
public struct QuotaProjection: Equatable, Sendable {
    public let series: QuotaSeriesIdentity
    public let burnRate: QuotaBurnRate
    /// The reset this projects to.
    public let resetAt: Date
    /// Fraction expected to remain when the window resets, clamped to 0...1.
    /// Zero means "expected to run out first", which `exhaustionAt` dates.
    public let projectedRemainingAtReset: Double
    /// When the balance is expected to reach zero, but only when that falls
    /// before the reset. A window that outlasts its quota exhausts; a window
    /// that resets first never does.
    public let exhaustionAt: Date?

    /// Quota expected to still be there at reset, and therefore expected to
    /// expire unused. Nil when exhaustion is expected instead.
    public var likelyUnusedFraction: Double? {
        exhaustionAt == nil && projectedRemainingAtReset > 0
            ? projectedRemainingAtReset : nil
    }

    public var expectsExhaustion: Bool { exhaustionAt != nil }

    /// The level every value in this type carries. A projection is never
    /// OBSERVED, and this is the only level it can report.
    public var evidenceLevel: EvidenceLevel { .forecast }
}

/// One series' complete derived picture: a rate when the readings support one,
/// a projection when the rate and a reset horizon both do.
///
/// The two are separate because they fail separately. A window with no reported
/// reset still has a measurable burn rate, and telling the owner "8% per hour,
/// reset time unknown" is more useful — and more honest — than reporting
/// nothing because one field was missing.
public struct QuotaOutlook: Equatable, Sendable {
    public let series: QuotaSeriesIdentity
    public let burnRate: QuotaBurnRate?
    public let projection: QuotaProjection?
    /// Why the burn rate is absent. Nil when it is present.
    public let burnRateUnavailable: QuotaProjectionUnavailable?
    /// Why the projection is absent. Nil when it is present.
    public let projectionUnavailable: QuotaProjectionUnavailable?
    /// The reset window the figures describe, when there was one to describe.
    public let segment: QuotaWindowSegment?

    public var hasBurnRate: Bool { burnRate != nil }
    public var hasProjection: Bool { projection != nil }
}

public enum QuotaForecast {

    /// Minimum readings in one reset window before a rate can be measured.
    ///
    /// Two, and not as a tuning choice: a slope is defined by two points and
    /// undefined by one. It is named rather than inlined so the rule is
    /// findable, not so it can be adjusted.
    public static let minimumObservations = 2

    // MARK: Burn rate

    /// Measures consumption across a reset window's observed span.
    ///
    /// Endpoints, not a fit: `(first.remaining − last.remaining) / elapsed`.
    /// A regression over six points would be no more accurate about a future the
    /// owner controls, and it could not be explained in the one line the
    /// interface has room for.
    ///
    /// The segment is one reset window by construction, so this can never
    /// measure a slope across a replenishment.
    public static func burnRate(in segment: QuotaWindowSegment?) -> Result<
        QuotaBurnRate, QuotaProjectionUnavailable
    > {
        guard let segment else { return .failure(.noHistory) }
        guard segment.count >= minimumObservations else {
            return .failure(.insufficientObservations)
        }
        let elapsed = segment.observedSpan
        guard elapsed > 0 else { return .failure(.noElapsedTime) }

        let consumed = segment.first.remainingFraction - segment.last.remainingFraction
        // A balance that rose is not a negative burn rate. Reporting one would
        // let the projection extrapolate upward to a surplus the provider never
        // promised; refusing says the truthful thing instead.
        guard consumed >= 0 else { return .failure(.remainingIncreased) }

        return .success(
            QuotaBurnRate(
                fractionPerSecond: consumed / elapsed,
                sampleCount: segment.count,
                spanSeconds: elapsed,
                observedAt: segment.last.observedAt,
                remainingFraction: segment.last.remainingFraction
            )
        )
    }

    // MARK: Projection

    /// Projects a reset window forward from its measured burn rate.
    ///
    /// `remaining_at_reset = clamp(remaining_now − rate × seconds_until_reset)`,
    /// clamped into 0...1 because the linear extrapolation is only meaningful
    /// inside the range a percentage can occupy. The clamp is where exhaustion
    /// is detected: a projection that would go below zero means the balance runs
    /// out first, and `exhaustionAt` says when.
    ///
    /// The horizon is the reset instant. Nothing is projected past it, because a
    /// reset ends the window this rate describes.
    ///
    /// `now` is a parameter rather than `Date()` so the whole model is testable
    /// and so a countdown cannot drift between the projection and the label
    /// beside it.
    public static func project(
        segment: QuotaWindowSegment?,
        burnRate: QuotaBurnRate,
        now: Date
    ) -> Result<QuotaProjection, QuotaProjectionUnavailable> {
        guard let segment else { return .failure(.noHistory) }
        guard let resetAt = segment.resetAt else { return .failure(.resetUnknown) }
        let secondsUntilReset = resetAt.timeIntervalSince(now)
        guard secondsUntilReset > 0 else { return .failure(.resetAlreadyPassed) }

        let remaining = burnRate.remainingFraction
        let projectedRaw = remaining - burnRate.fractionPerSecond * secondsUntilReset
        let projected = min(1, max(0, projectedRaw))

        // Exhaustion is only claimed when the straight line actually reaches
        // zero inside the window. A flat rate never does, and neither does a
        // rate slow enough to outlast the reset.
        var exhaustionAt: Date?
        if let secondsToExhaustion = burnRate.secondsToExhaustion,
            secondsToExhaustion <= secondsUntilReset
        {
            // Measured from the last reading, not from now: the rate describes
            // the span it was measured over, and dating exhaustion from a later
            // instant would silently grant the window free time.
            exhaustionAt = burnRate.observedAt.addingTimeInterval(secondsToExhaustion)
        }

        return .success(
            QuotaProjection(
                series: segment.series,
                burnRate: burnRate,
                resetAt: resetAt,
                projectedRemainingAtReset: exhaustionAt == nil ? projected : 0,
                exhaustionAt: exhaustionAt
            )
        )
    }

    // MARK: Outlook

    /// The complete derived picture for one series.
    public static func outlook(for series: QuotaSeries, now: Date) -> QuotaOutlook {
        let segment = series.currentSegment
        switch burnRate(in: segment) {
        case .failure(let reason):
            return QuotaOutlook(
                series: series.identity,
                burnRate: nil,
                projection: nil,
                burnRateUnavailable: reason,
                // Without a rate there is nothing to project; the projection's
                // reason is the rate's reason, not a second independent fault.
                projectionUnavailable: reason,
                segment: segment
            )
        case .success(let rate):
            switch project(segment: segment, burnRate: rate, now: now) {
            case .failure(let reason):
                return QuotaOutlook(
                    series: series.identity,
                    burnRate: rate,
                    projection: nil,
                    burnRateUnavailable: nil,
                    projectionUnavailable: reason,
                    segment: segment
                )
            case .success(let projection):
                return QuotaOutlook(
                    series: series.identity,
                    burnRate: rate,
                    projection: projection,
                    burnRateUnavailable: nil,
                    projectionUnavailable: nil,
                    segment: segment
                )
            }
        }
    }

    /// Outlooks for every series in a history, in the series' stable order.
    public static func outlooks(
        from history: QuotaHistoryView?, providerId: String? = nil, now: Date
    ) -> [QuotaOutlook] {
        let all = QuotaHistory.series(from: history)
        let scoped = providerId.map { id in all.filter { $0.identity.providerId == id } } ?? all
        return scoped.map { outlook(for: $0, now: now) }
    }
}
