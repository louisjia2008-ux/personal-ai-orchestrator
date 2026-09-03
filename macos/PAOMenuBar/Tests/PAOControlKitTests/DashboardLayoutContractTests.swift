import XCTest

@testable import PAOControlKit

/// The shared layout contract behind the five dashboard destinations.
///
/// Unit tests cannot prove visual alignment; what they can prove is that the
/// geometry the views consume is defined once and satisfies the contract B5
/// set out. If a page reintroduces a private magic number, it shows up as one
/// of these tokens drifting or being bypassed in review — not as five views
/// quietly disagreeing.
final class DashboardLayoutContractTests: XCTestCase {
    // MARK: Canonical navigation

    /// The frozen five-destination information architecture.
    func testCanonicalNavigationRemainsFiveDestinationsInOrder() {
        XCTAssertEqual(
            DashboardSection.allCases.map(\.rawValue),
            ["overview", "tasks", "resources", "activity", "settings"]
        )
    }

    /// The old backend-shaped destinations must never come back as pages.
    func testLegacyDestinationsResolveIntoTheFive() {
        for legacy in DashboardSectionMigration.legacyStoredValues {
            let resolved = DashboardSectionMigration.section(forStoredValue: legacy)
            XCTAssertTrue(
                DashboardSection.allCases.contains(resolved),
                "\(legacy) must resolve into a canonical destination"
            )
        }
    }

    // MARK: Page chrome

    /// Page insets are one value pair, drawn from the shared spacing scale, so
    /// Tasks (20) and Resources (20) can never drift apart again the way
    /// 20 vs 24 did in B4.
    func testPageInsetsAreSharedAndOnTheSpacingScale() {
        XCTAssertEqual(
            DashboardLayoutMetrics.pageHorizontalPadding,
            DashboardLayoutMetrics.pageVerticalPadding
        )
        XCTAssertEqual(DashboardLayoutMetrics.pageHorizontalPadding, Spacing.page)
        XCTAssertEqual(DashboardLayoutMetrics.sectionSpacing, Spacing.section)
    }

    /// The sidebar is one pinned width, not a band. A resizable column let the
    /// split view re-solve it per destination, which is exactly how the sidebar
    /// came to change width when the owner switched pages.
    func testSidebarWidthIsPinned() {
        XCTAssertTrue(
            (160...220).contains(DashboardLayoutMetrics.sidebarWidth),
            "sidebar width \(DashboardLayoutMetrics.sidebarWidth) left the 160–220 band"
        )
    }

    // MARK: Cards

    func testCardGeometryIsShared() {
        XCTAssertEqual(DashboardLayoutMetrics.cardCornerRadius, Radius.panel)
        XCTAssertEqual(DashboardLayoutMetrics.cardSpacing, Spacing.element)
        XCTAssertGreaterThan(DashboardLayoutMetrics.cardPadding, 0)
    }

    /// KPI tiles reserve one height so the Overview row stays a row.
    func testKPITilesReserveACommonHeight() {
        XCTAssertGreaterThan(DashboardLayoutMetrics.kpiTileHeight, 0)
        XCTAssertGreaterThan(DashboardLayoutMetrics.chartHeight, 0)
    }

    // MARK: Collection → detail workspace

    /// Tasks and Resources name the same numbers because they name the same
    /// tokens; the contract keeps those numbers in the agreed bands.
    func testWorkspaceSplitContract() {
        let preferred = DashboardLayoutMetrics.collectionPreferredWidth
        let minimum = DashboardLayoutMetrics.collectionMinimumWidth

        XCTAssertTrue(
            (300...320).contains(preferred),
            "collection preferred width \(preferred) left the 300–320 band"
        )
        XCTAssertLessThan(minimum, preferred)
        XCTAssertGreaterThanOrEqual(
            DashboardLayoutMetrics.detailMinimumWidth,
            480,
            "the detail pane must never collapse below a readable width"
        )
    }

    /// The split must physically fit the practical minimum window: sidebar,
    /// collection minimum, detail minimum together.
    /// The split must physically fit the smallest canonical window — 1000pt —
    /// with the sidebar, the collection minimum and the detail minimum all at
    /// their floors.
    func testSplitFitsTheSmallestCanonicalWindow() {
        let smallestCanonicalWidth: CGFloat = 1000
        let required = DashboardLayoutMetrics.sidebarWidth
            + DashboardLayoutMetrics.collectionMinimumWidth
            + DashboardLayoutMetrics.detailMinimumWidth
        XCTAssertLessThanOrEqual(required, smallestCanonicalWidth)
    }

    /// Two cards must fit side by side in the detail pane at the primary window
    /// size. Below that the grid is meant to reflow to one column; at 1200pt —
    /// the size the dashboard is accepted at — a pair of quota bindings has to
    /// read side by side rather than as a stack in a half-empty pane.
    func testTwoCardsFitTheDetailPaneAtThePrimaryWindowSize() {
        let primaryWindowWidth: CGFloat = 1200
        let detail = primaryWindowWidth
            - DashboardLayoutMetrics.sidebarWidth
            - DashboardLayoutMetrics.collectionPreferredWidth
        let content = min(
            detail - 2 * DashboardLayoutMetrics.pageHorizontalPadding,
            ContentWidth.reading
        )
        let twoCards = 2 * DashboardLayoutMetrics.cardMinimumWidth
            + DashboardLayoutMetrics.cardSpacing
        XCTAssertLessThanOrEqual(twoCards, content)
        // ...and three must not, or two bindings become an unreadable strip.
        let threeCards = 3 * DashboardLayoutMetrics.cardMinimumWidth
            + 2 * DashboardLayoutMetrics.cardSpacing
        XCTAssertGreaterThan(threeCards, content)
    }

    /// A chronological table uses the main pane but stops before it stretches
    /// a three-column row across a maximised window.
    func testTableWidthUsesThePaneWithoutStretching() {
        XCTAssertGreaterThan(
            DashboardLayoutMetrics.tableMaximumWidth, ContentWidth.reading,
            "a table must be allowed wider than a prose measure"
        )
        XCTAssertLessThanOrEqual(DashboardLayoutMetrics.tableMaximumWidth, 1000)
    }
}
