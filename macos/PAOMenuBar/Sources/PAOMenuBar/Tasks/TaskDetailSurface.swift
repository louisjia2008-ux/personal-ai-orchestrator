import SwiftUI

import PAOControlKit

/// The canonical workspace for one task.
///
/// Ordered by what the owner needs first: what this is and what state it is in,
/// then anything demanding attention, then how it is executing, then why that
/// worker, then what changed, then whether it passed. Machine identity is not
/// in this order at all — it lives in the inspector.
///
/// Nothing here depends on the inspector being open. That is the point of the
/// split: the inspector holds ids and paths, and Task Detail holds the answers.
struct TaskDetailSurface: View {
    @EnvironmentObject private var store: OrchestratorStore
    let detail: TaskDetailView?
    let selectedTaskId: String?
    let projectName: String?
    /// False on macOS 13, where `.inspector` does not exist and the same
    /// technical metadata renders as a disclosure at the end of this page.
    let inspectorAvailable: Bool
    let onNewTask: () -> Void
    let onOpenProviders: () -> Void

    /// The loaded detail, but only when it is the detail of the task that is
    /// actually selected.
    ///
    /// Selection changes before the fetch returns, so the previously loaded task
    /// is briefly still in the store. Rendering it under the new selection would
    /// attribute one task's routing, changes and verification to another.
    private var currentDetail: TaskDetailView? {
        guard let detail, detail.task.taskId == selectedTaskId else { return nil }
        return detail
    }

    var body: some View {
        Group {
            if currentDetail == nil && selectedTaskId == nil {
                // No selection is a state of the whole pane, not a document in
                // it: one centered composition, with no scrolling document
                // chrome and no panel left hanging beneath it.
                noSelection
            } else {
                document
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .accessibilityIdentifier("workspace.detail")
    }

    private var document: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.section) {
                if let detail = currentDetail {
                    TaskDetailHeader(
                        detail: detail,
                        projectName: projectName,
                        onReload: reload,
                        onCancel: cancel
                    )
                    cancellationNotice(detail)
                    TaskAttentionBanner(reasons: TaskAttention.reasons(for: detail))
                    // AUTO_PLANNED / AUTO_GRACE are their own supervision mode.
                    // They must never look like ordinary manual dispatch states.
                    TaskAutoSupervisionSection(detail: detail)
                    TaskExecutionSection(detail: detail)
                    TaskLifecycleSection(detail: detail)
                    TaskRoutingSection(summary: detail.routingSummary)
                    TaskChangesSection(
                        changes: TaskChangeSet.derive(from: detail),
                        workspace: detail.workspace
                    )
                    TaskVerificationSection(
                        summary: TaskVerificationSummary(report: detail.verification),
                        approvals: detail.approvals.approvals
                    )
                    activity(detail)
                    if !inspectorAvailable {
                        additionalDetails(detail)
                    }
                } else {
                    // A task is selected but its detail has not arrived. Loading
                    // is not "no task selected", and it is not an error either.
                    loading
                }
            }
            .frame(maxWidth: ContentWidth.reading, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, DashboardLayoutMetrics.pageHorizontalPadding)
            .padding(.vertical, DashboardLayoutMetrics.pageVerticalPadding)
        }
    }

    // MARK: - States

    private var loading: some View {
        VStack(spacing: Spacing.inner) {
            ProgressView().controlSize(.small)
            Text(L10n.emptyTasksLoading)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 240)
    }

    /// No selection is its own state, centered in the detail pane as one
    /// composition. The routing readiness card is part of that composition —
    /// "no eligible provider is connected" is the one thing that makes every
    /// future task fail, and it is worth knowing before a task is picked —
    /// rather than a detached panel floating below the empty state.
    private var noSelection: some View {
        VStack(spacing: DashboardLayoutMetrics.sectionSpacing) {
            EmptyStateView(
                title: L10n.emptyTasksNoSelectionTitle,
                symbol: "sidebar.left",
                message: L10n.emptyTasksNoSelectionMessage,
                hint: L10n.taskBrowserHint
            )
            // Only when it has something to say. With a provider connected and
            // tasks in the list it had nothing left but a restatement of the
            // empty state, drawn as a full-width panel below it.
            TaskRoutingReadinessNotice(
                onNewTask: onNewTask,
                onOpenProviders: onOpenProviders
            )
            .frame(maxWidth: 420)
        }
        .padding(DashboardLayoutMetrics.pageHorizontalPadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
    }

    /// The result of a Stop the owner just pressed. The daemon's semantics are
    /// preserved verbatim — "already cancelled" and "running conflict" are
    /// different answers and read differently.
    @ViewBuilder
    private func cancellationNotice(_ detail: TaskDetailView) -> some View {
        if let notice = store.cancellationNotice, noticeApplies(notice, to: detail.task.taskId) {
            TaskSectionNotice(
                text: L10n.cancelNotice(notice),
                symbol: "info.circle",
                tone: .caution
            )
        }
    }

    private func noticeApplies(_ notice: CancelNotice, to taskId: String) -> Bool {
        switch notice {
        case .cancelled(let id), .alreadyCancelled(let id), .runningConflict(let id):
            return id == taskId
        case .failed, .malformedResponse:
            return true
        }
    }

    @ViewBuilder
    private func activity(_ detail: TaskDetailView) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                EventList(events: detail.events)
            }
            .padding(.top, Spacing.inner)
        } label: {
            Text(L10n.detailPanelLiveActivity)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func additionalDetails(_ detail: TaskDetailView) -> some View {
        DisclosureGroup {
            TaskInspectorContent(detail: detail)
                .frame(minHeight: 320)
        } label: {
            Text(L10n.advancedDetails)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Actions

    private func reload() {
        guard let selectedTaskId else { return }
        Task { await store.loadTaskDetail(taskId: selectedTaskId) }
    }

    private func cancel() {
        guard let selectedTaskId else { return }
        Task { await store.cancel(taskId: selectedTaskId) }
    }
}

// MARK: - Header

/// What this task is, where it stands, and what can be done to it.
///
/// Typography, status and timing all come from the B1 foundations. `PageHeader`
/// itself is not reused here: it titles a destination with a fixed label, and a
/// task's title is owner-written prose that has to wrap rather than truncate.
struct TaskDetailHeader: View {
    let detail: TaskDetailView
    let projectName: String?
    let onReload: () -> Void
    let onCancel: () -> Void

    private var status: StatusPresentation { StatusStyle.task(state: detail.task.state) }

    private var isLive: Bool { !TaskTiming.isTerminal(state: detail.task.state) }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            Text(detail.task.intent)
                .font(.title2.weight(.semibold))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: Spacing.element) {
                TaskStatusLabel(presentation: status, text: detail.task.state)
                Label(
                    projectName ?? detail.task.projectId ?? L10n.tasksNoProject,
                    systemImage: "folder"
                )
                .foregroundStyle(.secondary)
                elapsed
            }
            .font(.callout)

            actions
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A live task's elapsed reading advances with the wall clock rather than
    /// freezing at its last state change; a finished one stops where it stopped.
    /// `TimelineView` supplies the tick without a timer this view has to own.
    @ViewBuilder
    private var elapsed: some View {
        if isLive {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                elapsedLabel(now: context.date)
            }
        } else {
            elapsedLabel(now: Date())
        }
    }

    private func elapsedLabel(now: Date) -> some View {
        let interval = TaskTiming.elapsed(task: detail.task, now: now)
        return Label {
            Text(interval.map(Timestamps.duration) ?? L10n.valueUnknown)
                .monospacedDigit()
        } icon: {
            Image(systemName: "timer")
        }
        .foregroundStyle(.secondary)
        .help(L10n.detailElapsed)
    }

    /// Only actions the control API actually supports. There is no resolve
    /// endpoint for approvals, so there is no Approve button.
    private var actions: some View {
        HStack(spacing: Spacing.inner) {
            if TaskStates.isCancellable(detail.task.state) {
                Button(role: .destructive, action: onCancel) {
                    Label(L10n.detailStop, systemImage: "stop.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .help(L10n.detailStopHelp)
            }
            Button(action: onReload) {
                Label(L10n.reload, systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help(L10n.reloadHint)

            Button {
                Pasteboard.copy(detail.task.taskId)
            } label: {
                Label(L10n.copyTaskId, systemImage: "doc.on.doc")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help(L10n.copyTaskIdHint)
        }
    }
}

// MARK: - Attention

/// Why this task needs the owner, at the top where it is read first.
struct TaskAttentionBanner: View {
    let reasons: [TaskAttention]

    var body: some View {
        if !reasons.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                ForEach(reasons) { reason in
                    let style = StatusStyle.attention(reason.kind)
                    HStack(alignment: .top, spacing: Spacing.inner) {
                        Image(systemName: style.symbol)
                            .foregroundStyle(style.color)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(reason.title)
                                .font(.callout.weight(.semibold))
                            // The daemon's own words, verbatim. When it recorded
                            // none, the absence is stated rather than filled in.
                            Text(reason.detail ?? L10n.taskAttentionNoDetail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                        Spacer(minLength: 0)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(Spacing.element)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                Color(nsColor: .controlBackgroundColor),
                in: RoundedRectangle(cornerRadius: Radius.panel)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Radius.panel)
                    .stroke(StatusStyle.attention(reasons[0].kind).color.opacity(0.5), lineWidth: 1)
            )
        }
    }
}

// MARK: - Lifecycle

/// Where the work is, from authoritative records only.
///
/// Discrete stages, no percentage. Intra-stage progress is BACKEND_WORK_REQUIRED
/// (Phase A): the daemon publishes stage transitions, so a bar drawn here would
/// be a number the client made up.
struct TaskLifecycleSection: View {
    let detail: TaskDetailView

    var body: some View {
        TaskDetailSection(
            L10n.taskDetailSectionLifecycle,
            symbol: "list.bullet.indent",
            footnote: L10n.taskLifecycleFooter
        ) {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                ForEach(TaskLifecycle.steps(for: detail)) { step in
                    let style = StatusStyle.lifecycle(step.status)
                    HStack(alignment: .firstTextBaseline, spacing: Spacing.inner) {
                        Image(systemName: style.symbol)
                            .foregroundStyle(style.color)
                            .frame(width: 16)
                        Text(step.stage.title)
                            .font(.callout)
                        Spacer(minLength: Spacing.inner)
                        VStack(alignment: .trailing, spacing: 0) {
                            Text(L10n.taskLifecycleStatus(step.status))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let at = step.at {
                                Text(Timestamps.friendly(at))
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(
                        "\(step.stage.title), \(L10n.taskLifecycleStatus(step.status))"
                    )
                }
            }
        }
    }
}
