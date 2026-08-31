import Foundation

import XCTest

@testable import PAOControlKit

/// Localization coverage: catalogs must exist for both languages, keys must be total,
/// and raw protocol enums must stay untranslated (only presentation labels localize).
final class LocalizationTests: XCTestCase {
    func testEveryRequiredKeyExistsInBothLanguages() {
        for key in L10n.requiredKeys {
            XCTAssertNotNil(
                L10n.catalogString(key: key, language: "en"),
                "missing en entry for \(key)"
            )
            XCTAssertNotNil(
                L10n.catalogString(key: key, language: "zh-Hans"),
                "missing zh-Hans entry for \(key)"
            )
        }
    }

    func testZhHansPresentationDiffersFromEnglishFallback() {
        let pairs: [(String, String, String)] = [
            ("status.healthy", "Healthy", "正常"),
            ("status.working", "Working", "运行中"),
            ("status.blocked", "Blocked", "已阻塞"),
            ("status.quotaLimited", "Quota limited", "额度受限"),
            ("status.disconnected", "Disconnected", "未连接"),
            ("connection.connected", "connected", "已连接"),
            ("reason.daemonNotRunning", "daemon not running", "守护进程未运行"),
            ("section.recentTasks", "Recent tasks", "最近任务"),
            ("section.noTasks", "No tasks", "暂无任务"),
            ("section.quickSubmit", "Quick submit", "快速提交"),
            ("section.providersQuota", "Providers / quota", "提供商 / 额度"),
            ("section.noProviders", "No providers registered", "尚未注册提供商"),
            ("section.productionActive", "Production ACTIVE", "生产 ACTIVE"),
            ("action.submit", "Submit", "提交"),
            ("action.cancel", "Cancel", "取消"),
            ("action.refresh", "Refresh", "刷新"),
            ("action.quit", "Quit", "退出"),
        ]
        for (key, english, chinese) in pairs {
            XCTAssertEqual(L10n.catalogString(key: key, language: "en"), english, key)
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    func testRawProtocolEnumsAreNotTranslatedInCatalogs() {
        // Machine values stay verbatim in every catalog: localizing them would
        // change protocol semantics.
        let rawEnums = ["EXACT", "ESTIMATED", "UNKNOWN", "DISABLED_BY_DESIGN",
                        "EXHAUSTED_OBSERVED", "COOLDOWN", "RUNNING", "SUBMITTED"]
        for language in ["en", "zh-Hans"] {
            for value in rawEnums {
                XCTAssertNil(L10n.catalogString(key: value, language: language))
            }
        }
    }

    func testQuotaRenderingPreservesConfidenceSemanticsWhenLocalized() {
        // The localized renderer still refuses percentages unless EXACT.
        let exact = L10n.quotaRemaining(fraction: 0.8123, confidence: "EXACT")
        XCTAssertTrue(exact.hasSuffix("%"))
        let estimated = L10n.quotaRemaining(fraction: 0.8, confidence: "ESTIMATED")
        XCTAssertTrue(estimated.contains("ESTIMATED"))
        XCTAssertFalse(estimated.contains("%"))
        let unknown = L10n.quotaRemaining(fraction: nil, confidence: "UNKNOWN")
        XCTAssertFalse(unknown.contains("%"))
    }

    func testResolvedLanguageIsARealLocalization() {
        let supported = ["en", "zh-Hans"]
        XCTAssertTrue(supported.contains(L10n.resolvedLanguageCode))
    }

    func testLanguageSelectionFollowsPreferredLanguagesWithEnglishFallback() {
        let available = ["en", "zh-Hans"]
        // Real-world Chinese macOS preference list resolves to zh-Hans.
        XCTAssertEqual(
            L10n.selectLanguage(available: available, preferred: ["zh-Hans-CN", "en-CN"]),
            "zh-Hans"
        )
        XCTAssertEqual(
            L10n.selectLanguage(available: available, preferred: ["zh-Hans"]),
            "zh-Hans"
        )
        XCTAssertEqual(L10n.selectLanguage(available: available, preferred: ["zh"]), "zh-Hans")
        // English users and unknown languages fall back to English.
        XCTAssertEqual(L10n.selectLanguage(available: available, preferred: ["en-CN"]), "en")
        XCTAssertEqual(L10n.selectLanguage(available: available, preferred: ["fr-FR", "en-CN"]), "en")
        XCTAssertEqual(L10n.selectLanguage(available: available, preferred: []), "en")
        XCTAssertEqual(L10n.selectLanguage(available: [], preferred: ["fr-FR"]), "en")
    }
}
