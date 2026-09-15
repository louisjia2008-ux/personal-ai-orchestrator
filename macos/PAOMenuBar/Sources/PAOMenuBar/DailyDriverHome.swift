import SwiftUI

import PAOControlKit

/// Daily-driver Home surface.
///
/// This view intentionally consumes only authoritative projections already held
/// by OrchestratorStore. It never infers dispatch authority, quota certainty or
/// task completion from presentation state.
struct DailyDriverHome: View {
    @EnvironmentObject private var store: OrchestratorStore
    @State private var isChangingMode = false
    @State private var modeNotice: String?

    let onOpenTask: (String) -> Void
    let onOpenTasks: () -> Void
    let onOpenResources: () -> Void
    let onOpenActivity: () -> Void

    private var tasks: [TaskView] { store.tasks?.tasks ?? [] }
    private var quotaProviders: [QuotaProviderCardView] { store.quota?.providers ?? [] }
    private var risks: [RiskItemView] { store.dashboard?.risks ?? [] }

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

    /// Orchestrator operating mode, not the routing policy. BALANCED / QUALITY_FIRST
    /// etc. are task-routing policy choices and must never be presented as MANUAL /
    /// SUPERVISED_AUTO / ACTIVE state.
    private var schedulingMode: String {
        store.schedulingSettings?.mode ?? "UNKNOWN"
    }

    private var isReady: Bool {
        guard store.connection.isConnected else { return false }
        guard attentionTasks.isEmpty && risks.isEmpty else { return false }
        guard let quotaSummary = store.quota?.summary else { return true }
        return quotaSummary.quotaWarningCount == 0 && quotaSummary.quotaExhaustedCount == 0
    }

    var body: some View {
        DashboardPageContainer {
            readinessHeader

            if let modeNotice {
                Label(modeNotice, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            homeSection("Now", symbol: "bolt.fill") {
                nowContent
            }

            homeSection("Needs Attention", symbol: "exclamationmark.triangle") {
                attentionContent
            }

            homeSection("Capacity", symbol: "gauge.with.dots.needle.67percent") {
                capacityContent
            }

            homeSection("Recent", symbol: "clock") {
                recentContent
            }
        }
        .accessibilityIdentifier("home.dailyDriver")
    }

    private var readinessHeader: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(isReady ? Color.green : Color.orange)
                .frame(width: 9, height: 9)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(isReady ? "Ready to work" : "Needs attention")
                    .font(.title3.weight(.semibold))
                Text(capacitySummary)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            if !store.connection.isConnected {
                Label("Daemon unavailable", systemImage: "bolt.slash")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.secondary)
            } else {
                automationModeMenu
            }
        }
        .padding(.vertical, 2)
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

            if schedulingMode == "ACTIVE" {
                Divider()
                Label("Full Automation is controlled by production gates", systemImage: "lock")
            }
        } label: {
            if isChangingMode {
                ProgressView()
                    .controlSize(.small)
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
        .help("Change between Manual and Supervised Auto without changing the routing policy.")
    }

    @ViewBuilder
    private var nowContent: some View {
        if let task = liveTasks.first {
            Button {
                onOpenTask(task.taskId)
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
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))

            if liveTasks.count > 1 {
                Button("View \(liveTasks.count - 1) more active task\(liveTasks.count == 2 ? "" : "s")") {
                    onOpenTasks()
                }
                .buttonStyle(.link)
            }
        } else {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Nothing is running")
                        .font(.headline)
                    Text("Start a task from the toolbar when you are ready.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
    }

    @ViewBuilder
    private var attentionContent: some View {
        if attentionTasks.isEmpty && risks.isEmpty {
            Label("No blockers or failed tasks", systemImage: "checkmark.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else {
            VStack(spacing: 0) {
                ForEach(Array(attentionTasks.prefix(3))) { task in
                    Button {
                        onOpenTask(task.taskId)
                    } label: {
                        attentionRow(
                            title: task.intent,
                            detail: "\(stateLabel(task.state)) · \(relative(task.updatedAt))",
                            symbol: task.state == "FAILED" ? "xmark.circle.fill" : "exclamationmark.circle.fill"
                        )
                    }
                    .buttonStyle(.plain)
                    if task.id != attentionTasks.prefix(3).last?.id { Divider() }
                }
            }

            if attentionTasks.count > 3 || !risks.isEmpty {
                Button("Review all attention items") { onOpenTasks() }
                    .buttonStyle(.link)
            }
        }
    }

    @ViewBuilder
    private var capacityContent: some View {
        if quotaProviders.isEmpty {
            HStack {
                Label("No connected provider capacity available", systemImage: "externaldrive.badge.questionmark")
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Open Resources") { onOpenResources() }
                    .buttonStyle(.link)
            }
        } else {
            VStack(spacing: 0) {
                ForEach(Array(quotaProviders.prefix(4))) { provider in
                    Button {
                        onOpenResources()
                    } label: {
                        providerCapacityRow(provider)
                    }
                    .buttonStyle(.plain)
                    if provider.id != quotaProviders.prefix(4).last?.id { Divider() }
                }
            }
            if quotaProviders.count > 4 {
                Button("View all resources") { onOpenResources() }
                    .buttonStyle(.link)
            }
        }
    }

    @ViewBuilder
    private var recentContent: some View {
        if recentTerminalTasks.isEmpty {
            Text("No recent completed or blocked work")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else {
            VStack(spacing: 0) {
                ForEach(recentTerminalTasks) { task in
                    Button {
                        onOpenTask(task.taskId)
                    } label: {
                        HStack(spacing: 10) {
                            stateIcon(task.state)
                                .frame(width: 20)
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

            Button("Open Activity") { onOpenActivity() }
                .buttonStyle(.link)
        }
    }

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

    private func attentionRow(title: String, detail: String, symbol: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(.orange)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 9)
        .contentShape(Rectangle())
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
                Text(capacityDetail(providerName: provider.displayName, title: title, quotaText: quotaText))
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

    private func quotaText(_ provider: QuotaProviderCardView) -> String {
        if provider.poolKind == "unmetered" {
            if let observed = provider.unmetered {
                return "Unmetered · \(observed.rpmObserved) RPM observed"
            }
            return "Unmetered"
        }

        if let plan = provider.plan {
            let readable = plan.windows.prefix(2).map(windowText)
            if !readable.isEmpty { return readable.joined(separator: " · ") }
            if let reason = plan.unknownReason, !reason.isEmpty { return "Quota unknown" }
            return stateLabelForQuota(plan.state)
        }

        if let pool = provider.quotaPools.first {
            let readable = pool.windows.prefix(2).map { window -> String in
                var value: String
                if let remaining = window.remainingFraction {
                    value = "\(windowLabel(window.windowKind)) \(Int((remaining * 100).rounded()))%"
                } else {
                    value = "\(windowLabel(window.windowKind)) unknown"
                }
                if let reset = resetText(window.resetAt) {
                    value += " · \(reset)"
                }
                return value
            }
            if !readable.isEmpty { return readable.joined(separator: " · ") }
            return stateLabelForQuota(pool.state)
        }

        return provider.quotaState == "UNKNOWN" ? "Quota unknown" : provider.quotaState.capitalized
    }

    private func windowText(_ window: QuotaPlanWindowView) -> String {
        var value: String
        if let remaining = window.remainingFraction {
            value = "\(windowLabel(window.windowKind)) \(Int((remaining * 100).rounded()))%"
        } else {
            value = "\(windowLabel(window.windowKind)) unknown"
        }
        if let reset = resetText(window.resetAt) {
            value += " · \(reset)"
        }
        return value
    }

    private func capacityDetail(providerName: String, title: String, quotaText: String) -> String {
        if title.caseInsensitiveCompare(providerName) == .orderedSame {
            return quotaText
        }
        return "\(providerName) · \(quotaText)"
    }

    private func setAutomationMode(_ mode: String) {
        guard mode != schedulingMode, schedulingMode != "ACTIVE" else { return }
        guard store.schedulingSettings != nil else { return }
        isChangingMode = true
        modeNotice = nil
        let socketPath = store.socketPath

        Task {
            let client = PAOControlClient(socketPath: socketPath)
            do {
                // Re-read immediately before PUT so a mode change never rewrites
                // the owner's routing policy with stale client state.
                let current = try await client.schedulingSettings()
                let updated = try await client.setSchedulingMode(
                    mode,
                    defaultSchedulingPolicy: current.defaultSchedulingPolicy
                )
                await store.loadSchedulingSettings()
                await store.refreshNow()
                modeNotice = updated.mode == mode
                    ? "Scheduling mode changed to \(modeLabel(updated.mode))."
                    : "Daemon returned \(modeLabel(updated.mode)); no local mode was assumed."
            } catch let error as PAOClientError {
                modeNotice = "Scheduling mode change failed · \(error.displayDetail)"
            } catch {
                modeNotice = "Scheduling mode response could not be decoded."
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
            return quotaProviders.isEmpty ? "capacity unavailable" : "quota loading"
        }
        if summary.quotaExhaustedCount > 0 {
            return "\(summary.quotaExhaustedCount) quota exhausted"
        }
        if summary.quotaWarningCount > 0 {
            return "\(summary.quotaWarningCount) quota warning\(summary.quotaWarningCount == 1 ? "" : "s")"
        }
        if summary.quotaUnknownProviderCount > 0 {
            return "\(summary.quotaUnknownProviderCount) quota unknown"
        }
        return "\(summary.quotaObservableProviderCount)/\(summary.connectedProviderCount) quota observed"
    }

    private func modeLabel(_ value: String) -> String {
        switch value {
        case "MANUAL": return "Manual"
        case "SUPERVISED_AUTO": return "Supervised Auto"
        case "ACTIVE": return "Full Automation"
        case "BALANCED": return "Balanced"
        case "QUALITY_FIRST": return "Quality First"
        case "QUOTA_SAVER": return "Save Quota"
        case "SPEED_FIRST": return "Low Latency"
        case "UNKNOWN": return "Mode unavailable"
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
        case "AUTO_PLANNED": return "Waiting for approval"
        case "AUTO_GRACE": return "Dispatch countdown"
        case "RUNNING": return "Running"
        case "VERIFYING": return "Verifying"
        case "VERIFIED": return "Verified"
        case "COMPLETED": return "Completed"
        case "BLOCKED": return "Blocked"
        case "FAILED": return "Failed"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func stateLabelForQuota(_ value: String) -> String {
        switch value {
        case "AVAILABLE", "OBSERVED": return "Available"
        case "LIMITED", "WARNING": return "Limited"
        case "EXHAUSTED": return "Exhausted"
        case "UNKNOWN": return "Quota unknown"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func confidenceLabel(_ value: String) -> String {
        switch value.uppercased() {
        case "EXACT": return "Exact"
        case "ESTIMATED": return "Estimated"
        default: return "Unknown"
        }
    }

    private func windowLabel(_ value: String) -> String {
        let lowered = value.lowercased()
        if (lowered.contains("5") || lowered.contains("five")) && lowered.contains("hour") { return "5h" }
        if lowered.contains("week") { return "Week" }
        if lowered.contains("month") { return "Month" }
        return value.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func resetText(_ raw: String?) -> String? {
        guard let raw, let date = ISO8601DateFormatter().date(from: raw) else { return nil }
        let seconds = Int(date.timeIntervalSinceNow)
        if seconds <= 0 { return "reset due" }
        if seconds < 3600 { return "reset \(max(1, seconds / 60))m" }
        if seconds < 86_400 { return "reset \(seconds / 3600)h" }
        return "reset \(seconds / 86_400)d"
    }

    private func relative(_ raw: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: raw) else { return raw }
        let seconds = max(0, Int(Date().timeIntervalSince(date)))
        if seconds < 60 { return "now" }
        if seconds < 3600 { return "\(seconds / 60)m ago" }
        if seconds < 86_400 { return "\(seconds / 3600)h ago" }
        return "\(seconds / 86_400)d ago"
    }
}
