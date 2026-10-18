import ActivityKit
import SwiftUI
import WidgetKit

struct BeaconLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: BeaconActivityAttributes.self) { context in
            let status = liveState(context)
            HStack(spacing: 12) {
                Circle().fill(statusColor(status)).frame(width: 15, height: 15)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Agent Beacon").font(.headline)
                    Text(status.label).font(.subheadline).foregroundStyle(statusColor(status))
                }
                Spacer()
                Text(context.state.updatedAt, style: .time).font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.vertical, 7)
            .activityBackgroundTint(Color.black)
            .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let status = liveState(context)
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Circle().fill(statusColor(status)).frame(width: 14, height: 14)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.updatedAt, style: .time).font(.caption2)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text("Agent Beacon").font(.headline)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    Text(status.label).font(.title3.weight(.semibold)).foregroundStyle(statusColor(status))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                Circle().fill(statusColor(status)).frame(width: 9, height: 9)
            } compactTrailing: {
                Text(compactLabel(status)).font(.system(size: 11, weight: .bold)).lineLimit(1)
            } minimal: {
                Circle().fill(statusColor(status))
            }
            .keylineTint(statusColor(status))
        }
    }

    private func liveState(_ context: ActivityViewContext<BeaconActivityAttributes>) -> BeaconState {
        guard !context.isStale,
              let expectedBoot = context.state.bootIdentifier,
              expectedBoot == BeaconClock.bootIdentifier,
              let deadline = context.state.validUntilUptime,
              ProcessInfo.processInfo.systemUptime < deadline else { return .unknown }
        return context.state.state
    }

    private func compactLabel(_ state: BeaconState) -> String {
        switch state {
        case .working: return "ПРАЦ"
        case .awaitingUser: return "ЧЕКА"
        case .ready: return "ГОТОВ"
        case .unknown: return "?"
        }
    }

    private func statusColor(_ state: BeaconState) -> Color {
        switch state {
        case .working: return Color(red: 0.91, green: 0.20, blue: 0.22)
        case .awaitingUser: return Color(red: 0.96, green: 0.67, blue: 0.10)
        case .ready: return Color(red: 0.19, green: 0.75, blue: 0.39)
        case .unknown: return Color(red: 0.32, green: 0.34, blue: 0.38)
        }
    }
}

