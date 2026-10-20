# Android 0.1.15 / code 16

This candidate improves how the Android monitor handles confirmed ready and prolonged unknown status:

- A confirmed `ready` result remains visible after its worker exits; a closed worker alone does not turn that result unknown.
- Background monitoring pauses after 30 minutes when status remains unknown. Opening the app resumes a monitor paused by this timeout.
- The notification's **Stop** action remains a manual stop. Opening the app does not restart monitoring after a manual stop.
- Foreground checks continue to update the app and widgets live while the app is open, regardless of whether background monitoring is enabled.
- No Android dependencies are changed.

## Build and validation

The Android candidate was built offline: `lintDebug` and `assembleDebug` succeeded in 1 minute 21 seconds. APK metadata reports package `com.agentbeacon.android`, version name `0.1.15`, and version code `16`. The runtime verifier found all 10 required classes, and APK v2 signature verification passed with the same certificate fingerprint as the previously staged APK. The Java `ProtocolSmokeTest` passed 29 checks; 32 temporary Java offline-budget checks also passed. The existing Python suite passed 119 tests and the browser suite passed 23 tests. Three independent Luna review passes were completed; the latest found no substantive issues and marked the code production-ready at code level.

Use the existing workspace-local offline Android build recipe in [apps/android/README.md](../apps/android/README.md). The output is `apps/android/app/build/outputs/apk/debug/app-debug.apk`; package metadata should report version name `0.1.15` and version code `16`. The existing local debug signing setup is required to update the currently installed development build.

The desktop companion also needs its corresponding bridge/hook-backlog update and a restart for the retained-ready and hook-backlog fixes to take effect end to end; the Android APK alone does not update desktop behavior. Independent code-level review found no substantive issues and marked the change ready at code level. Physical-phone and clean-machine installer acceptance remain unverified; no public release or physical-device validation is claimed.

The local Windows 0.2.0-beta.4 package build completed successfully. The APK staged at `dist/AgentBeacon-debug.apk` (SHA-256 `56d01a440726e2d586cf3a38a28b0d8d76b29230bd34dd4224ddd4a2d469d169`) matches the APK in the frozen runtime, portable package, and Setup EXE's embedded payload. The beta.4 portable package and embedded payload SHA-256 is `d670d8898ba6ba227281fd7495bc1f0b1f5ed92235c2cea94dccd7896b1cb573`; it matches the recorded payload checksum and the checksum embedded in Setup. Read-only inspection of both frozen executables confirmed their packaged `agent_beacon.adapters.bridge` bytecode matches the current source. This verifies package contents, not an installer run on a clean Windows machine.

## Manual phone checks

- Complete a task, confirm the app and widget show ready, then close the worker and verify the confirmed ready state remains.
- Leave only unknown status for 30 minutes; confirm background monitoring pauses and the notification reflects the stopped/paused state.
- Open the app after that automatic pause and confirm monitoring resumes.
- Start monitoring, use the notification's **Stop** action, then reopen the app and confirm monitoring remains stopped until explicitly started again.
- Keep the app open while background monitoring is stopped and confirm foreground checks continue updating the app and widgets.
- Switch Wi-Fi off and on, then stop and restart the server; confirm reconnect behavior and that stale data eventually becomes unknown.

## Implemented plan

| Subtask | Change and files | Affected component | Depends on | Key edge risk |
|---|---|---|---|---|
| Desktop ready and ownership | Retain bounded ready-owner identity and accept a terminal event from an exited owner only when provider, session, PID/creation identity, turn, and bridge lease still match; ignore late same-turn nonterminal hooks. `agent_beacon/adapters/bridge.py` | Desktop hook bridge and lifecycle aggregation | Independent foundation | Active/unresolved work still becomes unknown; stale events or a lost bridge lease cannot restore ready. |
| Hook queue backlog | Consume queued lifecycle events before process-health reconciliation; defer a trailing sequence-gap decision while the next expected event remains queued, then apply the existing grace period after the queue drains. `agent_beacon/adapters/bridge.py` | Desktop event spool and observer continuity | Independent change; shares bridge integration with desktop ready/ownership | A persistent missing sequence still breaks continuity after grace and may make active status unknown. |
| Android usable-status budget | Track last usable status and a boot-scoped 30-minute budget; pause only after no usable status, resume an offline auto-pause on app open, and retain a distinct manual-stop reason. `OfflineBudget.java`, `MonitorState.java`, `Snapshot.java`, `StatusService.java`, `MainActivity.java` | Android foreground monitor lifecycle | Independent of desktop changes; relies on fresh protocol snapshots | Reboot invalidates elapsed-time continuity; resuming requires a saved pairing and enabled notification settings. |
| Transport and widget handoff | Keep status delivery coordinated between foreground and service checks; refresh compact and wide widgets from foreground snapshots even when background monitoring is stopped. `BeaconClient.java`, `MainActivity.java`, `StatusService.java`, `BeaconWidget.java`, `WideBeaconWidget.java`, `Snapshot.java` | Android status transport, app UI, widgets | Android usable-status budget and monitor lifecycle decisions | Android/OEM scheduling can delay widget redraws while the app is closed; no precise expiry redraw is promised. |
| Validation, packaging, review | Set Android version to 0.1.15/code16; build and verify the APK, package it into the Windows portable ZIP and Setup payload, verify payload hashes and frozen bridge bytecode, and complete code-level review. `apps/android/app/build.gradle`, `apps/android/README.md`, `docs/ANDROID_0.1.15.md`, `dist/AgentBeacon-debug.apk`, `dist/windows/*` | Android APK and Windows frozen candidate | All preceding subtask integrations | Physical-phone and clean-machine installer checks remain required before public release. |
