import Foundation
import Security

enum BeaconKeychain {
    private static var service: String { "\(Bundle.main.bundleIdentifier ?? "com.example.AgentBeacon").device" }
    private static let account = "paired-device"

    static func save(_ credentials: PairCredentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let identity: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(identity as CFDictionary)
        var query = identity
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        query[kSecValueData as String] = data
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainFailure(status: status) }
    }

    static func load() -> PairCredentials? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        guard let value = try? JSONDecoder().decode(PairCredentials.self, from: data),
              let address = IPv4Address(value.host), address.isPrivateLAN,
              address.description == value.host, (1...65_535).contains(value.port),
              value.deviceID.range(of: "^[0-9a-f-]{36}$", options: .regularExpression) != nil,
              value.deviceToken.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              !value.instanceID.isEmpty, value.instanceID.count <= 128 else { return nil }
        return value
    }

    static func delete() {
        SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ] as CFDictionary)
    }
}

struct KeychainFailure: Error {
    let status: OSStatus
}
