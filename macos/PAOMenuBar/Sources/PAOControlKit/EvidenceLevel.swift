import SwiftUI

/// How a displayed number was arrived at.
///
/// The quota surface is the reason this exists. "61% remaining" read from a
/// provider and "about 34% remaining at reset" extrapolated from six local
/// observations are different kinds of claim, and presenting them identically
/// would let an estimate be mistaken for a provider fact. The level travels with
/// the value rather than being applied at the call site, so a view cannot forget
/// to mark one.
///
/// B1 introduces the foundation only; B4 supplies the quota projections that use
/// it. Nothing here computes a forecast.
public enum EvidenceLevel: String, Equatable, Sendable, CaseIterable {
    /// Reported directly by a provider or the daemon.
    case observed = "OBSERVED"
    /// Computed deterministically from observed values (a rate, an elapsed span,
    /// a count). True given its inputs, but not itself a reading.
    case derived = "DERIVED"
    /// An extrapolation about the future. Never a reading, never exact.
    case forecast = "FORECAST"

    public var label: String {
        switch self {
        case .observed: return L10n.evidenceObserved
        case .derived: return L10n.evidenceDerived
        case .forecast: return L10n.evidenceForecast
        }
    }

    /// Forecasts are prefixed with `≈` wherever they appear, so an estimate is
    /// recognisable as one even when its label is out of view.
    public var valuePrefix: String {
        self == .forecast ? "≈\u{202F}" : ""
    }

    /// Only observed readings may fill a meter.
    ///
    /// A projection drawn inside the same bar as a reading would claim to *be*
    /// that reading; projections belong beside the meter, never inside it.
    public var mayFillMeter: Bool { self == .observed }

    /// Forecast marks are dashed so they stay distinguishable from an observed
    /// line in greyscale and for colour-blind viewers.
    public var isDashed: Bool { self == .forecast }

    /// Derived and forecast values sit below observed ones in the type hierarchy,
    /// but never below legibility: the reduction is weight, not contrast.
    public var fontWeight: Font.Weight {
        self == .observed ? .semibold : .regular
    }

    public var foregroundStyle: HierarchicalShapeStyle {
        self == .observed ? .primary : .secondary
    }
}

/// A number plus the standing of the claim it makes.
///
/// `level` is not optional and has no default: a call site cannot present a
/// figure without stating how it was arrived at.
public struct MetricPresentation: Equatable, Sendable {
    public let text: String
    public let level: EvidenceLevel
    /// When the underlying observation was taken. Derived and forecast values are
    /// only as fresh as their inputs, so freshness travels with them.
    public let observedAt: String?
    /// Why no value is available. Set only when `text` is nil-equivalent — an
    /// absent projection must explain itself rather than render as zero.
    public let unavailableReason: String?

    public init(
        text: String,
        level: EvidenceLevel,
        observedAt: String? = nil,
        unavailableReason: String? = nil
    ) {
        self.text = text
        self.level = level
        self.observedAt = observedAt
        self.unavailableReason = unavailableReason
    }

    /// A value that could not be produced. Renders as an explanation, never as a
    /// number: absent is not zero, and the two must not look alike.
    public static func unavailable(
        reason: String, level: EvidenceLevel
    ) -> MetricPresentation {
        MetricPresentation(
            text: L10n.evidenceUnavailable,
            level: level,
            unavailableReason: reason
        )
    }

    public var isUnavailable: Bool { unavailableReason != nil }

    /// The string as it should appear, including the forecast marker.
    public var displayText: String {
        isUnavailable ? text : level.valuePrefix + text
    }
}
