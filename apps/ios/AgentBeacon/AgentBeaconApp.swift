import SwiftUI

@main
struct AgentBeaconApp: App {
    @StateObject private var store = BeaconStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(store)
        }
    }
}

