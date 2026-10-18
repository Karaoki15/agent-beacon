import XCTest
@testable import AgentBeacon

final class BeaconCoreTests: XCTestCase {
    func testPairingLinkAcceptsOnlyPrivateNumericIPv4AndExactFields() throws {
        let valid = "agentbeacon://pair?host=192.168.1.10&port=8765#code=" + String(repeating: "A", count: 43)
        XCTAssertEqual(try PairingTarget.parse(valid), PairingTarget(host: "192.168.1.10", port: 8765,
                                                                    code: String(repeating: "A", count: 43)))
        for invalid in [
            "agentbeacon://pair?host=example.local&port=8765#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=8.8.8.8&port=8765#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=127.0.0.1&port=8765#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=192.168.1.10&port=0&extra=x#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=192.168.1.10&port=+8765#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=192.168.1.10&port=08765#code=" + String(repeating: "A", count: 43),
            "agentbeacon://pair?host=192.168.1.10&port=8765&name=x#code=" + String(repeating: "A", count: 43)
        ] {
            XCTAssertThrowsError(try PairingTarget.parse(invalid))
        }
    }

    func testFreshnessExpiresToUnknownAndDoesNotStayGreen() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let uptime = ProcessInfo.processInfo.systemUptime
        let boot = BeaconClock.bootIdentifier
        let ready = BeaconSnapshot(schemaVersion: 1, instanceID: "server-1", revision: 3,
                                   available: true, state: .ready, validForSeconds: 4,
                                   serverTime: "2026-10-16T12:00:00.000Z", sessions: [],
                                   receivedAt: now, validUntil: now.addingTimeInterval(4),
                                   validUntilUptime: uptime + 4, bootIdentifier: boot)
        XCTAssertEqual(ready.effectiveState(at: now.addingTimeInterval(3.99), uptime: uptime + 3.99,
                                            currentBootIdentifier: boot), .ready)
        XCTAssertEqual(ready.effectiveState(at: now.addingTimeInterval(4), uptime: uptime + 4,
                                            currentBootIdentifier: boot), .unknown)
        XCTAssertEqual(ready.effectiveState(uptime: uptime + 1, currentBootIdentifier: "new-boot"), .unknown)
        XCTAssertEqual(ready.effectiveState(at: now.addingTimeInterval(-3600), uptime: uptime + 4.01,
                                            currentBootIdentifier: boot), .unknown,
                       "Rolling the wall clock backwards must not extend the monotonic lease.")
    }

    func testRevisionOrderingRejectsOlderSnapshotForSameServerInstance() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let uptime = ProcessInfo.processInfo.systemUptime
        let boot = BeaconClock.bootIdentifier
        let current = BeaconSnapshot(schemaVersion: 1, instanceID: "server-1", revision: 8,
                                    available: true, state: .working, validForSeconds: 5,
                                    serverTime: "2026-10-16T12:00:00.000Z", sessions: [],
                                    receivedAt: now, validUntil: now.addingTimeInterval(5),
                                    validUntilUptime: uptime + 5, bootIdentifier: boot)
        let older = BeaconSnapshot(schemaVersion: 1, instanceID: "server-1", revision: 7,
                                  available: true, state: .ready, validForSeconds: 5,
                                  serverTime: "2026-10-16T12:00:00.000Z", sessions: [],
                                  receivedAt: now, validUntil: now.addingTimeInterval(5),
                                  validUntilUptime: uptime + 5, bootIdentifier: boot)
        let sameRevisionRefresh = current.withTiming(receivedAt: now, validUntil: now.addingTimeInterval(6),
                                                    validUntilUptime: uptime + 6, bootIdentifier: boot)
        XCTAssertFalse(older.supersedes(current))
        XCTAssertTrue(sameRevisionRefresh.supersedes(current))
        let restarted = BeaconSnapshot(schemaVersion: 1, instanceID: "server-2", revision: 0,
                                       available: true, state: .working, validForSeconds: 5,
                                       serverTime: "2026-10-16T12:00:00.000Z", sessions: [],
                                       receivedAt: now, validUntil: now.addingTimeInterval(5),
                                       validUntilUptime: uptime + 5, bootIdentifier: boot)
        XCTAssertTrue(restarted.supersedes(current))
    }

    func testServerFixtureParsesFractionalTimeAndKeepsUnavailableSessionUnknown() throws {
        let fixture = #"""
        {
          "schema_version": 1,
          "instance_id": "server-1",
          "revision": 12,
          "ttl_seconds": 30.0,
          "valid_for_seconds": 15.0,
          "available": true,
          "state": "ready",
          "task_state": "ready",
          "reason": null,
          "partial": false,
          "sources": [
            {"source":"codex","healthy":true,"inventory_complete":true,"coverage":"observed_sessions"},
            {"source":"claude","healthy":false,"inventory_complete":false,"coverage":"observed_sessions"}
          ],
          "server_time": "2026-10-16T12:00:00.123Z",
          "sessions": [
            {
              "agent_id": "codex",
              "session_id": "active-1",
              "source": "codex",
              "sequence": 20,
              "turn_id": "turn-1",
              "parent_session_id": null,
              "state": "ready",
              "available": true,
              "updated_at": "2026-10-16T12:00:00.123Z",
              "active": true,
              "display_state": "ready",
              "project_name": "Agent Beacon",
              "question_reminder_active": false,
              "question_reminder_remaining_seconds": 0.0
            },
            {
              "agent_id": "claude",
              "session_id": "closed-1",
              "source": "claude",
              "sequence": 21,
              "turn_id": null,
              "parent_session_id": null,
              "state": null,
              "available": false,
              "updated_at": "2026-10-16T12:00:00.123Z",
              "active": false,
              "display_state": null,
              "project_name": null,
              "question_reminder_active": false,
              "question_reminder_remaining_seconds": 0.0
            },
            {
              "agent_id": "operator-a",
              "session_id": "shared-1",
              "source": "manual",
              "sequence": 22,
              "turn_id": null,
              "parent_session_id": null,
              "state": "working",
              "available": true,
              "updated_at": "2026-10-16T12:00:00.123Z",
              "active": true,
              "display_state": "working",
              "project_name": null,
              "question_reminder_active": false,
              "question_reminder_remaining_seconds": 0.0
            },
            {
              "agent_id": "operator-b",
              "session_id": "shared-1",
              "source": "manual",
              "sequence": 23,
              "turn_id": null,
              "parent_session_id": null,
              "state": "ready",
              "available": true,
              "updated_at": "2026-10-16T12:00:00.123Z",
              "active": true,
              "display_state": "ready",
              "project_name": null,
              "question_reminder_active": false,
              "question_reminder_remaining_seconds": 0.0
            }
          ]
        }
        """#
        let snapshot = try JSONDecoder().decode(BeaconSnapshot.self, from: Data(fixture.utf8))
        XCTAssertEqual(snapshot.state, .ready)
        XCTAssertEqual(snapshot.sessions.first?.state, .ready)
        XCTAssertEqual(snapshot.sessions[1].state, .unknown)
        XCTAssertEqual(snapshot.sessions.first?.projectName, "Agent Beacon")
        XCTAssertNotEqual(snapshot.sessions[2].id, snapshot.sessions[3].id,
                          "Manual agents may use the same session ID independently.")
        XCTAssertEqual(snapshot.validUntil, snapshot.receivedAt)
        XCTAssertNil(BeaconSnapshot.parseServerTime("2026-10-16T12:00:00Z"))
    }

    func testPrivateAddressClassifierRejectsNonRFC1918Ranges() {
        XCTAssertTrue(IPv4Address("10.3.5.9")!.isPrivateLAN)
        XCTAssertTrue(IPv4Address("172.16.0.1")!.isPrivateLAN)
        XCTAssertTrue(IPv4Address("172.31.255.254")!.isPrivateLAN)
        XCTAssertTrue(IPv4Address("192.168.1.1")!.isPrivateLAN)
        XCTAssertFalse(IPv4Address("172.32.0.1")!.isPrivateLAN)
        XCTAssertFalse(IPv4Address("169.254.1.1")!.isPrivateLAN)
        XCTAssertFalse(IPv4Address("127.0.0.1")!.isPrivateLAN)
    }

    func testServerTimestampsRequireFractionalUTCISO8601() {
        XCTAssertNotNil(BeaconSnapshot.parseServerTime("2026-10-16T12:00:00.000Z"))
        XCTAssertNil(BeaconSnapshot.parseServerTime("2026-10-16T12:00:00Z"))
        XCTAssertNil(BeaconSnapshot.parseServerTime("2026-10-16 12:00:00.000Z"))
    }
}

