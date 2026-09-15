import XCTest

@testable import PAOControlKit

final class DailyDriverLocalizationTests: XCTestCase {
    func testEveryDailyDriverKeyExistsInBothLanguages() {
        for key in DailyDriverL10n.requiredKeys {
            XCTAssertNotNil(
                DailyDriverL10n.catalogString(key: key, language: "en"),
                "missing en DailyDriver entry for \(key)"
            )
            XCTAssertNotNil(
                DailyDriverL10n.catalogString(key: key, language: "zh-Hans"),
                "missing zh-Hans DailyDriver entry for \(key)"
            )
        }
    }

    func testDailyDriverCatalogsHaveIdenticalKeySets() {
        let english = DailyDriverL10n.catalogKeys(language: "en")
        let chinese = DailyDriverL10n.catalogKeys(language: "zh-Hans")
        XCTAssertFalse(english.isEmpty)
        XCTAssertEqual(
            english.symmetricDifference(chinese),
            [],
            "DailyDriver catalogs drifted: \(english.symmetricDifference(chinese).sorted())"
        )
        XCTAssertEqual(english, Set(DailyDriverL10n.requiredKeys))
    }

    func testDailyDriverChineseIsActuallyTranslated() {
        let pairs: [(String, String, String)] = [
            ("home.ready", "Ready to work", "可以开始工作"),
            ("home.configureProjectAutomation", "Configure Project Automation…", "配置项目自动化…"),
            ("mode.supervisedAuto", "Supervised Auto", "受监督自动"),
            ("menu.stopSupervisedAuto", "Stop Supervised Auto", "停止受监督自动"),
            ("project.title", "Project Automation", "项目自动化"),
            ("auto.dispatchNow", "Dispatch Now", "立即派发"),
            ("task.running", "Running", "运行中"),
            ("resources.availableTargets", "Available targets", "可用执行目标")
        ]
        for (key, english, chinese) in pairs {
            XCTAssertEqual(DailyDriverL10n.catalogString(key: key, language: "en"), english, key)
            XCTAssertEqual(DailyDriverL10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    func testRawProtocolValuesRemainOutsideDailyDriverCatalog() {
        for language in ["en", "zh-Hans"] {
            for value in ["AUTO_PLANNED", "AUTO_GRACE", "SUPERVISED_AUTO", "ACTIVE", "EXACT"] {
                XCTAssertNil(DailyDriverL10n.catalogString(key: value, language: language), value)
            }
        }
    }
}
