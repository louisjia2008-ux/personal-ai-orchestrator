import SwiftUI

import PAOControlKit

/// What is running this task right now, and what the owner may start.
///
/// The owner-dispatch controls moved here unchanged. Every gate they enforce is
/// the same one: the persisted owner setting must be on, the task must be in a
/// dispatchable state, and the target must be an execution-verified one. None of
/// that implies autonomous Production ACTIVE, which stays disabled by design.
struct TaskExecutionSection: View {
    @EnvironmentObject private var store: OrchestratorStore
    let detail: TaskDetailView
    @State private var selectedTargetId: String = ""

    /// Who is doing the work: the run's worker if one started, otherwise the
    /// primary role's selection. Never "Automatic" — that word described a
    /// policy, not a worker, and read as though something had been chosen.
    private var currentWorker: String? {
        detail.runs.last?.workerId
            ?? detail.routingSummary?.primary?.activeDecision?.selectedExecutionTargetId
    }

    private var verifiedTargets: [ExecutionTargetHealthView] {
        (store.providers?.providers ?? [])
            .flatMap(\.executionTargets)
            .filter { $0.enabled && $0.isExecutionVerified }
    }

    private var ownerSettingEnabled: Bool {
        store.ownerExecutionSettings?.ownerInitiatedExecutionEnabled ?? false
    }

    private var taskDispatchable: Bool {
        detail.task.state == "SUBMITTED" || detail.task.state == "READY"
    }

    private var canDispatch: Bool {
        ownerSettingEnabled && taskDispatchable && !verifiedTargets.isEmpty
            && !selectedTargetId.isEmpty
    }

    var body: some View {
        TaskDetailSection(L10n.taskDetailSectionExecution, symbol: "play.rectangle") {
            TaskFieldRow(
                label: L10n.labelCurrentWorker,
                value: currentWorker,
                monospaced: true,
                absentText: L10n.labelNoWorkerYet
            )
            if let run = detail.runs.last {
                TaskFieldRow(label: L10n.detailRunStatus, value: run.status)
            }
            dispatch
            workerLog
        }
        .onAppear {
            if selectedTargetId.isEmpty {
                selectedTargetId = preferredTargetId
            }
        }
    }

    /// The target the task itself names first: a MANUAL scheduling policy
    /// recorded its choice at submit time, and honoring it here is the one
    /// place the policy visibly drives execution today. Everything else
    /// falls back to the first execution-verified target.
    private var preferredTargetId: String {
        if let manual = detail.task.manualExecutionTargetId,
           verifiedTargets.contains(where: { $0.executionTargetId == manual }) {
            return manual
        }
        return verifiedTargets.first?.executionTargetId ?? ""
    }

    /// What the worker actually did, in the worker's own narration.
    ///
    /// opencode narrates progress on stderr (reads, edits, failures); stdout
    /// is usually empty. The daemon stores a bounded sanitized tail — this is
    /// display evidence, never an input to any gate.
    @ViewBuilder
    private var workerLog: some View {
        if let run = detail.runs.last, run.status != "RUNNING",
           run.stderrTail != nil || run.stdoutTail != nil {
            VStack(alignment: .leading, spacing: Spacing.tight) {
                Divider()
                Text(L10n.detailRawWorkerOutput)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                if run.outputTruncated == true {
                    Text(L10n.workerLogTruncated)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                ScrollView(.vertical) {
                    VStack(alignment: .leading, spacing: Spacing.tight) {
                        if let stderr = run.stderrTail {
                            Text(stderr)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                        if let stdout = run.stdoutTail {
                            Text(stdout)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                    }
                }
                .frame(maxHeight: 160)
                .border(Color(nsColor: .separatorColor), width: 1)
            }
        }
    }

    /// Only shown while dispatch is a live possibility for this task. On a task
    /// that has already run, a disabled Dispatch button is noise.
    @ViewBuilder
    private var dispatch: some View {
        if taskDispatchable {
            VStack(alignment: .leading, spacing: Spacing.inner) {
                Divider()
                if !ownerSettingEnabled {
                    TaskSectionNotice(text: L10n.ownerExecutionDisabled, symbol: "lock")
                }
                if verifiedTargets.isEmpty {
                    TaskSectionNotice(text: L10n.noVerifiedTargets, symbol: "xmark.shield")
                } else {
                    Picker(L10n.executionTarget, selection: $selectedTargetId) {
                        ForEach(verifiedTargets) { target in
                            Text(target.executionTargetId).tag(target.executionTargetId)
                        }
                    }
                    if let target = verifiedTargets.first(where: {
                        $0.executionTargetId == selectedTargetId
                    }) {
                        TaskFieldRow(label: L10n.providerModel, value: target.modelSkuId)
                    }
                }
                HStack(spacing: Spacing.inner) {
                    Button {
                        Task {
                            await store.dispatch(
                                taskId: detail.task.taskId,
                                executionTargetId: selectedTargetId
                            )
                        }
                    } label: {
                        Label(L10n.dispatch, systemImage: "play")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(!canDispatch)
                    .help(canDispatch ? L10n.dispatchHelp : L10n.dispatchBlockedHint)
                    Button {
                        Task {
                            await store.recommendDispatch(taskId: detail.task.taskId)
                        }
                    } label: {
                        Label(L10n.recommendDispatch, systemImage: "wand.and.stars")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(!ownerSettingEnabled || verifiedTargets.isEmpty)
                    .help(L10n.recommendDispatchHelp)
                }
                if let policy = detail.task.schedulingPolicy {
                    TaskFieldRow(
                        label: L10n.newTaskSchedulingPolicy,
                        value: L10n.schedulingPolicyName(policy)
                    )
                    Text(policy == "MANUAL" ? L10n.schedulingPolicyManualLinked : L10n.schedulingPolicyArchived)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let recommendation = store.dispatchRecommendation,
                   recommendation.taskId == detail.task.taskId {
                    DispatchRecommendationPanel(
                        recommendation: recommendation,
                        onDismiss: { store.clearDispatchRecommendation() },
                        onDispatch: { targetId in
                            Task {
                                await store.dispatch(
                                    taskId: detail.task.taskId,
                                    executionTargetId: targetId
                                )
                                store.clearDispatchRecommendation()
                            }
                        }
                    )
                }
                Text(L10n.ownerDispatchFooter)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// What the policy-driven recommender returned for this task: top pick first,
/// every other target with the reason it was admitted, demoted, or excluded.
struct DispatchRecommendationPanel: View {
    let recommendation: DispatchRecommendationView
    let onDismiss: () -> Void
    let onDispatch: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            Divider()
            HStack {
                Text(L10n.recommendationPanelTitle(recommendation.schedulingPolicy))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(L10n.recommendationDismiss)
            }
            Text(recommendation.decisionReason)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: Spacing.tight) {
                ForEach(recommendation.candidates) { candidate in
                    DispatchRecommendationRow(
                        candidate: candidate,
                        isTopPick: candidate.executionTargetId == recommendation.topPick,
                        onDispatch: { onDispatch(candidate.executionTargetId) }
                    )
                }
            }
        }
    }
}

struct DispatchRecommendationRow: View {
    let candidate: DispatchRecommendationCandidate
    let isTopPick: Bool
    let onDispatch: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.inner) {
            Image(systemName: candidate.admitted
                  ? (isTopPick ? "star.fill" : "checkmark.circle")
                  : "xmark.circle")
                .foregroundStyle(isTopPick ? .yellow : StatusStyle.attention(.blocked).color)
            VStack(alignment: .leading, spacing: 1) {
                Text(candidate.executionTargetId)
                    .font(.system(.caption, design: .monospaced))
                if let score = candidate.score {
                    Text(L10n.recommendationScore(
                        score,
                        candidate.headroomMean ?? 0,
                        candidate.evidenceFresh
                    ))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }
                if let state = candidate.quotaState, state != "AVAILABLE_OBSERVED" {
                    Text(L10n.recommendationQuotaState(state))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                ForEach(candidate.reasons, id: \.self) { reason in
                    Text(reason)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            Spacer(minLength: 0)
            if candidate.admitted {
                Button(L10n.dispatch, action: onDispatch)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.mini)
            }
        }
        .padding(.vertical, 2)
    }
}
struct TaskRoutingReadinessNotice: View {
    @EnvironmentObject private var store: OrchestratorStore
    let onNewTask: () -> Void
    let onOpenProviders: () -> Void

    private var connectedCount: Int {
        store.providerConnections?.connected.count ?? 0
    }

    private var importCandidateCount: Int {
        store.providerConnections?.importCandidates.count ?? 0
    }

    private var hasTasks: Bool { !(store.tasks?.tasks ?? []).isEmpty }

    /// Whether there is anything here worth a panel.
    ///
    /// With a provider connected and tasks already in the list, this notice had
    /// nothing to add to the empty state it sat under — so it renders nothing
    /// rather than a panel restating "select a task".
    private var hasSomethingToSay: Bool { connectedCount == 0 || !hasTasks }

    var body: some View {
        if hasSomethingToSay {
            notice
        }
    }

    private var notice: some View {
        TaskDetailSection(L10n.routingTitle, symbol: "point.topleft.down.curvedto.point.bottomright.up") {
            if connectedCount == 0 {
                TaskSectionNotice(
                    text: L10n.routingNoConnectedProviders,
                    symbol: "exclamationmark.triangle",
                    tone: .caution
                )
                HStack(spacing: Spacing.inner) {
                    Button(action: onOpenProviders) {
                        Label(L10n.providersAddProvider, systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    if importCandidateCount > 0 {
                        Button(action: onOpenProviders) {
                            Label(L10n.providersImportExisting, systemImage: "square.and.arrow.down")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                }
            } else {
                Text(hasTasks ? L10n.routingNoTaskSelected : L10n.routingNeedsTask)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(L10n.routingConsiders)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if !hasTasks {
                    Button(action: onNewTask) {
                        Label(L10n.newTask, systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
            }
        }
    }
}
