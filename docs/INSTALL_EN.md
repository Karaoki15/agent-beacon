# Install Agent Beacon

[Українська](INSTALL_UK.md) · **English**

Versions: Windows **0.2.0-beta.4**, Android **0.1.15**. Requirements: Windows x64, Android 8 or newer, and an installed, signed-in supported agent on your computer.

## 1. Download on your computer

Open https://github.com/Karaoki15/agent-beacon and click **Download for Windows — EXE** in the English README. Alternatively open Releases → v0.2.0-beta.4 → Assets → AgentBeacon-Setup.exe.

You do not need a GitHub account, Git, GitHub Desktop, Python or the source folder. The automatically generated Source code ZIP is for developers, not the installer.

For another laptop, download the EXE there or copy the installer using a USB drive. Do not copy private tokens or account settings from your old computer.

## 2. Install Windows

Run AgentBeacon-Setup.exe as your regular Windows user, choose whether to create a desktop shortcut and click **Встановити й відкрити** (*Install and open*). The installer and browser wizard currently use Ukrainian; this guide includes the corresponding labels. Android has a Ukrainian/English language selector.

The package includes its runtime, shortcuts and an uninstall entry. Installation is per user:

Program: %LOCALAPPDATA%\Programs\AgentBeacon

Settings and paired devices: %LOCALAPPDATA%\AgentBeacon

This beta is not Authenticode-signed, so Windows may display a notice. Verify the download source and release SHA256SUMS.txt. Do not disable system protection to run an untrusted file.

## 3. Connect your agents

The local browser wizard opens after installation. Select Codex, Claude Code and/or Antigravity. Click **Переглянути зміни** (*Review changes*), read the plan, then **Підключити вибраних агентів** (*Connect selected agents*).

Hooks are small commands that tell Beacon when an agent starts, asks a question or finishes. They are added only after you apply the plan. Existing settings are backed up and unrelated hooks are preserved.

Restart the agent or open a new local chat, send a short task and click **Перевірити підключення** (*Check connection*). A saved hook configuration is not yet proof of a received event.

If configuration is blocked, **Допомогти підключити** (*Help connect*) provides a copyable prompt containing the correct helper command for this computer. Sign-in and folder trust remain in the agent itself. Ordinary Claude Chat, cloud and SSH/WSL sessions are not covered. Antigravity remains experimental.

## 4. Android: install first, then pair

Enable phone/LAN access in the Windows wizard. Keep the phone and computer on the same trusted network.

1. Scan the **APK installation QR** with your normal phone camera, or open GitHub Releases on the phone and download AgentBeacon-Android.apk.
2. Install the APK. If Android requests installation permission for that source, review it yourself. The current APK uses a debug development certificate.
3. Create the **separate pairing QR** in the Windows wizard. Inside Agent Beacon on Android, tap **Scan pairing QR**, scan it and tap **Connect**.
4. The code is single-use and expires after two minutes. Generate a new one if needed.
5. Allow notifications and enable background monitoring if you want updates while the app is closed.

A different computer needs its first pairing. Restarts or port changes on the same saved server normally use automatic reconnection or **Check & connect**. The Android app currently uses one selected computer connection rather than a simultaneous view of several PCs.

## 5. Language and widgets

At the bottom of the Android home screen, tap **App language / Мова застосунку**. Choose Automatic, Українська or English. Automatic uses English only when the phone language is English; all other phone languages use Ukrainian. An explicit choice survives restarts and updates. Changing language preserves pairing and does not replay question/completion sounds.

Short widget labels such as Ready, Working, Needs you and Live remain English. To add a widget, touch and hold an empty home-screen area → Widgets → Agent Beacon. In **Widget settings**, choose a particular large widget, select 1–3 agents, reorder rows and toggle counts. Every large widget is configured independently.

A low widget hides its header to keep agent rows readable. A narrow widget shows names and colored indicators. Increase width for state text and height for very large fonts. `Working · 3` means three current chats for that agent, including active work; it does not mean all three are working.

Samsung Good Lock / LockStar can place the widget on supported lock screens. Placement and permissions depend on the phone. Android Live Updates is a separate experimental notification feature.

## 6. Daily use

Red: working. Yellow: your answer is needed or a valid question reminder remains. Green: ready for another task, not a judgement of the result. Gray: no confirmed fresh signal.

Questions and completed requests can alert with sound and vibration. Starts and routine updates stay silent. Ready chats leave the current list after 30 minutes of inactivity. “Session monitoring ended” does not delete the saved chat.

Closing the browser tab keeps Beacon in the tray. Use the tray **Вийти** (*Exit*) command to stop it. Starting with Windows is optional. Android automatically pauses background monitoring after 30 minutes if only unknown status remains. Opening the app resumes an automatic pause. The notification's **Stop** action is manual; reopening the app does not restart monitoring after you use it.

## When something does not connect

- Hooks saved but gray status: restart the agent or open a new local chat and run a real task.
- PC works, phone cannot connect: check LAN access, the same network, VPN and guest Wi-Fi client isolation. The separate Windows firewall button asks for administrator approval and scopes its rule to Beacon, Private and LocalSubnet. It does not change your network profile.
- Address or port changed: keep one intended Beacon copy running and use Check & connect. Busy ports are skipped. If the network blocks discovery, create a new pairing QR.
- No alerts: check Agent Beacon permissions, channels, volume, Do Not Disturb and Android background restrictions.
- Old widget after an update: open the app, check the connection and, if necessary, stop and restart background monitoring. Do not clear app data.
- Sleeping or powered-off PC: it cannot send a current agent state.

When reporting a bug, include versions, the agent and reproduction steps. Exclude pairing QR codes, tokens, private account configuration and customer chat contents.

## Updates, migration and uninstall

Install Android updates over the existing app, without uninstalling. Use a newer Setup EXE for Windows. Settings and pairing are stored separately from program files.

When moving from a source-folder launch on the same PC, close the old Beacon in its tray. The wizard can offer to import old phones if exactly one recognized registry exists and the new registry is empty. Then tap Check & connect on Android. Two Beacon copies may compete for discovery.

Uninstall through Windows Settings → Apps → Agent Beacon beta, or Uninstall-AgentBeacon.exe. Phone data is retained. A separate choice removes only recognized hooks owned by that installation. Modified files and previous versions are retained. Firewall rules are not automatically removed.

This is a beta preview. Clean Windows, a complete manual installer/UAC/shortcut flow and physical Samsung Home/LockStar still require acceptance checks. iPhone is postponed.
