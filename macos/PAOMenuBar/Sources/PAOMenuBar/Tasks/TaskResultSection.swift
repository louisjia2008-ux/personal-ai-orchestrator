import SwiftUI
import AppKit
import PAOControlKit

/// The answer and next decision precede execution telemetry. All content comes
/// from the safe host projection, never a worker's self-reported final answer.
struct TaskResultSection: View {
    @EnvironmentObject private var store: OrchestratorStore
    let taskId: String
    @State private var copied = false
    @State private var confirmsRecovery = false
    @State private var recoveryConfirmation: RecoveryRequest?

    private var outcome: TaskContinuationPayload? {
        guard let outcome = store.selectedTaskResult,
              outcome.task.taskId == taskId,
              let current = store.selectedTaskDetail?.task,
              outcome.matches(current) else { return nil }
        return store.selectedTaskResult
    }

    private var candidate: RecoveryCandidate? {
        guard let detail = store.selectedSafeDetail,
              detail.payload.task.taskId == taskId,
              detail.payload.task.stateVersion == store.selectedTaskDetail?.task.stateVersion,
              detail.payload.task.state == store.selectedTaskDetail?.task.state
        else { return nil }
        return detail.payload.recoveryCandidate
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.section) {
            TaskDetailSection(L10n.controlText("result.title"), symbol: "doc.text.magnifyingglass") {
                if let outcome {
                    report(outcome.result)
                } else {
                    TaskSectionNotice(text: store.taskResultError ?? L10n.controlText("result.loading"))
                }
            }
            TaskDetailSection(L10n.controlText("next.title"), symbol: "arrow.right.circle") {
                if let outcome {
                    ForEach(Array(outcome.continuation.pending.enumerated()), id: \.offset) { _, item in
                        Label(item, systemImage: "circle")
                            .font(.callout)
                            .textSelection(.enabled)
                    }
                    Text(outcome.continuation.nextStep)
                        .font(.callout)
                        .textSelection(.enabled)
                    Button {
                        NSPasteboard.general.clearContents()
                        copied = NSPasteboard.general.setString(outcome.continuation.text, forType: .string)
                    } label: {
                        Label(L10n.controlText(copied ? "continuation.copied" : "continuation.copy"),
                              systemImage: "doc.on.doc")
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("task.copyContinuation")
                    Text(L10n.controlText("continuation.manual"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(L10n.controlText("next.review"))
                        .font(.callout).foregroundStyle(.secondary)
                }
                recoveryActions
            }
        }
        .accessibilityIdentifier("task.resultAndNextSteps")
        .alert(L10n.controlText("recovery.title"), isPresented: $confirmsRecovery,
               presenting: recoveryConfirmation) { request in
            Button(L10n.controlText("recovery.action")) {
                Task { await store.recoverTask(request) }
            }
            Button(L10n.cancel, role: .cancel) {}
        } message: { _ in
            Text(L10n.controlText("recovery.explanation"))
        }
    }

    private func report(_ result: HostResultReport) -> some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            Text(L10n.controlText("result.delivery.\(result.completeness)"))
                .font(.headline)
            Text(L10n.controlText("result.boundary"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TaskFieldRow(label: L10n.verification, value: result.verification.status)
            if result.goalRedacted || result.truncated.values.contains(true) {
                TaskSectionNotice(text: L10n.controlText("result.omissions"), tone: .caution)
            }
            if !result.tests.isEmpty {
                ForEach(Array(result.tests.enumerated()), id: \.offset) { _, test in
                    LabeledContent(test.name, value: test.status).font(.callout)
                }
            }
            if !result.changedFiles.isEmpty {
                DisclosureGroup(L10n.controlText("result.files")) {
                    ForEach(result.changedFiles, id: \.self) { path in
                        Text(path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            DisclosureGroup(L10n.controlText("result.evidence")) {
                VStack(alignment: .leading, spacing: Spacing.tight) {
                    Text(result.resultId)
                    if let base = result.binding.baseCommit { Text("Base: \(base)") }
                    ForEach(Array(result.artifacts.enumerated()), id: \.offset) { _, artifact in
                        Text(artifact.reference)
                        Text("SHA-256: \(artifact.sha256)")
                    }
                    ForEach(result.limitations, id: \.self) { limitation in
                        Text(L10n.controlText("result.limit.\(limitation)"))
                    }
                }
                .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
    }

    @ViewBuilder
    private var recoveryActions: some View {
        if let notice = store.recoveryNotices[taskId] {
            TaskSectionNotice(text: notice, tone: .caution)
        }
        if store.pendingRecoveries[taskId] != nil {
            Button(L10n.controlText("recovery.check")) {
                Task { await store.reconcileRecovery(taskId: taskId) }
            }
            .disabled(store.isRecoveringTask)
        } else if candidate != nil {
            Text(L10n.controlText("recovery.explanation"))
                .font(.caption).foregroundStyle(.secondary)
            Button(L10n.controlText("recovery.action")) {
                recoveryConfirmation = store.prepareRecovery()
                confirmsRecovery = recoveryConfirmation != nil
            }
            .buttonStyle(.bordered)
            .disabled(store.isRecoveringTask || !store.connection.isConnected)
            .accessibilityIdentifier("task.recoverInterrupted")
        }
    }
}
