import Foundation

/// Localized vocabulary introduced by the Daily Driver redesign.
///
/// Kept in its own table while the redesign is in Draft so the existing mature
/// `L10n` surface does not churn with every visual iteration. Before merge this
/// remains a normal Bundle.module-backed localization path, with English and
/// zh-Hans at parity.
public enum DailyDriverL10n {
    private static func tr(_ key: String, _ arguments: CVarArg...) -> String {
        let format = Bundle.module.localizedString(
            forKey: key,
            value: key,
            table: "DailyDriver"
        )
        guard !arguments.isEmpty else { return format }
        return String(format: format, locale: Locale.current, arguments: arguments)
    }

    public static var readyToWork: String { tr("home.ready") }
    public static var needsAttention: String { tr("home.needsAttention") }
    public static var now: String { tr("home.now") }
    public static var attention: String { tr("home.attention") }
    public static var capacity: String { tr("home.capacity") }
    public static var recent: String { tr("home.recent") }
    public static var daemonUnavailable: String { tr("home.daemonUnavailable") }
    public static var fullAutomationLocked: String { tr("home.fullAutomationLocked") }
    public static var configureProjectAutomation: String { tr("home.configureProjectAutomation") }
    public static var modeHelp: String { tr("home.modeHelp") }
    public static var supervisedNeedsProject: String { tr("home.supervisedNeedsProject") }
    public static func supervisedProjectCount(_ count: Int) -> String {
        tr(count == 1 ? "home.supervisedProjectCount.one" : "home.supervisedProjectCount.other", count)
    }
    public static func viewMoreActiveTasks(_ count: Int) -> String {
        tr(count == 1 ? "home.moreActive.one" : "home.moreActive.other", count)
    }
    public static var nothingRunning: String { tr("home.nothingRunning") }
    public static var startTaskHint: String { tr("home.startTaskHint") }
    public static var noBlockers: String { tr("home.noBlockers") }
    public static var noEligibleProject: String { tr("home.noEligibleProject") }
    public static var noEligibleProjectDetail: String { tr("home.noEligibleProjectDetail") }
    public static var reviewAttention: String { tr("home.reviewAttention") }
    public static var noCapacity: String { tr("home.noCapacity") }
    public static var openResources: String { tr("home.openResources") }
    public static var viewAllResources: String { tr("home.viewAllResources") }
    public static var noRecent: String { tr("home.noRecent") }
    public static var openActivity: String { tr("home.openActivity") }
    public static func modeChanged(_ mode: String) -> String { tr("home.modeChanged", mode) }
    public static func modeChangeFailed(_ detail: String) -> String { tr("home.modeChangeFailed", detail) }
    public static var modeChangeMalformed: String { tr("home.modeChangeMalformed") }

    public static var unmetered: String { tr("quota.unmetered") }
    public static func unmeteredRPM(_ rpm: Int) -> String { tr("quota.unmeteredRpm", rpm) }
    public static var quotaUnknown: String { tr("quota.unknown") }
    public static var quotaLoading: String { tr("quota.loading") }
    public static var capacityUnavailable: String { tr("quota.unavailable") }
    public static func quotaExhausted(_ count: Int) -> String { tr("quota.exhausted", count) }
    public static func quotaWarnings(_ count: Int) -> String { tr("quota.warnings", count) }
    public static func quotaUnknownCount(_ count: Int) -> String { tr("quota.unknownCount", count) }
    public static func quotaObserved(_ observed: Int, _ connected: Int) -> String {
        tr("quota.observed", observed, connected)
    }
    public static var quotaAvailable: String { tr("quota.state.available") }
    public static var quotaLimited: String { tr("quota.state.limited") }
    public static var quotaExhaustedState: String { tr("quota.state.exhausted") }
    public static var weekWindow: String { tr("quota.window.week") }
    public static var monthWindow: String { tr("quota.window.month") }
    public static var resetDue: String { tr("quota.resetDue") }
    public static func resetMinutes(_ value: Int) -> String { tr("quota.resetMinutes", value) }
    public static func resetHours(_ value: Int) -> String { tr("quota.resetHours", value) }
    public static func resetDays(_ value: Int) -> String { tr("quota.resetDays", value) }

    public static var manual: String { tr("mode.manual") }
    public static var supervisedAuto: String { tr("mode.supervisedAuto") }
    public static var fullAutomation: String { tr("mode.fullAutomation") }
    public static var balanced: String { tr("mode.balanced") }
    public static var qualityFirst: String { tr("mode.qualityFirst") }
    public static var saveQuota: String { tr("mode.saveQuota") }
    public static var lowLatency: String { tr("mode.lowLatency") }
    public static var modeUnavailable: String { tr("mode.unavailable") }
    public static var exact: String { tr("confidence.exact") }
    public static var estimated: String { tr("confidence.estimated") }
    public static var unknown: String { tr("confidence.unknown") }

    public static var scheduling: String { tr("menu.scheduling") }
    public static var stoppingSupervisedAuto: String { tr("menu.stoppingSupervisedAuto") }
    public static var stopSupervisedAuto: String { tr("menu.stopSupervisedAuto") }
    public static var stopSupervisedAutoHelp: String { tr("menu.stopSupervisedAutoHelp") }
    public static var currentWork: String { tr("menu.currentWork") }
    public static var aiCapacity: String { tr("menu.aiCapacity") }
    public static var stoppedSupervisedAuto: String { tr("menu.stoppedSupervisedAuto") }
    public static func daemonReturnedMode(_ mode: String) -> String { tr("menu.daemonReturnedMode", mode) }
    public static func emergencyStopFailed(_ detail: String) -> String { tr("menu.emergencyStopFailed", detail) }
    public static var emergencyStopMalformed: String { tr("menu.emergencyStopMalformed") }
    public static func menuQuotaExhausted(_ exhausted: Int, _ warnings: Int) -> String {
        tr("menu.quotaExhausted", exhausted, warnings)
    }
    public static func menuQuotaWarnings(_ warnings: Int, _ observed: Int, _ connected: Int) -> String {
        tr("menu.quotaWarnings", warnings, observed, connected)
    }
    public static func menuQuotaUnknown(_ unknown: Int, _ observed: Int, _ connected: Int) -> String {
        tr("menu.quotaUnknown", unknown, observed, connected)
    }
    public static func menuQuotaObserved(_ observed: Int, _ connected: Int) -> String {
        tr("menu.quotaObserved", observed, connected)
    }

    public static var projectAutomationTitle: String { tr("project.title") }
    public static var projectAutomationSubtitle: String { tr("project.subtitle") }
    public static var done: String { tr("action.done") }
    public static var noRegisteredProjects: String { tr("project.noneTitle") }
    public static var registerProjectFirst: String { tr("project.noneMessage") }
    public static var projectGone: String { tr("project.gone") }
    public static var projectSaved: String { tr("project.saved") }
    public static func projectUpdateFailed(_ detail: String) -> String { tr("project.updateFailed", detail) }
    public static var projectMalformed: String { tr("project.malformed") }
    public static var enabled: String { tr("project.enabled") }
    public static var allowSupervisedAuto: String { tr("project.allowSupervisedAuto") }
    public static var allowUnattended: String { tr("project.allowUnattended") }
    public static var graceWindow: String { tr("project.graceWindow") }
    public static var seconds: String { tr("project.seconds") }
    public static var apply: String { tr("action.apply") }
    public static var graceRange: String { tr("project.graceRange") }
    public static var graceHelp: String { tr("project.graceHelp") }
    public static func disableProjectTitle(_ name: String) -> String { tr("project.disableTitle", name) }
    public static var disable: String { tr("action.disable") }
    public static var cancel: String { tr("action.cancel") }
    public static var disableProjectMessage: String { tr("project.disableMessage") }

    public static var autoSectionTitle: String { tr("auto.sectionTitle") }
    public static var autoSectionFootnote: String { tr("auto.footnote") }
    public static var autoPlanFrozen: String { tr("auto.planFrozen") }
    public static var autoApprovalRequired: String { tr("auto.approvalRequired") }
    public static var autoCountingDown: String { tr("auto.countingDown") }
    public static var autoExpiredRefreshing: String { tr("auto.expiredRefreshing") }
    public static var autoPlannedTarget: String { tr("auto.plannedTarget") }
    public static var autoNoTarget: String { tr("auto.noTarget") }
    public static var autoWhyPlan: String { tr("auto.whyPlan") }
    public static var autoAcknowledged: String { tr("auto.acknowledged") }
    public static var autoUntilDispatch: String { tr("auto.untilDispatch") }
    public static var autoCountdownAccessibility: String { tr("auto.countdownAccessibility") }
    public static var autoAcknowledge: String { tr("auto.ack") }
    public static var autoDispatchNow: String { tr("auto.dispatchNow") }
    public static var autoVeto: String { tr("auto.veto") }
    public static var autoAckHelp: String { tr("auto.ackHelp") }
    public static var autoDispatchHelp: String { tr("auto.dispatchHelp") }
    public static var autoVetoHelp: String { tr("auto.vetoHelp") }
    public static var dismissResult: String { tr("auto.dismissResult") }
    public static var autoNoticeAcknowledged: String { tr("auto.noticeAcknowledged") }
    public static var autoNoticeVetoed: String { tr("auto.noticeVetoed") }
    public static func autoNoticeDispatch(_ status: String) -> String { tr("auto.noticeDispatch", status) }
    public static var autoNoticeStale: String { tr("auto.noticeStale") }
    public static func autoNoticeBlocked(_ code: String) -> String { tr("auto.noticeBlocked", code) }
    public static func autoNoticeFailed(_ detail: String) -> String { tr("auto.noticeFailed", detail) }
    public static var autoNoticeMalformed: String { tr("auto.noticeMalformed") }

    public static var taskAwaitingApproval: String { tr("task.awaitingApproval") }
    public static var taskGraceWindow: String { tr("task.graceWindow") }
    public static var taskReadyToRoute: String { tr("task.readyToRoute") }
    public static var taskReady: String { tr("task.ready") }
    public static var taskRunning: String { tr("task.running") }
    public static var taskVerifying: String { tr("task.verifying") }
    public static var taskVerified: String { tr("task.verified") }
    public static var taskCompleted: String { tr("task.completed") }
    public static var taskBlocked: String { tr("task.blocked") }
    public static var taskFailed: String { tr("task.failed") }
    public static var taskCancelled: String { tr("task.cancelled") }

    public static var resourcesAvailableTargets: String { tr("resources.availableTargets") }
    public static var resourcesNoRunnableTarget: String { tr("resources.noRunnableTarget") }
    public static func resourcesUnavailableTargets(_ count: Int) -> String {
        tr("resources.unavailableTargets", count)
    }

    public static var relativeNow: String { tr("relative.now") }
    public static func minutesAgo(_ value: Int) -> String { tr("relative.minutes", value) }
    public static func hoursAgo(_ value: Int) -> String { tr("relative.hours", value) }
    public static func daysAgo(_ value: Int) -> String { tr("relative.days", value) }
}
