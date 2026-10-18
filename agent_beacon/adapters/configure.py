"""Generate reviewable project-local hook configuration examples."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

CLAUDE_ELICITATION_MIN = (2, 1, 76)
REPORTER_EXECUTABLE: Path | None = None


def _reporter_executable() -> Path:
    if REPORTER_EXECUTABLE is not None:
        return REPORTER_EXECUTABLE.resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("AgentBeacon-Hooks.exe")
    return Path(sys.executable).resolve()


def _arguments(provider: str, state_dir: Path) -> list[str]:
    script = Path(__file__).with_name("reporter.py").resolve()
    prefix = [] if getattr(sys, "frozen", False) else [str(script)]
    return [*prefix, provider, "--state-dir", str(state_dir)]


def _codex_command(provider: str, state_dir: Path) -> dict[str, str]:
    executable = str(_reporter_executable())
    args = [executable, *_arguments(provider, state_dir)]
    if sys.platform == "win32":
        def ps_quote(value: str) -> str:
            return "'" + value.replace("\\", "/").replace("'", "''") + "'"

        windows_command = "& " + " ".join(ps_quote(value) for value in args)
        return {"command": shlex.join(args), "commandWindows": windows_command}
    return {"command": shlex.join(args)}


def _claude_handler(provider: str, state_dir: Path) -> dict[str, object]:
    # Claude's command + args form executes directly and avoids shell quoting.
    return {"type": "command", "command": str(_reporter_executable()),
            "args": _arguments(provider, state_dir), "timeout": 3}


def _claude_version() -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True,
                                timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout)
    return tuple(map(int, match.groups())) if match else None


def _codex_config(provider: str, state_dir: Path) -> dict:
    events = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest",
              "PreToolUse", "PostToolUse", "Stop", "Interrupt"]
    handler = {"type": "command", **_codex_command(provider, state_dir), "timeout": 3}
    return {"hooks": {name: [{"hooks": [handler]}] for name in events}}


def _codex_notify_command(state_dir: Path, backup_path: Path) -> list[str]:
    script = Path(__file__).with_name("reporter.py").resolve()
    return [str(_reporter_executable()), *_arguments("codex-notify", state_dir.resolve()),
            "--notify-backup", str(backup_path.resolve())]


def _codex_notify_config(state_dir: Path) -> str:
    script = Path(__file__).with_name("reporter.py").resolve()
    command = [str(_reporter_executable()), *_arguments("codex-notify", state_dir.resolve())]
    return "notify = " + json.dumps(command, ensure_ascii=False) + "\n"


def _same_path(left: object, right: Path) -> bool:
    if not isinstance(left, str):
        return False
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except (OSError, ValueError):
        return False


def _legacy_source_command(parts: object, provider: str) -> bool:
    """Migrate only identifiable Beacon source hooks, not arbitrary callbacks."""
    if not isinstance(parts, list) or len(parts) < 5 or not all(isinstance(p, str) for p in parts):
        return False
    script = Path(parts[1])
    if (not script.is_absolute() or script.name != "reporter.py" or script.parent.name != "adapters"
            or script.parent.parent.name != "agent_beacon" or parts[2:4] != [provider, "--state-dir"]
            or not Path(parts[4]).is_absolute()):
        return False
    try:
        with script.open("rb") as source:
            return source.read(100).startswith(b'"""Fail-open command hook. Persist only bounded lifecycle metadata."""')
    except OSError:
        return False


def _managed_handler(handler: object, provider: str) -> bool:
    if not isinstance(handler, dict):
        return False
    reporter = Path(__file__).with_name("reporter.py").resolve()
    executable = Path(sys.executable).resolve()
    args = handler.get("args")
    if getattr(sys, "frozen", False):
        if isinstance(args, list) and len(args) == 4 and _legacy_source_command([handler.get("command"), *args], provider):
            return True
        if provider == "claude":
            return (_same_path(handler.get("command"), _reporter_executable()) and isinstance(args, list)
                    and len(args) == 3 and args[:2] == [provider, "--state-dir"] and isinstance(args[2], str))
        try:
            parts = shlex.split(handler.get("command", ""))
        except (ValueError, TypeError):
            return False
        if len(parts) == 5 and _legacy_source_command(parts, provider):
            return True
        return (provider == "codex" and len(parts) == 4 and _same_path(parts[0], _reporter_executable())
                and parts[1:3] == [provider, "--state-dir"] and bool(parts[3]))
    if isinstance(args, list):
        return (provider == "claude" and _same_path(handler.get("command"), executable)
                and len(args) == 4 and _same_path(args[0], reporter) and args[1] == provider
                and args[2] == "--state-dir" and isinstance(args[3], str))
    command = handler.get("command")
    if provider != "codex" or not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return (len(parts) == 5 and _same_path(parts[0], executable)
            and _same_path(parts[1], reporter) and parts[2] == provider
            and parts[3] == "--state-dir" and bool(parts[4]))


def _merge_hooks(existing: dict, provider: str, state_dir: Path) -> dict:
    merged = json.loads(json.dumps(existing))
    if provider == "codex":
        new_hooks = _codex_config(provider, state_dir)["hooks"]
    else:
        include_elicitation = (_claude_version() or (0, 0, 0)) >= CLAUDE_ELICITATION_MIN
        new_hooks = _claude_config(provider, state_dir,
                                   include_elicitation=include_elicitation)["hooks"]
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")
    for event, matchers in new_hooks.items():
        event_hooks = hooks.setdefault(event, [])
        if not isinstance(event_hooks, list):
            raise ValueError(f"hooks.{event} must be a JSON array")
        replacement = (_codex_config(provider, state_dir)["hooks"][event][0]["hooks"][0]
                       if provider == "codex" else _claude_handler(provider, state_dir))
        notification_event = provider == "claude" and event == "Notification"
        if notification_event:
            found_idle_prompt = False
            for matcher in event_hooks:
                if not isinstance(matcher, dict) or not isinstance(matcher.get("hooks", []), list):
                    continue
                is_idle_prompt = matcher.get("matcher") == "idle_prompt"
                retained = []
                for handler in matcher.get("hooks", []):
                    if _managed_handler(handler, provider):
                        if is_idle_prompt and not found_idle_prompt:
                            retained.append(replacement)
                            found_idle_prompt = True
                    else:
                        retained.append(handler)
                matcher["hooks"] = retained
            if not found_idle_prompt:
                event_hooks.append({"matcher": "idle_prompt", "hooks": [replacement]})
            continue
        found = False
        for matcher in event_hooks:
            if not isinstance(matcher, dict) or not isinstance(matcher.get("hooks", []), list):
                continue
            retained = []
            for handler in matcher.get("hooks", []):
                if _managed_handler(handler, provider):
                    if not found:
                        retained.append(replacement)
                        found = True
                else:
                    retained.append(handler)
            matcher["hooks"] = retained
        if not found:
            matcher_entry = {"hooks": [replacement]}
        if not found:
            event_hooks.append(matcher_entry)
    return merged


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "wb", closefd=True) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _atomic_replace_if_current(path: Path, expected: bytes | None, content: bytes) -> None:
    if _read_bytes(path) != expected:
        raise ValueError(f"settings changed during install; refusing to replace {path}")
    _atomic_replace(path, content)


def _backup_path(path: Path, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    return path.with_name(f"{path.name}.agent-beacon.{digest}.bak")


def _notify_backup_reference(command: object) -> Path | None:
    if not _is_beacon_notify(command) or not isinstance(command, list) or "--notify-backup" not in command:
        return None
    return Path(command[-1])


def _is_beacon_notify(command: object) -> bool:
    if getattr(sys, "frozen", False):
        if (isinstance(command, list) and len(command) in {5, 7}
                and _legacy_source_command(command, "codex-notify")
                and (len(command) == 5 or (command[5] == "--notify-backup"
                     and isinstance(command[6], str) and Path(command[6]).is_absolute()))):
            return True
        return (isinstance(command, list) and len(command) in {4, 6}
                and _same_path(command[0], _reporter_executable())
                and command[1:3] == ["codex-notify", "--state-dir"]
                and isinstance(command[3], str) and Path(command[3]).is_absolute()
                and (len(command) == 4 or (command[4] == "--notify-backup"
                     and isinstance(command[5], str) and Path(command[5]).is_absolute())))
    script = Path(__file__).with_name("reporter.py").resolve()
    return (isinstance(command, list) and len(command) in {5, 7}
            and _same_path(command[0], Path(sys.executable).resolve())
            and _same_path(command[1], script) and command[2] == "codex-notify"
            and command[3] == "--state-dir" and isinstance(command[4], str)
            and Path(command[4]).is_absolute()
            and ((len(command) == 5) or (command[5] == "--notify-backup"
                 and isinstance(command[6], str) and Path(command[6]).is_absolute())))


def _validate_backup_reference(path: Path, backup_path: Path, backup_bytes: bytes) -> None:
    expected_path = _backup_path(path, backup_bytes)
    if (os.path.normcase(os.path.abspath(backup_path))
            != os.path.normcase(os.path.abspath(expected_path))):
        raise ValueError("managed Codex notify backup reference does not match its exact snapshot")
    if os.path.normcase(os.path.abspath(backup_path.parent)) != os.path.normcase(os.path.abspath(path.parent)):
        raise ValueError("managed Codex notify backup must remain beside the user config")
    try:
        config = tomllib.loads(backup_bytes.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("managed Codex notify backup is not valid TOML") from error
    old_notify = config.get("notify")
    if old_notify is not None:
        if (not isinstance(old_notify, list) or not old_notify
                or any(not isinstance(item, str) or not item for item in old_notify)):
            raise ValueError("Codex callback backup has an invalid notify command")
        if _is_beacon_notify(old_notify):
            raise ValueError("Codex callback backup contains a recursive Beacon notify")


def _consume_toml_value(text: str, start: int) -> int:
    """Return the end of one TOML value, including its terminating newline."""
    quote: str | None = None
    escaped = False
    square = 0
    curly = 0
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if quote in {"\"\"\"", "'''"}:
                if quote == '"""' and char == "\\":
                    index += 2
                    continue
                if text.startswith(quote, index):
                    quote = None
                    index += 3
                    continue
                index += 1
                continue
            if quote == '"' and escaped:
                escaped = False
                index += 1
                continue
            if quote == '"' and char == "\\":
                escaped = True
                index += 1
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            if square == 0 and curly == 0:
                return len(text) if newline < 0 else newline + 1
            if newline < 0:
                return len(text)
            index = newline + 1
            continue
        if char in {"'", '"'}:
            if text.startswith(char * 3, index):
                quote = char * 3
                index += 3
            else:
                quote = char
                index += 1
            continue
        if char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        elif char == "\n" and square == 0 and curly == 0:
            return index + 1
        index += 1
    return len(text)


def _scan_toml_fragment(fragment: str, state: dict[str, object]) -> None:
    quote = state["quote"]
    square = int(state["square"])
    curly = int(state["curly"])
    escaped = False
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if quote is not None:
            delimiter = str(quote)
            if delimiter in {'"""', "'''"}:
                if delimiter == '"""' and char == "\\":
                    index += 2
                    continue
                if fragment.startswith(delimiter, index):
                    quote = None
                    index += 3
                    continue
                index += 1
                continue
            if delimiter == '"' and escaped:
                escaped = False
                index += 1
                continue
            if delimiter == '"' and char == "\\":
                escaped = True
                index += 1
                continue
            if char == delimiter:
                quote = None
            index += 1
            continue
        if char == "#":
            newline = fragment.find("\n", index)
            if newline < 0:
                break
            index = newline + 1
            continue
        if char in {"'", '"'}:
            if fragment.startswith(char * 3, index):
                quote = char * 3
                index += 3
            else:
                quote = char
                index += 1
            continue
        if char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        index += 1
    state.update(quote=quote, square=square, curly=curly)


def _find_root_notify_span(text: str) -> tuple[int, int] | None:
    """Locate only an actual root-level notify assignment, skipping strings/comments."""
    state: dict[str, object] = {"quote": None, "square": 0, "curly": 0}
    root = True
    offset = 0
    for line in text.splitlines(keepends=True):
        neutral = state["quote"] is None and state["square"] == 0 and state["curly"] == 0
        if root and neutral:
            header = re.match(r"^[ \t]*\[", line)
            if header:
                root = False
            else:
                assignment = re.match(r"^[ \t]*notify[ \t]*=", line)
                if assignment:
                    value_start = offset + assignment.end()
                    return offset, _consume_toml_value(text, value_start)
        _scan_toml_fragment(line, state)
        offset += len(line)
    return None


def _merge_codex_toml(original: str, command: list[str]) -> str:
    parsed = tomllib.loads(original)
    current = parsed.get("notify")
    if current is None:
        newline = "\r\n" if "\r\n" in original else "\n"
        updated = "notify = " + json.dumps(command, ensure_ascii=False) + newline + original
    else:
        span = _find_root_notify_span(original)
        if span is None:
            raise ValueError("cannot safely locate Codex root notify assignment")
        start, end = span
        newline = "\r\n" if "\r\n" in original else "\n"
        replacement = "notify = " + json.dumps(command, ensure_ascii=False)
        if end > start and original[start:end].endswith(("\n", "\r")):
            replacement += newline
        updated = original[:start] + replacement + original[end:]
    if current == command:
        return original
    if current is not None and (not isinstance(current, list)
                                or any(not isinstance(item, str) for item in current)):
        raise ValueError("Codex notify must be an array of command strings")
    updated_config = tomllib.loads(updated)
    if updated_config.get("notify") != command:
        raise ValueError("Codex notify merge did not produce the requested root value")
    if {key: value for key, value in parsed.items() if key != "notify"} != {
            key: value for key, value in updated_config.items() if key != "notify"}:
        raise ValueError("Codex notify merge changed another parsed setting")
    return updated


def install_user(providers: list[str], state_dir: Path, *, apply: bool) -> list[str]:
    """Plan or apply a reversible merge into user-level provider settings."""
    home = Path.home()
    plans: list[tuple[Path, bytes | None, bytes]] = []
    planned: dict[Path, tuple[bytes | None, bytes]] = {}
    changed_paths: list[Path] = []

    def prepare(path: Path, original: bytes | None, updated: bytes, *, backup_original: bool = False) -> None:
        if original == updated:
            return
        if backup_original:
            snapshot = original if original is not None else b""
            prepare_backup(path, snapshot)
        normalized = path.resolve()
        existing_plan = planned.get(normalized)
        if existing_plan is not None:
            if existing_plan != (original, updated):
                raise ValueError(f"conflicting install plans for {path}")
            return
        planned[normalized] = (original, updated)
        plans.append((path, original, updated))
        changed_paths.append(path)

    def prepare_backup(path: Path, snapshot: bytes) -> None:
        backup_path = _backup_path(path, snapshot)
        current = _read_bytes(backup_path)
        if current is not None:
            if current != snapshot:
                raise ValueError(f"backup path collision for {path}")
            return
        prepare(backup_path, None, snapshot)

    if "codex" in providers:
        codex_dir = home / ".codex"
        hooks_path = codex_dir / "hooks.json"
        hooks_raw = _read_bytes(hooks_path)
        hooks_input = hooks_raw if hooks_raw is not None else b"{}\n"
        hooks = json.loads(hooks_input.decode("utf-8"))
        if not isinstance(hooks, dict):
            raise ValueError("Codex hooks.json must contain a JSON object")
        merged_hooks = _merge_hooks(hooks, "codex", state_dir)
        prepare(hooks_path, hooks_raw, (json.dumps(merged_hooks, indent=2) + "\n").encode("utf-8"),
                backup_original=True)

        config_path = codex_dir / "config.toml"
        config_raw = _read_bytes(config_path)
        config_text = (config_raw or b"").decode("utf-8")
        current_config = tomllib.loads(config_text)
        current_notify = current_config.get("notify")
        if _is_beacon_notify(current_notify):
            backup_path = _notify_backup_reference(current_notify)
            if backup_path is None:
                raise ValueError("managed Codex notify is missing its backup reference")
            backup_raw = _read_bytes(backup_path)
            if backup_raw is None:
                raise ValueError("managed Codex notify backup is missing")
            _validate_backup_reference(config_path, backup_path, backup_raw)
        else:
            backup_raw = config_raw if config_raw is not None else b""
            backup_path = _backup_path(config_path, backup_raw)
            prepare_backup(config_path, backup_raw)
        merged_config = _merge_codex_toml(
            config_text, _codex_notify_command(state_dir, backup_path)
        )
        prepare(config_path, config_raw, merged_config.encode("utf-8"))
    if "claude" in providers:
        claude_path = home / ".claude" / "settings.json"
        settings_raw = _read_bytes(claude_path)
        settings_input = settings_raw if settings_raw is not None else b"{}\n"
        settings = json.loads(settings_input.decode("utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("Claude settings.json must contain a JSON object")
        merged = _merge_hooks(settings, "claude", state_dir)
        prepare(claude_path, settings_raw, (json.dumps(merged, indent=2) + "\n").encode("utf-8"),
                backup_original=True)
    if "antigravity" in providers:
        path = home / ".gemini" / "config" / "hooks.json"
        original = _read_bytes(path)
        existing = json.loads((original or b"{}").decode("utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("Antigravity hooks must be an object")
        managed = _antigravity_config(state_dir)
        key = "agent-beacon-status"
        # Own one named definition and retain other hook definitions untouched.
        if key in existing and existing[key] != managed[key] and not _managed_antigravity_definition(existing[key]):
            raise ValueError("Antigravity agent-beacon-status already exists with different settings; merge the generated example manually")
        existing.update(managed)
        prepare(path, original, (json.dumps(existing, indent=2) + "\n").encode("utf-8"), backup_original=True)

    if apply:
        # Validate the complete reviewed snapshot before the first write, then
        # repeat each check immediately before its atomic replacement.
        for path, expected, _ in plans:
            if _read_bytes(path) != expected:
                raise ValueError(f"settings changed during install; refusing to replace {path}")
        for path, expected, content in plans:
            _atomic_replace_if_current(path, expected, content)
    return [f"{'Would update' if not apply else 'Updated'} {path}" for path in changed_paths]


def _claude_config(provider: str, state_dir: Path, *, include_elicitation: bool) -> dict:
    events = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest",
              "PreToolUse", "PostToolUse", "Stop"]
    if include_elicitation:
        events.extend(["Elicitation", "ElicitationResult"])
    handler = _claude_handler(provider, state_dir)
    hooks = {name: [{"hooks": [handler]}] for name in events}
    hooks["Notification"] = [{"matcher": "idle_prompt", "hooks": [handler]}]
    return {"hooks": hooks}


def _antigravity_config(state_dir: Path) -> dict:
    from .antigravity import EVENTS

    hooks: dict[str, object] = {}
    for event in sorted(EVENTS):
        args = [str(_reporter_executable()), *_arguments("antigravity", state_dir), "--event", event]
        if sys.platform == "win32":
            # A shell-neutral encoded invocation protects spaces, apostrophes,
            # dollars and backticks in installation paths on Windows shells.
            code = "& " + " ".join("'" + value.replace("\\", "/").replace("'", "''") + "'" for value in args)
            encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
            command = "powershell.exe -WindowStyle Hidden -NoProfile -NonInteractive -EncodedCommand " + encoded
        else:
            command = shlex.join(args)
        handler = {"type": "command", "command": command, "timeout": 3}
        hooks[event] = [{"matcher": ".*", "hooks": [handler]}] if event in {"PreToolUse", "PostToolUse"} else [handler]
    return {"agent-beacon-status": {"enabled": True, **hooks}}


def _managed_antigravity_definition(value: object) -> bool:
    from .antigravity import EVENTS
    if not isinstance(value, dict) or set(value) != EVENTS | {"enabled"} or value.get("enabled") is not True:
        return False
    for event in EVENTS:
        try:
            entries = value[event]
            if not isinstance(entries, list) or len(entries) != 1:
                return False
            handler = entries[0]
            if event in {"PreToolUse", "PostToolUse"}:
                if set(handler) != {"matcher", "hooks"} or handler["matcher"] != ".*" or len(handler["hooks"]) != 1:
                    return False
                handler = handler["hooks"][0]
            if set(handler) != {"type", "command", "timeout"} or handler["type"] != "command" or handler["timeout"] != 3:
                return False
            prefix = "powershell.exe -WindowStyle Hidden -NoProfile -NonInteractive -EncodedCommand "
            if not handler["command"].startswith(prefix):
                return False
            code = base64.b64decode(handler["command"][len(prefix):], validate=True).decode("utf-16-le")
            if not code.startswith("& "):
                return False
            matches = list(re.finditer(r"'((?:[^']|'')*)'", code[2:]))
            if not matches or any(code[2:][a.end():b.start()].strip() for a,b in zip(matches,matches[1:])) or code[2:][:matches[0].start()].strip() or code[2:][matches[-1].end():].strip():
                return False
            parts = [m.group(1).replace("''", "'") for m in matches]
            if parts[-2:] != ["--event", event]:
                return False
            args = parts[:-2]
            frozen = (len(args) == 4 and _same_path(args[0], _reporter_executable())
                      and args[1:3] == ["antigravity", "--state-dir"] and Path(args[3]).is_absolute())
            if not frozen and not (len(args) == 5 and _legacy_source_command(args, "antigravity")):
                return False
        except (KeyError, TypeError, ValueError, UnicodeError, IndexError):
            return False
    return True


def uninstall_user(providers: list[str], state_dir: Path, *, apply: bool) -> list[str]:
    """Remove only this installation's bindings; preserve unrelated edits."""
    plans = []
    expected = str(state_dir.resolve()).replace("\\", "/").lower()

    def owns(handler: object, provider: str) -> bool:
        if not _managed_handler(handler, provider):
            return False
        args = handler.get("args")
        if not isinstance(args, list):
            try:
                args = shlex.split(handler["command"])
            except (ValueError, KeyError):
                return False
        return any(str(value).replace("\\", "/").lower() == expected for value in args)

    home = Path.home()
    for provider in providers:
        path = home / (".codex/hooks.json" if provider == "codex" else ".claude/settings.json" if provider == "claude" else ".gemini/config/hooks.json")
        original = _read_bytes(path)
        if original is None:
            continue
        value = json.loads(original.decode("utf-8-sig"))
        updated = json.loads(json.dumps(value))
        if provider == "antigravity":
            current = updated.get("agent-beacon-status")
            generated = _antigravity_config(state_dir)["agent-beacon-status"]
            if current == generated:
                del updated["agent-beacon-status"]
        else:
            hooks = updated.get("hooks", {})
            if not isinstance(hooks, dict):
                raise ValueError("invalid hooks object")
            for matchers in hooks.values():
                if not isinstance(matchers, list):
                    raise ValueError("invalid hook list")
                for matcher in matchers:
                    if isinstance(matcher, dict) and isinstance(matcher.get("hooks"), list):
                        matcher["hooks"] = [h for h in matcher["hooks"] if not owns(h, provider)]
        if value != updated:
            plans.append((path, original, (json.dumps(updated, indent=2) + "\n").encode()))
        if provider == "codex":
            config_path = home / ".codex/config.toml"
            raw = _read_bytes(config_path)
            if raw is not None:
                text = raw.decode("utf-8")
                command = tomllib.loads(text).get("notify")
                if _is_beacon_notify(command) and any(str(p).replace("\\", "/").lower() == expected for p in command):
                    backup = _notify_backup_reference(command)
                    if backup is None:
                        raise ValueError("notify backup missing")
                    old_raw = backup.read_bytes()
                    _validate_backup_reference(config_path, backup, old_raw)
                    old_notify = tomllib.loads(old_raw.decode("utf-8")).get("notify")
                    if old_notify is None:
                        span = _find_root_notify_span(text)
                        if span is None:
                            raise ValueError("cannot locate notify")
                        new_text = text[:span[0]] + text[span[1]:]
                    else:
                        new_text = _merge_codex_toml(text, old_notify)
                    plans.append((config_path, raw, new_text.encode()))
    for path, original, updated in plans:
        if _read_bytes(path) != original:
            raise ValueError("settings changed; retry uninstall")
    if apply:
        for path, original, updated in plans:
            backup = _backup_path(path, original)
            existing = _read_bytes(backup)
            if existing is None:
                _atomic_replace_if_current(backup, None, original)
            elif existing != original:
                raise ValueError("backup conflict")
            _atomic_replace_if_current(path, original, updated)
    return [str(path) for path, _, _ in plans]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reviewable project-local Agent Beacon hook examples.")
    parser.add_argument("providers", nargs="+", choices=("codex", "claude", "antigravity"))
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "integrations" / "generated")
    parser.add_argument("--state-dir", type=Path, default=Path.cwd() / ".beacon")
    parser.add_argument("--install", action="store_true", help="plan a merge into user settings")
    parser.add_argument("--scope", choices=("user",), default="user")
    parser.add_argument("--apply", action="store_true", help="write the planned user settings changes")
    arguments = parser.parse_args(argv)
    providers = sorted(set(arguments.providers))
    output_dir = arguments.output_dir.resolve()
    state_dir = arguments.state_dir.resolve()
    if arguments.install:
        try:
            for message in install_user(providers, state_dir, apply=arguments.apply):
                print(message)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            print(f"Cannot prepare integration install: {error}", file=sys.stderr)
            return 2
        if not arguments.apply:
            print("Review the plan, then repeat with --apply to write the settings.")
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    if "antigravity" in providers:
        target = output_dir / "antigravity-hooks.json"
        target.write_text(json.dumps(_antigravity_config(state_dir), indent=2) + "\n", encoding="utf-8")
        print(f"Generated {target}; experimental App/CLI/IDE adapter, live validation required.")

    if "codex" in providers:
        config = _codex_config("codex", state_dir)
        target = output_dir / "codex-hooks.json"
        target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Generated {target}")
        notify_target = output_dir / "codex-notify.toml"
        notify_target.write_text(_codex_notify_config(state_dir), encoding="utf-8")
        print(f"Generated {notify_target}")

    if "claude" in providers:
        version = _claude_version()
        include_elicitation = version is not None and version >= CLAUDE_ELICITATION_MIN
        config = _claude_config("claude", state_dir, include_elicitation=include_elicitation)
        target = output_dir / "claude-settings-hooks.json"
        target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        if version is None:
            print(f"Generated {target}; Claude version was not detectable, so Elicitation hooks were omitted.")
        else:
            version_text = ".".join(map(str, version))
            if include_elicitation:
                print(f"Generated {target}; detected Claude Code {version_text}, including Elicitation hooks (introduced in 2.1.76).")
            else:
                print(f"Generated {target}; detected Claude Code {version_text}, omitting Elicitation hooks (requires 2.1.76+).")
    print("These files are examples. Review and merge them into the provider's project-local hook configuration; no global settings were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
