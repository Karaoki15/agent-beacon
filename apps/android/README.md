# Agent Beacon for Android

Native Java Android app for the local Agent Beacon status protocol. The project uses platform Views, `HttpURLConnection`, Android Keystore and platform widgets/notifications. It has no Kotlin or Compose. The only runtime library is the pinned Apache-2.0 ZXing Android Embedded QR scanner and its small AndroidX transitive dependencies; see [third-party notices](THIRD_PARTY_NOTICES.md). The full Apache license is bundled at `assets/licenses/Apache-2.0.txt`.

Current candidate: **0.1.14 / versionCode 15**. Ukrainian/English UI with an explicit language selector and the English-only-for-English phone default, native rounded/ripple buttons, and independent large-widget selection of Codex, Claude Code and experimental Antigravity, with 1–3 rows, ordering, counts and cached native preview. Black cards adapt rows and headers to available space, with concise multi-chat counts and large-font fallbacks. Preserves pairing, monitoring and attention alerts. Install over the current app without uninstalling. See [configuration, validation and phone checks](../../docs/ANDROID_0.1.14.md).

## Build requirements

- Android Studio Meerkat (2024.3.1 Patch 1) or newer, or command-line Android tools
- JDK 17
- Android SDK Platform 36 and Android Build Tools 36.x
- Android Gradle Plugin 8.13.0 and Gradle 8.13

AGP 8.13 supports API 36.1 and uses Gradle 8.13/JDK 17. The Android API 36 compatibility table lists AGP 8.9.1 as the minimum; AGP 8.9.0 itself supports only through API 35. References: [Android API/AGP compatibility](https://developer.android.com/build/releases/about-agp), [AGP 8.13 release notes](https://developer.android.com/build/releases/agp-8-13-0-release-notes), and [Android 16 SDK setup](https://developer.android.com/about/versions/16/setup-sdk).

From this directory, build a debug APK with:

```powershell
gradle --no-daemon assembleDebug
```

The output is `app/build/outputs/apk/debug/app-debug.apk`. Use Android Studio's Gradle JDK setting to select JDK 17. This repository intentionally does not include a Gradle wrapper JAR. Gradle, the Android SDK, and JDK must already be available; the build does not install them. Set `GRADLE_USER_HOME` and `ANDROID_USER_HOME` to workspace-local directories when working in a restricted/low-space environment.

JourneyApps 4.3.0's published POM omits its AndroidX runtime dependencies, so the app pins AndroidX Core 1.6.0 and Fragment 1.3.6 explicitly to match that release's build. After building, verify the classes are present in the APK with `python tools/verify_apk_runtime.py app/build/outputs/apk/debug/app-debug.apk` from this directory. Pairing status appears directly under Connect, including the destination while connecting and distinct messages for rejected/expired links, network timeouts, and encrypted-credential storage failures. The app displays its installed version from Android package metadata.

### Rebuild with this workspace's H: toolchain

On this PC, the JDK, Android SDK, and Gradle are already stored under the repository. From `apps/android`, this PowerShell recipe uses those workspace-local tools and caches, runs offline, and does not install dependencies or change global configuration:

```powershell
$root = (Resolve-Path ..\..).Path
$env:JAVA_HOME = (Resolve-Path (Join-Path $root '.tools\jdk\jdk-17.0.20.1+1')).Path
$env:ANDROID_HOME = Join-Path $root '.tools\android-sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:ANDROID_USER_HOME = Join-Path $root '.cache\android'
$env:GRADLE_USER_HOME = Join-Path $root '.cache\gradle'
$env:TEMP = Join-Path $root '.cache\tmp'
$env:TMP = $env:TEMP
$gradle = Join-Path $root '.tools\gradle\gradle-8.13\bin\gradle.bat'
& $gradle --offline --no-daemon "-Duser.home=$($root.Replace('\','/'))/.cache/java-home" -Pandroid.builder.sdkDownload=false :app:lintDebug :app:assembleDebug
```

The project is configured for Android 8.0/API 26 through target API 36.

## Pairing and status

The preferred link is `agentbeacon://pair?host=192.168.1.10&port=8765#code=<43-character-base64url-code>`. Scan the QR in the app or paste the link. Camera permission is requested only after the user taps Scan; declining it leaves manual paste available. The app validates a canonical RFC1918 IPv4 address before constructing any URL, then sends `POST /api/pair` with `code` and `device_name`. It expects `device_id`, `device_token`, and `instance_id` in the JSON response. The pairing code is only used for that request. The returned device token is encrypted with an AES-GCM key held in Android Keystore and stored in private app preferences. Android backup is disabled.

The phone then polls `GET /api/status` with an Authorization bearer header. Requests use HTTP only, disable redirects and caching, have fixed five-second connect/read timeouts, stop after a fifteen-second total deadline, and reject bodies above 128 KiB. DNS names, public IPs, IPv6, HTTPS URLs, userinfo, query strings on the legacy link, and non-root URL paths are rejected. The server implements the one-time phone pairing route and read-only device credentials.

For migration/demo use, a manually pasted old link `http://192.168.1.10:8765/#token=<token>` remains accepted. That token is process-local and stops working when that server process restarts. A newly paired device credential survives server restart when the server is configured with `--device-store`; without that option, pairings last only for the server process lifetime.

The status parser checks UTF-8, duplicate JSON keys, schema version, finite bounded freshness, revision, state names and session count. The app uses the server's `display_state` for observed sessions, shows only a sanitized project basename or safe agent identifier, and never requests task text. A fresh aggregate `awaiting_user` snapshot with `task_state=working` and active reminder rows is labeled as a question reminder; an available session actually awaiting a user keeps priority. Reminder countdowns subtract request elapsed time; reminder expiry restores a confirmed working display while the separate snapshot lease remains fresh. It subtracts the entire request duration from the validity lease, drops the displayed state to unknown at expiry, and invalidates persisted widget data across a detected reboot. It fails closed when the top-level snapshot is partial and claims `ready`; the server's aggregate state remains authoritative across provider source health and closed observed-session rows. Green is labeled “Готовий до завдання” (ready for the next task), not a claim that the previous result succeeded or was evaluated.

## Background checks and widgets

Foreground checks run only while the app is open. Background checks require a user tap and the `connectedDevice` foreground service; the persistent notification includes a Stop action. The app refuses to start that service when notifications are denied or either app notification channel is disabled. If Android notification permission/settings are disabled later, the service stops. Android 13+ notification permission and Android 14+ foreground-service type/permission rules are documented at [notification permission](https://developer.android.com/develop/ui/views/notifications/notification-permission) and [foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types).

The home-screen widget shows a cached snapshot and opens the app when tapped. Version 0.1.1 added a compact 1x1 widget (34dp status dot, 11sp label); remove and re-add an existing widget to receive the new default size. To keep status refreshed while the app is closed, start the optional foreground monitor from the app and leave its notification enabled; with monitoring stopped, the widget shows gray/paused after its update. Android controls refresh timing, so the widget cannot guarantee a precise freshness-expiry repaint while the launcher sleeps. The app does not use exact alarms or wakelocks to claim that guarantee. Android recommends balancing widget refresh work with system update timing: [widget update guidance](https://developer.android.com/develop/ui/views/appwidgets/advanced).

Fresh working and awaiting-user snapshots can request Android Live Update promotion when the user opts in. The control appears only when the runtime exposes the promoted-ongoing API (introduced in Android 16 QPR / API 36.1); the project remains compiled against API 36 and checks for that API reflectively. The request is standard-style, ongoing, uncolorized, and uses no custom RemoteViews; Android and each OEM decide whether it is promoted. Ready and stale states are ordinary timed notifications. Dismissing or unpinning a promoted status suppresses further status notifications until the user starts monitoring again. This does not claim Samsung Now Bar or AOD integration; the separate fixed foreground-service notification remains the monitor notice. See [Android Live Updates](https://developer.android.com/develop/ui/views/notifications/live-update) and the [API 36.1 builder reference](https://developer.android.com/reference/android/app/Notification.Builder#setRequestPromotedOngoing(boolean)).

## Host-side protocol smoke checks

The pure-Java protocol checks can run without an Android SDK, using JDK 8+. A prior host-side `ProtocolSmokeTest` run passed 29 checks; it predates the current scanner/widget source candidate. Gradle unit tests report `NO-SOURCE`. This does not replace Android framework or device testing.

```powershell
$out = Join-Path $env:TEMP 'agent-beacon-android-checks'
New-Item -ItemType Directory -Force $out | Out-Null
javac -d $out app/src/main/java/com/agentbeacon/android/Protocol.java app/src/main/java/com/agentbeacon/android/MiniJson.java app/src/main/java/com/agentbeacon/android/Snapshot.java app/src/main/java/com/agentbeacon/android/SnapshotOrder.java app/src/main/java/com/agentbeacon/android/PollGenerationGate.java app/src/main/java/com/agentbeacon/android/PairingInput.java tools/ProtocolSmokeTest.java
java -cp $out com.agentbeacon.android.ProtocolSmokeTest
```

These checks cover strict parsing, state/lease validation and accepted/rejected pairing-address forms. They do not replace an Android build, phone/server integration test or Samsung device test.

## Galaxy S25 Ultra / One UI 8.5 validation

The following matrix is a device-test checklist, not a claim that it has already passed:

| Area | Check |
| --- | --- |
| Pairing | Open a new pairing link from QR/browser; paste legacy link; reject public IP, hostname, malformed code and stale pairing code |
| States | Confirm working red, awaiting-user yellow, ready green, unavailable/stale gray; verify one old session cannot keep the aggregate green |
| Freshness | Stop server, wait past `valid_for_seconds`, and verify app and active-monitor notification move to unknown |
| Background | Start monitoring from visible app, lock screen, switch Wi-Fi, stop/restart server, use notification Stop, then force-stop app |
| Notification controls | Deny Android 13+ permission, disable each channel in One UI, re-enable it, and confirm service only runs with notification visibility enabled |
| Widget | Add home and lock-screen widget where One UI exposes it; test resize, monitor active, monitor stopped, stale server, app/service restart and reboot |
| Battery/OEM | Test unrestricted/optimized/restricted battery modes and Samsung sleeping/deep-sleeping app lists; describe any OEM suspension as an OS limitation |
| Privacy | Confirm no token in logcat, notification text, session list, clipboard or exported files; check app backup remains disabled |

One UI and Android may delay service/widget work under battery-saving and app-sleep policies. The ongoing notification makes a user-started monitor visible; it does not promise immunity from force-stop, OEM policy or server unavailability.

### Current candidate status

The Galaxy S25 Ultra report on 0.1.0 described Scan closing before the camera opened; 0.1.1's scan guard did not resolve it, and clearing the app cache did not help. APK inspection showed 0.1.1 omitted five AndroidX runtime definitions referenced by JourneyApps scanner classes. Version 0.1.2 adds JourneyApps 4.3.0's pinned AndroidX Core 1.6.0 and Fragment 1.3.6 dependencies and reports pairing progress and failures under Connect. The 0.1.2 code-3 APK passed offline lint/build, DEX verification (all 10 required classes: five AndroidX and five app/QR), v2 signature with unchanged certificate, exact LAN download verification, and source/web review 14; parent performed APK verification separately. It is 1,143,093 bytes; SHA-256 `caf292833c75f58c2c0007c122dbe58d2e7646677a47228a9bd92e8ac759faf9`. Physical Galaxy S25 camera, pairing, and Connect behavior remain unconfirmed. Download through the stable browser link/QR at `http://192.168.0.156:8765/downloads/AgentBeacon-debug.apk`. After installation, create a fresh native `agentbeacon://pair` QR because the pairing code expires after two minutes, then scan it inside the app.
