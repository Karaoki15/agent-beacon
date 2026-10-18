# LAN device pairing

The LAN pairing contract is implemented in T12. LAN mode uses plain HTTP and should stay inside a trusted private network. Native client credentials can read `/api/status` only; they cannot use local control routes.

## Pairing flow

For first-time Android setup, download/install the APK first. On this PC, the current 0.1.2 APK URL is `http://192.168.0.156:8765/downloads/AgentBeacon-debug.apk`; the address may differ on another network. The browser page provides a separate QR for this APK download when the server is LAN-enabled, plus a local-computer download link for loopback use. The APK-download QR is ordinary HTTP and is separate from the native pairing QR. A stock camera cannot open `agentbeacon://pair` before Agent Beacon is installed. After installation, open the app and use **Scan QR > Connect** for native pairing; create a fresh pairing code because it expires after two minutes. LAN is enabled on this PC; setup scopes and helper rule were independently read back. The Samsung browser reached the PC page, and the PC verified exact APK bytes plus temporary pairing/status/revoke. Physical native pairing and 0.1.2 camera/Connect behavior remain unconfirmed. On the Galaxy S25 Ultra, 0.1.0 Scan closed before camera open after “Only this time” permission; 0.1.1's scan guard did not fix it, and clearing cache did not help. APK inspection found five AndroidX runtime definitions referenced by the scanner missing from 0.1.1; 0.1.2 includes them. This is strong APK evidence for the scanner dependency issue, but not proof of the device-log root cause or physical fix.

1. Start Agent Beacon bound to `0.0.0.0` and open its local page on the computer running Beacon.
2. Enter the computer's RFC1918 IPv4 address and choose **Створити код для застосунку**. The local page calls `POST /api/pairing` with `{}` and receives a random code valid for 120 seconds. At most eight live codes exist at once.
3. The page copies a native deep link in this exact shape:

   ```text
   agentbeacon://pair?host=192.168.1.10&port=8765#code=<43-character-base64url-code>
   ```

   The host must be a canonical RFC1918 IPv4 address (`10/8`, `172.16/12`, or `192.168/16`). The page also renders this one-time URI as a local canvas QR code, shows its remaining lifetime, and clears both the QR and copied URI at expiry. This pairing QR is scanned from inside the installed native app. The fragment is handled by the native client and is not sent in HTTP. QR generation uses a locally vendored MIT encoder and makes no network request.
4. The native client sends `POST http://<host>:<port>/api/pair` with JSON `{"code":"<code>","device_name":"<display name>"}`. It may omit `Origin`; if it sends `Origin`, it must exactly match the HTTP host and port. A successful response contains a one-time `device_token`, `device_id`, and current `instance_id`.
5. Store the token securely on the client and send it as `Authorization: Bearer <device_token>` for `GET /api/status`. If `--device-store` is enabled, the read-only device credential remains valid after a Beacon restart until revoked or the store is lost. By default, registrations are in memory and are removed at restart. A new response `instance_id` is accepted while the same device token remains valid.

Codes are single-use. The server serializes consumption and device registration, limits the registry to 32 devices, and rejects malformed/expired codes and invalid display names. Existing browser links using `/#token=<viewer_token>` continue to work; that per-process viewer token still rotates at server restart.

## Local device management and persistence

- `GET /api/devices` is available only to a loopback peer addressing a loopback Host. It returns device IDs and bounded display names, never tokens or token digests.
- `POST /api/revoke` requires that same local boundary plus an exact same-origin `Origin`, and a JSON body containing only `device_id`. The response is `{"revoked":true|false}`.
- Pairing codes are process-local and disappear at restart.
- By default, device registrations are also process-local. To preserve them across restart, pass `--device-store <path>`. The file stores only device IDs, bounded display names, and SHA-256 token digests. Updates use a same-directory temporary file and atomic replacement; if loading or writing fails, startup/update fails closed and the prior store remains intact.
- The local `/api/control` viewer token remains random per process and is never persisted.

The HTTP service is not encrypted. Do not expose its port to the public internet. The native client must treat the token as a secret and send it only in the Authorization header.

## Windows scripts

`scripts/windows/Start-AgentBeacon.ps1` starts the tray and both provider integrations in a hidden process. It checks port 8765 before launch and saves its stop record only after confirming the launched PID owns the listener and the local API responds. `-Lan` enables `0.0.0.0` and persistent device storage by default; `-NoPersistDevices` disables that storage. `-Integrations codex` or `-Integrations claude` selects one provider. `scripts/windows/Stop-AgentBeacon.ps1` stops only the recorded process after checking its PID, creation time, executable, and Agent Beacon command line. Neither script configures Windows startup automatically; create a shortcut manually if desired.

From PowerShell in the project folder:

```powershell
.\scripts\windows\Start-AgentBeacon.ps1 -Lan -PersistDevices
.\scripts\windows\Stop-AgentBeacon.ps1
```

Omit `-Lan` for loopback-only use. Add `-PersistDevices` to keep pairings after restart in loopback mode, or `-NoPersistDevices` to avoid persistence in LAN mode. A manual shortcut can target `powershell.exe` with `-ExecutionPolicy Bypass -File "<project>\scripts\windows\Start-AgentBeacon.ps1" -Lan`.
