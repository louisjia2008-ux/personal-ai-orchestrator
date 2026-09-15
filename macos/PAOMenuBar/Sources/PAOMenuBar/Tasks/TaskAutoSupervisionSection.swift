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
            DailyDriverL10n.autoSectionTitle,
            symbol: "sparkles",
            footnote: DailyDriverL10n.autoSectionFootnote
        ) {
            phaseNotice(presentation)

            if presentation.phase == .countingDown {
                countdown(presentation)
            }

            TaskFieldRow(
                label: DailyDriverL10n.autoPlannedTarget,
                value: presentation.frozenTargetId,
                monospaced: true,
                absentText: DailyDriverL10n.autoNoTarget
            )

            if let reason = presentation.autoReason, !reason.isEmpty {
                TaskFieldRow(label: DailyDriverL10n.autoWhyPlan, value: reason)
            }

            if let ackedAt = presentation.ackedAt {
                TaskFieldRow(
                    label: DailyDriverL10n.autoAcknowledged,
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
                text: DailyDriverL10n.autoPlanFrozen,
                symbol: "hourglass",
                tone: .neutral
            )
        case .waitingAck:
            TaskSectionNotice(
                text: DailyDriverL10n.autoApprovalRequired,
                symbol: "hand.tap",
                tone: .caution
            )
        case .countingDown:
            TaskSectionNotice(
                text: DailyDriverL10n.autoCountingDown,
                symbol: "timer",
                tone: .caution
            )
        case .expiredRefreshing:
            TaskSectionNotice(
                text: DailyDriverL10n.autoExpiredRefreshing,
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
            Text(DailyDriverL10n.autoUntilDispatch)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(DailyDriverL10n.autoCountdownAccessibility)
        .accessibilityValue(clock)
    }

    @ViewBuilder
    private func actions(_ presentation: AutoSupervisionPresentation) -> some View {
        HStack(spacing: Spacing.inner) {
            if presentation.canAck {
                Button {
                    run(.ack)
                } label: {
                    Label(DailyDriverL10n.autoAcknowledge, systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(controller.isInFlight(.ack))
                .help(DailyDriverL10n.autoAckHelp)
            }

            if presentation.canDispatchNow {
                Button {
                    run(.dispatchNow)
                } label: {
                    Label(DailyDriverL10n.autoDispatchNow, systemImage: "paperplane.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(controller.isInFlight(.dispatchNow))
                .help(DailyDriverL10n.autoDispatchHelp)
            }

            if presentation.canVeto {
                Button(role: .destructive) {
                    run(.veto)
                } label: {
                    Label(DailyDriverL10n.autoVeto, systemImage: "xmark.octagon")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(controller.isInFlight(.veto))
                .help(DailyDriverL10n.autoVetoHelp)
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
                .accessibilityLabel(DailyDriverL10n.dismissResult)
            }
        }
    }

    private func noticeText(_ notice: SupervisedAutoTaskController.Notice) -> String {
        switch notice {
        case .acknowledged:
            return DailyDriverL10n.autoNoticeAcknowledged
        case .vetoed:
            return DailyDriverL10n.autoNoticeVetoed
        case .dispatchRequested(let status):
            return DailyDriverL10n.autoNoticeDispatch(status)
        case .staleState:
            return DailyDriverL10n.autoNoticeStale
        case .blocked(let code):
            return DailyDriverL10n.autoNoticeBlocked(code)
        case .failed(let detail):
            return DailyDriverL10n.autoNoticeFailed(detail)
        case .malformedResponse:
            return DailyDriverL10n.autoNoticeMalformed
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
