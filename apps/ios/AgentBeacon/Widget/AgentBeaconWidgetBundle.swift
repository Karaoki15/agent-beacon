import SwiftUI
import WidgetKit

@main
struct AgentBeaconWidgetBundle: WidgetBundle {
    var body: some Widget {
        BeaconWidget()
        BeaconLiveActivity()
    }
}

