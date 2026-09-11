import SwiftUI

import PAOControlKit

/// M1 WP5b: the one supervised-auto control surface in Task Detail.
///
/// Renders only for `AUTO_PLANNED` / `AUTO_GRACE`. Everything shown is
/// authoritative: the frozen target comes from the routing read model,
/// the deadline from the daemon, and every action goes through the
/// store's typed operations which re-fetch state and refresh after the
/// daemon answers. Local countdown expiry is presented as
/// "dispatch confirmation pending" — never as a local RUNNING claim.
struct TaskAutoSupervisionSection: View {
    @EnvironmentObject private var store: OrchestratorStore
    let detail: TaskDetailView
    /// One authoritative reload when the countdown expires locally, so
    /// the transition the daemon already made appears without waiting
    /// for the next cadence tick.
    @State private var requestedExpiryRefresh = false

    private var frozenTargetId: String? {
        detail.routing?.selectedExecutionTargetId
            ?? detail.routingSummary?.primary?.activeDecision?.selectedExecutionTargetId
    }

    private var isActive: Bool {
        detail.task.state == "AUTO_PLANNED" || detail.task.state == "AUTO_GRACE"
    }

    /// A ticking clock is needed only while a deadline is actually
    /// being counted down — no whole-app one-second redraw loop.
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
            L10n.autoTaskTitle,
            symbol: "sparkles",
            footnote: L10n.autoTaskFootnote
        ) {
            switch presentation.phase {
            case .planned:
                TaskSectionNotice(
                    text: L10n.autoPlannedText,
                    symbol: "hourglass",
                    tone: .neutral
                )
            case .waitingAck:
                TaskSectionNotice(
                    text: L10n.autoWaitingAckText,
                    symbol: "hand.tap",
                    tone: .caution
                )
            case .countingDown:
                countdown(presentation)
            case .expiredRefreshing:
                TaskSectionNotice(
                    text: L10n.autoExpiredText,
                    symbol: "arrow.triangle.2.circlepath",
                    tone: .neutral
                )
            case .inactive:
                EmptyView()
            }

            TaskFieldRow(
                label: L10n.autoLabelTarget,
                value: presentation.frozenTargetId,
                monospaced: true,
                absentText: L10n.autoTargetUnavailable
            )
            if let reason = presentation.autoReason, !reason.isEmpty {
                TaskFieldRow(
                    label: L10n.autoLabelReason,
                    value: reason,
                    monospaced: true
                )
            }
            if let ackedAt = presentation.ackedAt {
                TaskFieldRow(
                    label: L10n.autoLabelAcked,
                    value: Timestamps.absolute(ackedAt)
                )
            }

            actions(presentation)
            notice
        }
        .onChange(of: presentation.isExpiredLocally) { expired in
            if expired && !requestedExpiryRefresh {
                requestedExpiryRefresh = true
                Task { await store.loadTaskDetail(taskId: detail.task.taskId) }
            }
        }
    }

    /// The prominent deadline reading. The number is client-side
    /// display only; the accessibility value is the same stable text a
    /// sighted owner reads, so meaning never depends on color alone.
    private func countdown(_ presentation: AutoSupervisionPresentation) -> some View {
        let clock = AutoSupervisionPresentation.clockText(
            secondsRemaining: presentation.secondsRemaining ?? 0
        )
        return Text(L10n.autoCountdownText(clock))
            .font(.title3.weight(.semibold).monospacedDigit())
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(L10n.autoCountdownAccessibilityLabel)
            .accessibilityValue(L10n.autoCountdownText(clock))
    }

    @ViewBuilder
    private func actions(_ presentation: AutoSupervisionPresentation) -> some View {
        HStack(spacing: Spacing.inner) {
            if presentation.canAck {
                Button {
                    Task { await store.autoAck(taskId: detail.task.taskId) }
                } label: {
                    Label(L10n.autoActionAck, systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(store.isAutoActionInFlight(taskId: detail.task.taskId, action: .ack))
                .help(L10n.autoActionAckHelp)
            }
            if presentation.canDispatchNow {
                Button {
                    Task { await store.autoDispatchNow(taskId: detail.task.taskId) }
                } label: {
                    Label(L10n.autoActionDispatchNow, systemImage: "paperplane.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(
                    store.isAutoActionInFlight(
                        taskId: detail.task.taskId, action: .dispatchNow
                    )
                )
                .help(L10n.autoActionDispatchNowHelp)
            }
            if presentation.canVeto {
                Button(role: .destructive) {
                    Task { await store.autoVeto(taskId: detail.task.taskId) }
                } label: {
                    Label(L10n.autoActionVeto, systemImage: "xmark.octagon")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(store.isAutoActionInFlight(taskId: detail.task.taskId, action: .veto))
                .help(L10n.autoActionVetoHelp)
            }
        }
    }

    /// The last control outcome, closest to the controls that caused it.
    /// Only notices scoped to THIS task render here — scheduling-mode
    /// results, project-settings outcomes, and other tasks' errors all
    /// belong to their own surfaces.
    @ViewBuilder
    private var notice: some View {
        if let notice = store.autoControlNotice,
           notice.applies(to: .task(detail.task.taskId)) {
            HStack(alignment: .top, spacing: Spacing.tight) {
                TaskSectionNotice(
                    text: L10n.autoControlNoticeText(notice),
                    symbol: noticeSymbol(notice),
                    tone: noticeTone(notice)
                )
                Button {
                    store.clearAutoControlNotice()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .controlSize(.mini)
                .accessibilityLabel(L10n.autoNoticeDismiss)
            }
        }
    }

    private func noticeSymbol(_ notice: AutoControlNotice) -> String {
        switch notice {
        case .acknowledged, .vetoed, .dispatchRequested, .schedulingModeChanged,
             .supervisedAutoStopped, .projectAutoSettingsSaved:
            return "checkmark.circle"
        case .staleState:
            return "arrow.triangle.2.circlepath"
        case .schedulingModeBusy:
            return "hourglass"
        case .blocked, .failed, .malformedResponse:
            return "exclamationmark.triangle"
        }
    }

    private func noticeTone(_ notice: AutoControlNotice) -> StatusTone {
        switch notice {
        case .acknowledged, .vetoed, .dispatchRequested, .schedulingModeChanged,
             .supervisedAutoStopped, .projectAutoSettingsSaved:
            return .positive
        case .staleState, .schedulingModeBusy:
            return .caution
        case .blocked, .failed, .malformedResponse:
            return .caution
        }
    }
}
