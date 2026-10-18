import Foundation

final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

final class BeaconAPI {
    private static let maximumResponseBytes = 128 * 1024
    private let session: URLSession
    private let delegate = NoRedirectDelegate()
    private let decoder = JSONDecoder()

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 12
        configuration.waitsForConnectivity = false
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    func pair(target: PairingTarget, deviceName: String) async throws -> PairCredentials {
        let url = URL(string: "http://\(target.host):\(target.port)/api/pair")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 8
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "code": target.code,
            "device_name": String(deviceName.prefix(48))
        ], options: [.sortedKeys])
        let (data, response) = try await boundedData(for: request)
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 200 else { throw BeaconPairingError.invalidResponse }
        let value = try decoder.decode(PairResponse.self, from: data)
        guard value.deviceID.range(of: "^[0-9a-f-]{36}$", options: .regularExpression) != nil,
              value.deviceToken.count == 43,
              value.deviceToken.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              value.instanceID.count <= 128, !value.instanceID.isEmpty else {
            throw BeaconPairingError.invalidResponse
        }
        return PairCredentials(host: target.host, port: target.port, deviceID: value.deviceID,
                               deviceToken: value.deviceToken, instanceID: value.instanceID)
    }

    func status(credentials: PairCredentials) async throws -> BeaconSnapshot {
        let started = ProcessInfo.processInfo.systemUptime
        let url = URL(string: "http://\(credentials.host):\(credentials.port)/api/status")!
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 8
        request.setValue("Bearer \(credentials.deviceToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        let (data, response) = try await boundedData(for: request)
        let receivedAt = Date()
        let receivedUptime = ProcessInfo.processInfo.systemUptime
        let elapsed = receivedUptime - started
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 200 else { throw BeaconPairingError.invalidResponse }
        let parsed = try decoder.decode(BeaconSnapshot.self, from: data)
        let remaining = max(0, parsed.validForSeconds - elapsed)
        return parsed.withTiming(receivedAt: receivedAt, validUntil: receivedAt.addingTimeInterval(remaining),
                                 validUntilUptime: started + parsed.validForSeconds,
                                 bootIdentifier: BeaconClock.bootIdentifier)
    }

    private func boundedData(for request: URLRequest) async throws -> (Data, URLResponse) {
        let (bytes, response) = try await session.bytes(for: request)
        guard response.expectedContentLength < 0 || response.expectedContentLength <= Int64(Self.maximumResponseBytes) else {
            throw BeaconPairingError.invalidResponse
        }
        var data = Data()
        data.reserveCapacity(min(max(Int(response.expectedContentLength), 0), 16 * 1024))
        for try await byte in bytes {
            guard data.count < Self.maximumResponseBytes else { throw BeaconPairingError.invalidResponse }
            data.append(byte)
        }
        return (data, response)
    }
}

