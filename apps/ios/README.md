# Agent Beacon for iOS

Native SwiftUI client for the local Agent Beacon server. The project includes a WidgetKit extension and an opt-in ActivityKit Live Activity. It targets iOS 17; iPhone 13 and iPhone 16 are intended validation devices, but the source has not been built or run on this Windows host. Mac/Xcode build, signing, simulator, and physical-device checks remain pending.

## What it does

- Shows one aggregate status circle: red for Working, yellow for Waiting for you, green for Ready, and dim gray for Unknown.
- Pairs by scanning the server QR code with the native camera or pasting the complete `agentbeacon://` link.
- Reads the status API only while the app is in the foreground, every four seconds. The client never sends prompts, responses, transcripts, location, or audio.
- Shows safe session labels from `project_name`, when present, and source names. The widget and Live Activity show the aggregate status.
- Shares only a bounded status snapshot with the widget extension through an App Group. Pairing credentials are stored in the iOS Keychain and are never placed in the shared container or widget timeline.
- Rejects pairing links that contain DNS names, public/loopback IPs, unexpected query fields, invalid ports, or a code outside the 43-character base64url form.

The app connects to a private IPv4 host over plain HTTP, matching the current LAN server. Keep Agent Beacon on a trusted private Wi-Fi network and do not expose its port to the internet. The pairing endpoint is `POST /api/pair` with `{ "code": "…", "device_name": "iPhone" }`; status uses `GET /api/status` with a per-device bearer token. Redirects are refused and response data is limited to 128 KiB.

## Open and sign on a Mac

There is no Apple compiler or Xcode on the Windows development machine. Build and device validation must happen on a Mac with Xcode installed.

1. Copy or clone this repository onto the Mac and open `apps/ios/AgentBeacon.xcodeproj` in Xcode. Select the shared `AgentBeacon` scheme.
2. In project Build Settings, change the project-level `APP_BUNDLE_PREFIX` from `com.example` to a reverse-DNS prefix you control. The app, widget extension, test bundle, and default group identifier derive from this value.
3. Select the `AgentBeacon` app target, open **Signing & Capabilities**, turn on **Automatically manage signing**, and choose your Apple Account’s Team. Repeat for `AgentBeaconWidget` and `AgentBeaconTests` if Xcode does not inherit it.
4. Add the App Groups capability to both the app and widget extension targets, and register/select the group shown by `APP_GROUP_IDENTIFIER` (default: `group.com.example.AgentBeacon`). If you changed `APP_BUNDLE_PREFIX`, the default group becomes `group.<your-prefix>.AgentBeacon`.
5. Choose an iPhone 13 or iPhone 16 running iOS 17 or later and press Run. On the first launch, allow local network access. Camera access is requested only when scanning a QR code.

An Apple Account Personal Team can install and debug apps on personal devices. Apple limits Personal Team provisioning: app identifiers, registered devices, and provisioning profiles expire, so the app needs to be rebuilt and reinstalled periodically. App Groups are an additional capability and may not be provisionable by a Personal Team in a given Xcode/account configuration. If Xcode refuses the shared group entitlement, use a team/account that can provision App Groups; do not remove the group entitlement because widgets need it to read app status. App Store/TestFlight distribution and capabilities that require Apple Developer Program membership need paid membership. See [Apple’s Personal Team limits](https://developer.apple.com/help/account/membership/programs-overview/), [App Group setup](https://developer.apple.com/documentation/xcode/configuring-app-groups), and [capability setup](https://developer.apple.com/help/account/identifiers/enable-app-capabilities/).

No XcodeGen project generator, external package, paid API, Apple build server, APNs key, or push capability is part of this project.

## Pair and use

1. Start Agent Beacon on the computer with its existing LAN listener enabled and open its pairing QR code.
2. In Agent Beacon on iPhone, tap **Scan pairing QR code**, or paste the full pairing link. The link is accepted only for a numeric RFC1918 IPv4 address (`10/8`, `172.16/12`, or `192.168/16`) and a valid port.
3. Keep the app open to receive fresh status. Use **Start Live Activity** to opt in to the lock screen and Dynamic Island presentation. Disconnect removes the Keychain credential and dismisses the app’s Live Activities.
4. Add the Agent Beacon widget from the iOS widget gallery. Small and medium Home Screen sizes and circular, rectangular, and inline Lock Screen sizes are supported. iOS may also display compatible widgets in StandBy.

Pairing codes are single-use and short-lived according to the server. To pair again, create a new code on the computer. Re-pairing replaces the iPhone’s saved credential.

## Freshness and Apple platform limits

The app subtracts the entire request round trip from `valid_for_seconds`. A lower revision from the same server instance is ignored, and a new instance can reset the revision while the saved device credential remains valid. A request failure, disconnect, or expired lease becomes Unknown. Freshness uses a monotonic uptime deadline and the device boot time, so moving the wall clock backwards cannot extend a green status; cached app-group data and Live Activity content from another boot, or without a readable boot identity, fail closed. Backgrounding cancels LAN polling; timeline and Live Activity stale dates prompt system refreshes without background networking.

WidgetKit decides when to refresh widget timelines and can delay reloads to manage battery use. Every widget render checks the monotonic deadline, and a scheduled entry changes the display to Unknown at the known expiry when iOS renders that timeline; the system does not promise an immediate widget redraw at that instant. A widget never connects to the Beacon server. See [Keeping a widget up to date](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date) and [WidgetKit timelines](https://developer.apple.com/documentation/widgetkit/timeline/policy).

Live Activities cannot use the network. This app updates an active Live Activity only while its app can poll in the foreground; `staleDate`, `context.isStale`, and the monotonic deadline turn the display gray when the lease ends. APNs ActivityKit updates could be added later with server infrastructure and credentials, but are not configured here. Apple automatically ends a Live Activity after eight hours; its lock screen card can remain for up to four additional hours. See [Displaying live data with Live Activities](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities) and [ActivityKit stale dates](https://developer.apple.com/documentation/activitykit/activitycontent/staledate).

An iPhone 13 has no Dynamic Island, so it shows Live Activities on the Lock Screen. iPhone 16 has the Dynamic Island. The base iPhone 16 and iPhone 13 do not provide an always-on display; StandBy availability depends on the iPhone model and display settings.

## Device validation matrix

| Check | iPhone 13 | iPhone 16 |
| --- | --- | --- |
| iOS 17+ install, launch, and dark status screen | Required | Required |
| QR camera permission and successful pairing | Required | Required |
| Manual link paste, invalid/public-host rejection | Required | Required |
| Working / waiting / ready colors and session labels | Required | Required |
| Disconnect, background cancellation, resume refresh | Required | Required |
| Small and medium Home Screen widget; expiry to gray | Required | Required |
| Lock Screen inline, circular, and rectangular widgets | Required | Required |
| Live Activity stale-to-gray and manual dismissal | Lock Screen | Lock Screen and Dynamic Island |
| StandBy widget presentation | If available in system UI | If available in system UI |

The checked-in XCTest target covers strict pairing URL/address parsing and lease expiry. Run it on a Mac from Xcode using **Product → Test** or `xcodebuild test -project apps/ios/AgentBeacon.xcodeproj -scheme AgentBeacon -destination 'platform=iOS Simulator,name=iPhone 16'`. The repository authoring host is Windows, so this project has not been compiled or run in Xcode here.
