import CoreGraphics
import SwiftUI

// Shared geometry for the five product destinations.
//
// The Daily Driver redesign deliberately uses the whole content pane for
// operational surfaces. Only Settings should impose a reading measure. The
// previous dashboard solved density by shrinking type and packing bordered
// cards; this contract instead gives the hierarchy room to breathe while
// preserving the stable sidebar and collection/detail workspaces.

public enum DashboardLayoutMetrics {
    // MARK: Navigation

    public static let sidebarWidth: CGFloat = 184

    // MARK: Page chrome

    /// 24pt keeps content visually attached to the native toolbar while still
    /// separating it from the split-view edge.
    public static let pageHorizontalPadding: CGFloat = 24
    public static let pageVerticalPadding: CGFloat = 24
    /// Major product sections (Now, Needs Attention, Capacity, Recent) need a
    /// stronger break than rows inside a surface.
    public static let sectionSpacing: CGFloat = 32

    // MARK: Surfaces

    public static let cardSpacing: CGFloat = 16
    public static let cardCornerRadius: CGFloat = 12
    public static let cardPadding: CGFloat = 16
    /// Retained for legacy Overview until its KPI wall is removed in this round.
    public static let kpiTileHeight: CGFloat = 108
    /// Charts are secondary in the Daily Driver UI but must remain readable
    /// wherever a historical/analytics surface still uses them.
    public static let chartHeight: CGFloat = 208
    public static let cardMinimumWidth: CGFloat = 280

    // MARK: Collection -> detail workspaces

    public static let collectionPreferredWidth: CGFloat = 320
    public static let collectionMinimumWidth: CGFloat = 272
    public static let detailMinimumWidth: CGFloat = 520

    // MARK: Window

    public static var minimumWindowWidth: CGFloat {
        sidebarWidth + collectionPreferredWidth + detailMinimumWidth
    }
    public static let minimumWindowHeight: CGFloat = 640

    // MARK: Reading measure

    /// Activity may use a readable measure. Home and Resources must not use
    /// this as a global content cap.
    public static let tableMaximumWidth: CGFloat = 1040
    /// Settings is intentionally narrower, matching a native preferences form.
    public static let formMaximumWidth: CGFloat = 760
}
