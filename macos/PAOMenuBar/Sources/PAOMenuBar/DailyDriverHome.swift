import SwiftUI

import PAOControlKit

/// Daily operating surface: current work, actionable attention, commercial AI
/// capacity and recent outcomes. The view consumes daemon projections only; it
/// never promotes presentation state into execution authority.
struct DailyDriverHome: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var isChangingMode = false
    @State private var modeNotice: String?
    @State private var showsProjectAutomation = false

    let onNavigate: (NavigationIntent) -> Void

    private var tasks: [TaskView] { store.tasks?.tasks ?? [] }
    private var quotaProviders: [QuotaProviderCardView] { store.quota?.providers ?? [] }
    private var risks: [RiskItemView] { store.dashboard?.risks ?? [] }
    private var projects: [ProjectView] { store.projects?.projects ?? [] }

    /// Production ACTIVE evidence reminders stay in Settings rather than making
    /// a deliberately pre-ACTIVE Daily Driver look broken forever.
    private var actionableRisks: [RiskItemView] {
        DailyDriverRiskPresentation.actionable(risks)
    }

    private var liveTasks: [TaskView] {
        tasks
            .filter { ["AUTO_PLANNED", "AUTO_GRACE", "RUNNING", "VERIFYING"].contains($0.state) }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    private var recentTerminalTasks: [TaskView] {
        tasks
            .filter { ["VERIFIED", "COMPLETED", "BLOCKED", "FAILED"].contains($0.state) }
            .sorted { $0.updatedAt > $1.updatedAt }
            .prefix(4)
            .map { $0 }
    }

    private var attentionTasks: [TaskView] {
        tasks
            .filter { ["BLOCKED", "FAILED"].contains($0.state) }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    private var schedulingMode: String {
        store.schedulingSettings?.mode ?? "UNKNOWN"
    }

    private var supervisedProjectCount: Int {
        projects.filter { $0.isOnline && $0.supervisedAutoAllowed }.count
    }

    /// Readiness answers one narrow product question: can PAO accept and execute
    /// new work now? Historical task failures remain visible below, but do not
    /// poison this status indefinitely.
    private var readiness: DailyDriverReadinessSnapshot {
        DailyDriverReadiness.derive(
            connection: store.connection,
            schedulingMode: store.schedulingSettings?.mode,
            projects: projects,
            providers: store.providers
        )
    }

    private var projectAutomationNeedsSetup: Bool {
        readiness.blocker == .supervisedAutoNeedsProject
    }

    private var isReady: Bool { readiness.isReady }

    var body: some View {
        DashboardPageContainer {
            readinessHeader

            if let modeNotice {
                Label(modeNotice, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            homeSection(DailyDriverL10n.now, symbol: "bolt.fill") {
                nowContent
            }

            homeSection(DailyDriverL10n.attention, symbol: "exclamationmark.triangle") {
                attentionContent
            }

            homeSection(DailyDriverL10n.capacity, symbol: "gauge.with.dots.needle.67percent") {
                capacityContent
            }

            homeSection(DailyDriverL10n.recent, symbol: "clock") {
                recentContent
            }
        }
        .accessibilityIdentifier("home.dailyDriver")
        .sheet(isPresented: $showsProjectAutomation) {
            ProjectAutomationSettingsView()
                .environmentObject(store)
        }
    }

    // MARK: - Readiness

    private var readinessHeader: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(isReady ? Color.green : Color.orange)
                .frame(width: 9, height: 9)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(isReady ? DailyDriverL10n.readyToWork : DailyDriverL10n.needsAttention)
                    .font(.title3.weight(.semibold))
                Text(readinessDetail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            if !store.connection.isConnected {
                Label(DailyDriverL10n.daemonUnavailable, systemImage: "bolt.slash")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.secondary)
            } else {
                automationModeMenu
            }
        }
        .padding(.vertical, 2)
    }

    private var readinessDetail: String {
        switch readiness.blocker {
        case .disconnected:
            return DailyDriverL10n.daemonUnavailable
        case .schedulingModeUnknown:
            return DailyDriverL10n.modeUnavailable
        case .noOnlineProject:
            return L10n.projectsEmpty
        case .supervisedAutoNeedsProject:
            return DailyDriverL10n.supervisedNeedsProject
        case .noRunnableTarget:
            return L10n.noVerifiedTargets
        case .noAvailableCapacity:
            return capacitySummary
        case .none:
            if schedulingMode == "SUPERVISED_AUTO" {
                return "\(DailyDriverL10n.supervisedProjectCount(supervisedProjectCount)) · \(capacitySummary)"
            }
            return capacitySummary
        }
    }

    private var automationModeMenu: some View {
        Menu {
            ForEach(AutomationModeCatalog.selectable, id: \.self) { mode in
                Button {
                    setAutomationMode(mode)
                } label: {
                    if schedulingMode == mode {
                        Label(modeLabel(mode), systemImage: "checkmark")
                    } else {
                        Text(modeLabel(mode))
                    }
                }
                .disabled(schedulingMode == mode || schedulingMode == "ACTIVE")
            }

            if !projects.isEmpty {
                Divider()
                Button {
                    showsProjectAutomation = true
                } label: {
                    Label(
                        DailyDriverL10n.configureProjectAutomation,
                        systemImage: "folder.badge.gearshape"
                    )
                }
            }

            if schedulingMode == "ACTIVE" {
                Divider()
                Label(DailyDriverL10n.fullAutomationLocked, systemImage: "lock")
            }
        } label: {
            if isChangingMode {
                ProgressView().controlSize(.small)
            } else {
                Label(modeLabel(schedulingMode), systemImage: modeSymbol(schedulingMode))
            }
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .disabled(
            isChangingMode
                || !AutomationModePresentation.canSelectModes(
                    currentMode: store.schedulingSettings?.mode
                )
        )
        .help(DailyDriverL10n.modeHelp)
    }

    // MARK: - Now

    @ViewBuilder
    private var nowContent: some View {
        if let task = liveTasks.first {
            Button {
                openTask(task.taskId)
            } label: {
                HStack(spacing: 14) {
                    stateIcon(task.state)
                        .font(.title3)
                        .frame(width: 28)

                    VStack(alignment: .leading, spacing: 4) {
                        Text(task.intent)
                            .font(.headline)
                            .foregroundStyle(.primary)
                            .lineLimit(2)
                        HStack(spacing: 8) {
                            Text(stateLabel(task.state))
                            if let policy = task.schedulingPolicy {
                                Text("·")
                                Text(modeLabel(policy))
                            }
                            Text("·")
                            Text(relative(task.updatedAt))
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }

                    Spacer(minLength: 12)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(16)
            .background(
                Color(nsColor: .controlBackgroundColor),
                in: RoundedRectangle(cornerRadius: 12)
            )

            if liveTasks.count > 1 {
                Button(DailyDriverL10n.viewMoreActiveTasks(liveTasks.count - 1)) {
                    onNavigate(NavigationIntent(section: .tasks))
                }
                .buttonStyle(.link)
            }
        } else {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(DailyDriverL10n.nothingRunning)
                        .font(.headline)
                    Text(DailyDriverL10n.startTaskHint)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
    }

    // MARK: - Attention

    @ViewBuilder
    private var attentionContent: some View {
        if attentionTasks.isEmpty && actionableRisks.isEmpty && !projectAutomationNeedsSetup {
            Label(DailyDriverL10n.noBlockers, systemImage: "checkmark.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else {
            VStack(spacing: 0) {
                if projectAutomationNeedsSetup {
                    Button {
                        showsProjectAutomation = true
                    } label: {
                        attentionRow(
                            title: DailyDriverL10n.noEligibleProject,
                            detail: DailyDriverL10n.noEligibleProjectDetail,
                            symbol: "folder.badge.questionmark",
                            color: StatusTone.caution.color
                        )
                    }
                    .buttonStyle(.plain)
                    Divider()
                }

                ForEach(Array(attentionTasks.prefix(2))) { task in
                    Button {
                        openTask(task.taskId)
                    } label: {
                        attentionRow(
                            title: task.intent,
                            detail: "\(stateLabel(task.state)) · \(relative(task.updatedAt))",
                            symbol: task.state == "FAILED" ? "xmark.circle.fill" : "exclamationmark.circle.fill",
                            color: task.state == "FAILED"
                                ? StatusTone.critical.color : StatusTone.caution.color
                        )
                    }
                    .buttonStyle(.plain)
                    Divider()
                }

                ForEach(Array(actionableRisks.prefix(3))) { risk in
                    riskRow(risk)
                    if risk.id != actionableRisks.prefix(3).last?.id { Divider() }
                }
            }

            if attentionTasks.count > 2 {
                Button(DailyDriverL10n.reviewAttention) {
                    onNavigate(NavigationIntent(section: .tasks))
                }
                .buttonStyle(.link)
            }
            if actionableRisks.count > 3 {
                Button(DailyDriverL10n.reviewAttention) {
                    onNavigate(NavigationIntent(section: .activity))
                }
                .buttonStyle(.link)
            }
        }
    }

    private func riskRow(_ risk: RiskItemView) -> some View {
        let presentation = StatusStyle.severity(risk.severity)
        let title = L10n.riskTitle(
            rawCode: risk.rawCode,
            count: risk.count,
            fallback: risk.title
        )
        let detail = L10n.riskDetail(rawCode: risk.rawCode, fallback: risk.detail)
        let intent = risk.destination.flatMap { RiskDestination.intent(for: $0) }

        return Button {
            if let intent { onNavigate(intent) }
        } label: {
            attentionRow(
                title: title,
                detail: detail,
                symbol: presentation.symbol,
                color: presentation.color,
                showsChevron: intent != nil
            )
        }
        .buttonStyle(.plain)
        .disabled(intent == nil)
    }

    private func attentionRow(
        title: String,
        detail: String,
        symbol: String,
        color: Color,
        showsChevron: Bool = true
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 12)
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 9)
        .contentShape(Rectangle())
    }

    // MARK: - Capacity

    @ViewBuilder
    private var capacityContent: some View {
        if quotaProviders.isEmpty {
            HStack {
                Label(
                    DailyDriverL10n.noCapacity,
                    systemImage: "externaldrive.badge.questionmark"
                )
                .foregroundStyle(.secondary)
                Spacer()
                Button(DailyDriverL10n.openResources) {
                    onNavigate(NavigationIntent(section: .resources))
                }
                .buttonStyle(.link)
            }
        } else {
            VStack(spacing: 0) {
                ForEach(Array(quotaProviders.prefix(4))) { provider in
                    Button {
                        onNavigate(NavigationIntent(section: .resources))
                    } label: {
                        providerCapacityRow(provider)
                    }
                    .buttonStyle(.plain)
                    if provider.id != quotaProviders.prefix(4).last?.id { Divider() }
                }
            }
            if quotaProviders.count > 4 {
                Button(DailyDriverL10n.viewAllResources) {
                    onNavigate(NavigationIntent(section: .resources))
                }
                .buttonStyle(.link)
            }
        }
    }

    private func providerCapacityRow(_ provider: QuotaProviderCardView) -> some View {
        let title = provider.plan?.displayName ?? provider.planSurface ?? provider.displayName
        let quotaText = quotaText(provider)
        let confidence = provider.plan?.confidence ?? provider.confidence

        return HStack(spacing: 12) {
            Image(systemName: provider.poolKind == "unmetered" ? "infinity" : "server.rack")
                .foregroundStyle(.secondary)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.primary)
                Text(capacityDetail(
                    providerName: provider.displayName,
                    title: title,
                    quotaText: quotaText
                ))
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            Spacer(minLength: 12)
            Text(confidenceLabel(confidence))
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }

    // MARK: - Recent

    @ViewBuilder
    private var recentContent: some View {
        if recentTerminalTasks.isEmpty {
            Text(DailyDriverL10n.noRecent)
                .font(.callout)
                .foregroundStyle(.secondary)
        } else {
            VStack(spacing: 0) {
                ForEach(recentTerminalTasks) { task in
                    Button {
                        openTask(task.taskId)
                    } label: {
                        HStack(spacing: 10) {
                            stateIcon(task.state).frame(width: 20)
                            Text(task.intent)
                                .lineLimit(1)
                                .foregroundStyle(.primary)
                            Spacer(minLength: 12)
                            Text(relative(task.updatedAt))
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                            Image(systemName: "chevron.right")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 9)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if task.id != recentTerminalTasks.last?.id { Divider() }
                }
            }

            Button(DailyDriverL10n.openActivity) {
                onNavigate(NavigationIntent(section: .activity))
            }
            .buttonStyle(.link)
        }
    }

    // MARK: - Actions and formatting

    private func homeSection<Content: View>(
        _ title: String,
        symbol: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: symbol)
                .font(.headline)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func openTask(_ taskId: String) {
        onNavigate(NavigationIntent(section: .tasks, taskId: taskId))
    }

    private func setAutomationMode(_ mode: String) {
        guard mode != schedulingMode, schedulingMode != "ACTIVE" else { return }
        guard store.schedulingSettings != nil else { return }
        isChangingMode = true
        modeNotice = nil
        let socketPath = store.socketPath

        Task {
            do {
                let updated = try await AutomationModeMutationCoordinator.shared.setMode(
                    socketPath: socketPath,
                    mode: mode
                )
                await store.loadSchedulingSettings()
                await store.refreshNow()
                modeNotice = updated.mode == mode
                    ? DailyDriverL10n.modeChanged(modeLabel(updated.mode))
                    : DailyDriverL10n.daemonReturnedMode(modeLabel(updated.mode))
            } catch let error as PAOClientError {
                modeNotice = DailyDriverL10n.modeChangeFailed(error.displayDetail)
            } catch {
                modeNotice = DailyDriverL10n.modeChangeMalformed
            }
            isChangingMode = false
        }
    }

    @ViewBuilder
    private func stateIcon(_ state: String) -> some View {
        switch state {
        case "RUNNING":
            Image(systemName: "bolt.circle.fill").foregroundStyle(.blue)
        case "VERIFYING":
            Image(systemName: "checkmark.seal").foregroundStyle(.blue)
        case "VERIFIED", "COMPLETED":
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case "BLOCKED":
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange)
        case "FAILED":
            Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        case "AUTO_PLANNED", "AUTO_GRACE":
            Image(systemName: "timer").foregroundStyle(.orange)
        default:
            Image(systemName: "circle").foregroundStyle(.secondary)
        }
    }

    private var capacitySummary: String {
        guard let summary = store.quota?.summary else {
            return quotaProviders.isEmpty
                ? DailyDriverL10n.capacityUnavailable
                : DailyDriverL10n.quotaLoading
        }
        if summary.quotaExhaustedCount > 0 {
            return DailyDriverL10n.quotaExhausted(summary.quotaExhaustedCount)
        }
        if summary.quotaWarningCount > 0 {
            return DailyDriverL10n.quotaWarnings(summary.quotaWarningCount)
        }
        if summary.quotaUnknownProviderCount > 0 {
            return DailyDriverL10n.quotaUnknownCount(summary.quotaUnknownProviderCount)
        }
        return DailyDriverL10n.quotaObserved(
            summary.quotaObservableProviderCount,
            summary.connectedProviderCount
        )
    }

    private func quotaText(_ provider: QuotaProviderCardView) -> String {
        if provider.poolKind == "unmetered" {
            if let observed = provider.unmetered {
                return DailyDriverL10n.unmeteredRPM(observed.rpmObserved)
            }
            return DailyDriverL10n.unmetered
        }

        if let plan = provider.plan {
            let readable = plan.windows.prefix(2).map(windowText)
            if !readable.isEmpty { return readable.joined(separator: " · ") }
            return plan.state == "UNKNOWN"
                ? DailyDriverL10n.quotaUnknown : quotaStateLabel(plan.state)
        }

        if let pool = provider.quotaPools.first {
            let readable = pool.windows.prefix(2).map { window -> String in
                var value = window.remainingFraction.map {
                    "\(windowLabel(window.windowKind)) \(Int(($0 * 100).rounded()))%"
                } ?? "\(windowLabel(window.windowKind)) \(DailyDriverL10n.quotaUnknown)"
                if let reset = resetText(window.resetAt) { value += " · \(reset)" }
                return value
            }
            if !readable.isEmpty { return readable.joined(separator: " · ") }
            return quotaStateLabel(pool.state)
        }

        return provider.quotaState == "UNKNOWN"
            ? DailyDriverL10n.quotaUnknown : quotaStateLabel(provider.quotaState)
    }

    private func windowText(_ window: QuotaPlanWindowView) -> String {
        var value = window.remainingFraction.map {
            "\(windowLabel(window.windowKind)) \(Int(($0 * 100).rounded()))%"
        } ?? "\(windowLabel(window.windowKind)) \(DailyDriverL10n.quotaUnknown)"
        if let reset = resetText(window.resetAt) { value += " · \(reset)" }
        return value
    }

    private func capacityDetail(providerName: String, title: String, quotaText: String) -> String {
        title.caseInsensitiveCompare(providerName) == .orderedSame
            ? quotaText : "\(providerName) · \(quotaText)"
    }

    private func modeLabel(_ value: String) -> String {
        switch value {
        case "MANUAL": return DailyDriverL10n.manual
        case "SUPERVISED_AUTO": return DailyDriverL10n.supervisedAuto
        case "ACTIVE": return DailyDriverL10n.fullAutomation
        case "BALANCED": return DailyDriverL10n.balanced
        case "QUALITY_FIRST": return DailyDriverL10n.qualityFirst
        case "QUOTA_SAVER": return DailyDriverL10n.saveQuota
        case "SPEED_FIRST": return DailyDriverL10n.lowLatency
        case "UNKNOWN": return DailyDriverL10n.modeUnavailable
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func modeSymbol(_ value: String) -> String {
        switch value {
        case "SUPERVISED_AUTO": return "sparkles"
        case "ACTIVE": return "bolt.shield"
        case "MANUAL": return "hand.raised"
        default: return "questionmark.circle"
        }
    }

    private func stateLabel(_ value: String) -> String {
        switch value {
        case "AUTO_PLANNED": return DailyDriverL10n.taskAwaitingApproval
        case "AUTO_GRACE": return DailyDriverL10n.taskGraceWindow
        case "SUBMITTED": return DailyDriverL10n.taskReadyToRoute
        case "READY": return DailyDriverL10n.taskReady
        case "RUNNING": return DailyDriverL10n.taskRunning
        case "VERIFYING": return DailyDriverL10n.taskVerifying
        case "VERIFIED": return DailyDriverL10n.taskVerified
        case "COMPLETED": return DailyDriverL10n.taskCompleted
        case "BLOCKED": return DailyDriverL10n.taskBlocked
        case "FAILED": return DailyDriverL10n.taskFailed
        case "CANCELLED": return DailyDriverL10n.taskCancelled
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func quotaStateLabel(_ value: String) -> String {
        switch value {
        case "AVAILABLE", "OBSERVED": return DailyDriverL10n.quotaAvailable
        case "LIMITED", "WARNING": return DailyDriverL10n.quotaLimited
        case "EXHAUSTED": return DailyDriverL10n.quotaExhaustedState
        case "UNKNOWN": return DailyDriverL10n.quotaUnknown
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func confidenceLabel(_ value: String) -> String {
        switch value.uppercased() {
        case "EXACT": return DailyDriverL10n.exact
        case "ESTIMATED": return DailyDriverL10n.estimated
        default: return DailyDriverL10n.unknown
        }
    }

    private func windowLabel(_ value: String) -> String {
        let lowered = value.lowercased()
        if (lowered.contains("5") || lowered.contains("five")) && lowered.contains("hour") {
            return "5h"
        }
        if lowered.contains("week") { return DailyDriverL10n.weekWindow }
        if lowered.contains("month") { return DailyDriverL10n.monthWindow }
        return value.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func resetText(_ raw: String?) -> String? {
        guard let raw, let date = ISO8601DateFormatter().date(from: raw) else { return nil }
        let seconds = Int(date.timeIntervalSinceNow)
        if seconds <= 0 { return DailyDriverL10n.resetDue }
        if seconds < 3_600 { return DailyDriverL10n.resetMinutes(max(1, seconds / 60)) }
        if seconds < 86_400 { return DailyDriverL10n.resetHours(seconds / 3_600) }
        return DailyDriverL10n.resetDays(seconds / 86_400)
    }

    private func relative(_ raw: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: raw) else { return raw }
        let seconds = max(0, Int(Date().timeIntervalSince(date)))
        if seconds < 60 { return DailyDriverL10n.relativeNow }
        if seconds < 3_600 { return DailyDriverL10n.minutesAgo(seconds / 60) }
        if seconds < 86_400 { return DailyDriverL10n.hoursAgo(seconds / 3_600) }
        return DailyDriverL10n.daysAgo(seconds / 86_400)
    }
}
