import Foundation
import os

enum BeaconPersistence {
    static let defaultAppGroup = "group.com.example.AgentBeacon"
    static let widgetKind = "AgentBeaconStatusWidget"
    private static let snapshotName = "latest-status.json"
    private static let logger = Logger(subsystem: Bundle.main.bundleIdentifier ?? "com.example.AgentBeacon",
                                       category: "StatusStorage")

    private struct SavedStatus: Codable {
        let snapshot: BeaconSnapshot
        let receivedAt: Date
        let validUntil: Date
        let validUntilUptime: TimeInterval
        let bootIdentifier: String
    }

    static var sharedDirectory: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)
    }

    static var appGroup: String {
        Bundle.main.object(forInfoDictionaryKey: "BeaconAppGroupIdentifier") as? String ?? defaultAppGroup
    }

    @discardableResult
    static func save(_ snapshot: BeaconSnapshot) -> Bool {
        guard let folder = sharedDirectory else {
            logger.error("Shared App Group container is unavailable")
            return false
        }
        let file = folder.appendingPathComponent(snapshotName)
        if snapshot.serverTime.isEmpty {
            if FileManager.default.fileExists(atPath: file.path) {
                do { try FileManager.default.removeItem(at: file) }
                catch {
                    logger.error("Could not clear the shared status snapshot")
                    return false
                }
            }
            return true
        }
        do {
            guard let bootIdentifier = snapshot.bootIdentifier,
                  bootIdentifier == BeaconClock.bootIdentifier else { return false }
            let data = try JSONEncoder().encode(SavedStatus(snapshot: snapshot, receivedAt: snapshot.receivedAt,
                                                            validUntil: snapshot.validUntil,
                                                            validUntilUptime: snapshot.validUntilUptime,
                                                            bootIdentifier: bootIdentifier))
            guard data.count <= 128 * 1024 else {
                logger.error("Status snapshot exceeded the local storage limit")
                return false
            }
            try data.write(to: file, options: .atomic)
            return true
        } catch {
            logger.error("Could not save the shared status snapshot")
            return false
        }
    }

    static func load() -> BeaconSnapshot {
        guard let folder = sharedDirectory else {
            logger.error("Shared App Group container is unavailable")
            return .unknown()
        }
        let file = folder.appendingPathComponent(snapshotName)
        guard FileManager.default.fileExists(atPath: file.path) else { return .unknown() }
        do {
            let data = try Data(contentsOf: file)
            guard data.count <= 128 * 1024 else { throw BeaconDecodeError.invalidSnapshot }
            let stored = try JSONDecoder().decode(SavedStatus.self, from: data)
            guard stored.bootIdentifier == BeaconClock.bootIdentifier else { return .unknown() }
            return stored.snapshot.withTiming(receivedAt: stored.receivedAt, validUntil: stored.validUntil,
                                              validUntilUptime: stored.validUntilUptime,
                                              bootIdentifier: stored.bootIdentifier)
        } catch {
            logger.error("Could not load the shared status snapshot")
            return .unknown()
        }
    }

}

