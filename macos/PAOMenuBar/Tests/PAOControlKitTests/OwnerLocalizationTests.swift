import Foundation

import XCTest

@testable import PAOControlKit

/// P4.2.6.4 localization regression contract.
///
/// The owner launched the product and found the Dashboard still partly English
/// under a zh-Hans locale. These tests pin the surfaces that regressed, and add
/// a source lint so a newly introduced `Text("Some English")` in the main
/// dashboard views fails the build instead of shipping.
final class OwnerLocalizationTests: XCTestCase {

    // MARK: - Catalog coverage

    /// Every owner-facing key must exist in both catalogs. A key present only in
    /// English silently falls back and reintroduces the exact defect.
    func testSweptKeysExistInBothCatalogs() {
        for key in Self.sweptKeys {
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

    /// zh-Hans must actually differ from English for owner-facing product text.
    /// A catalog that copies the English string is a silent regression.
    func testZhHansIsRealChineseNotAnEnglishCopy() {
        for key in Self.sweptKeys {
            guard
                let english = L10n.catalogString(key: key, language: "en"),
                let chinese = L10n.catalogString(key: key, language: "zh-Hans")
            else {
                continue
            }
            XCTAssertNotEqual(english, chinese, "zh-Hans must not copy English for \(key)")
            XCTAssertTrue(
                chinese.contains(where: Self.isCJK),
                "zh-Hans value for \(key) has no Chinese characters: \(chinese)"
            )
        }
    }

    /// Format specifiers must survive translation, or `String(format:)` produces
    /// wrong or truncated owner-facing text.
    func testFormatSpecifiersMatchAcrossLanguages() {
        for key in Self.sweptKeys {
            guard
                let english = L10n.catalogString(key: key, language: "en"),
                let chinese = L10n.catalogString(key: key, language: "zh-Hans")
            else {
                continue
            }
            XCTAssertEqual(
                Self.specifiers(in: english),
                Self.specifiers(in: chinese),
                "format specifiers diverge for \(key)"
            )
        }
    }

    // MARK: - Sidebar groups (§3)

    func testSidebarGroupsAreLocalized() {
        let expected: [(String, String, String)] = [
            ("sidebar.group.work", "Work", "工作"),
            ("sidebar.group.aiResources", "AI Resources", "AI 资源"),
            ("sidebar.group.execution", "Execution", "执行"),
            ("sidebar.group.system", "System", "系统"),
        ]
        for (key, english, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "en"), english, key)
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    /// The five first-level destinations, and only those, title the sidebar.
    func testSidebarDestinationTitlesAreLocalized() {
        let expected = [
            "overview": "总览",
            "tasks": "任务",
            "resources": "资源",
            "activity": "活动",
            "settings": "设置",
        ]
        XCTAssertEqual(Set(expected.keys), Set(DashboardSection.allCases.map(\.rawValue)))
        for (raw, chinese) in expected {
            XCTAssertEqual(
                L10n.catalogString(key: "dashboard.\(raw)", language: "zh-Hans"),
                chinese,
                raw
            )
            XCTAssertEqual(L10n.dashboardSection(raw).isEmpty, false, raw)
        }
    }

    /// Titles for surfaces that stopped being destinations but still title a tab
    /// or a panel inside one. They must stay localized, or the relocation would
    /// quietly reintroduce English inside Resources / Settings / Task Detail.
    func testRelocatedSurfaceTitlesStayLocalized() {
        let expected = [
            "dashboard.projects": "项目",
            "dashboard.providers": "模型与服务",
            "dashboard.agents": "执行目标",
            "dashboard.quota": "额度",
            "dashboard.routing": "调度决策",
            "dashboard.verification": "验证",
            "dashboard.history": "历史",
        ]
        for (key, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    /// The grouped destinations introduce owner-facing pickers; both catalogs
    /// must carry them in the same change.
    func testGroupedDestinationPickersAreLocalized() {
        let expected: [(String, String, String)] = [
            ("resources.pickerTitle", "Resource view", "资源视图"),
            ("settings.pickerTitle", "Settings view", "设置视图"),
            ("settings.tab.general", "General", "通用"),
        ]
        for (key, english, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "en"), english, key)
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    // MARK: - Overview (§4)

    func testOverviewCardsAreLocalized() {
        let expected: [(String, String)] = [
            ("overview.basicInfo", "基本信息"),
            ("overview.connection", "连接状态"),
            ("overview.projects", "项目"),
            ("overview.providers", "提供商"),
            ("overview.runnableTargets", "可执行模型"),
            ("overview.runningTasks", "运行中任务"),
            ("overview.tasksToday", "今日任务"),
            ("overview.routingToday", "今日调度"),
            ("overview.quotaWarnings", "额度警告"),
            ("overview.taskTrend", "任务趋势"),
            ("overview.taskStates", "任务状态"),
            ("overview.risks", "风险提醒"),
            ("overview.recentActivity", "最近活动"),
        ]
        for (key, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    func testOverviewEmptyStatesAreLocalized() {
        XCTAssertEqual(
            L10n.catalogString(key: "overview.taskTrend.empty", language: "zh-Hans"),
            "正在收集历史任务数据。"
        )
        XCTAssertEqual(
            L10n.catalogString(key: "overview.taskStates.empty", language: "zh-Hans"),
            "暂无任务状态数据。"
        )
    }

    // MARK: - Risks localize from raw_code, not the daemon's English

    func testRiskTextLocalizesFromRawCodeAndCount() {
        // Keep the daemon fallback deliberately different from the English
        // catalog. On an English CI host the real localized sentence may be
        // identical to the daemon's normal English wording, which cannot prove
        // that raw_code localization rather than fallback was used.
        let risk = RiskItemView(
            title: "daemon fallback title",
            detail: "daemon fallback detail",
            severity: "UNKNOWN",
            destination: "quota",
            rawCode: "QUOTA_UNKNOWN",
            count: 3
        )
        let title = L10n.riskTitle(rawCode: risk.rawCode, count: risk.count, fallback: risk.title)
        XCTAssertTrue(title.contains("3"), "the count must survive localization: \(title)")
        XCTAssertNotEqual(title, risk.title)
        XCTAssertNotEqual(
            L10n.riskDetail(rawCode: risk.rawCode, fallback: risk.detail),
            risk.detail
        )
    }

    /// A newer daemon may emit a risk code this build does not know. It must
    /// render the daemon's own sentence rather than a wrong or empty label.
    func testUnknownRiskCodeFallsBackToDaemonText() {
        XCTAssertEqual(
            L10n.riskTitle(rawCode: "SOME_FUTURE_CODE", count: 2, fallback: "future risk"),
            "future risk"
        )
        XCTAssertEqual(L10n.riskTitle(rawCode: nil, count: nil, fallback: "raw"), "raw")
    }

    // MARK: - Provider state labels (§2)

    func testProviderStateLabelsComeFromTheCatalogNotHardCodedSwift() {
        // Before P4.2.6.4 these were hard-coded Chinese in DashboardView, so an
        // English user saw Chinese. Both catalogs must now answer.
        XCTAssertEqual(L10n.catalogString(key: "auth.authenticated", language: "en"), "Signed in")
        XCTAssertEqual(
            L10n.catalogString(key: "auth.authenticated", language: "zh-Hans"),
            "已确认登录"
        )
        XCTAssertEqual(
            L10n.catalogString(key: "connectionState.connected", language: "en"),
            "Connected"
        )
        XCTAssertEqual(
            L10n.catalogString(key: "runtimeState.unavailable", language: "zh-Hans"),
            "当前运行环境不可用"
        )
    }

    /// Raw protocol enums must pass through untouched when unrecognized.
    func testUnknownProviderStateEnumsStayVerbatim() {
        XCTAssertEqual(L10n.authStateLabel("SOME_NEW_STATE"), "SOME_NEW_STATE")
        XCTAssertEqual(L10n.connectionStateLabel("SOME_NEW_STATE"), "SOME_NEW_STATE")
        XCTAssertEqual(L10n.runtimeStateLabel("SOME_NEW_STATE"), "SOME_NEW_STATE")
    }

    // MARK: - Quota page (§7-§13)

    func testQuotaPageStringsAreLocalized() {
        let expected: [(String, String)] = [
            ("quota.refresh", "刷新额度"),
            ("quota.empty.title", "尚未连接可查看额度的提供商"),
            ("quota.noReliableData", "暂无可靠额度数据"),
            ("quota.neverChecked", "尚未检查"),
            ("quota.summary.connected", "已连接提供商"),
            ("quota.summary.observable", "可观测额度"),
            ("quota.summary.warnings", "额度警告"),
            ("quota.summary.exhausted", "额度已耗尽"),
            ("quota.estimatedBadge", "估算"),
        ]
        for (key, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    /// "Refresh Quota" and "Refresh Providers" are different operations and must
    /// never collapse into one owner-facing label.
    func testQuotaRefreshLabelIsDistinctFromProviderRefresh() {
        for language in ["en", "zh-Hans"] {
            let quota = L10n.catalogString(key: "quota.refresh", language: language)
            let providers = L10n.catalogString(key: "action.refreshProviders", language: language)
            XCTAssertNotNil(quota)
            XCTAssertNotNil(providers)
            XCTAssertNotEqual(quota, providers, "labels must differ in \(language)")
        }
    }

    /// The connected-but-unknown empty state must not reuse the
    /// "no providers registered" sentence — they are different truths.
    func testQuotaEmptyStateIsNotTheNoProvidersRegisteredSentence() {
        for language in ["en", "zh-Hans"] {
            XCTAssertNotEqual(
                L10n.catalogString(key: "quota.empty.title", language: language),
                L10n.catalogString(key: "section.noProviders", language: language)
            )
        }
    }

    func testQuotaFailureReasonsAreLocalizedAndFallBackSafely() {
        XCTAssertEqual(
            L10n.catalogString(key: "quota.reason.noReadonlySource", language: "zh-Hans"),
            "当前提供商没有可用的只读额度来源。"
        )
        // An unrecognized sanitized code still renders a truthful sentence
        // rather than leaking the raw code as the primary message.
        let generic = L10n.quotaFailureReason("SOME_NEW_CODE")
        XCTAssertFalse(generic.isEmpty)
        XCTAssertFalse(generic.contains("SOME_NEW_CODE"))
    }

    // MARK: - Projects / Providers (§2)

    func testProjectsAndProvidersStringsAreLocalized() {
        let expected: [(String, String)] = [
            ("projects.add", "添加项目"),
            ("projects.detectedRepository", "已识别的代码仓库"),
            ("projects.register", "注册"),
            ("projects.empty", "尚未注册任何项目。"),
            ("projects.revealInFinder", "在访达中显示"),
            ("projects.remove", "从编排器移除"),
            ("projects.gitRoot", "Git 根目录"),
            ("projects.lastUsed", "最近使用"),
            ("common.never", "从未"),
            ("providers.notConnected", "未连接"),
            ("providers.providerId", "提供商 ID"),
            ("providers.credentialReference", "凭据来源"),
            ("common.advancedDetails", "高级详情"),
            ("models.ready", "可用"),
            ("models.needsVerification", "待验证"),
        ]
        for (key, chinese) in expected {
            XCTAssertEqual(L10n.catalogString(key: key, language: "zh-Hans"), chinese, key)
        }
    }

    // MARK: - Source lint (§5)

    /// Flags newly introduced owner-facing English literals in the main
    /// dashboard views.
    ///
    /// Deliberately narrow to stay useful: it inspects only the SwiftUI
    /// constructors that render owner-visible copy, and only flags a literal
    /// that reads like an English sentence (two or more words, or a capitalized
    /// word that is not a protocol constant). SF Symbol names, raw protocol
    /// enums, and identifiers are never flagged.
    func testMainDashboardViewsIntroduceNoHardCodedOwnerEnglish() throws {
        var offenders: [String] = []
        for url in try Self.dashboardSourceFiles() {
            let source = try String(contentsOf: url, encoding: .utf8)
            for (index, line) in source.components(separatedBy: .newlines).enumerated() {
                for literal in Self.ownerFacingLiterals(in: line) where Self.looksLikeEnglishCopy(literal) {
                    offenders.append("\(url.lastPathComponent):\(index + 1): \"\(literal)\"")
                }
            }
        }
        XCTAssertTrue(
            offenders.isEmpty,
            "owner-facing English literals must move into the localization catalogs:\n"
                + offenders.joined(separator: "\n")
        )
    }

    /// The lint must actually catch the defect it exists to prevent.
    func testSourceLintDetectsAReintroducedEnglishLiteral() {
        let reintroduced = #"                DisclosureGroup("Advanced Details") {"#
        let literals = Self.ownerFacingLiterals(in: reintroduced)
        XCTAssertEqual(literals, ["Advanced Details"])
        XCTAssertTrue(Self.looksLikeEnglishCopy("Advanced Details"))
        XCTAssertTrue(Self.looksLikeEnglishCopy("No registered projects."))
    }

    /// ...and must not fire on the things it deliberately tolerates.
    func testSourceLintDoesNotFlagSymbolsEnumsOrIdentifiers() {
        for tolerated in [
            "chart.pie", "arrow.clockwise", "exclamationmark.triangle",  // SF Symbols
            "EXACT", "ESTIMATED", "UNKNOWN", "EXHAUSTED_OBSERVED",       // protocol enums
            "BALANCED", "QUOTA_SAVER", "PROVIDER_EXACT",
            "provider_id", "execution_target_id", "raw_code",            // wire keys
            "", " ", "%@", "%d",
        ] {
            XCTAssertFalse(
                Self.looksLikeEnglishCopy(tolerated),
                "lint must not flag \(tolerated)"
            )
        }
    }

    // MARK: - Helpers

    private static func isCJK(_ character: Character) -> Bool {
        character.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }
    }

    private static func specifiers(in value: String) -> [String] {
        var found: [String] = []
        var iterator = Array(value)
        var index = 0
        while index < iterator.count {
            if iterator[index] == "%", index + 1 < iterator.count {
                var token = "%"
                var cursor = index + 1
                while cursor < iterator.count, "0123456789$.".contains(iterator[cursor]) {
                    token.append(iterator[cursor])
                    cursor += 1
                }
                if cursor < iterator.count {
                    token.append(iterator[cursor])
                    found.append(token)
                    index = cursor
                }
            }
            index += 1
        }
        return found.sorted()
    }

    /// SwiftUI constructors that put a bare string in front of the owner.
    private static let ownerFacingConstructors = [
        "Text(", "Label(", "Section(", "Button(", "Toggle(", "Picker(",
        "LabeledContent(", "DisclosureGroup(", "EmptyChartState(", "DashboardCard(",
        "EmptyStateView(", "BasicInfoCell(", "StatusBadge(",
    ]

    static func ownerFacingLiterals(in line: String) -> [String] {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.hasPrefix("//"), !trimmed.hasPrefix("///") else { return [] }
        var results: [String] = []
        for constructor in ownerFacingConstructors {
            var searchRange = line.startIndex..<line.endIndex
            while let hit = line.range(of: constructor, range: searchRange) {
                searchRange = hit.upperBound..<line.endIndex
                // Only a literal immediately following the paren counts; a
                // computed argument (L10n.x, a variable) is fine.
                let rest = line[hit.upperBound...]
                let afterLabel = rest.drop(while: { $0 == " " })
                guard afterLabel.first == "\"" else { continue }
                var literal = ""
                var iterator = afterLabel.dropFirst().makeIterator()
                var escaped = false
                var interpolated = false
                while let character = iterator.next() {
                    if escaped {
                        // Interpolated values are runtime data, not copy.
                        if character == "(" { interpolated = true }
                        escaped = false
                        literal.append(character)
                        continue
                    }
                    if character == "\\" { escaped = true; continue }
                    if character == "\"" { break }
                    literal.append(character)
                }
                if !interpolated { results.append(literal) }
            }
        }
        return results
    }

    static func looksLikeEnglishCopy(_ literal: String) -> Bool {
        let trimmed = literal.trimmingCharacters(in: .whitespaces)
        guard trimmed.count > 2 else { return false }
        // SF Symbol names and dotted identifiers.
        if trimmed.contains(".") && !trimmed.contains(" ") { return false }
        // Raw protocol enums and wire keys.
        if trimmed == trimmed.uppercased() && !trimmed.contains(" ") { return false }
        if trimmed.contains("_") { return false }
        if trimmed.hasPrefix("%") { return false }
        // Anything with Chinese is already localized copy (or a test fixture).
        if trimmed.contains(where: isCJK) { return false }
        let words = trimmed.split(separator: " ")
        guard let first = words.first else { return false }
        let startsCapitalized = first.first.map { $0.isUppercase } == true
        return words.count >= 2 || startsCapitalized
    }

    private static func dashboardSourceFiles() throws -> [URL] {
        // #filePath is Tests/PAOControlKitTests/OwnerLocalizationTests.swift
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // PAOControlKitTests
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // PAOMenuBar
        let views = root.appendingPathComponent("Sources/PAOMenuBar")
        let files = try FileManager.default.contentsOfDirectory(
            at: views,
            includingPropertiesForKeys: nil
        )
        let swift = files.filter { $0.pathExtension == "swift" }
        XCTAssertFalse(swift.isEmpty, "dashboard sources must be discoverable at \(views.path)")
        return swift.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Owner-facing copy introduced by the B2 navigation architecture. Listed
    /// explicitly rather than by prefix: `settings.activeGate.*` are raw gate
    /// states that must stay verbatim in both catalogs.
    private static let b2NavigationKeys: Set<String> = [
        "dashboard.resources",
        "dashboard.activity",
        "resources.pickerTitle",
        "settings.pickerTitle",
        "settings.tab.general",
    ]

    /// Keys introduced or repaired by the P4.2.6.4 sweep.
    private static let sweptKeys: [String] = L10n.requiredKeys.filter { key in
        key.hasPrefix("sidebar.group.")
            || key.hasPrefix("overview.")
            || key.hasPrefix("projects.")
            || key.hasPrefix("providers.")
            || key.hasPrefix("models.")
            || key.hasPrefix("quota.")
            || OwnerLocalizationTests.b2NavigationKeys.contains(key)
            || key.hasPrefix("risk.")
            || key.hasPrefix("auth.")
            || key.hasPrefix("connectionState.")
            || key.hasPrefix("runtimeState.")
            || key.hasPrefix("detail.")
            || key.hasPrefix("command.")
            || key.hasPrefix("common.")
            || key.hasPrefix("newTask.")
    }
}
