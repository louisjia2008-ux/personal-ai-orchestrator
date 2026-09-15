import XCTest

@testable import PAOControlKit

/// Shared geometry contract for the five Daily Driver destinations.
///
/// These tests do not pretend to prove visual quality. They prevent the layout
/// tokens that every destination consumes from drifting back toward per-screen
/// magic numbers or the retired admin-dashboard density.
final class DashboardLayoutContractTests: XCTestCase {
    // MARK: Canonical navigation

    func testCanonicalNavigationRemainsFiveDestinationsInOrder() {
        XCTAssertEqual(
            DashboardSection.allCases.map(\.rawValue),
            ["overview", "tasks", "resources", "activity", "settings"]
        )
    }

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

    /// Daily Driver pages use a 24pt outer inset and 32pt separation between
    /// major product sections. Both remain on the app's 4pt rhythm without
    /// forcing every legacy component to adopt those larger values.
    func testDailyDriverPageRhythmIsSharedAndOnFourPointGrid() {
        XCTAssertEqual(
            DashboardLayoutMetrics.pageHorizontalPadding,
            DashboardLayoutMetrics.pageVerticalPadding
        )
        XCTAssertEqual(DashboardLayoutMetrics.pageHorizontalPadding, 24)
        XCTAssertEqual(DashboardLayoutMetrics.sectionSpacing, 32)
        XCTAssertEqual(Int(DashboardLayoutMetrics.pageHorizontalPadding) % 4, 0)
        XCTAssertEqual(Int(DashboardLayoutMetrics.sectionSpacing) % 4, 0)
    }

    func testSidebarWidthIsPinned() {
        XCTAssertTrue(
            (160...220).contains(DashboardLayoutMetrics.sidebarWidth),
            "sidebar width \(DashboardLayoutMetrics.sidebarWidth) left the 160–220 band"
        )
    }

    // MARK: Grouped surfaces

    /// The redesign deliberately uses a quieter but roomier grouped surface than
    /// the earlier 8pt-radius / 12pt-gap admin card wall.
    func testGroupedSurfaceGeometryIsStable() {
        XCTAssertEqual(DashboardLayoutMetrics.cardCornerRadius, 12)
        XCTAssertEqual(DashboardLayoutMetrics.cardSpacing, 16)
        XCTAssertEqual(DashboardLayoutMetrics.cardPadding, 16)
        XCTAssertEqual(Int(DashboardLayoutMetrics.cardCornerRadius) % 4, 0)
        XCTAssertEqual(Int(DashboardLayoutMetrics.cardSpacing) % 4, 0)
        XCTAssertEqual(Int(DashboardLayoutMetrics.cardPadding) % 4, 0)
    }

    func testHistoricalChartsRetainReadableHeight() {
        XCTAssertGreaterThanOrEqual(DashboardLayoutMetrics.chartHeight, 180)
    }

    // MARK: Collection → detail workspace

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

    /// The declared minimum window must be large enough to hold the preferred
    /// collection width and the detail floor, rather than advertising a size the
    /// split view can only satisfy by immediately crushing one column.
    func testDeclaredMinimumWindowContainsPreferredWorkspaceGeometry() {
        let required = DashboardLayoutMetrics.sidebarWidth
            + DashboardLayoutMetrics.collectionPreferredWidth
            + DashboardLayoutMetrics.detailMinimumWidth
        XCTAssertEqual(DashboardLayoutMetrics.minimumWindowWidth, required)
        XCTAssertTrue((1000...1100).contains(DashboardLayoutMetrics.minimumWindowWidth))
        XCTAssertGreaterThanOrEqual(DashboardLayoutMetrics.minimumWindowHeight, 600)
    }

    /// At the default 1180pt window, two ordinary cards still fit in the detail
    /// pane while a third does not. That keeps quota/resource groupings readable
    /// instead of becoming a row of tiny admin widgets.
    func testTwoCardsFitTheDetailPaneAtDefaultWindowSize() {
        let defaultWindowWidth: CGFloat = 1180
        let detail = defaultWindowWidth
            - DashboardLayoutMetrics.sidebarWidth
            - DashboardLayoutMetrics.collectionPreferredWidth
        let content = min(
            detail - 2 * DashboardLayoutMetrics.pageHorizontalPadding,
            ContentWidth.reading
        )
        let twoCards = 2 * DashboardLayoutMetrics.cardMinimumWidth
            + DashboardLayoutMetrics.cardSpacing
        XCTAssertLessThanOrEqual(twoCards, content)

        let threeCards = 3 * DashboardLayoutMetrics.cardMinimumWidth
            + 2 * DashboardLayoutMetrics.cardSpacing
        XCTAssertGreaterThan(threeCards, content)
    }

    // MARK: Reading measures

    func testActivityAndSettingsMeasuresStayPurposeSpecific() {
        XCTAssertGreaterThan(
            DashboardLayoutMetrics.tableMaximumWidth,
            ContentWidth.reading,
            "an activity/audit table may be wider than prose"
        )
        XCTAssertTrue((960...1100).contains(DashboardLayoutMetrics.tableMaximumWidth))
        XCTAssertTrue((680...800).contains(DashboardLayoutMetrics.formMaximumWidth))
        XCTAssertLessThan(DashboardLayoutMetrics.formMaximumWidth, DashboardLayoutMetrics.tableMaximumWidth)
    }
}
