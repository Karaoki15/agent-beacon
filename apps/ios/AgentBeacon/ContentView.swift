import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: BeaconStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var pairingLink = ""
    @State private var showingDisconnect = false
    @State private var scannerError = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        VStack(spacing: 12) {
                            statusCircle
                                .padding(.top, 24)
                            Text(store.state.label)
                                .font(.title2.weight(.semibold))
                                .accessibilityAddTraits(.updatesFrequently)
                            Text(lastUpdatedText)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }

                    if !store.sharedStorageAvailable {
                        Label("Віджети недоступні: перевірте App Groups для обох цілей підпису.", systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(.orange).multilineTextAlignment(.center)
                    }

                    if let message = store.message {
                        Text(message)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }

                    if store.paired {
                        pairedControls
                    } else {
                        pairingControls
                    }

                    sessionSection
                }
                .frame(maxWidth: 560)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 20)
                .padding(.bottom, 32)
            }
            .background(Color(uiColor: .systemBackground))
            .navigationTitle("Agent Beacon")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if store.paired {
                        Button("Від’єднатися", role: .destructive) { showingDisconnect = true }
                    }
                }
            }
            .confirmationDialog("Від’єднатися від Agent Beacon?", isPresented: $showingDisconnect) {
                Button("Від’єднатися", role: .destructive) { Task { await store.disconnect() } }
            }
            .sheet(isPresented: $store.showScanner) {
                NavigationStack {
                    PairingScanner(onCode: { code in
                        store.showScanner = false
                        Task { await store.handlePairingURL(URL(string: code) ?? URL(fileURLWithPath: "/")) }
                    }, onFailure: { error in
                        store.message = error
                        scannerError = error
                    })
                    .ignoresSafeArea()
                    .navigationTitle("Сканування QR-коду")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Готово") { store.showScanner = false } } }
                    .alert("Не вдалося відкрити камеру", isPresented: Binding(
                        get: { !scannerError.isEmpty },
                        set: { if !$0 { scannerError = "" } }
                    )) {
                        Button("Гаразд", role: .cancel) { scannerError = "" }
                    } message: {
                        Text(scannerError)
                    }
                }
            }
            .onOpenURL { url in Task { await store.handlePairingURL(url) } }
            .onChange(of: scenePhase) { _, phase in store.setForeground(phase == .active) }
            .task { store.setForeground(scenePhase == .active) }
        }
        .preferredColorScheme(.dark)
    }

    private var statusCircle: some View {
        Circle()
            .fill(color(for: store.state).gradient)
            .frame(width: 174, height: 174)
            .shadow(color: color(for: store.state).opacity(store.state == .unknown ? 0.08 : 0.35), radius: 24)
            .accessibilityLabel("Статус Agent Beacon: \(store.state.label)")
    }

    private var pairedControls: some View {
        VStack(spacing: 12) {
            Button { store.refresh() } label: {
                Label(store.busy ? "Оновлення…" : "Оновити зараз", systemImage: "arrow.clockwise")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(store.busy)

            Button {
                Task { await store.startLiveActivity() }
            } label: {
                Label("Увімкнути Live Activity", systemImage: "livephoto")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)

            Label("Статус оновлюється, поки застосунок відкритий. Віджети й Live Activity показують останній відомий стан до завершення його терміну актуальності.", systemImage: "info.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 4)
        }
    }

    private var pairingControls: some View {
        VStack(spacing: 12) {
            Button { store.showScanner = true } label: {
                Label("Сканувати QR-код для підключення", systemImage: "qrcode.viewfinder")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)

            Text("Або вставте повне посилання з Agent Beacon")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("agentbeacon://pair?…", text: $pairingLink, axis: .vertical)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel("Посилання для підключення")
            Button("Підключити пристрій") { Task { await store.pairManually(pairingLink) } }
                .buttonStyle(.bordered)
                .disabled(pairingLink.isEmpty || store.busy)
        }
    }

    private var sessionSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Сесії").font(.headline)
                Spacer()
                Text("\(store.snapshot.sessions.count)").font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
            }
            if store.snapshot.sessions.isEmpty {
                Text("У свіжому статусі немає даних про сесії.")
                    .font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
                    .background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 14))
            } else {
                ForEach(store.snapshot.sessions) { session in
                    HStack(spacing: 12) {
                        Circle().fill(color(for: store.snapshot.effectiveState() == .unknown ? .unknown : session.state))
                            .frame(width: 11, height: 11)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(session.projectName ?? session.source.capitalized)
                                .font(.subheadline.weight(.medium)).lineLimit(1)
                            Text(session.state.label)
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                        Text(session.source.uppercased())
                            .font(.system(size: 9, weight: .bold, design: .rounded))
                            .foregroundStyle(.secondary)
                    }
                    .padding(13)
                    .background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 14))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var lastUpdatedText: String {
        guard store.snapshot.receivedAt > .distantPast, !store.snapshot.serverTime.isEmpty else { return "Очікуємо на свіжий статус" }
        if store.state == .unknown { return "Останній статус застарів або недоступний" }
        return "Оновлено \(store.snapshot.receivedAt.formatted(date: .omitted, time: .shortened))"
    }

    private func color(for state: BeaconState) -> Color {
        switch state {
        case .working: return Color(red: 0.91, green: 0.20, blue: 0.22)
        case .awaitingUser: return Color(red: 0.96, green: 0.67, blue: 0.10)
        case .ready: return Color(red: 0.19, green: 0.75, blue: 0.39)
        case .unknown: return Color(red: 0.32, green: 0.34, blue: 0.38)
        }
    }

}

