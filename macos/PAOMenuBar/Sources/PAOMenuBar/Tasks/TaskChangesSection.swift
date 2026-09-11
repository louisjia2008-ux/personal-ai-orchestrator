import AppKit
import SwiftUI

import PAOControlKit

/// What the task changed.
///
/// The payload already carries the paths; the previous surface printed only a
/// count, so "3" was all the owner ever learned about a task's output. The list
/// is shown, out-of-scope writes are marked, and the two things the daemon does
/// not record — per-file change kind and line counts — are named as absent
/// rather than invented.
struct TaskChangesSection: View {
    let changes: TaskChangeSet
    let workspace: WorkspaceView?

    var body: some View {
        TaskDetailSection(
            L10n.detailPanelChanges,
            symbol: "doc.on.clipboard",
            footnote: changes.isMeasured ? L10n.changesFooter : nil
        ) {
            switch changes {
            case .unavailable(let reason):
                // Not measured is not "zero files changed".
                TaskSectionNotice(text: L10n.changesUnavailable(reason), symbol: "tray")
            case .files(let files) where files.isEmpty:
                TaskSectionNotice(text: L10n.changesNone, symbol: "doc")
            case .files(let files):
                fileList(files)
            }
            if let workspace {
                worktreeAction(workspace)
            }
        }
    }

    @ViewBuilder
    private func fileList(_ files: [TaskChangedFile]) -> some View {
        let unexpected = files.filter(\.isUnexpected).count
        VStack(alignment: .leading, spacing: Spacing.inner) {
            HStack(spacing: Spacing.inner) {
                Text(L10n.changesCount(files.count))
                    .font(.callout.monospacedDigit())
                if unexpected > 0 {
                    TaskStatusLabel(
                        presentation: StatusStyle.attention(.unexpectedChanges),
                        text: L10n.changesUnexpectedCount(unexpected),
                        font: .caption
                    )
                }
            }
            // A scroll container rather than an unbounded stack: a large task
            // must not push verification off the bottom of the page.
            ScrollView(.vertical) {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(files) { file in
                        TaskChangedFileRow(file: file)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 220)
        }
    }

    private func worktreeAction(_ workspace: WorkspaceView) -> some View {
        HStack(spacing: Spacing.inner) {
            Button {
                NSWorkspace.shared.activateFileViewerSelecting(
                    [URL(fileURLWithPath: workspace.worktreePath)]
                )
            } label: {
                Label(L10n.changesRevealWorktree, systemImage: "folder")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            Text(workspace.branch)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
    }
}

/// One changed path. The file name leads; the directory follows in secondary
/// type, because a list of full paths reads as a wall of prefixes.
struct TaskChangedFileRow: View {
    let file: TaskChangedFile

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.tight) {
            Image(systemName: file.isUnexpected ? "exclamationmark.triangle" : "doc")
                .imageScale(.small)
                .foregroundStyle(
                    file.isUnexpected
                        ? StatusStyle.attention(.unexpectedChanges).color
                        : Color.secondary
                )
            Text(file.fileName)
                .font(.system(.caption, design: .monospaced))
            if let directory = file.directory {
                Text(directory)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.head)
            }
            Spacer(minLength: 0)
            if file.isUnexpected {
                Text(L10n.changesUnexpected)
                    .font(.caption2)
                    .foregroundStyle(StatusStyle.attention(.unexpectedChanges).color)
            }
        }
        .textSelection(.enabled)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            file.isUnexpected ? "\(file.path), \(L10n.changesUnexpected)" : file.path
        )
    }
}
