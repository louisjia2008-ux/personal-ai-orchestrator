import SwiftUI

import PAOControlKit

// Small presentation primitives shared by the task workspace.
//
// Deliberately short of a design system: these exist because Task Detail's
// sections would otherwise repeat the same section header, the same status
// label and the same label/value row in six files. Anything used once stays
// where it is used.

/// One section of Task Detail.
///
/// A header, a rule, and content — not a rounded card. Six stacked cards read as
/// six separate widgets; a task is one thing, and its sections are parts of it.
struct TaskDetailSection<Content: View>: View {
    let title: String
    var symbol: String?
    var footnote: String?
    @ViewBuilder var content: Content

    init(
        _ title: String,
        symbol: String? = nil,
        footnote: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.symbol = symbol
        self.footnote = footnote
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.inner) {
            HStack(spacing: Spacing.tight) {
                if let symbol {
                    Image(systemName: symbol)
                        .imageScale(.small)
                        .foregroundStyle(.secondary)
                }
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .accessibilityAddTraits(.isHeader)
            Divider()
            content
            if let footnote {
                Text(footnote)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Symbol plus text for one status.
///
/// The symbol is not decoration: it is the second channel that keeps the status
/// legible in greyscale and for colour-blind viewers, which is why every
/// `StatusPresentation` carries one and this view always draws it.
struct TaskStatusLabel: View {
    let presentation: StatusPresentation
    let text: String
    var font: Font = .callout

    var body: some View {
        HStack(spacing: Spacing.tight) {
            Image(systemName: presentation.symbol)
                .foregroundStyle(presentation.color)
            Text(text)
        }
        .font(font)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(text)
    }
}

/// A label/value pair in Task Detail.
///
/// `LabeledContent` with one addition: a value the daemon did not supply renders
/// as an explicit absence in tertiary type rather than as an empty string, so a
/// missing field never reads as a blank success.
struct TaskFieldRow: View {
    let label: String
    let value: String?
    var monospaced: Bool = false
    /// Shown when `value` is nil. Says why there is nothing, never "--".
    var absentText: String = L10n.valueUnknown

    var body: some View {
        LabeledContent(label) {
            if let value, !value.isEmpty {
                Text(value)
                    .font(monospaced ? .system(.body, design: .monospaced) : .body)
                    .textSelection(.enabled)
                    .multilineTextAlignment(.trailing)
            } else {
                Text(absentText)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

/// An inline explanation for a section that has nothing to show.
///
/// Distinct from `EmptyStateView`: that fills a pane, this sits inside a section
/// that is one of several. Both say what is absent and why.
struct TaskSectionNotice: View {
    let text: String
    var symbol: String = "info.circle"
    var tone: StatusTone = .neutral

    var body: some View {
        Label {
            Text(text)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: symbol)
                .foregroundStyle(tone.color)
        }
        .font(.callout)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
