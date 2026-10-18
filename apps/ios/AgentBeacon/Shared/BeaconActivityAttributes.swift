import ActivityKit
import Foundation

struct BeaconActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var state: BeaconState
        var validUntil: Date
        var validUntilUptime: TimeInterval?
        var bootIdentifier: String?
        var updatedAt: Date
    }

    var serverLabel: String
}

