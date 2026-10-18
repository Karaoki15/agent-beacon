import SwiftUI
import WidgetKit

struct BeaconTimelineEntry: TimelineEntry {
    let date: Date
    let snapshot: BeaconSnapshot
}

struct BeaconTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> BeaconTimelineEntry {
        BeaconTimelineEntry(date: .now, snapshot: .unknown())
    }

    func getSnapshot(in context: Context, completion: @escaping (BeaconTimelineEntry) -> Void) {
        completion(BeaconTimelineEntry(date: .now, snapshot: BeaconPersistence.load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<BeaconTimelineEntry>) -> Void) {
        let now = Date()
        let snapshot = BeaconPersistence.load()
        let current = BeaconTimelineEntry(date: now, snapshot: snapshot)
        if snapshot.effectiveState(at: now) == .unknown {
            completion(Timeline(entries: [current], policy: .after(now.addingTimeInterval(15 * 60))))
            return
        }
        let remaining = max(0, snapshot.validUntilUptime - ProcessInfo.processInfo.systemUptime)
        let expiryDate = now.addingTimeInterval(remaining)
        let expires = BeaconTimelineEntry(date: expiryDate, snapshot: snapshot)
        completion(Timeline(entries: [current, expires], policy: .after(expiryDate.addingTimeInterval(60))))
    }
}

struct BeaconWidget: Widget {
    let kind = BeaconPersistence.widgetKind

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: BeaconTimelineProvider()) { entry in
            BeaconWidgetView(snapshot: entry.snapshot, date: entry.date)
        }
        .configurationDisplayName("Agent Beacon")
        .description("See the latest Agent Beacon status at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

struct BeaconWidgetView: View {
    let snapshot: BeaconSnapshot
    let date: Date
    private var status: BeaconState { snapshot.effectiveState(at: max(Date.now, date)) }

    var body: some View {
        if family == .accessoryInline {
            Label(status.label, systemImage: symbol)
        } else if family == .accessoryCircular {
            Image(systemName: "circle.fill")
                .font(.system(size: 28))
                .foregroundStyle(color)
            .accessibilityLabel("Agent Beacon: \(status.label)")
        } else if family == .accessoryRectangular {
            VStack(alignment: .leading, spacing: 3) {
                Label("Agent Beacon", systemImage: symbol).font(.headline)
                Text(status.label).font(.caption)
                Text(updatedLabel).font(.caption2).foregroundStyle(.secondary)
            }
        } else {
            standardLayout
        }
    }

    @Environment(\.widgetFamily) private var family

    @ViewBuilder private var standardLayout: some View {
        if family == .systemSmall {
            VStack(spacing: 8) {
                Spacer(minLength: 0)
                Circle().fill(color.gradient).frame(width: 66, height: 66)
                Text(status.label).font(.headline).lineLimit(1).minimumScaleFactor(0.8)
                Text(updatedLabel).font(.caption2).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerBackground(.background, for: .widget)
        } else {
            HStack(spacing: 15) {
                Circle().fill(color.gradient).frame(width: 64, height: 64)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Agent Beacon").font(.headline)
                    Text(status.label).font(.title3.weight(.semibold)).foregroundStyle(color)
                    Text(updatedLabel).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if let session = snapshot.sessions.first {
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(session.projectName ?? session.source.capitalized)
                            .font(.subheadline.weight(.medium)).lineLimit(1)
                        Text("\(snapshot.sessions.count) sessions")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .containerBackground(.background, for: .widget)
        }
    }

    private var updatedLabel: String {
        guard !snapshot.serverTime.isEmpty else { return "Статусу ще немає" }
        return status == .unknown ? "Статус застарів" : "Оновлено \(snapshot.receivedAt.formatted(date: .omitted, time: .shortened))"
    }

    private var symbol: String {
        switch status {
        case .working: return "arrow.trianglehead.2.clockwise.rotate.90"
        case .awaitingUser: return "hand.raised.fill"
        case .ready: return "circle.fill"
        case .unknown: return "questionmark"
        }
    }

    private var color: Color {
        switch status {
        case .working: return Color(red: 0.91, green: 0.20, blue: 0.22)
        case .awaitingUser: return Color(red: 0.96, green: 0.67, blue: 0.10)
        case .ready: return Color(red: 0.19, green: 0.75, blue: 0.39)
        case .unknown: return Color(red: 0.32, green: 0.34, blue: 0.38)
        }
    }
}

