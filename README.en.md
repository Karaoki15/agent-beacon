# Agent Beacon

[Українська](README.md) · **English**

**Know when your AI agents need you.** A local Windows tray indicator, Android notifications and customizable widgets for Codex and Claude Code, with experimental Antigravity support.

[**Download for Windows — EXE**](https://github.com/Karaoki15/agent-beacon/releases/download/v0.2.0-beta.3/AgentBeacon-Setup.exe) · [**Download for Android — APK**](https://github.com/Karaoki15/agent-beacon/releases/download/v0.2.0-beta.3/AgentBeacon-Android.apk)

Windows **0.2.0-beta.3** · Android **0.1.14** · [Release files and checksums](https://github.com/Karaoki15/agent-beacon/releases/tag/v0.2.0-beta.3)

## Install in a few steps

You **do not need Git, Python, GitHub Desktop or a GitHub account** to download and use the release.

1. Click **Download for Windows — EXE** above, save the file and run it on the computer where your agents work.
2. Click **Встановити й відкрити** (*Install and open*). The installer includes the runtime; the setup UI is currently Ukrainian.
3. In the browser wizard, select your agents → **Переглянути зміни** (*Review changes*) → **Підключити вибраних агентів** (*Connect selected agents*). Beacon backs up existing settings before adding its hooks.
4. Restart your agent or open a new local chat and send a short task. Confirm that Beacon receives a real event.
5. For Android, enable phone access in the wizard. Install the APK using its download QR, then scan the **separate pairing QR inside Agent Beacon**. Keep the phone and computer on the same trusted network.

[**Full installation guide →**](docs/INSTALL_EN.md) · [Troubleshooting and updates](docs/INSTALL_EN.md#when-something-does-not-connect)

## What you see

| Indicator | Meaning |
| --- | --- |
| 🔴 Working | An enrolled agent is working. |
| 🟡 Needs you | Your answer is needed, or a valid question reminder is still active. |
| 🟢 Ready | Ready for another request. This does not evaluate whether the result is correct. |
| ⚪ No update | No confirmed fresh signal is available. |

- Windows tray status and a local browser dashboard with chat and project names.
- Android alerts for questions and completed requests. Starts and routine updates stay silent.
- A compact widget and an agent overview widget. Each large widget has its own agents, row order and optional chat counts.
- Automatic reconnection, a battery saver and connection diagnostics.
- **Ukrainian / English** Android UI. Automatic mode uses English only for an English phone; every other phone language falls back to Ukrainian. You can override it in the app. Short widget labels stay English.

![Widget layout illustration](docs/assets/widgets-preview.png)

*Layout illustration generated from the Android layouts; not a screenshot of a physical phone. Widget spacing also depends on the launcher.*

## Supported agents and privacy

| Agent | Support in this beta |
| --- | --- |
| Codex | Enrolled local sessions through the supported hook/observer integration. |
| Claude Code | Local Code sessions with Beacon hooks enabled. |
| Antigravity | Experimental hook adapter; live application validation remains open. |

Ordinary **Claude Chat**, cloud sessions, SSH/WSL sessions and iPhone are not covered by this beta. Installing Beacon does not sign you into an agent or change its tool permissions.

The phone receives status and chat/project metadata, rather than prompt or response bodies. Beacon uses your computer and trusted local network; it does not require a hosted Beacon account or cloud relay. Pairing QR codes are single-use and expire after two minutes. Do not publish them.

## Beta status

The EXE is currently **unsigned**, and the APK uses a **debug development certificate**. Windows or Android can display a security notice. Verify the source and the release checksums; do not disable system protection to run an untrusted file.

Builds, payload integrity, actual bundled EXE/helper/tray behavior and automated checks are verified. Clean-machine installation, native installer/UAC/shortcuts and physical Android/LockStar acceptance remain manual checks. This is a beta preview, not a claim that every device has been tested.

[Development and building](docs/DEVELOPMENT.md) · [Protocol](docs/PROTOCOL.md) · [Antigravity setup](docs/ANTIGRAVITY.md) · [MIT license](LICENSE)
