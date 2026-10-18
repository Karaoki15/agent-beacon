"""Per-user Windows runtime and local, explicit first-run setup actions."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from .adapters import configure
from .adapters.process import process_info
from .server import create_server
from .pairing import PairingManager, DeviceStoreError

VERSION = "0.2.0-beta.3"
PROVIDERS = ("codex", "claude", "antigravity")
NAMES = {"codex": "Codex", "claude": "Claude Code", "antigravity": "Antigravity"}
logger = logging.getLogger(__name__)


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ValueError("LOCALAPPDATA is unavailable")
    return Path(base).resolve() / "AgentBeacon"


def read_settings(directory: Path) -> dict:
    default = {"providers": [], "lan": False, "port": 8765, "onboarded": False}
    try:
        value = json.loads((directory / "desktop-settings.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    if (not isinstance(value, dict) or set(value) != set(default)
            or not isinstance(value["providers"], list) or len(value["providers"]) > 3
            or any(item not in PROVIDERS for item in value["providers"])
            or len(value["providers"]) != len(set(value["providers"]))
            or not isinstance(value["lan"], bool) or not isinstance(value["onboarded"], bool)
            or type(value["port"]) is not int or not 1024 <= value["port"] <= 65535):
        raise ValueError("invalid desktop settings; restore the last saved configuration")
    return value


def save_settings(directory: Path, value: dict) -> None:
    configure._atomic_replace(directory / "desktop-settings.json", (json.dumps(value, indent=2) + "\n").encode())


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def provider_paths(provider: str) -> list[Path]:
    home = Path.home()
    return {"codex": [home / ".codex/hooks.json", home / ".codex/config.toml"],
            "claude": [home / ".claude/settings.json"],
            "antigravity": [home / ".gemini/config/hooks.json"]}[provider]


def _fingerprint(providers: list[str]) -> str:
    digest = hashlib.sha256()
    for provider in providers:
        for path in provider_paths(provider):
            data = configure._read_bytes(path)
            digest.update(str(path).encode()); digest.update(b"missing" if data is None else b"present" + data)
    return digest.hexdigest()


def legacy_pairing(directory: Path) -> dict | None:
    candidates = set()
    for provider in ("codex", "claude"):
        try:
            raw = json.loads((configure._read_bytes(provider_paths(provider)[0]) or b"{}").decode("utf-8-sig"))
            for matchers in raw.get("hooks", {}).values():
                if not isinstance(matchers, list):
                    continue
                for matcher in matchers:
                    if not isinstance(matcher, dict) or not isinstance(matcher.get("hooks"), list):
                        continue
                    for handler in matcher["hooks"]:
                        if not isinstance(handler, dict):
                            continue
                        args = handler.get("args")
                        parts = [handler.get("command"), *args] if isinstance(args, list) else shlex.split(handler.get("command", ""))
                        if len(parts) == 5 and configure._legacy_source_command(parts, provider):
                            source_root = Path(parts[1]).parents[2]
                            candidates.add(source_root / ".beacon/windows/devices.json")
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    valid = []
    for path in candidates:
        if path.resolve() == (directory / "devices.json").resolve() or not path.is_file():
            continue
        try:
            count = len(PairingManager(path).devices())
            if count:
                valid.append({"path":str(path.resolve()), "count":count,
                              "digest":hashlib.sha256(path.read_bytes()).hexdigest()})
        except (OSError, DeviceStoreError):
            continue
    return valid[0] if len(valid) == 1 else None


def fallback_prompt(provider: str, directory: Path) -> str:
    helper = configure._reporter_executable()
    command = [str(helper), "--configure", provider, "--state-dir", str(directory / "hooks"), "--install"]
    if not getattr(sys, "frozen", False):
        command = [sys.executable, "-m", "agent_beacon.adapters.configure", *command[2:]]
    line = "& " + " ".join(powershell_quote(part) for part in command)
    return (f"Допоможи підключити {NAMES[provider]} до локального Agent Beacon.\n"
            "Спочатку переглянь план цієї команди PowerShell:\n" + line + "\n"
            "Поясни, які файли зміняться. Збережи всі сторонні хуки й налаштування; резервна копія обов'язкова. "
            "Після мого підтвердження повтори команду з --apply. Не змінюй .env, авторизацію, дозволи інструментів "
            "чи firewall. Не читай і не надсилай тексти чатів, транскрипти, токени або ключі. "
            "Якщо команда не підтримується, не вигадуй хуки — поясни причину. Потім підкажи, як відкрити новий "
            "локальний чат і надіслати короткий тестовий запит, щоб майстер Beacon отримав справжній сигнал.")


class DesktopController:
    def __init__(self, directory: Path, settings: dict, server, stop: threading.Event, restart: threading.Event):
        self.directory, self.settings, self.server = directory, dict(settings), server
        self.stop, self.restart = stop, restart
        self._lock = threading.Lock()
        self._plan: dict | None = None

    def status(self) -> dict:
        snapshot = self.server.core.snapshot()
        result = []
        for provider in PROVIDERS:
            paths = provider_paths(provider)
            detected = any(path.parent.exists() for path in paths) or shutil.which(provider) is not None
            configured = False
            try:
                raw = json.loads((configure._read_bytes(paths[0]) or b"{}").decode("utf-8-sig"))
                if provider == "antigravity":
                    configured = raw.get("agent-beacon-status") == configure._antigravity_config(self.directory / "hooks")["agent-beacon-status"]
                else:
                    hooks = raw.get("hooks", {})
                    expected = (configure._codex_config(provider, self.directory / "hooks")["hooks"]["Stop"][0]["hooks"][0]
                                if provider == "codex" else configure._claude_handler(provider, self.directory / "hooks"))
                    mandatory = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
                    configured = all(any(handler == expected
                                         for matcher in hooks.get(event, []) if isinstance(matcher, dict)
                                         for handler in matcher.get("hooks", []) if isinstance(matcher.get("hooks"), list))
                                     for event in mandatory)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            rows = [r for r in snapshot["sessions"] if r["source"] == provider]
            result.append({"id": provider, "name": NAMES[provider], "detected": detected,
                           "enabled": provider in self.settings["providers"], "configured": configured,
                           "signal_received": bool(rows), "active_chats": sum(r["active"] for r in rows),
                           "paths": [str(path) for path in paths], "prompt": fallback_prompt(provider, self.directory)})
        migration = legacy_pairing(self.directory) if not self.server.pairing.devices() else None
        return {"version": VERSION, "providers": result, "lan": self.settings["lan"], "port": self.server.server_port,
                "onboarded": self.settings["onboarded"], "data_directory": str(self.directory),
                "startup": startup_enabled(), "packaged": bool(getattr(sys, "frozen", False)),
                "paired_devices": len(self.server.pairing.devices()), "discovery": getattr(self.server, "discovery_active", False),
                "migration": {"path":migration["path"], "count":migration["count"]} if migration else None}

    def action(self, action: str, payload: dict) -> dict:
        with self._lock:
            if action == "plan":
                providers, lan = payload["providers"], payload["lan"]
                if (not isinstance(providers, list) or len(providers) > 3 or not isinstance(lan, bool)
                        or any(not isinstance(p, str) or p not in PROVIDERS for p in providers)
                        or len(set(providers)) != len(providers) or not isinstance(payload["import_pairing"], bool)):
                    raise ValueError("invalid agent selection")
                messages = configure.install_user(providers, self.directory / "hooks", apply=False)
                migration = legacy_pairing(self.directory) if payload["import_pairing"] else None
                if payload["import_pairing"] and (migration is None or self.server.pairing.devices()):
                    raise ValueError("no unique empty-registry migration available")
                if migration:
                    messages.append("Перенести підключення телефона: " + migration["path"] + " → " + str(self.directory / "devices.json"))
                self._plan = {"id": secrets.token_urlsafe(24), "providers": providers[:], "lan": lan,
                              "fingerprint": _fingerprint(providers), "expires": time.monotonic() + 300, "migration":migration}
                return {"plan_id": self._plan["id"], "changes": messages,
                        "restart": providers != self.settings["providers"] or lan != self.settings["lan"]}
            if action == "apply":
                plan, self._plan = self._plan, None
                if (plan is None or not isinstance(payload["plan_id"], str)
                        or not secrets.compare_digest(plan["id"], payload["plan_id"])
                        or time.monotonic() > plan["expires"] or _fingerprint(plan["providers"]) != plan["fingerprint"]):
                    raise ValueError("plan expired or settings changed; review again")
                if plan["migration"]:
                    path = Path(plan["migration"]["path"])
                    if hashlib.sha256(path.read_bytes()).hexdigest() != plan["migration"]["digest"]:
                        raise ValueError("source device registry changed; review again")
                    if self.server.pairing.devices():
                        raise ValueError("destination already contains paired devices")
                    PairingManager(path)
                changes = configure.install_user(plan["providers"], self.directory / "hooks", apply=True)
                if plan["migration"]:
                    self.server.pairing.import_into_empty_registry(path)
                updated = {**self.settings, "providers": plan["providers"], "lan": plan["lan"], "onboarded": True}
                save_settings(self.directory, updated)
                reload = updated["providers"] != self.settings["providers"] or updated["lan"] != self.settings["lan"]
                self.settings = updated
                if reload:
                    self.restart.set()
                    threading.Timer(0.5, self.stop.set).start()
                return {"saved": True, "restart": reload, "changes": changes}
            if action == "stop":
                threading.Timer(0.3, self.stop.set).start()
                return {"stopping": True}
            if action == "startup":
                if not isinstance(payload["enabled"], bool):
                    raise ValueError("invalid startup flag")
                set_startup(payload["enabled"])
                return {"startup": startup_enabled()}
            if action == "firewall":
                return {"requested": request_firewall(self.server.server_port)}
            raise ValueError("unknown setup action")


def startup_enabled() -> bool:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _ = winreg.QueryValueEx(key, "AgentBeacon")
        return value == subprocess.list2cmdline([sys.executable, "--no-browser"])
    except FileNotFoundError:
        return False


def set_startup(enabled: bool) -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise ValueError("startup is only supported by the installed EXE")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "AgentBeacon", 0, winreg.REG_SZ, subprocess.list2cmdline([sys.executable, "--no-browser"]))
        elif startup_enabled():
            winreg.DeleteValue(key, "AgentBeacon")


def request_firewall(port: int) -> bool:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise ValueError("firewall setup requires the installed EXE")
    import base64
    executable = powershell_quote(str(Path(sys.executable).resolve()))
    # The exact program only, Private profile and local subnet; ports can change.
    script = (f"$ErrorActionPreference='Stop'; $app={executable}; "
              "if (-not (Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Private')) { exit 3 }; "
              "foreach ($proto in @('TCP','UDP')) { "
              "$name='AgentBeacon-Beta-'+$proto+'-'+[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($app)).TrimEnd('='); "
              "if (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) { "
              "$ports=if ($proto -eq 'UDP') { '47665' } else { 'Any' }; "
              "New-NetFirewallRule -Name $name -DisplayName ('Agent Beacon beta '+$proto) -Direction Inbound "
              "-Action Allow -Program $app -Protocol $proto -Profile Private -RemoteAddress LocalSubnet "
              "-LocalPort $ports -EdgeTraversalPolicy Block | Out-Null } }")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    wrapper = ("$ErrorActionPreference='Stop'; $p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -Wait "
               "-ArgumentList '-NoProfile -NonInteractive -EncodedCommand " + encoded + "'; exit $p.ExitCode")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", wrapper],
                            capture_output=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0


def owned_instance(directory: Path, expected_executable: Path | None = None) -> dict | None:
    try:
        record = json.loads((directory / "desktop-runtime.json").read_text(encoding="utf-8"))
        info = process_info(record["pid"])
        if info is None or info[1] != record["created"]:
            return None
        if expected_executable is not None and not configure._same_path(record["executable"], expected_executable):
            raise ValueError("a different Beacon installation owns this data directory")
        if type(record["port"]) is not int or not 1024 <= record["port"] <= 65535:
            return None
        with urllib.request.urlopen(f"http://127.0.0.1:{record['port']}/api/control", timeout=2) as response:
            control = json.load(response)
        if record["instance_id"] != control["instance_id"]:
            return None
        return {**record, "viewer_token": control["viewer_token"]}
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        return None


def stop_owned(directory: Path, executable: Path) -> None:
    record = owned_instance(directory, executable)
    if record is None:
        return
    origin = f"http://127.0.0.1:{record['port']}"
    req = urllib.request.Request(origin + "/api/setup/stop", data=b"{}", headers={
        "Content-Type": "application/json", "Origin": origin, "Authorization": "Bearer " + record["viewer_token"]})
    urllib.request.urlopen(req, timeout=3).close()
    for _ in range(80):
        info = process_info(record["pid"])
        if info is None or info[1] != record["created"]:
            return
        time.sleep(0.1)
    raise ValueError("Beacon did not stop; close it from the tray and try again")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Beacon desktop beta")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    directory = (args.data_dir or data_directory()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "desktop.log", maxBytes=262144, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    lock = (directory / "desktop.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if lock.seek(0, 2) == 0:
                lock.write(b"0"); lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                record = owned_instance(directory, Path(sys.executable))
                if record and not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{record['port']}/setup.html")
                return 0 if record else 2
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        first_launch = True
        while True:
            settings = read_settings(directory)
            server = None
            ports = [settings["port"], *range(8765, 8816)]
            for port in dict.fromkeys(ports):
                try:
                    server = create_server("0.0.0.0" if settings["lan"] else "127.0.0.1", port,
                                           state_settle_seconds=1, device_store=directory / "devices.json")
                    break
                except OSError as error:
                    if getattr(error, "winerror", None) not in {10048, 10013} and error.errno not in {48, 98, 10048}:
                        raise
            if server is None:
                raise OSError("no free Beacon port")
            settings["port"] = server.server_port
            save_settings(directory, settings)
            stop, restart = threading.Event(), threading.Event()
            controller = DesktopController(directory, settings, server, stop, restart)
            server.desktop = controller
            bridge = discovery = None
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval":0.1}, daemon=True)
            try:
                if settings["providers"]:
                    from .adapters.bridge import HookBridge
                    bridge = HookBridge(server.core, settings["providers"], directory / "hooks")
                    bridge.start()
                if settings["lan"]:
                    from .discovery import LanDiscovery
                    discovery = LanDiscovery(server.pairing, server.server_port, server.core.instance_id)
                    try:
                        discovery.start(); server.discovery_active = True
                    except OSError:
                        discovery.close(); discovery = None
                        logger.warning("LAN discovery unavailable; check for another running Beacon")
                info = process_info(os.getpid())
                if info is None:
                    raise OSError("cannot verify own process identity")
                record = {"pid":os.getpid(), "created":info[1], "executable":str(Path(sys.executable).resolve()),
                          "port":server.server_port, "instance_id":server.core.instance_id}
                configure._atomic_replace(directory / "desktop-runtime.json", json.dumps(record).encode())
                thread.start()
                if not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{server.server_port}/setup.html" if not settings["onboarded"] or not first_launch
                                    else f"http://127.0.0.1:{server.server_port}/")
                first_launch = False
                if args.headless:
                    stop.wait()
                else:
                    from .tray import run_tray
                    run_tray(server.server_port, stop_event=stop)
            finally:
                if discovery: discovery.close()
                if bridge: bridge.close()
                if thread.is_alive(): server.shutdown(); thread.join(timeout=3)
                server.server_close()
            if not restart.is_set():
                return 0
    except Exception as error:
        logger.error("Desktop startup/action failed (%s)", type(error).__name__)
        if os.name == "nt" and not args.headless:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Не вдалося запустити Agent Beacon. Перевірте desktop.log у папці даних або закрийте іншу копію програми.", "Agent Beacon", 0x10)
        return 2
    finally:
        lock.close(); handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
