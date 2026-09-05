import SwiftUI

import PAOControlKit

/// Secondary technical metadata for the selected task.
///
/// Everything here is verbatim machine identity — ids, paths, SHAs, raw
/// timestamps — which is exactly the material that must stay available and
/// exactly the material that must not lead. Nothing a task's status depends on
/// lives here: Task Detail answers what happened without this pane ever being
/// opened, and this pane answers "which record, exactly".
///
/// On macOS 14 and later it is presented through the native `.inspector`. The
/// deployment target is macOS 13, where that modifier does not exist, so the
/// same content renders as a collapsed disclosure at the end of Task Detail
/// rather than being unavailable on older systems.
struct TaskInspectorContent: View {
    let detail: TaskDetailView?

    var body: some View {
        Group {
            if let detail {
                ScrollView {
                    VStack(alignment: .leading, spacing: Spacing.section) {
                        identity(detail)
                        timestamps(detail)
                        if let workspace = detail.workspace {
                            workspaceSection(workspace)
                        }
                        if !detail.runs.isEmpty {
                            runsSection(detail)
                        }
                        if let summary = detail.routingSummary {
                            routingProvenance(summary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(Spacing.section)
                }
            } else {
                EmptyStateView(
                    title: L10n.inspectorTitle,
                    symbol: "sidebar.right",
                    message: L10n.inspectorEmpty
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    private func identity(_ detail: TaskDetailView) -> some View {
        TaskDetailSection(L10n.inspectorIdentity, symbol: "number") {
            TaskFieldRow(label: L10n.detailTaskId, value: detail.task.taskId, monospaced: true)
            TaskFieldRow(
                label: L10n.detailRequestId, value: detail.task.requestId, monospaced: true
            )
            TaskFieldRow(
                label: L10n.labelStateVersion,
                value: String(detail.task.stateVersion),
                monospaced: true
            )
            TaskFieldRow(
                label: L10n.labelProjectId,
                value: detail.task.projectId,
                monospaced: true,
                absentText: L10n.tasksNoProject
            )
            TaskFieldRow(
                label: L10n.detailBaseSha,
                value: detail.task.baseSha,
                monospaced: true,
                absentText: L10n.valueNone
            )
        }
    }

    /// Raw ISO-8601, deliberately. Task Detail reads these instants in the
    /// product language; the exact machine value belongs here.
    private func timestamps(_ detail: TaskDetailView) -> some View {
        TaskDetailSection(L10n.inspectorTimestamps, symbol: "clock") {
            TaskFieldRow(
                label: L10n.labelCreatedAt, value: detail.task.createdAt, monospaced: true
            )
            TaskFieldRow(
                label: L10n.labelUpdatedAt, value: detail.task.updatedAt, monospaced: true
            )
        }
    }

    private func workspaceSection(_ workspace: WorkspaceView) -> some View {
        TaskDetailSection(L10n.inspectorWorkspaceSection, symbol: "folder") {
            TaskFieldRow(
                label: L10n.labelRepositoryPath, value: workspace.repoPath, monospaced: true
            )
            TaskFieldRow(label: L10n.worktree, value: workspace.worktreePath, monospaced: true)
            TaskFieldRow(label: L10n.branch, value: workspace.branch, monospaced: true)
            TaskFieldRow(label: L10n.detailBaseSha, value: workspace.baseSha, monospaced: true)
            TaskFieldRow(
                label: L10n.projectsWorkingSubpath,
                value: workspace.workingSubpath,
                monospaced: true,
                absentText: L10n.valueNone
            )
            // Raw lock state: "HELD"/"RELEASED" are the machine words the rest of
            // the client uses for this field.
            TaskFieldRow(
                label: L10n.writerLock,
                value: workspace.writerLocked ? "HELD" : "RELEASED",
                monospaced: true
            )
        }
    }

    private func runsSection(_ detail: TaskDetailView) -> some View {
        TaskDetailSection(L10n.inspectorRunsSection, symbol: "terminal") {
            ForEach(detail.runs) { run in
                VStack(alignment: .leading, spacing: 2) {
                    TaskFieldRow(label: L10n.detailRunId, value: run.runId, monospaced: true)
                    TaskFieldRow(
                        label: L10n.executionTarget, value: run.workerId, monospaced: true
                    )
                    TaskFieldRow(
                        label: L10n.detailRunStatus, value: run.status, monospaced: true
                    )
                    TaskFieldRow(
                        label: "PID",
                        value: run.pid.map { pid in
                            // When the daemon confirms the OS no longer sees
                            // the worker process, swap the stale "running
                            // pid N" line for an honest "exited" so the
                            // owner never wonders why a finished run shows
                            // a live pid.
                            if run.pidAlive == false {
                                return L10n.runProcessExited
                            }
                            if run.pidAlive == true {
                                return "\(pid) (\(L10n.runProcessAlive))"
                            }
                            return String(pid)
                        },
                        monospaced: true,
                        absentText: L10n.valueNone
                    )
                    TaskFieldRow(
                        label: "exit_code",
                        value: run.exitCode.map(String.init),
                        monospaced: true,
                        absentText: L10n.valueNone
                    )
                    TaskFieldRow(
                        label: "stdout_sha256",
                        value: run.stdoutSHA256,
                        monospaced: true,
                        absentText: L10n.valueNone
                    )
                    TaskFieldRow(
                        label: "stderr_sha256",
                        value: run.stderrSHA256,
                        monospaced: true,
                        absentText: L10n.valueNone
                    )
                    if run.outputTruncated == true {
                        Text("output_truncated=true")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    if run.timedOut == true {
                        Text("timed_out=true")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.bottom, Spacing.tight)
                Divider()
            }
        }
    }

    /// Plan identity and policy resolution, verbatim.
    ///
    /// A synthesized summary reports no plan id and no revision, and the pane
    /// says so rather than leaving the rows blank — an absent plan id is the
    /// evidence that this daemon predates the contract.
    private func routingProvenance(_ summary: TaskRoutingSummary) -> some View {
        TaskDetailSection(
            L10n.inspectorRoutingProvenance,
            symbol: "point.topleft.down.curvedto.point.bottomright.up",
            footnote: summary.isLegacySynthesized ? L10n.routingLegacySynthesized : nil
        ) {
            TaskFieldRow(
                label: L10n.routingPlanLabel,
                value: summary.planId,
                monospaced: true,
                absentText: L10n.valueNone
            )
            TaskFieldRow(
                label: L10n.routingPlanRevisionLabel,
                value: summary.planRevision.map(String.init),
                monospaced: true,
                absentText: L10n.valueNone
            )
            TaskFieldRow(
                label: L10n.routingResolvedPolicy,
                value: summary.resolvedPolicy ?? summary.policyId,
                monospaced: true,
                absentText: L10n.valueUnknown
            )
            TaskFieldRow(
                label: L10n.policyResolutionSourceLabel,
                value: summary.policyResolutionSource,
                monospaced: true,
                absentText: L10n.valueUnknown
            )
            TaskFieldRow(
                label: L10n.routingDeclaredRoles,
                value: summary.roles.map(\.role.rawValue).joined(separator: ", "),
                monospaced: true,
                absentText: L10n.valueNone
            )
            ForEach(summary.roles) { role in
                if let decision = role.activeDecision {
                    TaskFieldRow(
                        label: "\(role.role.rawValue) \(L10n.detailRoutingDecision)",
                        value: decision.decisionId,
                        monospaced: true
                    )
                    TaskFieldRow(
                        label: "\(role.role.rawValue) \(L10n.detailQuotaEvidence)",
                        value: decision.quotaSnapshotId,
                        monospaced: true,
                        absentText: L10n.valueNone
                    )
                }
            }
        }
    }
}
