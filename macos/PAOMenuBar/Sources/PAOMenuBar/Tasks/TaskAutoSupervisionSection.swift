import SwiftUI

import PAOControlKit

/// Supervised-auto control surface for AUTO_PLANNED / AUTO_GRACE tasks.
///
/// The countdown is presentation-only. Local expiry never advances task state;
/// it triggers one authoritative reload and waits for the daemon to publish the
/// actual transition.
struct TaskAutoSupervisionSection: View {
    @EnvironmentObject private var store: OrchestratorStore
    @StateObject private var controller = SupervisedAutoTaskController()
    @State private var requestedExpiryRefresh = false

    let detail: TaskDetailView

    private var frozenTargetId: String? {
        detail.routing?.selectedExecutionTargetId
            ?? detail.routingSummary?.primary?.activeDecision?.selectedExecutionTargetId
    }

    private var isActive: Bool {
        detail.task.state == "AUTO_PLANNED" || detail.task.state == "AUTO_GRACE"
    }

    private var needsClock: Bool {
        detail.task.state == "AUTO_GRACE" && detail.task.autoGraceDeadlineAt != nil
    }

    var body: some View {
        if isActive {
            if needsClock {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    content(now: context.date)
                }
            } else {
                content(now: Date())
            }
        }
    }

    @ViewBuilder
    private func content(now: Date) -> some View {
        let presentation = AutoSupervisionPresentation.derive(
            task: detail.task,
            now: now,
            frozenTargetId: frozenTargetId
        )

        TaskDetailSection(
            "Supervised Auto",
            symbol: "sparkles",
            footnote: "PAO revalidates task state, routing, project policy and quota before every action."
        ) {
            phaseNotice(presentation)

            if presentation.phase == .countingDown {
                countdown(presentation)
            }

            TaskFieldRow(
                label: "Planned target",
                value: presentation.frozenTargetId,
                monospaced: true,
                absentText: "No frozen target published"
            )

            if let reason = presentation.autoReason, !reason.isEmpty {
                TaskFieldRow(label: "Why this plan", value: reason)
            }

            if let ackedAt = presentation.ackedAt {
                TaskFieldRow(
                    label: "Acknowledged",
                    value: ackedAt.formatted(date: .abbreviated, time: .standard)
                )
            }

            actions(presentation)
            notice
        }
        .onChange(of: presentation.isExpiredLocally) { expired in
            guard expired, !requestedExpiryRefresh else { return }
            requestedExpiryRefresh = true
            Task { await store.loadTaskDetail(taskId: detail.task.taskId) }
        }
        .onChange(of: detail.task.stateVersion) { _ in
            requestedExpiryRefresh = false
        }
    }

    @ViewBuilder
    private func phaseNotice(_ presentation: AutoSupervisionPresentation) -> some View {
        switch presentation.phase {
        case .planned:
            TaskSectionNotice(
                text: "Plan frozen. Waiting for the daemon to promote this task into its supervised grace step.",
                symbol: "hourglass",
                tone: .neutral
            )
        case .waitingAck:
            TaskSectionNotice(
                text: "Approval is required before the grace countdown begins.",
                symbol: "hand.tap",
                tone: .caution
            )
        case .countingDown:
            TaskSectionNotice(
                text: "Acknowledged. PAO will dispatch after the grace window unless you veto or dispatch now.",
                symbol: "timer",
                tone: .caution
            )
        case .expiredRefreshing:
            TaskSectionNotice(
                text: "Grace time elapsed. Refreshing daemon truth; execution is not assumed locally.",
                symbol: "arrow.triangle.2.circlepath",
                tone: .neutral
            )
        case .inactive:
            EmptyView()
        }
    }

    private func countdown(_ presentation: AutoSupervisionPresentation) -> some View {
        let clock = AutoSupervisionPresentation.clockText(
            secondsRemaining: presentation.secondsRemaining ?? 0
        )
        return VStack(alignment: .leading, spacing: 2) {
            Text(clock)
                .font(.title2.weight(.semibold).monospacedDigit())
            Text("until automatic dispatch")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Automatic dispatch countdown")
        .accessibilityValue(clock)
    }

    @ViewBuilder
    private func actions(_ presentation: AutoSupervisionPresentation) -> some View {
        HStack(spacing: Spacing.inner) {
            if presentation.canAck {
                Button {
                    run(.ack)
                } label: {
                    Label("Acknowledge", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(controller.isInFlight(.ack))
                .help("Approve this frozen plan and start the daemon-owned grace window.")
            }

            if presentation.canDispatchNow {
                Button {
                    run(.dispatchNow)
                } label: {
                    Label("Dispatch Now", systemImage: "paperplane.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(controller.isInFlight(.dispatchNow))
                .help("Ask the daemon to dispatch now; all admission gates still apply.")
            }

            if presentation.canVeto {
                Button(role: .destructive) {
                    run(.veto)
                } label: {
                    Label("Veto", systemImage: "xmark.octagon")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(controller.isInFlight(.veto))
                .help("Stop this supervised-auto lifecycle without bypassing daemon state checks.")
            }
        }
    }

    private func run(_ action: SupervisedAutoTaskController.Action) {
        let taskId = detail.task.taskId
        let socketPath = store.socketPath
        Task {
            switch action {
            case .ack:
                await controller.acknowledge(taskId: taskId, socketPath: socketPath)
            case .veto:
                await controller.veto(taskId: taskId, socketPath: socketPath)
            case .dispatchNow:
                await controller.dispatchNow(taskId: taskId, socketPath: socketPath)
            }
            await store.loadTaskDetail(taskId: taskId)
            await store.refreshNow()
        }
    }

    @ViewBuilder
    private var notice: some View {
        if let value = controller.notice {
            HStack(alignment: .top, spacing: Spacing.tight) {
                TaskSectionNotice(
                    text: noticeText(value),
                    symbol: noticeSymbol(value),
                    tone: noticeTone(value)
                )
                Button {
                    controller.clearNotice()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .controlSize(.mini)
                .accessibilityLabel("Dismiss result")
            }
        }
    }

    private func noticeText(_ notice: SupervisedAutoTaskController.Notice) -> String {
        switch notice {
        case .acknowledged:
            return "Acknowledged. Daemon truth has been refreshed."
        case .vetoed:
            return "Veto accepted. Daemon truth has been refreshed."
        case .dispatchRequested(let status):
            return "Dispatch requested · \(status)"
        case .staleState:
            return "Task changed before the action completed. The current state was reloaded."
        case .blocked(let code):
            return "Daemon blocked the action · \(code)"
        case .failed(let detail):
            return "Action failed · \(detail)"
        case .malformedResponse:
            return "Daemon response could not be decoded."
        }
    }

    private func noticeSymbol(_ notice: SupervisedAutoTaskController.Notice) -> String {
        switch notice {
        case .acknowledged, .vetoed, .dispatchRequested:
            return "checkmark.circle"
        case .staleState:
            return "arrow.triangle.2.circlepath"
        case .blocked, .failed, .malformedResponse:
            return "exclamationmark.triangle"
        }
    }

    private func noticeTone(_ notice: SupervisedAutoTaskController.Notice) -> StatusTone {
        switch notice {
        case .acknowledged, .vetoed, .dispatchRequested:
            return .positive
        case .staleState, .blocked, .failed, .malformedResponse:
            return .caution
        }
    }
}
