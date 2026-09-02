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
        "notice.duplicateBlocked", "notice.projectRequired", "notice.manualTargetRequired",
        "notice.submitFailed", "notice.submitMalformed",
        "notice.cancelled", "notice.alreadyCancelled", "notice.runningConflict",
        "notice.cancelFailed", "notice.cancelMalformed", "quota.unknown",
        "quota.unknownConfidence", "quota.confidence", "quota.source", "provider.target",
        "provider.accounts", "help.taskCounts", "help.cancel",
        "app.dashboard", "action.openDashboard", "dashboard.overview", "dashboard.projects", "dashboard.tasks",
        "dashboard.agents", "dashboard.providers", "dashboard.quota", "dashboard.routing",
        "dashboard.verification", "dashboard.history", "dashboard.settings",
        "label.connection", "label.systemHealth", "label.blockers", "label.events",
        "label.search", "label.stateFilter", "label.allStates", "label.taskDetail",
        "label.routingExplanation", "label.actualExecutionTarget", "label.wouldSelect",
        "label.verification", "label.history", "label.daemonLifecycle", "label.socket",
        "label.runtimeConfig", "label.autoStartDaemon", "label.launchAtLogin",
        "empty.unsupported", "empty.selectTask",
        "action.newTask", "action.newTask.help", "action.newTask.title",
        "action.newTask.subtitle", "action.submitting",
        "help.metricTile", "empty.noBlockers", "empty.noEvents",
        "empty.noTasks.hint", "empty.tasksNoMatch", "empty.disconnectedTitle",
        "empty.tasksDisconnected.hint",
        "label.taskContext", "label.pickerNoSelection", "label.pickerChangeHint",
        "label.routingTitle", "label.verificationTitle",
        "empty.routingNeedsTask", "empty.verificationNeedsTask",
        "empty.routingNoSelection", "empty.verificationNoSelection",
        "empty.routingNotYetDecided",
        "label.agentsTitle", "label.providersTitle", "label.quotaTitle",
        "empty.noProviders.hint",
        "label.modelSku", "label.runtimeId", "label.runtimeAvailability",
        "label.executionVerified",
        "label.ownerDispatch",
        "label.ownerExecutionSetting",
        "label.ownerExecutionToggle",
        "label.ownerExecutionState",
        "label.ownerExecutionMeaning",
        "label.ownerExecutionDisabled",
        "label.ownerExecutionFooter",
        "label.executionTarget",
        "label.providerModel",
        "label.dispatch",
        "label.dispatchHelp",
        "label.dispatchBlockedHint",
        "label.dispatchStatus",
        "label.failureCode",
        "label.worktree",
        "label.branch",
        "label.writerLock",
        "label.workerRun",
        "label.noVerifiedTargets",
        "label.ownerDispatchFooter",
        "label.taskNotDispatchable",
        "label.observedState", "label.measurementSource", "label.confidence",
        "label.observedAt", "label.reasonCode", "empty.availabilityUnknown",
        "label.evidence",
        "settings.launchAtLogin.footer", "settings.autoStartDaemon.footer",
        "settings.activeReadOnly",
        "quota.resetUnknown", "quota.observedAt",
        "quota.explain.exact", "quota.explain.estimated", "quota.explain.unknown",
        "quota.source.providerExact", "quota.source.locallyMeasured",
        "quota.source.locallyInferred", "quota.source.unknown",
        "quota.state.exhausted", "quota.state.recovered", "quota.state.unknown",
        "action.copyTaskId", "action.copyTaskId.help", "action.reload",
        "build.title", "build.version", "build.commit", "build.short",
        "build.configuration", "build.timestamp", "build.daemon", "build.advanced",
        "build.unknown", "build.mismatchTitle", "build.mismatchApp",
        "build.mismatchDaemon", "build.mismatchFooter", "build.matched",
        "build.indeterminate", "action.restartDaemon",
        "action.reload.help", "empty.tasksHint", "empty.taskContextHint",
        "empty.taskDetailHint", "settings.activeAuthorized",
        "settings.activeNotAuthorized", "settings.activeGateLabel",
        "settings.activeGate.open", "settings.activeGate.closed",
        "label.taskBrowser", "label.taskDetailCanvas",
        "empty.selectTaskForDetails", "label.taskBrowserHint",
        "action.backToTasks", "action.refreshProviders",
        "label.providerDiscoveryState", "label.providerLastDiscovered",
        "label.discoveryEmpty", "label.discoveryError",
        "provider.authStatus", "provider.executionStatus",
        "provider.executionTargets", "provider.quotaState",
        "provider.lastChecked", "provider.evidenceSource",
        "evidence.discoveredFromCatalog", "evidence.authFromEnvPresence",
        "evidence.executionProbeNotRun", "evidence.executionProbeRun",
        "kpi.running", "kpi.ready", "kpi.blocked", "kpi.verification", "kpi.completed",
        "kpi.verificationDetail",
        "policy.balanced", "policy.qualityFirst", "policy.quotaSaver",
        "policy.speedFirst", "policy.manual",
        "policy.balanced.detail", "policy.qualityFirst.detail",
        "policy.quotaSaver.detail", "policy.speedFirst.detail", "policy.manual.detail",
        "policy.title", "policy.change", "policy.useGlobalDefault",
        "policy.globalDefault", "policy.resolutionSource",
        "policy.source.task", "policy.source.project", "policy.source.global",
        "providers.tab.connected", "providers.tab.available",
        "providers.connectedEmpty.title", "providers.connectedEmpty.message",
        "providers.addProvider", "providers.importExisting", "providers.disconnect",
        "providers.importTitle", "providers.importFooter",
        "providers.availableEmpty.message",
        "providers.connectedNotAuthenticated", "providers.connectedNotVerified",
        "providers.executionVerified", "providers.available", "providers.unavailable",
        "routing.activePolicy", "routing.modes", "routing.whySelected",
        "routing.whyNotSelected", "routing.candidates",
        "routing.noTaskSelected", "routing.considers",
        "routing.noConnectedProviders", "routing.noEligibleTargets",
        "routing.selectedModel", "routing.decidedAt", "routing.resolvedPolicy",
        "settings.defaultSchedulingPolicy", "settings.defaultSchedulingPolicy.footer",
        "newTask.schedulingPolicy", "newTask.manualModel", "newTask.chooseModel",
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
        case .projectRequired:
            return tr("notice.projectRequired")
        case .manualTargetRequired:
            return tr("notice.manualTargetRequired")
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
        if confidence == "EXACT" || confidence == "ESTIMATED" {
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

    // MARK: - P4.2.3 interactive dashboard

    public static var newTask: String { tr("action.newTask") }
    public static var newTaskHelp: String { tr("action.newTask.help") }
    public static var newTaskTitle: String { tr("action.newTask.title") }
    public static var newTaskSubtitle: String { tr("action.newTask.subtitle") }
    public static var submitting: String { tr("action.submitting") }


    // MARK: - Overview KPI

    public static var kpiRunning: String { tr("kpi.running") }
    public static var kpiReady: String { tr("kpi.ready") }
    public static var kpiBlocked: String { tr("kpi.blocked") }
    public static var kpiVerification: String { tr("kpi.verification") }
    public static var kpiCompleted: String { tr("kpi.completed") }

    /// Secondary line under the aggregated verification tile. The aggregate alone
    /// would hide whether work is still in flight, so both counts stay visible.
    public static func kpiVerificationDetail(verifying: Int, verified: Int) -> String {
        tr("kpi.verificationDetail", [verifying, verified])
    }

    // MARK: - Scheduling policy

    public static func schedulingPolicyName(_ policy: String) -> String {
        switch policy {
        case "BALANCED": return tr("policy.balanced")
        case "QUALITY_FIRST": return tr("policy.qualityFirst")
        case "QUOTA_SAVER": return tr("policy.quotaSaver")
        case "SPEED_FIRST": return tr("policy.speedFirst")
        case "MANUAL": return tr("policy.manual")
        default: return policy
        }
    }

    public static func schedulingPolicyDetail(_ policy: String) -> String {
        switch policy {
        case "BALANCED": return tr("policy.balanced.detail")
        case "QUALITY_FIRST": return tr("policy.qualityFirst.detail")
        case "QUOTA_SAVER": return tr("policy.quotaSaver.detail")
        case "SPEED_FIRST": return tr("policy.speedFirst.detail")
        case "MANUAL": return tr("policy.manual.detail")
        default: return policy
        }
    }

    /// Which level supplied the policy. Unknown values stay verbatim rather than
    /// being rendered as a friendlier but wrong label.
    public static func policyResolutionSource(_ source: String) -> String {
        switch source {
        case "TASK_OVERRIDE": return tr("policy.source.task")
        case "PROJECT_OVERRIDE": return tr("policy.source.project")
        case "GLOBAL_DEFAULT": return tr("policy.source.global")
        default: return source
        }
    }

    public static var policyTitle: String { tr("policy.title") }
    public static var policyChange: String { tr("policy.change") }
    public static var policyUseGlobalDefault: String { tr("policy.useGlobalDefault") }
    public static var policyGlobalDefault: String { tr("policy.globalDefault") }
    public static var policyResolutionSourceLabel: String { tr("policy.resolutionSource") }

    // MARK: - Providers & connections

    public static var providersTabConnected: String { tr("providers.tab.connected") }
    public static var providersTabAvailable: String { tr("providers.tab.available") }
    public static var providersConnectedEmptyTitle: String { tr("providers.connectedEmpty.title") }
    public static var providersConnectedEmptyMessage: String {
        tr("providers.connectedEmpty.message")
    }
    public static var providersAddProvider: String { tr("providers.addProvider") }
    public static var providerDisconnect: String { tr("providers.disconnect") }
    public static var providersImportExisting: String { tr("providers.importExisting") }
    public static var providersImportTitle: String { tr("providers.importTitle") }
    public static var providersImportFooter: String { tr("providers.importFooter") }
    public static var providersAvailableEmptyMessage: String {
        tr("providers.availableEmpty.message")
    }
    public static var providerNotAuthenticated: String { tr("providers.connectedNotAuthenticated") }
    public static var providerNotExecutionVerified: String { tr("providers.connectedNotVerified") }
    public static var providerExecutionVerified: String { tr("providers.executionVerified") }
    public static var providerAvailable: String { tr("providers.available") }
    public static var providerUnavailable: String { tr("providers.unavailable") }

    // MARK: - Routing

    public static var routingActivePolicy: String { tr("routing.activePolicy") }
    public static var routingModes: String { tr("routing.modes") }
    public static var routingWhySelected: String { tr("routing.whySelected") }
    public static var routingWhyNotSelected: String { tr("routing.whyNotSelected") }
    public static var routingCandidates: String { tr("routing.candidates") }
    public static var routingNoTaskSelected: String { tr("routing.noTaskSelected") }
    public static var routingConsiders: String { tr("routing.considers") }
    public static var routingNoConnectedProviders: String { tr("routing.noConnectedProviders") }
    public static var routingNoEligibleTargets: String { tr("routing.noEligibleTargets") }
    public static var routingSelectedModel: String { tr("routing.selectedModel") }
    public static var routingDecidedAt: String { tr("routing.decidedAt") }
    public static var routingResolvedPolicy: String { tr("routing.resolvedPolicy") }

    // MARK: - Settings & New Task

    public static var settingsDefaultSchedulingPolicy: String {
        tr("settings.defaultSchedulingPolicy")
    }
    public static var settingsDefaultSchedulingPolicyFooter: String {
        tr("settings.defaultSchedulingPolicy.footer")
    }
    public static var newTaskSchedulingPolicy: String { tr("newTask.schedulingPolicy") }
    public static var newTaskManualModel: String { tr("newTask.manualModel") }
    public static var newTaskChooseModel: String { tr("newTask.chooseModel") }

    public static var metricTileHelp: String { tr("help.metricTile") }
    public static var noBlockers: String { tr("empty.noBlockers") }
    public static var noEvents: String { tr("empty.noEvents") }

    public static var noTasksHint: String { tr("empty.noTasks.hint") }
    public static var tasksNoMatch: String { tr("empty.tasksNoMatch") }
    public static var disconnectedLabel: String { tr("empty.disconnectedTitle") }
    public static var tasksDisconnectedHint: String { tr("empty.tasksDisconnected.hint") }

    public static var taskContext: String { tr("label.taskContext") }
    public static var pickerNoSelection: String { tr("label.pickerNoSelection") }
    public static var pickerChangeHint: String { tr("label.pickerChangeHint") }

    public static var routingTitle: String { tr("label.routingTitle") }
    public static var verificationTitle: String { tr("label.verificationTitle") }
    public static var routingNeedsTask: String { tr("empty.routingNeedsTask") }
    public static var verificationNeedsTask: String { tr("empty.verificationNeedsTask") }
    public static var routingNoSelection: String { tr("empty.routingNoSelection") }
    public static var verificationNoSelection: String { tr("empty.verificationNoSelection") }
    public static var routingNotYetDecided: String { tr("empty.routingNotYetDecided") }

    public static var agentsTitle: String { tr("label.agentsTitle") }
    public static var providersTitle: String { tr("label.providersTitle") }
    public static var quotaTitle: String { tr("label.quotaTitle") }
    public static var noProvidersHint: String { tr("empty.noProviders.hint") }

    public static var modelSku: String { tr("label.modelSku") }
    public static var runtimeIdLabel: String { tr("label.runtimeId") }
    public static var runtimeAvailability: String { tr("label.runtimeAvailability") }
    public static var executionVerified: String { tr("label.executionVerified") }
    public static var ownerDispatch: String { tr("label.ownerDispatch") }
    public static var ownerExecutionSetting: String { tr("label.ownerExecutionSetting") }
    public static var ownerExecutionToggle: String { tr("label.ownerExecutionToggle") }
    public static var ownerExecutionState: String { tr("label.ownerExecutionState") }
    public static var ownerExecutionMeaning: String { tr("label.ownerExecutionMeaning") }
    public static var ownerExecutionDisabled: String { tr("label.ownerExecutionDisabled") }
    public static var ownerExecutionFooter: String { tr("label.ownerExecutionFooter") }
    public static var executionTarget: String { tr("label.executionTarget") }
    public static var providerModel: String { tr("label.providerModel") }
    public static var dispatch: String { tr("label.dispatch") }
    public static var dispatchHelp: String { tr("label.dispatchHelp") }
    public static var dispatchBlockedHint: String { tr("label.dispatchBlockedHint") }
    public static var dispatchStatus: String { tr("label.dispatchStatus") }
    public static var failureCode: String { tr("label.failureCode") }
    public static var worktree: String { tr("label.worktree") }
    public static var branch: String { tr("label.branch") }
    public static var writerLock: String { tr("label.writerLock") }
    public static var workerRun: String { tr("label.workerRun") }
    public static var noVerifiedTargets: String { tr("label.noVerifiedTargets") }
    public static var ownerDispatchFooter: String { tr("label.ownerDispatchFooter") }

    public static func taskNotDispatchable(_ state: String) -> String {
        String(format: tr("label.taskNotDispatchable"), state)
    }
    public static var observedState: String { tr("label.observedState") }
    public static var measurementSource: String { tr("label.measurementSource") }
    public static var confidenceLabel: String { tr("label.confidence") }
    public static var observedAt: String { tr("label.observedAt") }
    public static var reasonCode: String { tr("label.reasonCode") }
    public static var availabilityUnknown: String { tr("empty.availabilityUnknown") }
    public static var evidenceLabel: String { tr("label.evidence") }

    public static var launchAtLoginFooter: String { tr("settings.launchAtLogin.footer") }
    public static var autoStartDaemonFooter: String { tr("settings.autoStartDaemon.footer") }
    public static var activeReadOnlyNote: String { tr("settings.activeReadOnly") }

    public static var quotaResetUnknown: String { tr("quota.resetUnknown") }
    public static var quotaObservedAtPrefix: String { tr("quota.observedAt") }

    public static func quotaObservedAt(_ observedAt: String) -> String {
        tr("quota.observedAt", [observedAt])
    }

    /// Human-readable explanation for a quota confidence enum. Unknown enum
    /// values fall back to a generic UNKNOWN explanation; they never fabricate
    /// numeric percentages.
    public static func quotaConfidenceExplanation(_ confidence: String) -> String {
        switch confidence {
        case "EXACT": return tr("quota.explain.exact")
        case "ESTIMATED": return tr("quota.explain.estimated")
        default: return tr("quota.explain.unknown")
        }
    }

    public static func quotaSourceExplanation(_ source: String) -> String {
        switch source {
        case "PROVIDER_EXACT": return tr("quota.source.providerExact")
        case "LOCALLY_MEASURED": return tr("quota.source.locallyMeasured")
        case "LOCALLY_INFERRED": return tr("quota.source.locallyInferred")
        default: return tr("quota.source.unknown")
        }
    }

    public static func quotaStateNote(_ state: String) -> String {
        switch state {
        case "EXHAUSTED": return tr("quota.state.exhausted")
        case "RECOVERED": return tr("quota.state.recovered")
        default: return tr("quota.state.unknown")
        }
    }

    public static func quotaStateSymbol(_ state: String) -> String {
        switch state {
        case "EXHAUSTED": return "exclamationmark.triangle"
        case "RECOVERED": return "checkmark.circle"
        default: return "questionmark.circle"
        }
    }

    // MARK: - P4.2.3 polish

    public static var copyTaskId: String { tr("action.copyTaskId") }
    public static var copyTaskIdHint: String { tr("action.copyTaskId.help") }
    public static var reload: String { tr("action.reload") }
    public static var reloadHint: String { tr("action.reload.help") }
    public static var tasksEmptyHint: String { tr("empty.tasksHint") }
    public static var taskContextEmptyHint: String { tr("empty.taskContextHint") }
    public static var taskDetailEmptyHint: String { tr("empty.taskDetailHint") }
    public static var activeAuthorized: String { tr("settings.activeAuthorized") }
    public static var activeNotAuthorized: String { tr("settings.activeNotAuthorized") }
    public static var activeGateLabel: String { tr("settings.activeGateLabel") }
    public static var activeGateOpen: String { tr("settings.activeGate.open") }
    public static var activeGateClosed: String { tr("settings.activeGate.closed") }

    // MARK: - Build identity

    public static var buildTitle: String { tr("build.title") }
    public static var buildVersion: String { tr("build.version") }
    public static var buildCommit: String { tr("build.commit") }
    public static var buildShort: String { tr("build.short") }
    public static var buildConfiguration: String { tr("build.configuration") }
    public static var buildTimestamp: String { tr("build.timestamp") }
    public static var buildDaemon: String { tr("build.daemon") }
    public static var buildAdvanced: String { tr("build.advanced") }
    public static var buildUnknown: String { tr("build.unknown") }
    public static var buildMismatchTitle: String { tr("build.mismatchTitle") }
    public static var buildMismatchApp: String { tr("build.mismatchApp") }
    public static var buildMismatchDaemon: String { tr("build.mismatchDaemon") }
    public static var buildMismatchFooter: String { tr("build.mismatchFooter") }
    public static var buildMatched: String { tr("build.matched") }
    public static var buildIndeterminate: String { tr("build.indeterminate") }
    public static var restartDaemonAction: String { tr("action.restartDaemon") }

    // MARK: - P4.2.4-A unified shell

    public static var taskBrowserTitle: String { tr("label.taskBrowser") }
    public static var taskDetailCanvasTitle: String { tr("label.taskDetailCanvas") }
    public static var selectTaskForDetails: String { tr("empty.selectTaskForDetails") }
    public static var taskBrowserHint: String { tr("label.taskBrowserHint") }
    public static var backToTasks: String { tr("action.backToTasks") }
    public static var refreshProviders: String { tr("action.refreshProviders") }

    public static var providerAuthStatus: String { tr("provider.authStatus") }
    public static var providerExecutionStatus: String { tr("provider.executionStatus") }
    public static var providerExecutionTargets: String { tr("provider.executionTargets") }
    public static var providerQuotaState: String { tr("provider.quotaState") }
    public static var providerLastChecked: String { tr("provider.lastChecked") }
    public static var providerEvidenceSource: String { tr("provider.evidenceSource") }

    public static var evidenceDiscoveredFromCatalog: String { tr("evidence.discoveredFromCatalog") }
    public static var evidenceAuthFromEnvPresence: String { tr("evidence.authFromEnvPresence") }
    public static var evidenceExecutionProbeNotRun: String { tr("evidence.executionProbeNotRun") }
    public static var evidenceExecutionProbeRun: String { tr("evidence.executionProbeRun") }

    public static var providerDiscoveryState: String { tr("label.providerDiscoveryState") }
    public static var providerLastDiscovered: String { tr("label.providerLastDiscovered") }
    public static var discoveryEmpty: String { tr("label.discoveryEmpty") }
    public static var discoveryError: String { tr("label.discoveryError") }

    public static func providerLastDiscovered(_ timestamp: String) -> String {
        tr("label.providerLastDiscovered", [timestamp])
    }
    public static func discoveryError(_ code: String) -> String {
        tr("label.discoveryError", [code])
    }
}
