import SwiftUI

import PAOControlKit

/// Daily-driver Home surface.
///
/// This view intentionally consumes only authoritative projections already held
/// by OrchestratorStore. It never infers dispatch authority, quota certainty or
/// task completion from presentation state.
struct DailyDriverHome: View {
    @EnvironmentObject private var store: OrchestratorStore

    let onOpenTask: (String) -> Void
    let onOpenTasks: () -> Void
    let onOpenResources: () -> Void
    let onOpenActivity: () -> Void

    private var tasks: [TaskView] { store.tasks?.tasks ?? [] }

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
        store.schedulingSettings?.defaultSchedulingPolicy ?? "UNKNOWN"
    }

    private var isReady: Bool {
        store.connection.isConnected && attentionTasks.isEmpty
    }

    var body: some View {
        DashboardPageContainer {
            readinessHeader

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
                Text("\(modeLabel(schedulingMode)) · \(capacitySummary)")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            if !store.connection.isConnected {
                Label("Daemon unavailable", systemImage: "bolt.slash")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
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
        let risks = store.dashboard?.risks ?? []
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
        let providers = store.providers?.providers ?? []
        if providers.isEmpty {
            HStack {
                Label("No provider capacity available", systemImage: "externaldrive.badge.questionmark")
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Open Resources") { onOpenResources() }
                    .buttonStyle(.link)
            }
        } else {
            VStack(spacing: 0) {
                ForEach(providers.prefix(4)) { provider in
                    Button {
                        onOpenResources()
                    } label: {
                        providerCapacityRow(provider)
                    }
                    .buttonStyle(.plain)
                    if provider.id != providers.prefix(4).last?.id { Divider() }
                }
            }
            if providers.count > 4 {
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

    private func providerCapacityRow(_ provider: ProviderHealthView) -> some View {
        let pool = provider.quotaPools.first
        let windows = pool?.windows ?? []
        let readableWindows = windows.prefix(2).map { window -> String in
            if let remaining = window.remainingFraction {
                return "\(windowLabel(window.windowKind)) \(Int((remaining * 100).rounded()))%"
            }
            return "\(windowLabel(window.windowKind)) unknown"
        }
        let quotaText = readableWindows.isEmpty ? (pool?.state ?? "No quota evidence") : readableWindows.joined(separator: " · ")
        let confidence = pool?.confidence ?? "UNKNOWN"

        return HStack(spacing: 12) {
            Image(systemName: "server.rack")
                .foregroundStyle(.secondary)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(provider.displayName)
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.primary)
                Text(quotaText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Text(confidence.capitalized)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
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
        let providers = store.providers?.providers ?? []
        guard !providers.isEmpty else { return "capacity unavailable" }
        let healthy = providers.filter { provider in
            provider.quotaPools.contains { ["AVAILABLE", "LIMITED"].contains($0.state) }
        }.count
        return "\(healthy)/\(providers.count) provider\(providers.count == 1 ? "" : "s") available"
    }

    private func modeLabel(_ value: String) -> String {
        switch value {
        case "MANUAL": return "Manual"
        case "SUPERVISED_AUTO": return "Supervised Auto"
        case "BALANCED": return "Balanced"
        case "QUALITY_FIRST": return "Quality First"
        case "QUOTA_SAVER": return "Save Quota"
        case "SPEED_FIRST": return "Low Latency"
        case "UNKNOWN": return "Mode unavailable"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
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

    private func windowLabel(_ value: String) -> String {
        let lowered = value.lowercased()
        if lowered.contains("5") && lowered.contains("hour") { return "5h" }
        if lowered.contains("week") { return "Week" }
        if lowered.contains("month") { return "Month" }
        return value.replacingOccurrences(of: "_", with: " ").capitalized
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
