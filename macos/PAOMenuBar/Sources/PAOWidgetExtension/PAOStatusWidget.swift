#if canImport(WidgetKit)
import PAOControlKit
import SwiftUI
import WidgetKit

struct PAOStatusWidgetEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetSnapshot?
}

struct PAOStatusWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> PAOStatusWidgetEntry {
        PAOStatusWidgetEntry(date: Date(), snapshot: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (PAOStatusWidgetEntry) -> Void) {
        completion(PAOStatusWidgetEntry(date: Date(), snapshot: loadSnapshot()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<PAOStatusWidgetEntry>) -> Void) {
        let entry = PAOStatusWidgetEntry(date: Date(), snapshot: loadSnapshot())
        let refresh = Calendar.current.date(byAdding: .minute, value: 5, to: entry.date) ?? entry.date
        completion(Timeline(entries: [entry], policy: .after(refresh)))
    }

    private func loadSnapshot() -> WidgetSnapshot? {
        guard let appGroupIdentifier = Bundle.main.object(
            forInfoDictionaryKey: "PAOWidgetAppGroup"
        ) as? String,
              let bridge = WidgetSnapshotBridge.sharedContainer(
                appGroupIdentifier: appGroupIdentifier
              ) else {
            return nil
        }
        return try? bridge.load()
    }
}

struct PAOStatusWidgetView: View {
    let entry: PAOStatusWidgetEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Personal AI")
                .font(.headline)
            if let snapshot = entry.snapshot {
                Text(snapshot.connectionState)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    count("RUN", snapshot.counts.running)
                    count("BLK", snapshot.counts.blocked)
                    count("OK", snapshot.counts.verified + snapshot.counts.completed)
                }
                Text(snapshot.productionActive)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Text("NO SNAPSHOT")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
    }

    private func count(_ label: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.title3.monospacedDigit())
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

@main
struct PAOStatusWidget: Widget {
    let kind = "PAOStatusWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PAOStatusWidgetProvider()) { entry in
            PAOStatusWidgetView(entry: entry)
        }
        .configurationDisplayName("Personal AI")
        .description("Read-only Personal AI Orchestrator status.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
#endif
