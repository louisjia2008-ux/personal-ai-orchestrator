import CoreGraphics
import SwiftUI

// The dashboard's shared layout contract.
//
// One source of truth for the geometry five destinations must agree on:
// sidebar width, page insets, section/card rhythm and the collection→detail
// split. Before this these were literals repeated per page (210/230, 260/320/460,
// 420, 24 vs 20), which is why the pages disagreed about where a title, a
// divider and the first line of content sit.
//
// Values that coincide with the `Spacing` scale reference it rather than
// restating a number, so the 4pt grid stays the only place spacing is defined.

public enum DashboardLayoutMetrics {
    // MARK: Navigation

    /// The app sidebar, pinned rather than flexible. Five short rows do not need
    /// a resizable column, and a column the split view was free to re-solve is
    /// exactly why the sidebar changed width between destinations.
    public static let sidebarWidth: CGFloat = 180

    // MARK: Page chrome

    /// Outer horizontal inset between the content pane and page content.
    public static let pageHorizontalPadding: CGFloat = Spacing.page
    /// Outer vertical inset between the content pane and page content.
    public static let pageVerticalPadding: CGFloat = Spacing.page
    /// Vertical gap between a page's top-level sections. Deliberately off the
    /// `Spacing.section` value: page sections must breathe noticeably more
    /// than the rows inside them, which was indistinguishable when both
    /// used 16. Still on the 4pt grid.
    public static let sectionSpacing: CGFloat = 28

    // MARK: Cards

    /// Gap between sibling cards (KPI row, chart row, quota binding cards).
    public static let cardSpacing: CGFloat = Spacing.element
    public static let cardCornerRadius: CGFloat = Radius.panel
    public static let cardPadding: CGFloat = 14
    /// Reserved height of one KPI cell, so every cell in the strip is identical.
    public static let kpiTileHeight: CGFloat = 118
    /// Reserved plot height of a chart inside a card, so cards sharing a row
    /// end the same height. Charts are primary Overview content, not inset
    /// thumbnails: the plot must stay tall enough for a week of buckets to
    /// stay readable.
    public static let chartHeight: CGFloat = 224
    /// Narrowest a card may become before a row of them wraps.
    public static let cardMinimumWidth: CGFloat = 260

    // MARK: Collection → detail workspaces

    /// Width of the collection column in Tasks and Resources. Both workspaces
    /// name this token, never their own number, so their split dividers start at
    /// the same position.
    ///
    /// `HSplitView` resolves a child to its *maximum* width, not its ideal, so
    /// this is applied as both the ideal and the maximum; `collectionMinimumWidth`
    /// is how far the owner may drag it in.
    public static let collectionPreferredWidth: CGFloat = 312
    public static let collectionMinimumWidth: CGFloat = 260
    /// The detail pane never collapses below a readable width; the collection
    /// gives way first.
    public static let detailMinimumWidth: CGFloat = 480

    // MARK: Window

    /// The smallest window the layout is designed to hold: the sidebar, the
    /// collection and a detail pane still wide enough to read.
    public static var minimumWindowWidth: CGFloat {
        sidebarWidth + collectionPreferredWidth + detailMinimumWidth
    }
    public static let minimumWindowHeight: CGFloat = 600

    // MARK: Content width roles
    //
    // Round 1 of the visual closeout split the old one-size cap into roles:
    //
    // - dashboards, tables and charts (Overview, Activity) use the available
    //   content-column width — a global narrow cap is what left them hugged to
    //   the left of a wide window;
    // - reading/form content (the Settings column) keeps a measure and centers
    //   it, the way System Settings does, so a wide window gains symmetric
    //   margins instead of a dead region on the right.

    /// Widest the Settings-style form column grows. Wide enough for the
    /// label/value rows and button rows it holds, bounded so a maximised
    /// window stretches nothing across the whole display, and centered at
    /// widths beyond it.
    public static let formMaximumWidth: CGFloat = 980
}
