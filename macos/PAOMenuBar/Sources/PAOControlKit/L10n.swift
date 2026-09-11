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
        "dashboard.resources", "dashboard.activity",
        "resources.pickerTitle", "settings.pickerTitle", "settings.tab.general",
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
        "label.workerLogTruncated",
        "label.recommendDispatch",
        "label.recommendDispatchHelp",
        "label.recommendationDismiss",
        "recommendation.panelTitle",
        "recommendation.score",
        "recommendation.quotaState",
        "scheduling.policyManualLinked",
        "scheduling.policyArchived",
        "taskCompletion.title",
        "taskCompletion.view",
        "taskCompletion.dismiss",
        "run.processExited",
        "run.processAlive",
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
        "policy.speedFirst", "policy.burnDown", "policy.manual",
        "policy.balanced.detail", "policy.qualityFirst.detail",
        "policy.quotaSaver.detail", "policy.speedFirst.detail",
        "policy.burnDown.detail", "policy.manual.detail",
        "policy.title", "policy.change", "policy.useGlobalDefault",
        "policy.globalDefault", "policy.resolutionSource",
        "policy.source.task", "policy.source.project", "policy.source.global",
        "providers.tab.connected", "providers.tab.available",
        // M1 WP4: free / unmetered resource group + per-card
        // metrics (rpm, error rate, cooldown, no-window affordance).
        "resource.group.unmetered", "quota.windowKind.unmetered",
        "quota.unmetered.errorRate", "quota.unmetered.rpm",
        "quota.unmetered.cooldownUntil", "quota.unmetered.noWindow",
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
        // P4.2.6.4 owner-facing localization sweep
        "sidebar.group.work", "sidebar.group.aiResources", "sidebar.group.execution",
        "sidebar.group.system", "common.advancedDetails", "common.never", "common.reason",
        "common.plan", "common.region", "common.retry", "overview.basicInfo",
        "overview.connection", "overview.projects", "overview.providers",
        "overview.runnableTargets", "overview.runningTasks", "overview.tasksToday",
        "overview.routingToday", "overview.quotaWarnings", "overview.lastRefresh",
        "overview.taskTrend", "overview.taskTrend.empty", "overview.taskTrend.help",
        "overview.taskStates", "overview.taskStates.empty", "overview.risks",
        "overview.recentActivity", "risk.projectUnavailable.title",
        "risk.projectUnavailable.detail", "risk.executionTargetUnverified.title",
        "risk.executionTargetUnverified.detail", "risk.quotaExhausted.title",
        "risk.quotaExhausted.detail", "risk.quotaUnknown.title", "risk.quotaUnknown.detail",
        "risk.ownerApproval.title", "risk.ownerApproval.detail", "risk.shadowEvidence.title",
        "risk.shadowEvidence.detail", "projects.add", "projects.detectedRepository",
        "projects.register", "projects.empty", "projects.revealInFinder", "projects.open",
        "projects.remove", "projects.gitRoot", "projects.workingSubpath", "projects.branch",
        "projects.recentTasks", "projects.lastUsed", "newTask.project",
        "newTask.selectProject", "newTask.execution", "newTask.executionAuto",
        "providers.pickerTitle", "providers.notConnected", "providers.surface",
        "providers.providerId", "providers.credentialReference", "providers.runtime",
        "providers.connectionLabel", "providers.accountsLabel",
        "providers.executionTargetsLabel", "providers.quotaPoolsLabel", "providers.modelCount",
        "providers.quotaPoolCount", "providers.catalogModelCount", "models.title",
        "models.empty", "models.ready", "models.needsVerification",
        "models.noExecutionTargets", "auth.authenticated", "auth.required", "auth.unknown",
        "connectionState.connected", "connectionState.disconnected",
        "connectionState.discovered", "runtimeState.available", "runtimeState.unavailable",
        "runtimeState.unknown", "quota.refresh", "quota.refresh.help", "quota.empty.title",
        "quota.empty.message", "quota.status", "quota.statusTitle", "quota.noReliableData",
        "quota.unreadable", "quota.lastChecked", "quota.neverChecked", "quota.lastAttempt",
        "quota.connectionStatus", "quota.summary.connected", "quota.summary.observable",
        "quota.summary.warnings", "quota.summary.exhausted", "quota.windows",
        "quota.windowDetails", "quota.poolId", "quota.limitingWindow",
        "quota.limitingWindow.empty", "quota.provider", "quota.window", "quota.remainingLabel",
        "quota.resetLabel", "quota.estimatedBadge", "quota.history.title",
        "quota.history.empty", "quota.history.footer", "quota.noReliablePercentage",
        "quota.reason.noReadonlySource",
        "quota.reason.codingScopeUnavailable", "quota.reason.codingScopeUnreadable",
        "quota.reason.codingWindowSemanticsUnknown", "quota.reason.codingScopeDisagree",
        "quota.plan.workloadScopeLabel", "quota.plan.workload.codingText",
        "quota.plan.workload.video", "quota.plan.workload.image",
        "quota.plan.workload.audio", "quota.plan.workload.unknown",
        "quota.plan.otherScopes", "quota.plan.otherScopesFooter",
        "quota.reason.noEntries", "quota.reason.fieldsUnavailable",
        "quota.reason.notInterpretable", "quota.reason.credentialMissing",
        "quota.reason.authRequired", "quota.reason.rateLimited", "quota.reason.providerError",
        "quota.reason.notConnected", "detail.currentPhase", "detail.elapsed", "detail.stop",
        "detail.stop.help", "detail.filesChanged", "detail.noWorktree",
        "detail.verifierProfile", "detail.noWorkerRun", "detail.rawWorkerOutput",
        "detail.taskId", "detail.requestId", "detail.routingDecision", "detail.routingRequest",
        "detail.baseSha", "detail.runId", "detail.runStatus", "detail.quotaEvidence",
        "detail.panel.summary", "detail.panel.progress", "detail.panel.liveActivity",
        "detail.panel.changes", "detail.panel.verification", "phase.preparing",
        "phase.routing", "phase.workspace", "phase.quota",
        "phase.startingWorker", "phase.editing", "phase.testing",
        "phase.verifying", "phase.finished", "candidate.selected",
        "candidate.eligible", "candidate.ineligible", "candidate.unknownModel",
        "value.none", "value.unknown", "value.automatic",
        "value.notVerifiedYet", "value.stagePassed", "value.stageFailed",
        "value.notShadowMode", "label.task", "label.updatedRelative",
        "evidence.observed", "evidence.derived", "evidence.forecast",
        "evidence.unavailable", "timestamp.justNow",
        "command.focusSearch", "command.previousTask", "command.nextTask",
        "command.copyTaskId", "command.reloadDetail",
        // B3 task workspace
        "taskGroup.queued", "taskGroup.active", "taskGroup.needsAttention",
        "taskGroup.completed", "taskGroup.cancelled", "tasks.filterMenu",
        "tasks.filter.allTasks", "tasks.filter.groups", "tasks.filter.states",
        "tasks.filter.project", "tasks.filter.allProjects", "tasks.filter.clear",
        "tasks.searchPrompt", "tasks.visibleCount", "tasks.truncated",
        "tasks.truncatedHelp", "tasks.noProject", "empty.tasks.none.title",
        "empty.tasks.none.message", "empty.tasks.search.title", "empty.tasks.search.message",
        "empty.tasks.search.hint", "empty.tasks.filter.title", "empty.tasks.filter.message",
        "empty.tasks.loading", "empty.tasks.noSelection.title", "empty.tasks.noSelection.message",
        "taskDetail.section.overview", "taskDetail.section.lifecycle", "taskDetail.section.execution",
        "taskDetail.section.attention", "taskDetail.openTaskFailed", "taskLifecycle.submitted",
        "taskLifecycle.routing", "taskLifecycle.workspace", "taskLifecycle.execution",
        "taskLifecycle.verification", "taskLifecycle.completion", "taskLifecycle.status.reached",
        "taskLifecycle.status.inProgress", "taskLifecycle.status.notReached", "taskLifecycle.status.failed",
        "taskLifecycle.status.stopped", "taskLifecycle.status.unknown", "taskLifecycle.footer",
        "taskAttention.verificationFailed", "taskAttention.failed", "taskAttention.blocked",
        "taskAttention.unknownState", "taskAttention.verificationEvidenceUnavailable", "taskAttention.unexpectedChanges",
        "taskAttention.awaitingApproval", "taskAttention.noDetail", "routing.role.primary",
        "routing.role.reviewer", "routing.role.finalAuditor", "routing.roleStatus.unassigned",
        "routing.roleStatus.assigned", "routing.roleStatus.running", "routing.roleStatus.completed",
        "routing.roleStatus.cancelled", "routing.outcome.pass", "routing.outcome.fail",
        "routing.outcome.error", "routing.outcome.none", "routing.plan",
        "routing.planRevision", "routing.superseded", "routing.legacySynthesized",
        "routing.declaredRoles", "routing.rerouteCount", "routing.rerouteReason",
        "routing.previousDecisions", "routing.decidedAtLabel", "routing.noDetails",
        "changes.title", "changes.count", "changes.none",
        "changes.notMeasured.verificationNotRun", "changes.notMeasured.noWorkspace", "changes.unexpected",
        "changes.unexpectedCount", "changes.footer", "changes.revealWorktree",
        "verification.status.verified", "verification.status.evidenceMissing", "verification.status.evidenceUnavailable",
        "verification.status.failed", "verification.status.inProgress", "verification.status.notVerified",
        "verification.checks", "verification.checkExit", "verification.noResult",
        "verification.notRunYet", "verification.overall", "verification.approvals",
        "verification.evidenceProblemFooter", "verification.stageFooter", "inspector.title",
        "inspector.toggle", "inspector.identity", "inspector.timestamps",
        "inspector.workspaceSection", "inspector.runsSection", "inspector.routingProvenance",
        "inspector.empty", "label.createdAt", "label.updatedAt",
        "label.stateVersion", "label.repositoryPath", "label.projectId",
        "label.currentWorker", "label.noWorkerYet", "routing.planRevisionLabel",
        // B4 resources + quota workspace
        "resources.searchPrompt", "resources.visibleCount",
        "resources.kind.connected", "resources.kind.available", "resources.kind.discovered",
        "resources.filter.all", "resources.filter.kind",
        "empty.resources.none.title", "empty.resources.none.message",
        "empty.resources.loading", "empty.resources.search.title",
        "empty.resources.search.message", "empty.resources.noSelection.title",
        "empty.resources.noSelection.message", "resources.disconnectedHint",
        "resource.section.availability", "resource.section.quota",
        "resource.section.usage", "resource.section.projection",
        "resource.section.executionTargets", "resource.section.observability",
        "resource.section.models",
        "resource.noQuotaTelemetry", "resource.noExecutionTargets",
        "resource.notConnected", "resource.connectAction",
        "resource.severalBindings", "resource.bindingLimiting",
        "resource.quotaObservable", "resource.quotaNotObservable",
        "resource.freshnessUnknown", "resource.observedAge", "resource.staleWarning",
        "resource.targetsSummary", "resource.modelsSummary",
        "resource.refreshQuota", "resource.refreshProviders",
        "resource.refreshQuotaHelp", "resource.refreshProvidersHelp",
        "resource.refreshFailed",
        "quota.rate.perHour", "quota.rate.belowThreshold",
        "quota.horizon.daysHours", "quota.horizon.hoursMinutes", "quota.horizon.minutes",
        "quota.burnRate.title", "quota.burnRate.basis", "quota.burnRate.flat",
        "quota.projection.title", "quota.projection.remainingAtReset",
        "quota.projection.exhaustionAt", "quota.projection.likelyUnused",
        "quota.projection.noExhaustion", "quota.projection.footer",
        // M1 WP1 burn pressure chips (UNMETERED / STALE / EXHAUSTED /
        // STARVED / AHEAD / BEHIND / ON_TRACK). STARVED's label is
        // "Expiring unused" / "将过期未用" — the verdict means a window
        // with lots of remaining quota about to reset, not "almost
        // empty". AHEAD / BEHIND use "消耗偏快" / "消耗偏慢" rather than
        // "快于/慢于计划" to avoid ambiguity between pace and direction.
        "quota.pressure.unmetered", "quota.pressure.stale",
        "quota.pressure.exhausted", "quota.pressure.starved",
        "quota.pressure.ahead", "quota.pressure.behind",
        "quota.pressure.onTrack",
        // M1 WP2: tier picker + chip labels.
        "newTask.minTier", "newTask.minTierHelp",
        "tier.t0", "tier.t1", "tier.t2", "tier.t3", "tier.unknown",
        // WP5b (brought forward): stale-verification chip.
        "target.verifiedStale",
        "quota.projection.unavailable.NO_HISTORY",
        "quota.projection.unavailable.INSUFFICIENT_OBSERVATIONS",
        "quota.projection.unavailable.NO_ELAPSED_TIME",
        "quota.projection.unavailable.REMAINING_INCREASED",
        "quota.projection.unavailable.RESET_UNKNOWN",
        "quota.projection.unavailable.RESET_ALREADY_PASSED",
        "quota.scarcity.unavailable",
        "quota.history.axisTime", "quota.history.axisRemaining",
        "quota.history.seriesLegend", "quota.history.resetBoundary",
        "quota.history.gapNote", "quota.history.accessibleSummary",
        "quota.history.singleObservation", "quota.history.observationCount",
        "quota.resetsIn", "quota.resetPassed", "quota.observedAge",
        "quota.equivalentCapacity.explanation",
        // M1 WP5b — supervised-auto control surfaces.
        "auto.mode.title", "auto.mode.choices", "auto.mode.footer",
        "auto.mode.name.MANUAL", "auto.mode.name.SUPERVISED_AUTO", "auto.mode.name.ACTIVE",
        "auto.mode.detail.MANUAL", "auto.mode.detail.SUPERVISED_AUTO",
        "auto.mode.gatedDetail", "auto.mode.activeReported", "auto.mode.gatedSymbol",
        "auto.task.title", "auto.task.footnote",
        "auto.task.planned", "auto.task.waitingAck", "auto.task.expired",
        "auto.task.countdown", "auto.task.countdownAccessibility",
        "auto.task.targetUnavailable",
        "auto.label.target", "auto.label.reason", "auto.label.acked",
        "auto.action.ack", "auto.action.ackHelp",
        "auto.action.veto", "auto.action.vetoHelp",
        "auto.action.dispatchNow", "auto.action.dispatchNowHelp",
        "auto.notice.dismiss",
        "auto.notice.acknowledged", "auto.notice.vetoed",
        "auto.notice.dispatchRequested", "auto.notice.staleState",
        "auto.notice.blocked", "auto.notice.failed", "auto.notice.malformed",
        "auto.notice.modeChanged",
        "menubar.schedulingMode",
        "menubar.stopSupervisedAuto", "menubar.stopSupervisedAutoHelp",
        "menubar.stopSupervisedAuto.success",
        "project.auto.title", "project.auto.allowSupervisedAuto",
        "project.auto.allowUnattended", "project.auto.graceSeconds",
        "project.auto.graceHelp", "project.auto.graceInvalid",
        "project.auto.disableWarning", "project.auto.disableConfirm",
        "project.auto.saved"
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

    // MARK: - Supervised auto (M1 WP5b)

    public static var autoModeTitle: String { tr("auto.mode.title") }
    public static var autoModeChoices: String { tr("auto.mode.choices") }
    public static var autoModeFooter: String { tr("auto.mode.footer") }
    public static var autoModeGatedDetail: String { tr("auto.mode.gatedDetail") }
    public static var autoModeActiveReported: String { tr("auto.mode.activeReported") }
    public static var autoModeGatedSymbol: String { tr("auto.mode.gatedSymbol") }

    public static func autoModeName(_ mode: String) -> String {
        switch mode {
        case "MANUAL": return tr("auto.mode.name.MANUAL")
        case "SUPERVISED_AUTO": return tr("auto.mode.name.SUPERVISED_AUTO")
        case "ACTIVE": return tr("auto.mode.name.ACTIVE")
        default: return mode
        }
    }

    public static func autoModeDetail(_ mode: String) -> String {
        switch mode {
        case "MANUAL": return tr("auto.mode.detail.MANUAL")
        case "SUPERVISED_AUTO": return tr("auto.mode.detail.SUPERVISED_AUTO")
        default: return tr("auto.mode.gatedDetail")
        }
    }

    public static var autoTaskTitle: String { tr("auto.task.title") }
    public static var autoTaskFootnote: String { tr("auto.task.footnote") }
    public static var autoPlannedText: String { tr("auto.task.planned") }
    public static var autoWaitingAckText: String { tr("auto.task.waitingAck") }
    public static var autoExpiredText: String { tr("auto.task.expired") }
    public static var autoTargetUnavailable: String { tr("auto.task.targetUnavailable") }
    public static var autoCountdownAccessibilityLabel: String {
        tr("auto.task.countdownAccessibility")
    }

    public static func autoCountdownText(_ clock: String) -> String {
        tr("auto.task.countdown", [clock])
    }

    public static var autoLabelTarget: String { tr("auto.label.target") }
    public static var autoLabelReason: String { tr("auto.label.reason") }
    public static var autoLabelAcked: String { tr("auto.label.acked") }

    public static var autoActionAck: String { tr("auto.action.ack") }
    public static var autoActionAckHelp: String { tr("auto.action.ackHelp") }
    public static var autoActionVeto: String { tr("auto.action.veto") }
    public static var autoActionVetoHelp: String { tr("auto.action.vetoHelp") }
    public static var autoActionDispatchNow: String { tr("auto.action.dispatchNow") }
    public static var autoActionDispatchNowHelp: String { tr("auto.action.dispatchNowHelp") }
    public static var autoNoticeDismiss: String { tr("auto.notice.dismiss") }

    /// Daemon semantics preserved verbatim; only the wrapping words are
    /// localized. Raw codes surface untouched after the colon.
    public static func autoControlNoticeText(_ notice: AutoControlNotice) -> String {
        switch notice {
        case .acknowledged:
            return tr("auto.notice.acknowledged")
        case .vetoed:
            return tr("auto.notice.vetoed")
        case .dispatchRequested(_, let dispatchId, let status):
            return tr("auto.notice.dispatchRequested", [dispatchId, status])
        case .staleState:
            return tr("auto.notice.staleState")
        case .blocked(let code):
            return tr("auto.notice.blocked", [code])
        case .schedulingModeChanged(let mode):
            return tr("auto.notice.modeChanged", [autoModeName(mode)])
        case .supervisedAutoStopped:
            return tr("menubar.stopSupervisedAuto.success")
        case .projectAutoSettingsSaved:
            return tr("project.auto.saved")
        case .failed(let action, let detail):
            return tr("auto.notice.failed", [action.rawValue, detail])
        case .malformedResponse:
            return tr("auto.notice.malformed")
        }
    }

    public static var menubarSchedulingMode: String { tr("menubar.schedulingMode") }
    public static var menubarStopSupervisedAuto: String { tr("menubar.stopSupervisedAuto") }
    public static var menubarStopSupervisedAutoHelp: String {
        tr("menubar.stopSupervisedAutoHelp")
    }

    public static var projectAutoTitle: String { tr("project.auto.title") }
    public static var projectAutoAllowSupervisedAuto: String {
        tr("project.auto.allowSupervisedAuto")
    }
    public static var projectAutoAllowUnattended: String { tr("project.auto.allowUnattended") }
    public static var projectAutoGraceSeconds: String { tr("project.auto.graceSeconds") }
    public static var projectAutoGraceHelp: String { tr("project.auto.graceHelp") }
    public static var projectAutoGraceInvalid: String { tr("project.auto.graceInvalid") }
    public static var projectAutoDisableWarning: String { tr("project.auto.disableWarning") }
    public static var projectAutoDisableConfirm: String { tr("project.auto.disableConfirm") }

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

    /// Every key defined in one catalog (tests).
    ///
    /// Reads the `.strings` file directly rather than through `Bundle`, because a
    /// bundle lookup can only answer "does this key resolve" — it cannot enumerate
    /// what a catalog defines, which is what a drift check needs.
    public static func catalogKeys(language: String) -> Set<String> {
        guard let entry = lprojDirectories[language.lowercased()] else { return [] }
        let url = URL(fileURLWithPath: entry.path)
            .appendingPathComponent("Localizable.strings")
        guard let contents = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        var keys: Set<String> = []
        for line in contents.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("\"") else { continue }
            let afterOpening = trimmed.dropFirst()
            guard let closing = afterOpening.firstIndex(of: "\"") else { continue }
            keys.insert(String(afterOpening[..<closing]))
        }
        return keys
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

    // MARK: - Sub-surface titles
    //
    // These titles no longer belong to a first-level destination. They title a
    // surface *inside* one (Resources tabs, Settings tabs, Task Detail panels),
    // so they are reached by name rather than through a navigation raw value.

    public static var sectionProjects: String { tr("dashboard.projects") }
    public static var sectionProviders: String { tr("dashboard.providers") }
    public static var sectionExecutionTargets: String { tr("dashboard.agents") }
    public static var sectionQuota: String { tr("dashboard.quota") }
    public static var sectionRouting: String { tr("dashboard.routing") }
    public static var sectionVerification: String { tr("dashboard.verification") }
    public static var sectionHistory: String { tr("dashboard.history") }
    public static var resourcesPickerTitle: String { tr("resources.pickerTitle") }
    public static var settingsPickerTitle: String { tr("settings.pickerTitle") }
    public static var settingsTabGeneral: String { tr("settings.tab.general") }

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
        case "BURN_DOWN": return tr("policy.burnDown")
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
        case "BURN_DOWN": return tr("policy.burnDown.detail")
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

    /// Caption under a truncated worker log tail.
    public static var workerLogTruncated: String { tr("label.workerLogTruncated") }

    /// Policy-driven dispatch recommender.
    public static var recommendDispatch: String { tr("label.recommendDispatch") }
    public static var recommendDispatchHelp: String { tr("label.recommendDispatchHelp") }
    public static var recommendationDismiss: String { tr("label.recommendationDismiss") }
    public static func recommendationPanelTitle(_ policy: String) -> String {
        String(format: tr("recommendation.panelTitle"), policy)
    }
    public static func recommendationScore(
        _ score: Double, _ headroom: Double, _ fresh: Bool
    ) -> String {
        String(
            format: tr("recommendation.score"),
            score, headroom, fresh ? "✓" : "—"
        )
    }
    public static func recommendationQuotaState(_ state: String) -> String {
        String(format: tr("recommendation.quotaState"), state)
    }

    /// Honest scheduling-policy semantics in the dispatch area.
    public static var schedulingPolicyManualLinked: String { tr("scheduling.policyManualLinked") }
    public static var schedulingPolicyArchived: String { tr("scheduling.policyArchived") }

    /// Completion banner and notification.
    public static func taskCompletionTitle(_ state: String) -> String {
        String(format: tr("taskCompletion.title"), state)
    }

    public static var taskCompletionView: String { tr("taskCompletion.view") }
    public static var taskCompletionDismiss: String { tr("taskCompletion.dismiss") }

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

    // MARK: - P4.2.6.4 owner-facing localization sweep

    // Sidebar groups
    public static var sidebarWork: String { tr("sidebar.group.work") }
    public static var sidebarAIResources: String { tr("sidebar.group.aiResources") }
    public static var sidebarExecution: String { tr("sidebar.group.execution") }
    public static var sidebarSystem: String { tr("sidebar.group.system") }

    // Shared labels
    public static var advancedDetails: String { tr("common.advancedDetails") }
    public static var never: String { tr("common.never") }
    public static var reasonLabel: String { tr("common.reason") }
    public static var planLabel: String { tr("common.plan") }
    public static var regionLabel: String { tr("common.region") }
    public static var retry: String { tr("common.retry") }

    // Overview
    public static var overviewBasicInfo: String { tr("overview.basicInfo") }
    public static var overviewConnection: String { tr("overview.connection") }
    public static var overviewProjects: String { tr("overview.projects") }
    public static var overviewProviders: String { tr("overview.providers") }
    public static var overviewRunnableTargets: String { tr("overview.runnableTargets") }
    public static var overviewRunningTasks: String { tr("overview.runningTasks") }
    public static var overviewTasksToday: String { tr("overview.tasksToday") }
    public static var overviewRoutingToday: String { tr("overview.routingToday") }
    public static var overviewQuotaWarnings: String { tr("overview.quotaWarnings") }
    public static var overviewTaskTrend: String { tr("overview.taskTrend") }
    public static var overviewTaskTrendEmpty: String { tr("overview.taskTrend.empty") }
    public static var overviewTaskStates: String { tr("overview.taskStates") }
    public static var overviewTaskStatesEmpty: String { tr("overview.taskStates.empty") }
    public static var overviewRisks: String { tr("overview.risks") }
    public static var overviewRecentActivity: String { tr("overview.recentActivity") }

    public static func overviewLastRefresh(_ timestamp: String) -> String {
        tr("overview.lastRefresh", [timestamp])
    }

    public static func taskTrendHelp(submitted: Int, completed: Int, blocked: Int) -> String {
        tr("overview.taskTrend.help", [submitted, completed, blocked])
    }

    /// Localized risk title for a daemon ``raw_code``.
    ///
    /// Unknown codes fall back to the daemon's own English sentence rather than
    /// inventing a label, so a newer daemon still renders something truthful.
    public static func riskTitle(rawCode: String?, count: Int?, fallback: String) -> String {
        guard let key = riskKeyPrefix(rawCode) else { return fallback }
        let template = tr("\(key).title")
        guard let count else { return template }
        return String(format: template, locale: Locale.current, count)
    }

    public static func riskDetail(rawCode: String?, fallback: String) -> String {
        guard let key = riskKeyPrefix(rawCode) else { return fallback }
        return tr("\(key).detail")
    }

    private static func riskKeyPrefix(_ rawCode: String?) -> String? {
        guard let rawCode else { return nil }
        switch rawCode {
        case "PROJECT_UNAVAILABLE": return "risk.projectUnavailable"
        case "EXECUTION_TARGET_UNVERIFIED": return "risk.executionTargetUnverified"
        case "QUOTA_EXHAUSTED": return "risk.quotaExhausted"
        case "QUOTA_UNKNOWN": return "risk.quotaUnknown"
        default: break
        }
        // Free-form activation blockers are matched on their stable substrings.
        if rawCode.contains("owner approval") { return "risk.ownerApproval" }
        if rawCode.contains("Shadow evidence") { return "risk.shadowEvidence" }
        return nil
    }

    // Projects
    public static var projectsAdd: String { tr("projects.add") }
    public static var projectsDetectedRepository: String { tr("projects.detectedRepository") }
    public static var projectsRegister: String { tr("projects.register") }
    public static var projectsEmpty: String { tr("projects.empty") }
    public static var projectsRevealInFinder: String { tr("projects.revealInFinder") }
    public static var projectsOpen: String { tr("projects.open") }
    public static var projectsRemove: String { tr("projects.remove") }
    public static var projectsGitRoot: String { tr("projects.gitRoot") }
    public static var projectsWorkingSubpath: String { tr("projects.workingSubpath") }
    public static var projectsBranch: String { tr("projects.branch") }
    public static var projectsRecentTasks: String { tr("projects.recentTasks") }
    public static var projectsLastUsed: String { tr("projects.lastUsed") }

    // New task sheet
    public static var newTaskProject: String { tr("newTask.project") }
    public static var newTaskSelectProject: String { tr("newTask.selectProject") }
    public static var newTaskExecution: String { tr("newTask.execution") }
    public static var newTaskExecutionAuto: String { tr("newTask.executionAuto") }

    // Providers & models
    public static var providersPickerTitle: String { tr("providers.pickerTitle") }
    public static var providersNotConnected: String { tr("providers.notConnected") }
    public static var providersSurface: String { tr("providers.surface") }
    public static var providersProviderId: String { tr("providers.providerId") }
    public static var providersCredentialReference: String { tr("providers.credentialReference") }
    public static var providersRuntime: String { tr("providers.runtime") }
    public static var providersConnectionLabel: String { tr("providers.connectionLabel") }
    public static var providersAccountsLabel: String { tr("providers.accountsLabel") }
    public static var providersExecutionTargetsLabel: String {
        tr("providers.executionTargetsLabel")
    }
    public static var providersQuotaPoolsLabel: String { tr("providers.quotaPoolsLabel") }
    public static var modelsTitle: String { tr("models.title") }
    public static var modelsEmpty: String { tr("models.empty") }
    public static var modelsReady: String { tr("models.ready") }
    public static var modelsNeedsVerification: String { tr("models.needsVerification") }
    public static var modelsNoExecutionTargets: String { tr("models.noExecutionTargets") }

    public static func modelCount(_ count: Int) -> String { tr("providers.modelCount", [count]) }
    public static func quotaPoolCount(_ count: Int) -> String {
        tr("providers.quotaPoolCount", [count])
    }
    public static func catalogModelCount(_ count: Int) -> String {
        tr("providers.catalogModelCount", [count])
    }

    /// Raw connection/auth/runtime enums stay verbatim on the wire; only their
    /// presentation label is localized, and unknown values pass through.
    public static func authStateLabel(_ value: String) -> String {
        switch value {
        case "AUTHENTICATED": return tr("auth.authenticated")
        case "AUTH_REQUIRED": return tr("auth.required")
        case "AUTH_UNKNOWN": return tr("auth.unknown")
        default: return value
        }
    }

    public static func connectionStateLabel(_ value: String) -> String {
        switch value {
        case "CONNECTED": return tr("connectionState.connected")
        case "DISCONNECTED": return tr("connectionState.disconnected")
        case "DISCOVERED": return tr("connectionState.discovered")
        default: return value
        }
    }

    public static func runtimeStateLabel(_ value: String) -> String {
        switch value {
        case "AVAILABLE": return tr("runtimeState.available")
        case "UNAVAILABLE": return tr("runtimeState.unavailable")
        case "UNKNOWN": return tr("runtimeState.unknown")
        default: return value
        }
    }

    // Quota page
    public static var quotaRefresh: String { tr("quota.refresh") }
    public static var quotaRefreshHelp: String { tr("quota.refresh.help") }
    public static var quotaEmptyTitle: String { tr("quota.empty.title") }
    public static var quotaEmptyMessage: String { tr("quota.empty.message") }
    public static var quotaStatus: String { tr("quota.status") }
    public static var quotaStatusTitle: String { tr("quota.statusTitle") }
    public static var quotaNoReliableData: String { tr("quota.noReliableData") }
    public static var quotaUnreadable: String { tr("quota.unreadable") }
    public static var quotaLastChecked: String { tr("quota.lastChecked") }
    public static var quotaNeverChecked: String { tr("quota.neverChecked") }
    public static var quotaLastAttempt: String { tr("quota.lastAttempt") }
    public static var quotaConnectionStatus: String { tr("quota.connectionStatus") }
    public static var quotaSummaryConnected: String { tr("quota.summary.connected") }
    public static var quotaSummaryObservable: String { tr("quota.summary.observable") }
    public static var quotaSummaryWarnings: String { tr("quota.summary.warnings") }
    public static var quotaSummaryExhausted: String { tr("quota.summary.exhausted") }
    public static var quotaWindows: String { tr("quota.windows") }
    public static var quotaWindowDetails: String { tr("quota.windowDetails") }
    public static var quotaPoolId: String { tr("quota.poolId") }
    public static var quotaLimitingWindow: String { tr("quota.limitingWindow") }
    public static var quotaLimitingWindowEmpty: String { tr("quota.limitingWindow.empty") }
    public static var quotaProviderLabel: String { tr("quota.provider") }
    public static var quotaWindowLabel: String { tr("quota.window") }
    public static var quotaRemainingLabel: String { tr("quota.remainingLabel") }
    public static var quotaResetLabel: String { tr("quota.resetLabel") }
    public static var quotaEstimatedBadge: String { tr("quota.estimatedBadge") }
    public static var quotaHistoryTitle: String { tr("quota.history.title") }
    public static var quotaHistoryEmpty: String { tr("quota.history.empty") }
    public static var quotaNoReliablePercentage: String { tr("quota.noReliablePercentage") }

    // Shared subscription plan (P4.2.6.5)
    public static var quotaPlanSharedQuota: String { tr("quota.plan.sharedQuota") }
    public static var quotaPlanSharedModels: String { tr("quota.plan.sharedModels") }
    public static var quotaPlanModelUsage: String { tr("quota.plan.modelUsage") }
    public static var quotaPlanModelUsageFooter: String { tr("quota.plan.modelUsageFooter") }
    public static var quotaPlanEquivalents: String { tr("quota.plan.equivalents") }
    public static var quotaPlanEquivalentsFooter: String { tr("quota.plan.equivalentsFooter") }
    public static var quotaPlanEstimatedCapacity: String { tr("quota.plan.estimatedCapacity") }
    public static var quotaPlanEstimatedCapacityFooter: String {
        tr("quota.plan.estimatedCapacityFooter")
    }
    public static var quotaPlanInsufficientHistory: String {
        tr("quota.plan.insufficientHistory")
    }
    public static var quotaPlanLevel: String { tr("quota.plan.level") }
    public static var quotaPlanPoolId: String { tr("quota.plan.poolId") }
    public static var quotaPlanSharedSemantics: String { tr("quota.plan.sharedSemantics") }
    public static var quotaPlanNoPlanFigure: String { tr("quota.plan.noPlanFigure") }

    /// The workload a plan's quota is being read for, e.g. "编码 / 文本".
    ///
    /// Shown on the card so the owner can see *why* a scope they know exists
    /// (MiniMax video) is not on it. Unknown workloads render nothing rather
    /// than a placeholder, because a label with no meaning is noise.
    public static func quotaWorkloadScope(_ value: String) -> String {
        switch value {
        case "CODING_TEXT": return tr("quota.plan.workload.codingText")
        case "VIDEO_GENERATION": return tr("quota.plan.workload.video")
        case "IMAGE_GENERATION": return tr("quota.plan.workload.image")
        case "AUDIO": return tr("quota.plan.workload.audio")
        default: return tr("quota.plan.workload.unknown")
        }
    }

    public static var quotaPlanWorkloadScopeLabel: String {
        tr("quota.plan.workloadScopeLabel")
    }

    public static var quotaPlanOtherScopes: String { tr("quota.plan.otherScopes") }
    public static var quotaPlanOtherScopesFooter: String {
        tr("quota.plan.otherScopesFooter")
    }
    public static var quotaPlanCredentialSource: String { tr("quota.plan.credentialSource") }
    public static var quotaPlanUnitLabel: String { tr("quota.plan.unitLabel") }
    public static var quotaPlanConsumedLabel: String { tr("quota.plan.consumedLabel") }
    public static var quotaPlanSamplesLabel: String { tr("quota.plan.samplesLabel") }
    public static var quotaPlanSmallSample: String { tr("quota.plan.smallSample") }
    public static var quotaBindingTitle: String { tr("quota.binding.title") }
    public static var quotaBindingResetsIn: String { tr("quota.binding.resetsIn") }
    public static var quotaBindingNone: String { tr("quota.binding.none") }

    /// Localized label for the confidence hierarchy.
    ///
    /// The raw enum stays visible under Advanced Details; this is the sentence
    /// the owner reads. EXACT and ESTIMATED must never render identically.
    public static func quotaConfidenceLevel(_ value: String) -> String {
        switch value {
        case "EXACT": return tr("quota.confidence.exact")
        case "ESTIMATED": return tr("quota.confidence.estimated")
        default: return tr("quota.confidence.unknownData")
        }
    }

    /// Where the quota credential came from. Never the credential itself.
    public static func quotaCredentialSource(_ value: String) -> String {
        switch value {
        case "ENVIRONMENT": return tr("quota.credential.environment")
        case "OPENCODE_AUTH_STORE": return tr("quota.credential.authStore")
        default: return tr("quota.credential.none")
        }
    }

    public static func quotaCapacityTasks(_ count: Int) -> String {
        tr("quota.capacity.tasks", [count])
    }

    public static func quotaCapacityBasis(_ count: Int) -> String {
        tr("quota.capacity.basis", [count])
    }

    public static func quotaHistoryFooter(retentionLimit: Int) -> String {
        tr("quota.history.footer", [retentionLimit])
    }

    /// Localized sentence for a sanitized quota failure code.
    ///
    /// Unknown codes fall back to the generic provider-error sentence; the raw
    /// code itself stays visible under Advanced Details.
    public static func quotaFailureReason(_ code: String?) -> String {
        switch code {
        case "NO_READONLY_QUOTA_SOURCE": return tr("quota.reason.noReadonlySource")
        case "CREDENTIAL_NOT_AVAILABLE": return tr("quota.reason.credentialMissing")
        case "PROVIDER_NOT_CONNECTED": return tr("quota.reason.notConnected")
        case "AUTHENTICATION_INTEGRATION_BLOCKED", "HTTP_401", "HTTP_403":
            return tr("quota.reason.authRequired")
        case "HTTP_429": return tr("quota.reason.rateLimited")
        // A read that succeeded but yielded no figure for the workload we
        // schedule is a different truth from a read that failed. None of these
        // may be raised because a scope outside that workload disagreed —
        // `QUOTA_VARIES_BY_MODEL` and `SHARED_POOL_VIEWED_PER_MODEL` said
        // exactly that and are no longer produced.
        case "GENERAL_QUOTA_NOT_AVAILABLE": return tr("quota.reason.codingScopeUnavailable")
        case "GENERAL_QUOTA_READ_FAILED": return tr("quota.reason.codingScopeUnreadable")
        case "GENERAL_WINDOW_SEMANTICS_UNKNOWN":
            return tr("quota.reason.codingWindowSemanticsUnknown")
        case "CODING_SCOPE_VIEWS_DISAGREE": return tr("quota.reason.codingScopeDisagree")
        case "PROVIDER_LIMIT_TYPE_UNRECOGNIZED":
            return tr("quota.reason.limitTypeUnrecognized")
        case "PROVIDER_WINDOW_DIMENSION_UNRECOGNIZED":
            return tr("quota.reason.windowUnrecognized")
        case "PROVIDER_REPORTED_FAILURE": return tr("quota.reason.providerReportedFailure")
        case "PROVIDER_REPORTED_NO_QUOTA_ENTRIES": return tr("quota.reason.noEntries")
        case "PROVIDER_FIELDS_UNAVAILABLE": return tr("quota.reason.fieldsUnavailable")
        case "PROVIDER_QUOTA_NOT_INTERPRETABLE": return tr("quota.reason.notInterpretable")
        default: return tr("quota.reason.providerError")
        }
    }

    // Task detail
    public static var detailCurrentPhase: String { tr("detail.currentPhase") }
    public static var detailElapsed: String { tr("detail.elapsed") }
    public static var detailStop: String { tr("detail.stop") }
    public static var detailStopHelp: String { tr("detail.stop.help") }
    public static var detailFilesChanged: String { tr("detail.filesChanged") }
    public static var detailNoWorktree: String { tr("detail.noWorktree") }
    public static var detailVerifierProfile: String { tr("detail.verifierProfile") }
    public static var detailNoWorkerRun: String { tr("detail.noWorkerRun") }
    public static var detailRawWorkerOutput: String { tr("detail.rawWorkerOutput") }
    public static var detailTaskId: String { tr("detail.taskId") }
    public static var detailRequestId: String { tr("detail.requestId") }
    public static var detailRoutingDecision: String { tr("detail.routingDecision") }
    public static var detailRoutingRequest: String { tr("detail.routingRequest") }
    public static var detailBaseSha: String { tr("detail.baseSha") }
    public static var detailRunId: String { tr("detail.runId") }
    public static var detailRunStatus: String { tr("detail.runStatus") }
    public static var detailQuotaEvidence: String { tr("detail.quotaEvidence") }
    public static var runProcessExited: String { tr("run.processExited") }
    public static var runProcessAlive: String { tr("run.processAlive") }

    // Task detail panels, lifecycle phases, and inline values (B1 l10n)
    public static var detailPanelSummary: String { tr("detail.panel.summary") }
    public static var detailPanelProgress: String { tr("detail.panel.progress") }
    public static var detailPanelLiveActivity: String { tr("detail.panel.liveActivity") }
    public static var detailPanelChanges: String { tr("detail.panel.changes") }
    public static var detailPanelVerification: String { tr("detail.panel.verification") }
    public static var phasePreparing: String { tr("phase.preparing") }
    public static var phaseRouting: String { tr("phase.routing") }
    public static var phaseWorkspace: String { tr("phase.workspace") }
    public static var phaseQuota: String { tr("phase.quota") }
    public static var phaseStartingWorker: String { tr("phase.startingWorker") }
    public static var phaseEditing: String { tr("phase.editing") }
    public static var phaseTesting: String { tr("phase.testing") }
    public static var phaseVerifying: String { tr("phase.verifying") }
    public static var phaseFinished: String { tr("phase.finished") }
    public static var candidateSelected: String { tr("candidate.selected") }
    public static var candidateEligible: String { tr("candidate.eligible") }
    public static var candidateIneligible: String { tr("candidate.ineligible") }
    public static var candidateUnknownModel: String { tr("candidate.unknownModel") }
    public static var valueNone: String { tr("value.none") }
    public static var valueUnknown: String { tr("value.unknown") }
    public static var valueAutomatic: String { tr("value.automatic") }
    public static var valueNotVerifiedYet: String { tr("value.notVerifiedYet") }
    public static var valueStagePassed: String { tr("value.stagePassed") }
    public static var valueStageFailed: String { tr("value.stageFailed") }
    public static var valueNotShadowMode: String { tr("value.notShadowMode") }
    public static var labelTask: String { tr("label.task") }

    /// Relative "updated" line for a task row. The instant is formatted by
    /// `Timestamps`, which follows the locale; this only supplies the frame.
    public static func updatedRelative(_ relative: String) -> String {
        String(format: tr("label.updatedRelative"), relative)
    }

    // Evidence levels (OBSERVED / DERIVED / FORECAST) and timestamps
    public static var evidenceObserved: String { tr("evidence.observed") }
    public static var evidenceDerived: String { tr("evidence.derived") }
    public static var evidenceForecast: String { tr("evidence.forecast") }
    public static var evidenceUnavailable: String { tr("evidence.unavailable") }
    public static var timestampJustNow: String { tr("timestamp.justNow") }

    // Keyboard commands (hidden buttons; still owner-visible to VoiceOver)
    public static var commandFocusSearch: String { tr("command.focusSearch") }
    public static var commandPreviousTask: String { tr("command.previousTask") }
    public static var commandNextTask: String { tr("command.nextTask") }
    public static var commandCopyTaskId: String { tr("command.copyTaskId") }
    public static var commandReloadDetail: String { tr("command.reloadDetail") }

    // MARK: - B3 task workspace

    /// Product groupings over authoritative task states.
    public static func taskStateGroup(_ rawValue: String) -> String {
        tr("taskGroup.\(rawValue)")
    }

    public static var tasksFilterMenu: String { tr("tasks.filterMenu") }
    public static var tasksFilterAllTasks: String { tr("tasks.filter.allTasks") }
    public static var tasksFilterGroups: String { tr("tasks.filter.groups") }
    public static var tasksFilterStates: String { tr("tasks.filter.states") }
    public static var tasksFilterProject: String { tr("tasks.filter.project") }
    public static var tasksFilterAllProjects: String { tr("tasks.filter.allProjects") }
    public static var tasksFilterClear: String { tr("tasks.filter.clear") }
    public static var tasksSearchPrompt: String { tr("tasks.searchPrompt") }
    public static var tasksNoProject: String { tr("tasks.noProject") }

    public static func tasksVisibleCount(shown: Int, total: Int) -> String {
        tr("tasks.visibleCount", [shown, total])
    }

    /// The honest scope line for a collection the daemon could not serve whole.
    /// Both numbers are authoritative: what arrived, and what the store holds.
    public static func tasksTruncated(shown: Int, total: Int) -> String {
        tr("tasks.truncated", [shown, total])
    }

    public static func tasksTruncatedHelp(limit: Int) -> String {
        tr("tasks.truncatedHelp", [limit])
    }

    // Task collection empty states. Each names its own cause, because "no tasks
    // exist" and "your search matched nothing" need different next actions.
    public static var emptyTasksNoneTitle: String { tr("empty.tasks.none.title") }
    public static var emptyTasksNoneMessage: String { tr("empty.tasks.none.message") }
    public static var emptyTasksSearchTitle: String { tr("empty.tasks.search.title") }
    public static func emptyTasksSearchMessage(_ query: String) -> String {
        tr("empty.tasks.search.message", [query])
    }
    public static var emptyTasksSearchHint: String { tr("empty.tasks.search.hint") }
    public static var emptyTasksFilterTitle: String { tr("empty.tasks.filter.title") }
    public static var emptyTasksFilterMessage: String { tr("empty.tasks.filter.message") }
    public static var emptyTasksLoading: String { tr("empty.tasks.loading") }
    public static var emptyTasksNoSelectionTitle: String { tr("empty.tasks.noSelection.title") }
    public static var emptyTasksNoSelectionMessage: String {
        tr("empty.tasks.noSelection.message")
    }

    // Task Detail sections
    public static var taskDetailSectionOverview: String { tr("taskDetail.section.overview") }
    public static var taskDetailSectionLifecycle: String { tr("taskDetail.section.lifecycle") }
    public static var taskDetailSectionExecution: String { tr("taskDetail.section.execution") }
    public static var taskDetailSectionAttention: String { tr("taskDetail.section.attention") }
    public static var taskDetailOpenFailed: String { tr("taskDetail.openTaskFailed") }

    public static func taskLifecycleStage(_ rawValue: String) -> String {
        tr("taskLifecycle.\(rawValue)")
    }

    public static func taskLifecycleStatus(_ status: TaskLifecycleStatus) -> String {
        tr("taskLifecycle.status.\(status.rawValue)")
    }

    public static var taskLifecycleFooter: String { tr("taskLifecycle.footer") }

    public static func taskAttentionTitle(_ rawValue: String) -> String {
        tr("taskAttention.\(rawValue)")
    }

    public static var taskAttentionNoDetail: String { tr("taskAttention.noDetail") }

    /// Localized role name. An unrecognized role keeps its machine value: a
    /// label this build invented would claim knowledge it does not have.
    public static func routingRoleName(_ role: RoutingRole) -> String {
        switch role {
        case .primary: return tr("routing.role.primary")
        case .reviewer: return tr("routing.role.reviewer")
        case .finalAuditor: return tr("routing.role.finalAuditor")
        case .other(let raw): return raw
        }
    }

    public static func routingRoleStatusName(_ status: RoutingRoleStatus) -> String {
        switch status {
        case .unassigned: return tr("routing.roleStatus.unassigned")
        case .assigned: return tr("routing.roleStatus.assigned")
        case .running: return tr("routing.roleStatus.running")
        case .completed: return tr("routing.roleStatus.completed")
        case .cancelled: return tr("routing.roleStatus.cancelled")
        case .unknown(let raw): return raw
        }
    }

    public static func routingOutcomeName(_ outcome: RoutingOutcome) -> String {
        switch outcome {
        case .pass: return tr("routing.outcome.pass")
        case .fail: return tr("routing.outcome.fail")
        case .error: return tr("routing.outcome.error")
        case .none: return tr("routing.outcome.none")
        case .unknown(let raw): return raw
        }
    }

    public static var routingPlanLabel: String { tr("routing.plan") }
    public static func routingPlanRevision(_ revision: Int) -> String {
        tr("routing.planRevision", [revision])
    }
    public static var routingPlanRevisionLabel: String { tr("routing.planRevisionLabel") }
    public static var routingSuperseded: String { tr("routing.superseded") }
    public static var routingLegacySynthesized: String { tr("routing.legacySynthesized") }
    public static var routingDeclaredRoles: String { tr("routing.declaredRoles") }
    public static func routingRerouteCount(_ count: Int) -> String {
        tr("routing.rerouteCount", [count])
    }
    public static var routingRerouteReason: String { tr("routing.rerouteReason") }
    public static var routingPreviousDecisions: String { tr("routing.previousDecisions") }
    public static var routingDecidedAtLabel: String { tr("routing.decidedAtLabel") }
    public static var routingNoDetails: String { tr("routing.noDetails") }

    // Changes
    public static var changesTitle: String { tr("changes.title") }
    public static func changesCount(_ count: Int) -> String { tr("changes.count", [count]) }
    public static var changesNone: String { tr("changes.none") }
    public static var changesUnexpected: String { tr("changes.unexpected") }
    public static func changesUnexpectedCount(_ count: Int) -> String {
        tr("changes.unexpectedCount", [count])
    }
    public static var changesFooter: String { tr("changes.footer") }
    public static var changesRevealWorktree: String { tr("changes.revealWorktree") }

    public static func changesUnavailable(_ reason: TaskChangesUnavailableReason) -> String {
        switch reason {
        case .verificationNotRun: return tr("changes.notMeasured.verificationNotRun")
        case .noWorkspace: return tr("changes.notMeasured.noWorkspace")
        }
    }

    /// Localized verification status. An unrecognized status shows its machine
    /// value rather than being mapped onto a status this build understands.
    public static func verificationStatusName(_ status: TaskVerificationStatus) -> String {
        switch status {
        case .verified: return tr("verification.status.verified")
        case .verifiedEvidenceMissing: return tr("verification.status.evidenceMissing")
        case .verifiedEvidenceUnavailable: return tr("verification.status.evidenceUnavailable")
        case .failedVerification: return tr("verification.status.failed")
        case .inProgress: return tr("verification.status.inProgress")
        case .notVerified: return tr("verification.status.notVerified")
        case .unrecognized(let raw): return raw
        }
    }

    public static var verificationChecks: String { tr("verification.checks") }
    public static func verificationCheckExit(_ code: Int) -> String {
        tr("verification.checkExit", [code])
    }
    public static var verificationNoResult: String { tr("verification.noResult") }
    public static var verificationNotRunYet: String { tr("verification.notRunYet") }
    public static var verificationOverall: String { tr("verification.overall") }
    public static var verificationApprovals: String { tr("verification.approvals") }
    public static var verificationEvidenceProblemFooter: String {
        tr("verification.evidenceProblemFooter")
    }
    public static var verificationStageFooter: String { tr("verification.stageFooter") }

    // Inspector
    public static var inspectorTitle: String { tr("inspector.title") }
    public static var inspectorToggle: String { tr("inspector.toggle") }
    public static var inspectorIdentity: String { tr("inspector.identity") }
    public static var inspectorTimestamps: String { tr("inspector.timestamps") }
    public static var inspectorWorkspaceSection: String { tr("inspector.workspaceSection") }
    public static var inspectorRunsSection: String { tr("inspector.runsSection") }
    public static var inspectorRoutingProvenance: String { tr("inspector.routingProvenance") }
    public static var inspectorEmpty: String { tr("inspector.empty") }

    public static var labelCreatedAt: String { tr("label.createdAt") }
    public static var labelUpdatedAt: String { tr("label.updatedAt") }
    public static var labelStateVersion: String { tr("label.stateVersion") }
    public static var labelRepositoryPath: String { tr("label.repositoryPath") }
    public static var labelProjectId: String { tr("label.projectId") }
    public static var labelCurrentWorker: String { tr("label.currentWorker") }
    public static var labelNoWorkerYet: String { tr("label.noWorkerYet") }

    // MARK: - B4 Resources workspace

    public static var resourcesSearchPrompt: String { tr("resources.searchPrompt") }

    public static func resourcesVisibleCount(shown: Int, total: Int) -> String {
        tr("resources.visibleCount", [shown, total])
    }

    public static func resourceKind(_ rawValue: String) -> String {
        tr("resources.kind.\(rawValue)")
    }

    public static var resourcesFilterAll: String { tr("resources.filter.all") }
    public static var resourcesFilterKind: String { tr("resources.filter.kind") }

    public static var emptyResourcesNoneTitle: String { tr("empty.resources.none.title") }
    public static var emptyResourcesNoneMessage: String { tr("empty.resources.none.message") }
    public static var emptyResourcesLoading: String { tr("empty.resources.loading") }
    public static var emptyResourcesSearchTitle: String { tr("empty.resources.search.title") }

    public static func emptyResourcesSearchMessage(_ query: String) -> String {
        tr("empty.resources.search.message", [query])
    }

    public static var emptyResourcesNoSelectionTitle: String {
        tr("empty.resources.noSelection.title")
    }
    public static var emptyResourcesNoSelectionMessage: String {
        tr("empty.resources.noSelection.message")
    }
    public static var resourcesDisconnectedHint: String { tr("resources.disconnectedHint") }

    // Resource detail sections
    public static var resourceSectionAvailability: String { tr("resource.section.availability") }
    public static var resourceSectionQuota: String { tr("resource.section.quota") }
    public static var resourceSectionUsage: String { tr("resource.section.usage") }
    public static var resourceSectionProjection: String { tr("resource.section.projection") }
    public static var resourceSectionExecutionTargets: String {
        tr("resource.section.executionTargets")
    }
    public static var resourceSectionObservability: String {
        tr("resource.section.observability")
    }
    public static var resourceSectionModels: String { tr("resource.section.models") }

    public static var resourceNoQuotaTelemetry: String { tr("resource.noQuotaTelemetry") }
    public static var resourceNoExecutionTargets: String { tr("resource.noExecutionTargets") }
    public static var resourceNotConnected: String { tr("resource.notConnected") }
    public static var resourceConnectAction: String { tr("resource.connectAction") }
    public static var resourceBindingLimiting: String { tr("resource.bindingLimiting") }
    public static var resourceQuotaObservable: String { tr("resource.quotaObservable") }
    public static var resourceQuotaNotObservable: String { tr("resource.quotaNotObservable") }
    public static var resourceFreshnessUnknown: String { tr("resource.freshnessUnknown") }
    public static var resourceStaleWarning: String { tr("resource.staleWarning") }

    /// "Several quota bindings" — the row state that replaces a fabricated
    /// average when a resource owns more than one readable window.
    public static func resourceSeveralBindings(_ count: Int) -> String {
        tr("resource.severalBindings", [count])
    }

    public static func resourceObservedAge(_ age: String) -> String {
        tr("resource.observedAge", [age])
    }

    public static func resourceTargetsSummary(verified: Int, total: Int) -> String {
        tr("resource.targetsSummary", [verified, total])
    }

    public static func resourceModelsSummary(_ count: Int) -> String {
        tr("resource.modelsSummary", [count])
    }

    public static var resourceRefreshQuota: String { tr("resource.refreshQuota") }
    public static var resourceRefreshProviders: String { tr("resource.refreshProviders") }
    public static var resourceRefreshQuotaHelp: String { tr("resource.refreshQuotaHelp") }
    public static var resourceRefreshProvidersHelp: String {
        tr("resource.refreshProvidersHelp")
    }

    public static func resourceRefreshFailed(_ reason: String) -> String {
        tr("resource.refreshFailed", [reason])
    }

    // MARK: - B4 Quota formatting

    public static func quotaRatePerHour(_ percent: String) -> String {
        tr("quota.rate.perHour", [percent])
    }

    public static func quotaRateBelowThreshold(_ percent: String) -> String {
        tr("quota.rate.belowThreshold", [percent])
    }

    public static func quotaHorizonDaysHours(_ days: Int, _ hours: Int) -> String {
        tr("quota.horizon.daysHours", [days, hours])
    }

    public static func quotaHorizonHoursMinutes(_ hours: Int, _ minutes: Int) -> String {
        tr("quota.horizon.hoursMinutes", [hours, minutes])
    }

    public static func quotaHorizonMinutes(_ minutes: Int) -> String {
        tr("quota.horizon.minutes", [minutes])
    }

    // MARK: - B4 Burn rate and projection

    public static var quotaBurnRateTitle: String { tr("quota.burnRate.title") }
    public static var quotaBurnRateFlat: String { tr("quota.burnRate.flat") }

    public static func quotaBurnRateBasis(samples: Int, span: String) -> String {
        tr("quota.burnRate.basis", [samples, span])
    }

    // MARK: - M1 WP1 Burn pressure chips

    /// UNMETERED — provider never reported a quota figure for this window.
    public static var quotaPressureUnmetered: String { tr("quota.pressure.unmetered") }
    /// STALE — cached reading older than ``reset_at``; the bar still draws
    /// but pressure refuses to fire STARVED on expired data.
    public static var quotaPressureStale: String { tr("quota.pressure.stale") }
    /// EXHAUSTED — used_fraction reached or passed 100%.
    public static var quotaPressureExhausted: String { tr("quota.pressure.exhausted") }
    /// STARVED — lots of quota remaining but reset imminent; "expire
    /// unused", not "almost empty".
    public static var quotaPressureStarved: String { tr("quota.pressure.starved") }
    /// AHEAD — used faster than the ideal line; orchestrator should
    /// throttle. "消耗偏快" / "Using too fast".
    public static var quotaPressureAhead: String { tr("quota.pressure.ahead") }
    /// BEHIND — used slower than the ideal line; room to dispatch more.
    /// "消耗偏慢" / "Using too slow".
    public static var quotaPressureBehind: String { tr("quota.pressure.behind") }
    /// ON_TRACK — within the band's tolerance of the ideal line.
    public static var quotaPressureOnTrack: String { tr("quota.pressure.onTrack") }

    /// Localize a burn-pressure enum value. Unknown values surface as
    /// the raw enum string so a future pressure addition does not crash
    /// pre-localization callers; the catalog check is what guarantees
    /// every value reaches the client.
    public static func quotaPressure(_ pressure: String) -> String {
        switch pressure {
        case "UNMETERED": return quotaPressureUnmetered
        case "STALE": return quotaPressureStale
        case "EXHAUSTED": return quotaPressureExhausted
        case "STARVED": return quotaPressureStarved
        case "AHEAD": return quotaPressureAhead
        case "BEHIND": return quotaPressureBehind
        case "ON_TRACK": return quotaPressureOnTrack
        default: return pressure
        }
    }

    // MARK: M1 WP2 — tier picker + tier chip labels

    /// ``newTask.minTier`` — picker title for the task-submit sheet.
    public static var newTaskMinTier: String { tr("newTask.minTier") }

    /// ``newTask.minTierHelp`` — help text shown under the tier picker.
    public static var newTaskMinTierHelp: String { tr("newTask.minTierHelp") }

    /// ``tier.t0`` — flagship tier.
    public static var tierT0: String { tr("tier.t0") }

    /// ``tier.t1`` — workhorse tier.
    public static var tierT1: String { tr("tier.t1") }

    /// ``tier.t2`` — fast tier.
    public static var tierT2: String { tr("tier.t2") }

    /// ``tier.t3`` — free tier.
    public static var tierT3: String { tr("tier.t3") }

    /// ``tier.unknown`` — table could not classify the target.
    public static var tierUnknown: String { tr("tier.unknown") }

    /// Localize a tier enum value. ``T0..T3`` map to the canonical
    /// names; any other string returns ``tierUnknown`` so a future tier
    /// addition does not crash pre-localization callers.
    public static func tierLabel(_ tier: String) -> String {
        switch tier {
        case "T0": return tierT0
        case "T1": return tierT1
        case "T2": return tierT2
        case "T3": return tierT3
        default: return tierUnknown
        }
    }

    // MARK: WP5b — stale-verification chip on target rows

    /// ``target.verifiedStale`` — the chip rendered when the latest
    /// evidence for an execution target is non-VERIFIED while an older
    /// VERIFIED row still exists (demote-fallback semantics).
    public static var targetVerifiedStale: String { tr("target.verifiedStale") }

    // MARK: M1 WP3 — BURN_DOWN objective

    /// ``policy.burnDown`` — picker label for the pressure-first preset.
    public static var policyBurnDown: String { tr("policy.burnDown") }

    /// ``policy.burnDown.detail`` — picker help text for BURN_DOWN.
    public static var policyBurnDownDetail: String { tr("policy.burnDown.detail") }

    public static var quotaProjectionTitle: String { tr("quota.projection.title") }
    public static var quotaProjectionRemainingAtReset: String {
        tr("quota.projection.remainingAtReset")
    }
    public static var quotaProjectionExhaustionAt: String { tr("quota.projection.exhaustionAt") }

    // MARK: M1 WP4 — free / unmetered resource group

    /// ``resource.group.unmetered`` — header for the unmetered
    /// section of the Resources page. OpenCode Zen free models and
    /// any other auth="none" / pool_kind="unmetered" providers
    /// render under this label.
    public static var resourceGroupUnmetered: String {
        tr("resource.group.unmetered")
    }

    /// ``quota.windowKind.unmetered`` — chip rendered on an
    /// unmetered card in place of the 5h/weekly progress bar.
    public static var quotaWindowKindUnmetered: String {
        tr("quota.windowKind.unmetered")
    }

    /// ``quota.unmetered.errorRate`` — label for the 1-hour
    /// non-VERIFIED ratio on the unmetered card.
    public static var quotaUnmeteredErrorRate: String {
        tr("quota.unmetered.errorRate")
    }

    /// ``quota.unmetered.rpm`` — label for the rolling 60s
    /// runs-per-minute metric on the unmetered card.
    public static var quotaUnmeteredRpm: String {
        tr("quota.unmetered.rpm")
    }

    /// ``quota.unmetered.cooldownUntil`` — label for the cooldown
    /// countdown chip on the unmetered card. The chip is hidden
    /// when no target is currently in COOLDOWN.
    public static var quotaUnmeteredCooldownUntil: String {
        tr("quota.unmetered.cooldownUntil")
    }

    /// ``quota.unmetered.noWindow`` — no-window affordance on the
    /// unmetered card (the "no quota window — usage is limited
    /// only by rate" copy).
    public static var quotaUnmeteredNoWindow: String {
        tr("quota.unmetered.noWindow")
    }
    public static var quotaProjectionLikelyUnused: String { tr("quota.projection.likelyUnused") }
    public static var quotaProjectionNoExhaustion: String {
        tr("quota.projection.noExhaustion")
    }
    public static var quotaProjectionFooter: String { tr("quota.projection.footer") }

    /// Why no forecast exists. Every reason is its own sentence: "never
    /// observed" and "the window already reset" are fixed by different actions.
    public static func quotaProjectionUnavailable(_ rawValue: String) -> String {
        tr("quota.projection.unavailable.\(rawValue)")
    }

    /// Stated where a scarcity verdict would otherwise go.
    ///
    /// The daemon computes `scarcity_class` from a pace the control API does not
    /// expose, and the client must not invent a second one. The facts are shown
    /// instead, and this says so rather than leaving a silent gap.
    public static var quotaScarcityUnavailable: String { tr("quota.scarcity.unavailable") }

    // MARK: - B4 Quota history chart

    public static var quotaHistoryAxisTime: String { tr("quota.history.axisTime") }
    public static var quotaHistoryAxisRemaining: String { tr("quota.history.axisRemaining") }
    public static var quotaHistorySeriesLegend: String { tr("quota.history.seriesLegend") }
    public static var quotaHistoryResetBoundary: String { tr("quota.history.resetBoundary") }
    public static var quotaHistoryGapNote: String { tr("quota.history.gapNote") }
    public static var quotaHistorySingleObservation: String {
        tr("quota.history.singleObservation")
    }

    public static func quotaHistoryObservationCount(_ count: Int) -> String {
        tr("quota.history.observationCount", [count])
    }

    /// Spoken equivalent of one series' line, for a reader who cannot see it.
    public static func quotaHistoryAccessibleSummary(
        series: String, count: Int, latest: String, span: String
    ) -> String {
        tr("quota.history.accessibleSummary", [series, count, latest, span])
    }

    public static func quotaResetsIn(_ horizon: String) -> String {
        tr("quota.resetsIn", [horizon])
    }

    public static var quotaResetPassed: String { tr("quota.resetPassed") }

    public static func quotaObservedAge(_ age: String) -> String {
        tr("quota.observedAge", [age])
    }

    public static var quotaEquivalentCapacityExplanation: String {
        tr("quota.equivalentCapacity.explanation")
    }
}
