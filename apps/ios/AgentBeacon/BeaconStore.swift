import ActivityKit
import Foundation
import SwiftUI
import WidgetKit

@MainActor
final class BeaconStore: ObservableObject {
    @Published private(set) var snapshot: BeaconSnapshot = BeaconPersistence.load()
    @Published private(set) var paired = BeaconKeychain.load() != nil
    @Published private(set) var sharedStorageAvailable = BeaconPersistence.sharedDirectory != nil
    @Published private(set) var busy = false
    @Published var message: String?
    @Published var showScanner = false

    private var credentials = BeaconKeychain.load()
    private var requestTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var pairingTask: Task<PairCredentials, Error>?
    private var activityUpdateTask: Task<Void, Never>?
    private var pendingActivityContent: ActivityContent<BeaconActivityAttributes.ContentState>?
    private var isFetching = false
    private var generation = 0
    private var foreground = false

    var state: BeaconState { snapshot.effectiveState() }

    func setForeground(_ active: Bool) {
        guard foreground != active else { return }
        foreground = active
        generation &+= 1
        requestTask?.cancel()
        requestTask = nil
        pollTask?.cancel()
        pollTask = nil
        pairingTask?.cancel()
        pairingTask = nil
        if active {
            let cached = BeaconPersistence.load()
            snapshot = cached.effectiveState() == .unknown ? .unknown() : cached
            sharedStorageAvailable = BeaconPersistence.save(snapshot)
            updateActivities(snapshot)
            if credentials != nil { beginPolling() }
        } else {
            sharedStorageAvailable = BeaconPersistence.save(snapshot)
        }
    }

    func handlePairingURL(_ url: URL) async {
        guard let target = try? PairingTarget.parse(url.absoluteString) else {
            message = "Посилання для підключення недійсне. Відскануйте новий QR-код Agent Beacon."
            return
        }
        await pair(with: target)
    }

    func pairManually(_ text: String) async {
        guard let target = try? PairingTarget.parse(text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            message = "Вставте повне посилання Agent Beacon або відскануйте QR-код."
            return
        }
        await pair(with: target)
    }

    private func pair(with target: PairingTarget) async {
        generation &+= 1
        let attempt = generation
        pairingTask?.cancel()
        pairingTask = nil
        requestTask?.cancel()
        pollTask?.cancel()
        credentials = nil
        paired = false
        BeaconKeychain.delete()
        markUnknown(message: nil)
        busy = true
        defer {
            if generation == attempt {
                busy = false
                pairingTask = nil
            }
        }
        do {
            let api = BeaconAPI()
            let pending = Task { try await api.pair(target: target, deviceName: "iPhone") }
            pairingTask = pending
            let saved = try await pending.value
            guard generation == attempt, foreground else { return }
            try BeaconKeychain.save(saved)
            pairingTask = nil
            credentials = saved
            paired = true
            message = "Пристрій підключено до Agent Beacon."
            beginPolling()
        } catch is CancellationError {
        } catch {
            guard generation == attempt else { return }
            message = "Не вдалося підключитися. Перевірте спільну приватну Wi‑Fi мережу та створіть новий QR-код."
        }
    }

    func disconnect() async {
        let activitiesToDismiss = Activity<BeaconActivityAttributes>.activities
        generation &+= 1
        requestTask?.cancel()
        requestTask = nil
        pollTask?.cancel()
        pollTask = nil
        pairingTask?.cancel()
        pairingTask = nil
        credentials = nil
        paired = false
        BeaconKeychain.delete()
        markUnknown(message: "Зв’язок з Agent Beacon розірвано.")
        await activityUpdateTask?.value
        for activity in activitiesToDismiss {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }

    func refresh() {
        guard foreground, credentials != nil, !isFetching else { return }
        requestTask = Task { await fetchOnce() }
    }

    func startLiveActivity() async {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            message = "Увімкніть Live Activities для Agent Beacon у Параметрах."
            return
        }
        guard Activity<BeaconActivityAttributes>.activities.isEmpty else {
            message = "Live Activity уже запущено."
            return
        }
        let validState = state
        let startGeneration = generation
        do {
            let attributes = BeaconActivityAttributes(serverLabel: "Agent Beacon")
            let content = ActivityContent(state: .init(state: validState, validUntil: snapshot.validUntil,
                                                        validUntilUptime: snapshot.validUntilUptime,
                                                        bootIdentifier: snapshot.bootIdentifier,
                                                        updatedAt: snapshot.receivedAt),
                                          staleDate: snapshot.validUntil)
            let activity = try Activity.request(attributes: attributes, content: content, pushType: nil)
            guard generation == startGeneration, paired else {
                await activity.end(nil, dismissalPolicy: .immediate)
                return
            }
            message = "Live Activity запущено. Після завершення терміну статус стане невідомим."
        } catch {
            message = "Не вдалося запустити Live Activity. Перевірте дозвіл у Параметрах."
        }
    }

    private func beginPolling() {
        guard foreground, credentials != nil, pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.fetchOnce()
                try? await Task.sleep(for: .seconds(4))
            }
        }
    }

    private func fetchOnce() async {
        guard foreground, let credentials, !isFetching else { return }
        isFetching = true
        let attempt = generation
        let api = BeaconAPI()
        busy = true
        defer {
            isFetching = false
            if generation == attempt {
                busy = false
                requestTask = nil
            }
        }
        do {
            let fresh = try await api.status(credentials: credentials)
            guard !Task.isCancelled, generation == attempt, foreground,
                  self.credentials?.deviceToken == credentials.deviceToken else { return }
            guard fresh.supersedes(snapshot) else { return }
            snapshot = fresh
            if credentials.instanceID != fresh.instanceID {
                let updatedCredentials = PairCredentials(host: credentials.host, port: credentials.port,
                                                         deviceID: credentials.deviceID, deviceToken: credentials.deviceToken,
                                                         instanceID: fresh.instanceID)
                try BeaconKeychain.save(updatedCredentials)
                self.credentials = updatedCredentials
            }
            sharedStorageAvailable = BeaconPersistence.save(fresh)
            updateActivities(fresh)
            message = sharedStorageAvailable ? nil : "Статус оновлюється, але віджети не мають доступу. Перевірте App Groups у підписі обох цілей."
        } catch is CancellationError {
        } catch {
            guard generation == attempt, foreground else { return }
            markUnknown(message: "Agent Beacon недоступний. Термін останнього статусу завершився; показано невідомий стан.")
        }
    }

    private func markUnknown(message: String?) {
        let unknown = BeaconSnapshot.unknown()
        snapshot = unknown
        sharedStorageAvailable = BeaconPersistence.save(unknown)
        updateActivities(unknown)
        self.message = sharedStorageAvailable ? message : "Віджети не читають статус. Перевірте App Groups у підписі обох цілей."
    }

    private func updateActivities(_ value: BeaconSnapshot) {
        pendingActivityContent = ActivityContent(state: .init(state: value.effectiveState(), validUntil: value.validUntil,
                                                               validUntilUptime: value.validUntilUptime,
                                                               bootIdentifier: value.bootIdentifier,
                                                               updatedAt: value.receivedAt),
                                                 staleDate: value.validUntil)
        guard activityUpdateTask == nil else { return }
        activityUpdateTask = Task { [weak self] in
            guard let self else { return }
            while let content = self.pendingActivityContent {
                self.pendingActivityContent = nil
                for activity in Activity<BeaconActivityAttributes>.activities {
                    await activity.update(content)
                }
            }
            self.activityUpdateTask = nil
        }
    }
}

