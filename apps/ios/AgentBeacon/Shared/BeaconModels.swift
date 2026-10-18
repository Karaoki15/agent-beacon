import Foundation
import Darwin

enum BeaconClock {
    static var bootIdentifier: String? {
        var bootTime = timeval()
        var size = MemoryLayout<timeval>.size
        guard sysctlbyname("kern.boottime", &bootTime, &size, nil, 0) == 0,
              size == MemoryLayout<timeval>.size else { return nil }
        return "\(bootTime.tv_sec):\(bootTime.tv_usec)"
    }
}

enum BeaconState: String, Codable, CaseIterable {
    case working
    case awaitingUser = "awaiting_user"
    case ready
    case unknown

    var label: String {
        switch self {
        case .working: return "Працює"
        case .awaitingUser: return "Очікує на вас"
        case .ready: return "Готовий до завдання"
        case .unknown: return "Невідомо"
        }
    }
}

struct BeaconSession: Codable, Identifiable, Equatable {
    var id: String { "\(source):\(agentID):\(sessionID)" }
    let source: String
    let agentID: String
    let sessionID: String
    let sequence: Int64
    let updatedAt: String
    let projectName: String?
    let state: BeaconState
    let active: Bool

    enum CodingKeys: String, CodingKey {
        case source
        case sessionID = "session_id"
        case projectName = "project_name"
        case state
        case displayState = "display_state"
        case active
        case available
        case agentID = "agent_id"
        case sequence
        case reminderActive = "question_reminder_active"
        case reminderRemaining = "question_reminder_remaining_seconds"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        source = try box.decode(String.self, forKey: .source)
        agentID = try box.decode(String.self, forKey: .agentID)
        sessionID = try box.decode(String.self, forKey: .sessionID)
        sequence = try box.decode(Int64.self, forKey: .sequence)
        let updatedAt = try box.decode(String.self, forKey: .updatedAt)
        self.updatedAt = updatedAt
        guard box.contains(.state), box.contains(.displayState) else { throw BeaconDecodeError.invalidSession }
        guard agentID.range(of: "^[A-Za-z0-9._-]{1,64}$", options: .regularExpression) != nil,
              sessionID.range(of: "^[A-Za-z0-9._-]{1,64}$", options: .regularExpression) != nil,
              sequence >= 0, sequence <= 9_007_199_254_740_991,
              BeaconSnapshot.parseServerTime(updatedAt) != nil,
              ["manual", "codex", "claude"].contains(source) else {
            throw BeaconDecodeError.invalidSession
        }
        let available = try box.decode(Bool.self, forKey: .available)
        let actualState = try box.decodeIfPresent(String.self, forKey: .state)
        let displayState = box.contains(.displayState)
            ? try box.decodeIfPresent(String.self, forKey: .displayState)
            : nil
        if let actualState {
            guard let parsed = BeaconState(rawValue: actualState), parsed != .unknown else {
                throw BeaconDecodeError.invalidSession
            }
        }
        if available, box.contains(.displayState) {
            guard let actualState, let actual = BeaconState(rawValue: actualState), actual != .unknown,
                  let displayState, let display = BeaconState(rawValue: displayState), display != .unknown else {
                throw BeaconDecodeError.invalidSession
            }
            let reminderActive = try box.decodeIfPresent(Bool.self, forKey: .reminderActive) ?? false
            let reminderRemaining = try box.decodeIfPresent(Double.self, forKey: .reminderRemaining) ?? 0
            let projectionMatches = actual == .working
                ? (display == .working || (display == .awaitingUser && reminderActive))
                : display == actual
            guard projectionMatches,
                  !reminderActive || (actual == .working && display == .awaitingUser && reminderRemaining.isFinite && reminderRemaining > 0) else {
                throw BeaconDecodeError.invalidSession
            }
        } else if !available, displayState != nil {
            throw BeaconDecodeError.invalidSession
        }
        let stateString = available ? (displayState ?? actualState) : nil
        if let stateString {
            guard let parsed = BeaconState(rawValue: stateString), parsed != .unknown else {
                throw BeaconDecodeError.invalidSession
            }
            state = available ? parsed : .unknown
        } else if available {
            throw BeaconDecodeError.invalidSession
        } else {
            state = .unknown
        }
        if source == "manual" {
            active = available
        } else {
            active = try box.decode(Bool.self, forKey: .active)
            if !active && (available || actualState != nil || displayState != nil) { throw BeaconDecodeError.invalidSession }
        }
        if let name = try box.decodeIfPresent(String.self, forKey: .projectName),
           name.count <= 160,
           name.rangeOfCharacter(from: CharacterSet.controlCharacters.union(CharacterSet(charactersIn: "/\\:"))) == nil {
            projectName = name
        } else {
            projectName = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var box = encoder.container(keyedBy: CodingKeys.self)
        try box.encode(source, forKey: .source)
        try box.encode(agentID, forKey: .agentID)
        try box.encode(sessionID, forKey: .sessionID)
        try box.encode(sequence, forKey: .sequence)
        try box.encode(updatedAt, forKey: .updatedAt)
        try box.encodeIfPresent(projectName, forKey: .projectName)
        if state == .unknown { try box.encodeNil(forKey: .state) }
        else { try box.encode(state.rawValue, forKey: .state) }
        if state == .unknown { try box.encodeNil(forKey: .displayState) }
        else { try box.encode(state.rawValue, forKey: .displayState) }
        try box.encode(active, forKey: .active)
        try box.encode(state != .unknown, forKey: .available)
        try box.encode(false, forKey: .reminderActive)
        try box.encode(0.0, forKey: .reminderRemaining)
    }
}

struct BeaconSnapshot: Codable, Equatable {
    let schemaVersion: Int
    let instanceID: String
    let revision: Int64
    let available: Bool
    let state: BeaconState
    let taskState: BeaconState?
    let reason: String?
    let validForSeconds: Double
    let serverTime: String
    let sessions: [BeaconSession]
    let receivedAt: Date
    let validUntil: Date
    let validUntilUptime: TimeInterval
    let bootIdentifier: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case instanceID = "instance_id"
        case revision
        case available
        case state
        case taskState = "task_state"
        case validForSeconds = "valid_for_seconds"
        case serverTime = "server_time"
        case sessions
        case partial
        case ttlSeconds = "ttl_seconds"
        case reason
    }

    init(from decoder: Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try box.decode(Int.self, forKey: .schemaVersion)
        instanceID = try box.decode(String.self, forKey: .instanceID)
        revision = try box.decode(Int64.self, forKey: .revision)
        available = try box.decode(Bool.self, forKey: .available)
        let rawState = try box.decodeIfPresent(String.self, forKey: .state)
        if let rawState {
            guard let parsed = BeaconState(rawValue: rawState), parsed != .unknown else {
                throw BeaconDecodeError.invalidSnapshot
            }
            state = parsed
        } else {
            state = .unknown
        }
        validForSeconds = try box.decode(Double.self, forKey: .validForSeconds)
        serverTime = try box.decode(String.self, forKey: .serverTime)
        sessions = try box.decode([BeaconSession].self, forKey: .sessions)
        receivedAt = Date()
        validUntil = receivedAt
        validUntilUptime = ProcessInfo.processInfo.systemUptime
        bootIdentifier = BeaconClock.bootIdentifier
        _ = try box.decode(Bool.self, forKey: .partial)
        let ttl = try box.decode(Double.self, forKey: .ttlSeconds)
        let reason = try box.decodeIfPresent(String.self, forKey: .reason)
        let reasonValid = box.contains(.reason) && (available ? reason == nil : ["stale", "unavailable"].contains(reason ?? ""))
        let hasTaskState = box.contains(.taskState)
        let taskStateRaw = try box.decodeIfPresent(String.self, forKey: .taskState)
        let decodedTaskState = taskStateRaw.flatMap { BeaconState(rawValue: $0) }
        taskState = decodedTaskState
        self.reason = reason
        let taskStateValid = hasTaskState && (available
            ? taskStateRaw != nil && decodedTaskState != nil && decodedTaskState != .unknown
            : taskStateRaw == nil)
        guard box.contains(.state), schemaVersion == 1, instanceID.count <= 128, !instanceID.isEmpty,
              revision >= 0, validForSeconds.isFinite, ttl.isFinite,
              ttl > 0, ttl <= 86_400, validForSeconds >= 0,
              validForSeconds <= ttl + 0.001, sessions.count <= 128,
              (available && state != .unknown) || (!available && state == .unknown),
              taskStateValid, reasonValid,
              !(available == false && validForSeconds != 0),
              Self.parseServerTime(serverTime) != nil else {
            throw BeaconDecodeError.invalidSnapshot
        }
    }

    init(schemaVersion: Int, instanceID: String, revision: Int64, available: Bool, state: BeaconState,
         validForSeconds: Double, serverTime: String, sessions: [BeaconSession], receivedAt: Date, validUntil: Date,
         taskState: BeaconState? = nil, reason: String? = nil,
         validUntilUptime: TimeInterval? = nil, bootIdentifier: String? = BeaconClock.bootIdentifier) {
        self.schemaVersion = schemaVersion
        self.instanceID = instanceID
        self.revision = revision
        self.available = available
        self.state = state
        self.taskState = taskState ?? (available ? state : nil)
        self.reason = reason
        self.validForSeconds = validForSeconds
        self.serverTime = serverTime
        self.sessions = sessions
        self.receivedAt = receivedAt
        self.validUntil = validUntil
        self.validUntilUptime = validUntilUptime ?? (ProcessInfo.processInfo.systemUptime + max(0, validUntil.timeIntervalSince(receivedAt)))
        self.bootIdentifier = bootIdentifier
    }

    func encode(to encoder: Encoder) throws {
        var box = encoder.container(keyedBy: CodingKeys.self)
        try box.encode(schemaVersion, forKey: .schemaVersion)
        try box.encode(instanceID, forKey: .instanceID)
        try box.encode(revision, forKey: .revision)
        try box.encode(available, forKey: .available)
        if available { try box.encode(state.rawValue, forKey: .state) }
        else { try box.encodeNil(forKey: .state) }
        try box.encode(taskState?.rawValue, forKey: .taskState)
        try box.encode(reason, forKey: .reason)
        try box.encode(validForSeconds, forKey: .validForSeconds)
        try box.encode(serverTime, forKey: .serverTime)
        try box.encode(sessions, forKey: .sessions)
        try box.encode(false, forKey: .partial)
        try box.encode(max(validForSeconds, 0.001), forKey: .ttlSeconds)
    }

    func effectiveState(at now: Date = Date(), uptime: TimeInterval = ProcessInfo.processInfo.systemUptime,
                        currentBootIdentifier: String? = BeaconClock.bootIdentifier) -> BeaconState {
        guard currentBootIdentifier != nil, currentBootIdentifier == bootIdentifier,
              now < validUntil, uptime < validUntilUptime else { return .unknown }
        return available ? state : .unknown
    }

    func supersedes(_ previous: BeaconSnapshot) -> Bool {
        instanceID != previous.instanceID || revision >= previous.revision
    }

    static func parseServerTime(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard value.range(of: "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{3}Z$",
                          options: .regularExpression) != nil else { return nil }
        return fractional.date(from: value)
    }

    func withTiming(receivedAt: Date, validUntil: Date, validUntilUptime: TimeInterval,
                    bootIdentifier: String?) -> BeaconSnapshot {
        BeaconSnapshot(schemaVersion: schemaVersion, instanceID: instanceID, revision: revision,
                       available: available, state: state, validForSeconds: validForSeconds,
                       serverTime: serverTime, sessions: sessions, receivedAt: receivedAt, validUntil: validUntil,
                       taskState: taskState, reason: reason,
                       validUntilUptime: validUntilUptime, bootIdentifier: bootIdentifier)
    }

    static func unknown(now: Date = Date()) -> BeaconSnapshot {
        BeaconSnapshot(schemaVersion: 1, instanceID: "", revision: 0, available: false,
                       state: .unknown, validForSeconds: 0, serverTime: "", sessions: [],
                       receivedAt: now, validUntil: now, taskState: nil, reason: "unavailable",
                       validUntilUptime: ProcessInfo.processInfo.systemUptime,
                       bootIdentifier: BeaconClock.bootIdentifier)
    }
}

enum BeaconDecodeError: Error {
    case invalidSnapshot
    case invalidSession
}

struct PairingTarget: Equatable {
    let host: String
    let port: Int
    let code: String

    static func parse(_ value: String) throws -> PairingTarget {
        guard let components = URLComponents(string: value), components.scheme == "agentbeacon",
              components.host == "pair", components.user == nil, components.password == nil,
              components.port == nil, components.path.isEmpty, components.queryItems?.count == 2,
              let hostItem = components.queryItems?.first(where: { $0.name == "host" }),
              let portItem = components.queryItems?.first(where: { $0.name == "port" }),
              let host = hostItem.value, let portString = portItem.value,
              portString.range(of: "^[0-9]{1,5}$", options: .regularExpression) != nil,
              let port = Int(portString), (1...65_535).contains(port),
              String(port) == portString,
              let fragment = components.percentEncodedFragment,
              fragment.hasPrefix("code="), !fragment.dropFirst(5).contains("&") else {
            throw BeaconPairingError.invalidCode
        }
        let code = String(fragment.dropFirst(5))
        guard code.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              let address = IPv4Address(host), address.isPrivateLAN else {
            throw BeaconPairingError.invalidCode
        }
        return PairingTarget(host: address.description, port: port, code: code)
    }
}

struct IPv4Address: CustomStringConvertible {
    let octets: [UInt8]
    init?(_ value: String) {
        let pieces = value.split(separator: ".", omittingEmptySubsequences: false)
        guard pieces.count == 4 else { return nil }
        var result: [UInt8] = []
        for piece in pieces {
            guard !piece.isEmpty, piece.count <= 3,
                  piece.utf8.allSatisfy({ (48...57).contains($0) }),
                  let number = UInt8(String(piece)), String(number) == String(piece) else { return nil }
            result.append(number)
        }
        octets = result
    }
    var description: String { octets.map(String.init).joined(separator: ".") }
    var isPrivateLAN: Bool {
        octets[0] == 10 || (octets[0] == 172 && (16...31).contains(octets[1])) ||
        (octets[0] == 192 && octets[1] == 168)
    }
}

enum BeaconPairingError: Error {
    case invalidCode
    case invalidResponse
    case network(String)
}

struct PairCredentials: Codable {
    let host: String
    let port: Int
    let deviceID: String
    let deviceToken: String
    let instanceID: String
}

struct PairResponse: Decodable {
    let deviceID: String
    let deviceToken: String
    let instanceID: String
    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case deviceToken = "device_token"
        case instanceID = "instance_id"
    }
}

