import SwiftUI

import PAOControlKit

/// Did verification pass, and on what evidence.
///
/// The distinction this section exists to preserve: a verifier that ran and
/// rejected the work is a verdict about the work, while a verifier whose
/// evidence cannot be produced is a fact about the infrastructure. The previous
/// surface matched on `"VERIFIED"` and `"FAILED"` only, so the daemon's actual
/// `FAILED_VERIFICATION` and `VERIFIED_EVIDENCE_MISSING` both fell through to
/// the unknown branch and rendered identically.
struct TaskVerificationSection: View {
    let summary: TaskVerificationSummary
    let approvals: [ApprovalView]

    private var presentation: StatusPresentation {
        StatusStyle.verification(summary.status)
    }

    var body: some View {
        TaskDetailSection(L10n.detailPanelVerification, symbol: "checkmark.seal", footnote: footnote) {
            TaskStatusLabel(
                presentation: presentation,
                text: L10n.verificationStatusName(summary.status)
            )

            if summary.status.isEvidenceProblem {
                TaskSectionNotice(
                    text: L10n.verificationEvidenceProblemFooter,
                    symbol: presentation.symbol,
                    tone: .unknown
                )
            }

            if let reason = summary.failureReason {
                // The daemon's own sentence for why verification rejected the
                // work, verbatim.
                Text(reason)
                    .font(.callout)
                    .foregroundStyle(StatusStyle.verification("FAILED_VERIFICATION").color)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }

            if summary.hasResult {
                TaskFieldRow(label: L10n.detailVerifierProfile, value: summary.profile)
                if let passed = summary.resultPassed {
                    LabeledContent(L10n.verificationOverall) {
                        TaskStatusLabel(
                            presentation: StatusStyle.verificationStage(passed: passed),
                            text: passed ? L10n.valueStagePassed : L10n.valueStageFailed,
                            font: .body
                        )
                    }
                }
                stages
            } else {
                TaskSectionNotice(
                    text: summary.status == .notVerified
                        ? L10n.verificationNotRunYet
                        : L10n.verificationNoResult,
                    symbol: "seal"
                )
            }

            TaskFieldRow(
                label: L10n.evidenceLabel,
                value: summary.evidenceId,
                monospaced: true,
                absentText: L10n.valueNone
            )

            if !approvals.isEmpty {
                approvalList
            }
        }
    }

    private var footnote: String? {
        summary.stages.isEmpty ? nil : L10n.verificationStageFooter
    }

    @ViewBuilder
    private var stages: some View {
        if summary.stages.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: Spacing.tight) {
                Text(L10n.verificationChecks)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ForEach(summary.stages) { stage in
                    HStack(spacing: Spacing.tight) {
                        let style = StatusStyle.verificationStage(passed: stage.passed)
                        Image(systemName: style.symbol)
                            .foregroundStyle(style.color)
                        Text(stage.name)
                            .font(.system(.caption, design: .monospaced))
                        Spacer(minLength: Spacing.inner)
                        Text(L10n.verificationCheckExit(stage.exitCode))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(
                        "\(stage.name), \(stage.passed ? L10n.valueStagePassed : L10n.valueStageFailed)"
                    )
                }
            }
        }
    }

    /// Approvals are read-only here: the control API exposes no resolve
    /// endpoint, so an Approve button would be a control that does nothing.
    private var approvalList: some View {
        VStack(alignment: .leading, spacing: Spacing.tight) {
            Text(L10n.verificationApprovals)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(approvals) { approval in
                HStack {
                    Text(approval.kind)
                        .font(.system(.caption, design: .monospaced))
                    Spacer()
                    Text(approval.status)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(
                            approval.status == "PENDING"
                                ? StatusStyle.attention(.awaitingApproval).color
                                : Color.secondary
                        )
                }
            }
        }
    }
}
