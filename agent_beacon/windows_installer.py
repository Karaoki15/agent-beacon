"""Per-user beta installer. Payload is checked before any installed file moves."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile

from .desktop import VERSION, data_directory, powershell_quote, stop_owned, PROVIDERS
from .adapters import configure

MARKER = ".agent-beacon-install.json"
REGISTRY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentBeaconBeta"


def install_directory() -> Path:
    return Path(os.environ["LOCALAPPDATA"]).resolve() / "Programs/AgentBeacon"


def _safe_root(path: Path) -> Path:
    absolute = path.absolute()
    if absolute != path.resolve() or path.name != "AgentBeacon" or len(path.parts) < 3:
        raise ValueError("unsafe or redirected install directory")
    return path.resolve()


def _relative(value: str) -> Path:
    name = PurePosixPath(value)
    if (not value or name.is_absolute() or "\\" in value or ":" in value
            or any(p in {"", ".", ".."} or p.rstrip(". ") != p for p in name.parts)):
        raise ValueError("invalid payload path")
    return Path(*name.parts)


def unpack_checked(payload: Path, stage: Path, digest: str) -> dict[str, str]:
    if hashlib.sha256(payload.read_bytes()).hexdigest() != digest:
        raise ValueError("installer payload checksum mismatch")
    manifest = {}
    seen = set()
    with zipfile.ZipFile(payload) as archive:
        if len(archive.infolist()) > 5000 or sum(i.file_size for i in archive.infolist()) > 600 * 1024 * 1024:
            raise ValueError("installer payload exceeds bounds")
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = _relative(info.filename)
            key = str(relative).casefold()
            if key in seen or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("duplicate or linked payload entry")
            seen.add(key)
            target = stage / relative
            if not target.resolve().is_relative_to(stage.resolve()):
                raise ValueError("payload escapes staging directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            target.write_bytes(data)
            manifest[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    for required in ["AgentBeacon.exe", "AgentBeacon-Hooks.exe"]:
        if required not in manifest:
            raise ValueError("payload is missing an executable")
    return manifest


def _marker(root: Path) -> dict | None:
    try:
        value = json.loads((root / MARKER).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (not isinstance(value, dict) or value.get("app") != "Agent Beacon beta"
            or value.get("root") != str(root) or not isinstance(value.get("files"), dict)
            or len(value["files"]) > 5000):
        raise ValueError("unrecognized installation")
    for name, digest in value["files"].items():
        if not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid installed-file manifest")
        _relative(name)
    return value


def _powershell(code: str) -> None:
    import base64
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError("Windows shortcut operation failed")


def shortcuts(root: Path, desktop: bool, *, remove: bool = False) -> None:
    target = powershell_quote(str(root / "AgentBeacon.exe"))
    code = "$shell=New-Object -ComObject WScript.Shell; $target=" + target + "; "
    folders = ["Programs"] + (["Desktop"] if desktop else [])
    for folder in folders:
        code += (f"$path=Join-Path ([Environment]::GetFolderPath('{folder}')) 'Agent Beacon.lnk'; "
                 "$link=$shell.CreateShortcut($path); ")
        if remove:
            code += "if ((Test-Path -LiteralPath $path) -and $link.TargetPath -eq $target) { Remove-Item -LiteralPath $path }; "
        else:
            code += "$link.TargetPath=$target; $link.WorkingDirectory=Split-Path -Parent $target; $link.Save(); "
    _powershell(code)


def register(root: Path) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
        values = {"DisplayName":"Agent Beacon beta", "DisplayVersion":VERSION, "Publisher":"Agent Beacon",
                  "InstallLocation":str(root), "DisplayIcon":str(root / "AgentBeacon.exe"),
                  "UninstallString":subprocess.list2cmdline([str(root / "Uninstall-AgentBeacon.exe"), "--uninstall"])}
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def install(payload: Path, digest: str, target: Path, data: Path, *, desktop: bool, setup_executable: Path,
            integrate_windows: bool = True) -> None:
    target = _safe_root(target)
    if target.exists() and _marker(target) is None:
        raise ValueError("the selected folder is not an Agent Beacon installation")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / (".AgentBeacon-stage-" + uuid.uuid4().hex)
    stage.mkdir()
    previous = None
    try:
        files = unpack_checked(payload, stage, digest)
        shutil.copy2(setup_executable, stage / "Uninstall-AgentBeacon.exe")
        files["Uninstall-AgentBeacon.exe"] = hashlib.sha256((stage / "Uninstall-AgentBeacon.exe").read_bytes()).hexdigest()
        marker = {"app":"Agent Beacon beta", "version":VERSION, "root":str(target), "files":files}
        (stage / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
        stop_owned(data, target / "AgentBeacon.exe")
        if target.exists():
            previous = target.parent / (".AgentBeacon-previous-" + uuid.uuid4().hex)
            # Both paths are validated siblings in the explicitly selected install parent.
            target.replace(previous)
        try:
            stage.replace(target)
        except OSError:
            if previous is not None:
                previous.replace(target)
            raise
        if integrate_windows:
            shortcuts(target, desktop)
            register(target)
    finally:
        if stage.exists() and stage.resolve().parent == target.parent and stage.name.startswith(".AgentBeacon-stage-"):
            shutil.rmtree(stage)
    # The previous folder stays recoverable; no settings, pairings or hooks are deleted.


def uninstall(target: Path, data: Path, *, remove_hooks: bool, integrate_windows: bool = True) -> list[str]:
    target = _safe_root(target)
    marker = _marker(target)
    if marker is None:
        raise ValueError("no owned installation")
    stop_owned(data, target / "AgentBeacon.exe")
    if remove_hooks:
        configure.REPORTER_EXECUTABLE = target / "AgentBeacon-Hooks.exe"
        configure.uninstall_user(list(PROVIDERS), data / "hooks", apply=True)
    if integrate_windows:
        import winreg
        shortcuts(target, True, remove=True)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
            if location == str(target):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY)
        except FileNotFoundError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                value, _ = winreg.QueryValueEx(key, "AgentBeacon")
                if value == subprocess.list2cmdline([str(target / "AgentBeacon.exe"), "--no-browser"]):
                    winreg.DeleteValue(key, "AgentBeacon")
        except FileNotFoundError:
            pass
    retained = []
    for name, digest in marker["files"].items():
        path = target / _relative(name)
        if not path.resolve().is_relative_to(target) or path.is_symlink():
            raise ValueError("redirected installed file")
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                retained.append(name)
                continue
            path.unlink()
    (target / MARKER).unlink()
    for path in sorted(target.rglob("*"), key=lambda p:len(p.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(target):
            try:
                path.rmdir()
            except OSError:
                pass  # Unknown or modified files are deliberately retained.
    try:
        target.rmdir()
    except OSError:
        pass
    return retained


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--remove-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--verify-payload", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.verify_payload:
        bundle=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))
        with tempfile.TemporaryDirectory(prefix="AgentBeacon-verify-",dir=args.verify_payload.resolve().parent) as folder:
            files=unpack_checked(bundle/"payload.zip",Path(folder),(bundle/"payload.sha256").read_text().strip())
            import tkinter
            interpreter=tkinter.Tcl()
            args.verify_payload.write_text(json.dumps({"version":VERSION,"files":len(files),"tcl":interpreter.call('info','patchlevel')}))
        return 0
    import tkinter as tk
    from tkinter import ttk, messagebox
    target = _safe_root(args.remove_root or (Path(sys.executable).resolve().parent if args.uninstall else install_directory()))
    removing = args.uninstall or args.remove_root is not None
    if args.uninstall and args.remove_root is None:
        if _marker(target) is None:
            return 2
        copy_dir = Path(tempfile.mkdtemp(prefix="AgentBeacon-uninstall-"))
        helper = copy_dir / "AgentBeacon-Uninstall.exe"
        shutil.copy2(sys.executable, helper)
        subprocess.Popen([str(helper), "--remove-root", str(target)], creationflags=subprocess.CREATE_NO_WINDOW)
        return 0
    window = tk.Tk();window.title("Agent Beacon beta");window.geometry("640x600");window.minsize(600,560)
    window.configure(bg="#101010")
    style = ttk.Style();style.theme_use("clam")
    style.configure("TFrame", background="#101010");style.configure("TLabel", background="#101010",foreground="#eeeeee",font=("Segoe UI",11))
    style.configure("Title.TLabel",font=("Segoe UI",24,"bold"));style.configure("TCheckbutton",background="#101010",foreground="#eeeeee",font=("Segoe UI",10))
    style.configure("TButton",font=("Segoe UI",11),padding=10)
    frame=ttk.Frame(window,padding=30);frame.pack(fill="both",expand=True)
    # Reserve the action row first so wrapped text never hides Install/Cancel.
    row=ttk.Frame(frame);row.pack(fill="x",side="bottom")
    ttk.Label(frame,text="Видалення Agent Beacon" if removing else "Вітаємо в Agent Beacon",style="Title.TLabel",wraplength=500).pack(anchor="w",pady=(0,18))
    message=("Буде видалено лише файли цієї копії програми та її ярлики. Дані й підключення телефона залишаться. Змінені файли також буде збережено." if removing else
             "Статус Codex, Claude Code та Antigravity на комп’ютері й телефоні. Python і команди в терміналі не потрібні. Після встановлення відкриється майстер підключення агентів.")
    ttk.Label(frame,text=message,wraplength=490).pack(anchor="w",pady=(0,18))
    ttk.Label(frame,text=str(target),wraplength=490,font=("Segoe UI",9)).pack(anchor="w",pady=(0,18))
    extra=tk.BooleanVar(value=True)
    ttk.Checkbutton(frame,text="Прибрати лише хуки цієї копії (з резервною копією)" if removing else "Створити ярлик на робочому столі",variable=extra).pack(anchor="w",pady=5)
    ttk.Label(frame,text="Бета-версія. Windows та Android; Antigravity — експериментально.\nМережа й автозапуск вмикаються окремо в майстрі.",wraplength=490,font=("Segoe UI",9)).pack(anchor="w",pady=15)
    status=tk.StringVar(value="");ttk.Label(frame,textvariable=status,wraplength=490).pack(anchor="w",pady=8)
    progress=ttk.Progressbar(frame,mode="indeterminate");progress.pack(fill="x",pady=8)
    cancel=ttk.Button(row,text="Скасувати",command=window.destroy);cancel.pack(side="right",padx=5)
    guide=ttk.Button(row,text="Інструкція",command=lambda:os.startfile(target/"USER-GUIDE-UK.txt"),
                     state="normal" if (target/"USER-GUIDE-UK.txt").is_file() else "disabled")
    guide.pack(side="left")
    def run():
        choice=extra.get();start.config(state="disabled");cancel.config(state="disabled");window.protocol("WM_DELETE_WINDOW",lambda:None)
        progress.start();status.set("Зачекайте, перевіряємо файли…")
        def work():
            try:
                if removing:
                    retained=uninstall(target,data_directory(),remove_hooks=choice)
                    text="Програму видалено. Дані підключення збережено." + (" Змінені файли залишено в папці програми." if retained else "")
                else:
                    bundle=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))
                    payload=bundle/"payload.zip";digest=(bundle/"payload.sha256").read_text().strip()
                    install(payload,digest,target,data_directory(),desktop=choice,setup_executable=Path(sys.executable))
                    subprocess.Popen([str(target/"AgentBeacon.exe")],cwd=target,creationflags=subprocess.CREATE_NO_WINDOW)
                    text="Встановлено. Майстер відкриється у браузері; Beacon працює в треї."
                window.after(0,lambda:done(text))
            except Exception as error:
                # Never show raw provider config/parser errors, which may include secrets.
                kind=type(error).__name__
                window.after(0,lambda:failed(kind))
        threading.Thread(target=work,daemon=True).start()
    def done(text):
        progress.stop();status.set(text);cancel.config(text="Готово",state="normal");window.protocol("WM_DELETE_WINDOW",window.destroy)
        guide.config(state="normal" if (target/"USER-GUIDE-UK.txt").is_file() else "disabled")
    def failed(kind):
        progress.stop();status.set("Дію не завершено ("+kind+"). Закрийте Beacon у треї та спробуйте знову. Ваші дані збережено.");cancel.config(state="normal");start.config(state="normal");window.protocol("WM_DELETE_WINDOW",window.destroy)
    start=ttk.Button(row,text="Видалити" if removing else "Встановити й відкрити",command=run);start.pack(side="right")
    window.mainloop()
    return 0
