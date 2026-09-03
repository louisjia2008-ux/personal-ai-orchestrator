import SwiftUI

// Shared layout constants.
//
// Deliberately small. macOS already supplies most of the metrics a native app
// needs, and a parallel design system competing with the platform is worse than
// no design system at all. What is collected here is only what the dashboard was
// otherwise repeating as literals at call sites — spacing and radii appearing as
// 10, 12, 14, 16, 18, 20 and 6, 7, 8, 10 with nothing distinguishing them.
//
// These are named at the top level rather than nested under a `Layout`
// namespace: SwiftUI already defines a `Layout` protocol, and shadowing it makes
// every call site ambiguous.

/// A 4pt scale. Anything not on it is a one-off and should justify itself.
public enum Spacing {
    /// Between tightly coupled elements: an icon and its label.
    public static let tight: CGFloat = 4
    /// Between related rows inside one container.
    public static let inner: CGFloat = 8
    /// Default gap between elements in a stack.
    public static let element: CGFloat = 12
    /// Between sections of a page.
    public static let section: CGFloat = 16
    /// Page margins.
    public static let page: CGFloat = 20
}

/// Two radii. Six were in use, with no rule distinguishing them.
public enum Radius {
    /// Inline elements: badges, chips, small wells.
    public static let inline: CGFloat = 6
    /// Panels and cards.
    public static let panel: CGFloat = 8
}

/// Reading measure for prose and label/value pairs. Tables and charts are
/// full-bleed and ignore this.
public enum ContentWidth {
    public static let reading: CGFloat = 720
}
