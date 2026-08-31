import Foundation

/// Presentation-layer localization.
///
/// Raw protocol enums (`EXACT`, `DISABLED_BY_DESIGN`, `EXHAUSTED_OBSERVED`, ...)
/// are preserved verbatim everywhere; this layer only maps them into localized
/// human-readable labels for display.
///
/// Language selection follows the macOS user's preferred languages
/// (`Locale.preferredLanguages`) matched against the bundled `.lproj` catalogs,
/// with English as the fallback. Selection is explicit because CFBundle's
/// automatic preferred-localization matching is unreliable for SwiftPM
/// library resource bundles launched from a plain executable.
public enum L10n {
    /// Localization keys used by the catalogs; exposed for coverage tests.
    public static let requiredKeys: [String] = [
        "status.healthy", "status.working", "status.blocked", "status.quotaLimited",
        "status.disconnected", "connection.connected", "reason.daemonNotRunning",
        "reason.socketInvalid", "reason.socketPathTooLong", "reason.accessDenied",
        "reason.apiVersionMismatch", "reason.malformedResponse", "reason.transportFailure",
        "section.recentTasks", "section.noTasks", "section.quickSubmit",
        "section.providersQuota", "section.noProviders", "section.productionActive",
        "action.submit", "action.cancel", "action.refresh", "action.quit",
        "submit.placeholder", "submit.authoritativeId", "notice.submitted",
        "notice.duplicateBlocked", "notice.submitFailed", "notice.submitMalformed",
        "notice.cancelled", "notice.alreadyCancelled", "notice.runningConflict",
        "notice.cancelFailed", "notice.cancelMalformed", "quota.unknown",
        "quota.unknownConfidence", "quota.confidence", "quota.source", "provider.target",
        "provider.accounts", "help.taskCounts", "help.cancel",
        "app.dashboard", "action.openDashboard", "dashboard.overview", "dashboard.tasks",
        "dashboard.agents", "dashboard.providers", "dashboard.quota", "dashboard.routing",
        "dashboard.verification", "dashboard.history", "dashboard.settings",
        "label.connection", "label.systemHealth", "label.blockers", "label.events",
        "label.search", "label.stateFilter", "label.allStates", "label.taskDetail",
        "label.routingExplanation", "label.actualExecutionTarget", "label.wouldSelect",
        "label.verification", "label.history", "label.daemonLifecycle", "label.socket",
        "label.runtimeConfig", "label.autoStartDaemon", "label.launchAtLogin",
        "empty.unsupported", "empty.selectTask",
    ]

    /// English is the guaranteed fallback (`defaultLocalization` in Package.swift).
    public static let fallbackLanguage = "en"

    // MARK: - Language selection

    /// Pure matching rule (unit tested): first preferred language that hits an
    /// available catalog wins; exact canonical match, then script form, then
    /// bare language code; English otherwise.
    public static func selectLanguage(available: [String], preferred: [String]) -> String {
        let lowered = Set(available.map { $0.lowercased() })
        for raw in preferred {
            let candidate = raw.lowercased()
            if lowered.contains(candidate) { return matchCase(of: candidate, in: available) }
            let scriptForm = candidate.split(separator: "-").prefix(2).joined(separator: "-")
            if lowered.contains(scriptForm) { return matchCase(of: scriptForm, in: available) }
            let bare = String(candidate.split(separator: "-").first ?? "")
            if !bare.isEmpty, let hit = lowered.first(where: { $0 == bare || $0.hasPrefix(bare + "-") }) {
                return matchCase(of: hit, in: available)
            }
        }
        return matchCase(of: fallbackLanguage, in: available.isEmpty ? [fallbackLanguage] : available)
    }

    private static func matchCase(of lowered: String, in available: [String]) -> String {
        available.first { $0.lowercased() == lowered } ?? fallbackLanguage
    }

    /// lproj directories discovered in the module's resource bundle, keyed
    /// case-insensitively (filesystems and SwiftPM processing may differ in case)
    /// while preserving the canonical name for reporting.
    private static let lprojDirectories: [String: (name: String, path: String)] = {
        guard let resourceURL = Bundle.module.resourceURL else { return [:] }
        let entries = (try? FileManager.default.contentsOfDirectory(
            at: resourceURL,
            includingPropertiesForKeys: nil
        )) ?? []
        var map: [String: (name: String, path: String)] = [:]
        for entry in entries where entry.pathExtension.lowercased() == "lproj" {
            let name = entry.deletingPathExtension().lastPathComponent
            map[name.lowercased()] = (name, entry.path)
        }
        return map
    }()

    /// Canonical catalog names bundled with the client.
    public static var availableLanguages: [String] {
        lprojDirectories.values.map(\.name).sorted()
    }

    /// The language actually resolved for this launch (acceptance evidence).
    public static var resolvedLanguageCode: String {
        selectLanguage(available: availableLanguages, preferred: Locale.preferredLanguages)
    }

    private static let cacheLock = NSLock()
    private static var cachedBundle: Bundle?

    /// The selected language is resolved once per process; switching the system
    /// language follows on the next launch (matching normal macOS app behavior).
    private static func currentBundle() -> Bundle {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        if let cachedBundle { return cachedBundle }
        let language = selectLanguage(available: availableLanguages,
                                      preferred: Locale.preferredLanguages)
        let bundle = languageBundle(for: language) ?? Bundle.module
        cachedBundle = bundle
        return bundle
    }

    private static func languageBundle(for language: String) -> Bundle? {
        guard let entry = lprojDirectories[language.lowercased()] else { return nil }
        return Bundle(path: entry.path)
    }

    private static func tr(_ key: String) -> String {
        currentBundle().localizedString(forKey: key, value: key, table: nil)
    }

    private static func tr(_ key: String, _ arguments: [CVarArg]) -> String {
        String(format: tr(key), locale: Locale.current, arguments: arguments)
    }

    /// Raw catalog lookup for a fixed language (tests); never used by the live UI.
    public static func catalogString(key: String, language: String) -> String? {
        guard let bundle = languageBundle(for: language) else { return nil }
        let value = bundle.localizedString(forKey: key, value: nil, table: nil)
        return value == key ? nil : value
    }

    // MARK: - Status summary

    public static func statusTitle(_ summary: StatusSummary) -> String {
        switch summary {
        case .disconnected: return tr("status.disconnected")
        case .blocked: return tr("status.blocked")
        case .quotaLimited: return tr("status.quotaLimited")
        case .working: return tr("status.working")
        case .healthy: return tr("status.healthy")
        }
    }

    public static var connectedLabel: String { tr("connection.connected") }

    public static func disconnectionReason(_ reason: ConnectionState.DisconnectionReason) -> String {
        switch reason {
        case .daemonNotRunning: return tr("reason.daemonNotRunning")
        case .socketInvalid: return tr("reason.socketInvalid")
        case .socketPathTooLong(let length): return tr("reason.socketPathTooLong", [length])
        case .accessDenied: return tr("reason.accessDenied")
        case .apiVersionMismatch(let version): return tr("reason.apiVersionMismatch", [version])
        case .malformedResponse: return tr("reason.malformedResponse")
        case .transportFailure: return tr("reason.transportFailure")
        }
    }

    // MARK: - Sections / actions

    public static var recentTasks: String { tr("section.recentTasks") }
    public static var noTasks: String { tr("section.noTasks") }
    public static var quickSubmit: String { tr("section.quickSubmit") }
    public static var providersQuota: String { tr("section.providersQuota") }
    public static var noProviders: String { tr("section.noProviders") }
    public static var productionActive: String { tr("section.productionActive") }
    public static var submit: String { tr("action.submit") }
    public static var cancel: String { tr("action.cancel") }
    public static var refresh: String { tr("action.refresh") }
    public static var quit: String { tr("action.quit") }
    public static var submitPlaceholder: String { tr("submit.placeholder") }
    public static var taskCountsHelp: String { tr("help.taskCounts") }
    public static var cancelHelp: String { tr("help.cancel") }
    public static var dashboardTitle: String { tr("app.dashboard") }
    public static var openDashboard: String { tr("action.openDashboard") }
    public static var connectionLabel: String { tr("label.connection") }
    public static var systemHealth: String { tr("label.systemHealth") }
    public static var blockers: String { tr("label.blockers") }
    public static var events: String { tr("label.events") }
    public static var search: String { tr("label.search") }
    public static var stateFilter: String { tr("label.stateFilter") }
    public static var allStates: String { tr("label.allStates") }
    public static var taskDetail: String { tr("label.taskDetail") }
    public static var routingExplanation: String { tr("label.routingExplanation") }
    public static var actualExecutionTarget: String { tr("label.actualExecutionTarget") }
    public static var wouldSelect: String { tr("label.wouldSelect") }
    public static var verification: String { tr("label.verification") }
    public static var history: String { tr("label.history") }
    public static var daemonLifecycle: String { tr("label.daemonLifecycle") }
    public static var socket: String { tr("label.socket") }
    public static var runtimeConfig: String { tr("label.runtimeConfig") }
    public static var autoStartDaemon: String { tr("label.autoStartDaemon") }
    public static var launchAtLogin: String { tr("label.launchAtLogin") }
    public static var unsupportedEmptyState: String { tr("empty.unsupported") }
    public static var selectTaskEmptyState: String { tr("empty.selectTask") }

    public static func dashboardSection(_ rawValue: String) -> String {
        tr("dashboard.\(rawValue)")
    }

    public static func authoritativeTaskId(_ taskId: String) -> String {
        tr("submit.authoritativeId", [taskId])
    }

    // MARK: - Operation notices (structured, daemon values stay verbatim)

    public static func submitNotice(_ notice: SubmitNotice) -> String {
        switch notice {
        case .submitted(let taskId, let state):
            return tr("notice.submitted", [taskId, state])
        case .duplicateBlocked(let windowSeconds):
            return tr("notice.duplicateBlocked", [windowSeconds])
        case .failed(let detail):
            return tr("notice.submitFailed", [detail])
        case .malformedResponse:
            return tr("notice.submitMalformed")
        }
    }

    public static func cancelNotice(_ notice: CancelNotice) -> String {
        switch notice {
        case .cancelled(let taskId):
            return tr("notice.cancelled", [taskId])
        case .alreadyCancelled(let taskId):
            return tr("notice.alreadyCancelled", [taskId])
        case .runningConflict(let taskId):
            return tr("notice.runningConflict", [taskId])
        case .failed(let detail):
            return tr("notice.cancelFailed", [detail])
        case .malformedResponse:
            return tr("notice.cancelMalformed")
        }
    }

    // MARK: - Quota rendering (confidence semantics preserved; enums stay raw)

    public static func quotaRemaining(fraction: Double?, confidence: String) -> String {
        guard let fraction else { return tr("quota.unknown") }
        if confidence == "EXACT" {
            return String(format: "%.1f%%", fraction * 100)
        }
        return tr("quota.unknownConfidence", [confidence])
    }

    public static var quotaConfidenceLabel: String { tr("quota.confidence") }
    public static var quotaSourceLabel: String { tr("quota.source") }
    public static var providerTargetLabel: String { tr("provider.target") }

    public static func accountsCount(_ count: Int) -> String {
        tr("provider.accounts", [count])
    }
}
