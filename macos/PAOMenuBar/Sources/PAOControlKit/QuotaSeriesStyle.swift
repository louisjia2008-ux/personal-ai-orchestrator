import AppKit
import SwiftUI

/// Stable, accessible styling for quota history series.
///
/// The previous chart hard-coded `Color.red` and `Color.blue` per series. Both
/// are wrong twice over: red already means *critical* everywhere else in this
/// interface, so a healthy provider drawn in red claimed a status it did not
/// have; and two literals cannot describe a third series at all.
///
/// Three rules replace them:
///
/// 1. **No status colour is ever a series colour.** Red, orange and green belong
///    to `StatusTone` and must keep meaning what they mean. The series palette
///    is drawn from the remaining system colours, which carry no verdict.
/// 2. **Colour is never the only channel.** Every series also gets a symbol
///    shape, so the chart survives greyscale and colour-blindness, and the
///    legend names each series in text regardless.
/// 3. **The assignment is deterministic.** Series are ordered by identity, so a
///    provider keeps its colour across refreshes rather than swapping with its
///    neighbour whenever the daemon returns rows in a different order.
public enum QuotaSeriesStyle {

    /// System colours only: they adapt to light and dark and respond to
    /// Increase Contrast, which fixed literals cannot. Deliberately excludes
    /// systemRed, systemOrange, systemGreen and systemYellow — those are the
    /// status vocabulary.
    public static let palette: [Color] = [
        Color(nsColor: .systemBlue),
        Color(nsColor: .systemPurple),
        Color(nsColor: .systemTeal),
        Color(nsColor: .systemIndigo),
        Color(nsColor: .systemPink),
        Color(nsColor: .systemBrown),
    ]

    /// Shape names paired with the palette. Four against six colours is not an
    /// oversight: the two lengths are chosen so colour and symbol advance out of
    /// step, giving twelve distinct combinations before any pair repeats.
    public static let symbolCount = 4

    public static func color(at index: Int) -> Color {
        guard index >= 0 else { return palette[0] }
        return palette[index % palette.count]
    }

    public static func symbolIndex(at index: Int) -> Int {
        guard index >= 0 else { return 0 }
        return index % symbolCount
    }

    /// Position of a series within a deterministically ordered set.
    ///
    /// Returns nil for a series that is not in the set, so a caller cannot
    /// silently style an unrelated series as index 0.
    public static func index(
        of identity: QuotaSeriesIdentity, in identities: [QuotaSeriesIdentity]
    ) -> Int? {
        identities.sorted().firstIndex(of: identity)
    }

    /// Owner-facing name of a series.
    ///
    /// Provider display name where the client holds one — the owner knows
    /// "GLM Coding Plan", not `glm-coding`. The window is always appended,
    /// because two windows of one provider are two series and a legend that
    /// named them identically would be worse than no legend.
    public static func label(
        for identity: QuotaSeriesIdentity, displayNames: [String: String] = [:]
    ) -> String {
        let provider = displayNames[identity.providerId] ?? identity.providerId
        return "\(provider) · \(identity.windowId)"
    }
}
