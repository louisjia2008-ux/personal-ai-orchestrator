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
        // Observed exact/estimated quota may show a percentage; UNKNOWN may not.
        let exact = L10n.quotaRemaining(fraction: 0.8123, confidence: "EXACT")
        XCTAssertTrue(exact.hasSuffix("%"))
        let estimated = L10n.quotaRemaining(fraction: 0.8, confidence: "ESTIMATED")
        XCTAssertTrue(estimated.hasSuffix("%"))
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

    // MARK: - Task Detail parity (B1)

    /// Task Detail was the one screen still rendering Swift string literals while
    /// the rest of the app was fully bilingual. These keys pin it.
    func testTaskDetailStringsExistInBothLanguages() {
        let keys = [
            "detail.panel.summary", "detail.panel.progress", "detail.panel.liveActivity",
            "detail.panel.changes", "detail.panel.verification",
            "phase.preparing", "phase.routing", "phase.workspace", "phase.quota",
            "phase.startingWorker", "phase.editing", "phase.testing",
            "phase.verifying", "phase.finished",
            "candidate.selected", "candidate.eligible", "candidate.ineligible",
            "candidate.unknownModel",
            "value.none", "value.unknown", "value.automatic", "value.notVerifiedYet",
            "value.stagePassed", "value.stageFailed", "value.notShadowMode",
            "label.task", "label.updatedRelative",
        ]
        for key in keys {
            for language in ["en", "zh-Hans"] {
                XCTAssertNotNil(
                    L10n.catalogString(key: key, language: language),
                    "missing \(language) entry for \(key)"
                )
            }
        }
    }

    func testTaskDetailChineseIsActuallyTranslated() {
        // A zh-Hans catalog that merely echoes English would satisfy a presence
        // check while leaving the screen in English.
        let pairs: [(String, String, String)] = [
            ("detail.panel.summary", "Summary", "摘要"),
            ("detail.panel.progress", "Progress", "进度"),
            ("detail.panel.changes", "Changes", "变更"),
            ("phase.preparing", "Preparing", "准备中"),
            ("phase.verifying", "Verifying", "验证中"),
            ("candidate.selected", "Selected", "已选"),
            ("value.stagePassed", "passed", "通过"),
            ("value.stageFailed", "failed", "未通过"),
        ]
        for (key, english, chinese) in pairs {
            XCTAssertEqual(L10n.catalogString(key: key, language: "en"), english, key)
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    /// D3: parity is a standing constraint, not a one-off cleanup. Any key added
    /// to one catalog without the other fails here.
    func testCatalogsHaveIdenticalKeySets() {
        let en = L10n.catalogKeys(language: "en")
        let zh = L10n.catalogKeys(language: "zh-Hans")
        XCTAssertFalse(en.isEmpty)
        XCTAssertEqual(
            en.symmetricDifference(zh), [],
            "catalogs drifted: \(en.symmetricDifference(zh).sorted())"
        )
    }

    /// Provider, protocol and identifier names are not translated. Translating
    /// them would change what the owner is looking at.
    func testProperNamesAreNotTranslated() {
        let properNames = ["Codex", "Claude Code", "MiniMax", "GLM", "ACP", "SHA"]
        for language in ["en", "zh-Hans"] {
            for name in properNames {
                XCTAssertNil(L10n.catalogString(key: name, language: language))
            }
        }
    }
}
