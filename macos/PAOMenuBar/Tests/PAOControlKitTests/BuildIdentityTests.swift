import XCTest
@testable import PAOControlKit

/// A stale dashboard is indistinguishable from a current one — every copy shares
/// a bundle identifier and version string — so these tests pin the only thing
/// that tells builds apart: the commit, and its honest absence.
final class BuildIdentityTests: XCTestCase {
    private let validSHA = "7421be25ae04e23d9b3f8897894a5438bd1509a2"

    // MARK: - Decoding

    func testDecodesIdentityFromInfoDictionary() {
        let identity = BuildIdentity.from(infoDictionary: [
            BuildIdentity.InfoKey.commit: validSHA,
            BuildIdentity.InfoKey.configuration: "Release",
            BuildIdentity.InfoKey.timestamp: "2026-09-02T10:00:00Z",
        ])

        XCTAssertTrue(identity.isKnown)
        XCTAssertEqual(identity.commitSHA, validSHA)
        XCTAssertEqual(identity.configuration, "Release")
        XCTAssertEqual(identity.builtAt, "2026-09-02T10:00:00Z")
    }

    func testShortSHAIsSevenCharacters() {
        let identity = BuildIdentity.from(infoDictionary: [
            BuildIdentity.InfoKey.commit: validSHA,
        ])

        XCTAssertEqual(identity.shortSHA, "7421be2")
    }

    func testUppercaseCommitIsNormalized() {
        let identity = BuildIdentity.from(infoDictionary: [
            BuildIdentity.InfoKey.commit: validSHA.uppercased(),
        ])

        XCTAssertEqual(identity.commitSHA, validSHA)
        XCTAssertEqual(identity.shortSHA, "7421be2")
    }

    // MARK: - Fail visible

    func testMissingIdentityIsUnknown() {
        XCTAssertEqual(BuildIdentity.from(infoDictionary: nil), .unknown)
        XCTAssertEqual(BuildIdentity.from(infoDictionary: [:]), .unknown)
    }

    /// An undefined build setting reaches Info.plist as its literal placeholder;
    /// that must read as "unresolved", never as a commit.
    func testUnexpandedBuildSettingIsUnknown() {
        let identity = BuildIdentity.from(infoDictionary: [
            BuildIdentity.InfoKey.commit: "$(PAO_BUILD_COMMIT)",
        ])

        XCTAssertFalse(identity.isKnown)
        XCTAssertEqual(identity.shortSHA, BuildIdentity.unknownValue)
    }

    func testMalformedCommitValuesAreUnknown() {
        for value in ["", "   ", "unknown", "7421be2", String(repeating: "z", count: 40)] {
            let identity = BuildIdentity.from(infoDictionary: [
                BuildIdentity.InfoKey.commit: value,
            ])
            XCTAssertFalse(identity.isKnown, "unexpectedly trusted \(value)")
            XCTAssertEqual(identity.shortSHA, BuildIdentity.unknownValue)
        }
    }

    // MARK: - App / daemon compatibility

    func testMatchingCommitsAreCompatible() {
        let app = BuildIdentity(commitSHA: validSHA, configuration: "Release", builtAt: "now")

        XCTAssertEqual(BuildCompatibility.compare(app: app, daemonCommitSHA: validSHA), .matched)
    }

    func testDifferingCommitsAreReportedAsMismatch() {
        let app = BuildIdentity(commitSHA: validSHA, configuration: "Release", builtAt: "now")
        let daemonSHA = "30a506b0000000000000000000000000000000ab"

        let compatibility = BuildCompatibility.compare(app: app, daemonCommitSHA: daemonSHA)

        XCTAssertEqual(compatibility, .mismatched(app: validSHA, daemon: daemonSHA))
        XCTAssertTrue(compatibility.isMismatch)
    }

    /// A daemon predating the build endpoint reports nothing; that is unknown,
    /// not agreement.
    func testAbsentDaemonIdentityIsIndeterminate() {
        let app = BuildIdentity(commitSHA: validSHA, configuration: "Release", builtAt: "now")

        let compatibility = BuildCompatibility.compare(app: app, daemonCommitSHA: nil)

        XCTAssertEqual(compatibility, .indeterminate)
        XCTAssertFalse(compatibility.isMismatch)
    }

    func testUnknownAppIdentityIsIndeterminate() {
        let compatibility = BuildCompatibility.compare(
            app: .unknown,
            daemonCommitSHA: validSHA
        )

        XCTAssertEqual(compatibility, .indeterminate)
    }

    func testMalformedDaemonIdentityIsIndeterminate() {
        let app = BuildIdentity(commitSHA: validSHA, configuration: "Release", builtAt: "now")

        XCTAssertEqual(
            BuildCompatibility.compare(app: app, daemonCommitSHA: "not-a-sha"),
            .indeterminate
        )
    }

    // MARK: - Wire decoding

    func testBuildViewDecodesDaemonPayload() throws {
        let json = """
        {
          "commit_sha": "\(validSHA)",
          "short_sha": "7421be2",
          "api_version": "v1",
          "configuration": "Release",
          "built_at": "2026-09-02T10:00:00Z"
        }
        """.data(using: .utf8)!

        let view = try JSONDecoder().decode(BuildView.self, from: json)

        XCTAssertEqual(view.commitSHA, validSHA)
        XCTAssertEqual(view.shortSHA, "7421be2")
        XCTAssertTrue(view.isCompatible)
    }
}

/// Contracts the P4.2.6 dashboard must keep, pinned so a stale binary can never
/// be mistaken for a regression in the source.
final class DashboardContractTests: XCTestCase {
    /// The standalone Agents destination was merged into Models & Providers.
    func testAgentsIsNotANormalSidebarItem() {
        XCTAssertFalse(DashboardSection.allCases.contains(.agents))
    }

    func testSidebarExposesProjectsAndProviders() {
        XCTAssertTrue(DashboardSection.allCases.contains(.projects))
        XCTAssertTrue(DashboardSection.allCases.contains(.providers))
    }

    func testSidebarOrderMatchesGroupedNavigation() {
        XCTAssertEqual(
            DashboardSection.allCases,
            [.overview, .projects, .tasks, .providers, .quota, .routing, .verification, .history, .settings]
        )
    }

    /// KPI tiles are localized; an English label in a Chinese catalog means the
    /// running binary predates the localization work.
    func testChineseKPILabels() {
        let expected = [
            "kpi.running": "运行中",
            "kpi.ready": "就绪",
            "kpi.blocked": "阻塞",
            "kpi.verification": "验证",
            "kpi.completed": "已完成",
        ]
        for (key, value) in expected {
            XCTAssertEqual(
                L10n.catalogString(key: key, language: "zh-Hans"),
                value,
                "zh-Hans catalog is missing \(key)"
            )
        }
    }

    func testVerificationDetailIsAComposedKPI() {
        XCTAssertEqual(
            L10n.catalogString(key: "kpi.verificationDetail", language: "zh-Hans"),
            "验证中 %d · 已验证 %d"
        )
    }

    func testBuildIdentityStringsAreLocalized() {
        XCTAssertEqual(L10n.catalogString(key: "build.title", language: "zh-Hans"), "构建信息")
        XCTAssertEqual(L10n.catalogString(key: "build.mismatchTitle", language: "zh-Hans"), "版本不一致")
        XCTAssertEqual(L10n.catalogString(key: "build.title", language: "en"), "Build Information")
    }
}
