import SwiftUI

import PAOControlKit

// Presentation primitives shared by the Resources workspace.
//
// The same three shapes would otherwise be re-declared in six files: a section
// header, a status chip, and a metric that states how it was arrived at. The
// last one is the important one — it is where the evidence rules stop being
// documentation and start being something a call site cannot forget.

/// One section of a resource's detail.
///
/// A header, a rule, and content. Deliberately not a rounded card: a provider is
/// one thing, and eight stacked cards would present its parts as eight
/// independent widgets. Matches `TaskDetailSection` so the two workspaces read
/// as the same application.
struct ResourceSection<Content: View>: View {
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

/// A status as symbol plus text.
///
/// The symbol is the second channel: colour alone would leave the status
/// unreadable in greyscale and for colour-blind viewers, which is why every
/// `StatusPresentation` carries one and this always draws it.
struct ResourceStatusLabel: View {
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

/// A machine value rendered verbatim in a chip.
///
/// Protocol enums are never translated — `EXACT`, `EXHAUSTED_OBSERVED` and
/// `CODING_TEXT` stay as the daemon wrote them — so this carries the tone and
/// the text carries the value.
struct ResourceChip: View {
    let text: String
    var tone: StatusTone = .neutral
    var symbol: String?

    var body: some View {
        HStack(spacing: Spacing.tight) {
            if let symbol {
                Image(systemName: symbol).imageScale(.small)
            }
            Text(text)
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, Spacing.inner)
        .padding(.vertical, 3)
        .background(tone.color.opacity(0.14), in: Capsule())
        .foregroundStyle(tone.color)
        .lineLimit(1)
    }
}

/// A figure and the standing of the claim it makes.
///
/// This is the only way a number reaches the Resources surface. The evidence
/// level is not optional, it decides weight and colour, and a forecast arrives
/// already prefixed with `≈` — so an estimate cannot be typed into the view as
/// though it were a reading. An unavailable metric renders its reason instead of
/// a number, because absent and zero mean opposite things.
struct ResourceMetric: View {
    let label: String
    let metric: MetricPresentation
    /// Shown under the value: the freshness or the basis of the figure.
    var caption: String?

    var body: some View {
        LabeledContent {
            VStack(alignment: .trailing, spacing: 2) {
                if metric.isUnavailable {
                    Text(metric.unavailableReason ?? metric.text)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.trailing)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(metric.displayText)
                        .font(.callout.weight(metric.level.fontWeight).monospacedDigit())
                        .foregroundStyle(metric.level.foregroundStyle)
                }
                if let caption {
                    Text(caption)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.trailing)
                }
            }
        } label: {
            HStack(spacing: Spacing.tight) {
                Text(label)
                // Derived and forecast values say so beside themselves. Observed
                // ones do not: a reading needs no qualifier, and labelling every
                // figure would make the qualifier invisible.
                if metric.level != .observed {
                    Text(metric.level.label)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            metric.isUnavailable
                ? "\(label): \(metric.unavailableReason ?? metric.text)"
                : "\(label): \(metric.level.label), \(metric.displayText)"
        )
    }
}

/// A quota meter that can only be filled by an observation.
///
/// The rule `EvidenceLevel.mayFillMeter` exists for exactly this view. A bar is
/// the strongest claim the interface can make about a quantity, and a bar filled
/// from a projection would claim to *be* the provider's reading. When the figure
/// is not observed, the track renders empty and the value appears beside it.
struct QuotaMeter: View {
    let fraction: Double?
    let level: EvidenceLevel
    let tone: StatusTone

    private var mayFill: Bool {
        level.mayFillMeter && fraction != nil
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color(nsColor: .quaternaryLabelColor))
                if mayFill, let fraction {
                    Capsule()
                        .fill(tone.fillColor)
                        .frame(width: geometry.size.width * CGFloat(min(1, max(0, fraction))))
                }
            }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
    }
}

/// An inline explanation for a section with nothing to show.
///
/// Distinct from `EmptyStateView`, which fills a pane. This sits inside a
/// section that is one of several, and like it, says what is absent and why.
struct ResourceNotice: View {
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

/// A label/value pair. A value the daemon did not supply renders as an explicit
/// absence rather than an empty string, so a missing field never reads as a
/// blank success.
struct ResourceFieldRow: View {
    let label: String
    let value: String?
    var monospaced: Bool = false
    var absentText: String = L10n.valueUnknown

    var body: some View {
        LabeledContent(label) {
            if let value, !value.isEmpty {
                Text(value)
                    .font(monospaced ? .system(.callout, design: .monospaced) : .callout)
                    .textSelection(.enabled)
                    .multilineTextAlignment(.trailing)
            } else {
                Text(absentText)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}
