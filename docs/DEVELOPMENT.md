# Development

Consumers should use the EXE/APK in GitHub Releases. This page is for building from source.

The desktop implementation uses Python 3.11 and the standard library. The Android app uses native Java Views and RemoteViews, with the pinned QR scanner dependencies documented in apps/android/THIRD_PARTY_NOTICES.md. There is no hosted Beacon service requirement.

```powershell
python -m agent_beacon --help
python -m agent_beacon --port 8766
python -m unittest discover -s tests
node --test tests/test_web.js
```

For local tray/LAN launching, see scripts/windows/Start-AgentBeacon.ps1. Use scripts/windows/Stop-AgentBeacon.ps1 for the owned source instance. Do not run multiple copies against the same hook queue.

Android build prerequisites and offline workspace commands are in apps/android/README.md. This machine's private debug keystore is excluded from the repository; another development checkout uses its own standard Android debug key. A build with a different certificate cannot update an existing installation signed with the previous key.

Android translations live in apps/android/tools/translations.json. Regenerate the matched default Ukrainian and values-en resources with:

```powershell
python apps/android/tools/generate_translations.py
```

Keep chat/project names, protocol states and short widget labels out of translation. LanguageChoice defines the English-only-for-English automatic policy. AppLanguage stores explicit choices separately from pairing. AppText localizes UI presentation; it does not change wire data. UiStyle defines the native rounded/ripple buttons.

Windows packaging uses the pinned apps/windows/build-requirements.txt in a dedicated build virtual environment. Installing build dependencies is a developer action, not part of the end-user installer. Stage the intended APK at dist/AgentBeacon-debug.apk before running scripts/windows/build_beta.py; the script installs no packages. PyInstaller collects a folder runtime, a separate hook helper and a checksum-validated setup payload. Both Ukrainian and English user guides are bundled.

Use docs/PROTOCOL.md for the protocol and docs/ANTIGRAVITY.md for experimental hooks. Runtime files under .beacon, .cache and .tools, .env files, device credentials, account configuration and signing keys must never enter Git or a release. Release upload should use an explicit file list and be verified against SHA-256 checksums.
