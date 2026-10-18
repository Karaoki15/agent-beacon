"""Fail-open command hook. Persist only bounded lifecycle metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from agent_beacon.adapters.process import find_agent_process
from agent_beacon.core import safe_session_name

MAX_INPUT = 64 * 1024
MAX_QUEUE_FILES = 512
MAX_EVENT_BYTES = 4096
_sequence_locks_guard = threading.Lock()
_sequence_locks: dict[str, threading.Lock] = {}
_queue_locks_guard = threading.Lock()
_queue_locks: dict[str, threading.Lock] = {}
SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z", re.ASCII)
EVENT_MAP = {
    "antigravity": {},
    "codex": {
        "SessionStart": "session_started",
        "SessionEnd": "session_closed",
        "UserPromptSubmit": "user_prompt",
        # PermissionRequest is a tool-approval hook, not proof that the agent
        # asked the human a question. The question tool is handled in PreToolUse.
        "PermissionRequest": "tool_pre",
        "PreToolUse": "tool_pre",
        "PostToolUse": "tool_post",
        "Stop": "response_completed",
        "Interrupt": "turn_stopped",
    },
    "claude": {
        "SessionStart": "session_started",
        "SessionEnd": "session_closed",
        "UserPromptSubmit": "user_prompt",
        "PermissionRequest": "awaiting_user",
        "Elicitation": "awaiting_user",
        "ElicitationResult": "human_answer_seen",
        "PreToolUse": "tool_pre",
        "PostToolUse": "tool_post",
        "Stop": "response_completed",
        "Notification": "response_completed",
    },
}
CODEX_NOTIFY_TYPE = "agent-turn-complete"


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and SAFE_ID.fullmatch(value) else None


def _locked_next_sequence(state_dir: Path, provider: str) -> int:
    """Allocate a per-provider capture sequence shared by hook processes."""
    counter = state_dir / f"{provider}.sequence"
    lock_path = state_dir / f"{provider}.sequence.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _sequence_locks_guard:
        thread_lock = _sequence_locks.setdefault(key, threading.Lock())
    # Windows byte-range locks coordinate hook processes, but do not serialize
    # threads in one Python process. Keep both layers for the test runner and
    # any embedded caller that invokes the reporter more than once.
    with thread_lock, lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                current = int(counter.read_text(encoding="ascii"))
            except (OSError, ValueError):
                current = 0
            if current >= 9_007_199_254_740_991:
                raise OverflowError("capture sequence exhausted")
            current += 1
            temporary = counter.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(str(current), encoding="ascii")
            os.replace(temporary, counter)
            return current
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_queue(state_dir: Path, provider: str) -> Iterator[None]:
    """Serialize queue capacity checks and publication across hook processes."""
    lock_path = state_dir / f"{provider}.queue.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _queue_locks_guard:
        thread_lock = _queue_locks.setdefault(key, threading.Lock())
    with thread_lock, lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _publish_event(
    state_dir: Path, provider: str, sequence: int, content: bytes, *, queue_locked: bool = False,
) -> None:
    event_dir = state_dir / "events"
    event_dir.mkdir(parents=True, exist_ok=True)

    def publish() -> None:
        if len(list(event_dir.glob(f"{provider}-*.json"))) >= MAX_QUEUE_FILES:
            return
        filename = event_dir / f"{provider}-{sequence:016d}-{uuid.uuid4().hex}.json"
        fd, temporary_name = tempfile.mkstemp(prefix=".hook-", suffix=".tmp", dir=event_dir)
        try:
            with os.fdopen(fd, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, filename)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    if queue_locked:
        publish()
    else:
        with _locked_queue(state_dir, provider):
            publish()


def _capture(provider: str, state_dir: Path, payload: dict[str, Any]) -> None:
    if provider == "antigravity":
        _capture_antigravity(state_dir, payload)
        return
    if provider == "codex-notify":
        _capture_codex_notify(state_dir, payload)
        return
    event_name = payload.get("hook_event_name")
    if not isinstance(event_name, str) or event_name not in EVENT_MAP[provider]:
        return
    if provider == "claude" and event_name == "Notification":
        # This install uses Claude's documented delayed idle_prompt signal.
        # Correlate only with a native prompt_id; session-only guesses can
        # incorrectly complete a newer turn.
        if payload.get("notification_type") != "idle_prompt" or _safe_id(payload.get("prompt_id")) is None:
            return
    session_id = _safe_id(payload.get("session_id"))
    if session_id is None:
        return

    child_agent_id = _safe_id(payload.get("agent_id"))
    # Codex's SubagentStop carries the parent session id; do not treat it as the
    # parent's lifecycle. Claude child hooks are likewise excluded unless the
    # hook payload can be safely associated with an independently-started scope.
    if child_agent_id is not None:
        return

    turn_id = _safe_id(payload.get("turn_id")) if provider == "codex" else _safe_id(payload.get("prompt_id"))
    tracking_lost = provider == "claude" and event_name in {
        "UserPromptSubmit", "PermissionRequest", "Elicitation", "ElicitationResult",
        "PreToolUse", "PostToolUse", "Stop", "Interrupt",
    } and turn_id is None
    if event_name in {"UserPromptSubmit", "PermissionRequest", "Elicitation", "ElicitationResult", "PreToolUse", "PostToolUse", "Stop", "Interrupt", "Notification"} and turn_id is None and not tracking_lost:
        return
    tool_name = payload.get("tool_name")
    if tracking_lost:
        # Missing native correlation invalidates readiness; never invent a turn.
        mapped = "tracking_lost"
    elif event_name == "PreToolUse":
        question_tools = (
            {"request_user_input", "request_user_input_async"}
            if provider == "codex" else {"AskUserQuestion"}
        )
        mapped = "awaiting_user" if tool_name in question_tools else "tool_pre"
    elif event_name == "PostToolUse":
        # A tool returning is progress, not proof that a human supplied its
        # result. Only Claude ElicitationResult supplies that proof here.
        mapped = "tool_post"
        if provider == "codex" and tool_name in {"request_user_input", "request_user_input_async"}:
            response = payload.get("tool_response")
            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except (ValueError, TypeError):
                    response = None
            answers = response.get("answers") if isinstance(response, dict) else None
            # Inspect only the answer shape in memory. Never persist its keys
            # or text, and never treat async {accepted: true} as a human answer.
            if isinstance(answers, dict) and any(
                isinstance(item, dict) and isinstance(item.get("answers"), list)
                and any(isinstance(answer, str) and answer.strip() for answer in item["answers"])
                for item in answers.values()
            ):
                mapped = "human_answer_seen"
    elif event_name == "ElicitationResult":
        if payload.get("action") not in {"accept", "decline", "cancel"}:
            return
        mapped = "human_answer_seen"
    elif provider == "claude" and event_name == "Notification":
        mapped = "response_completed"
    else:
        mapped = EVENT_MAP[provider][event_name]

    cwd = payload.get("cwd")
    project_name = None
    if isinstance(cwd, str) and cwd:
        try:
            project_name = Path(cwd).name[:160] or None
        except (OSError, ValueError):
            project_name = None

    agent_process = find_agent_process(provider, os.getppid())
    parent_pid, created = agent_process if agent_process is not None else (None, None)
    runtime_path = state_dir / "runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        instance_id = _safe_id(runtime.get("instance_id")) if isinstance(runtime, dict) else None
        if instance_id is None:
            return
        event = {
            "instance_id": instance_id,
            "provider": provider,
            "event_name": mapped,
            "session_id": session_id,
            "turn_id": turn_id,
            "agent_id": child_agent_id,
            "question_id": (_safe_id(payload.get("elicitation_id")) if event_name in {"Elicitation", "ElicitationResult"}
                            else _safe_id(payload.get("tool_use_id")) if provider == "codex"
                            and tool_name in {"request_user_input", "request_user_input_async"} else None),
            "project_name": project_name,
            "session_name": safe_session_name(payload.get("session_title")),
            # A null native transcript identifies a non-persisted run. Do not
            # publish the path itself or inspect transcript contents.
            "session_persistent": (
                bool(payload["transcript_path"]) if isinstance(payload.get("transcript_path"), str)
                else False if "transcript_path" in payload and payload["transcript_path"] is None else None
            ) if provider == "codex" else None,
            "parent_pid": parent_pid,
            "parent_created": created,
            "resume_reason": "unknown",
            # Only a new interactive session before any input proves initial idle.
            # Resume/compact/missing prompt IDs must not fabricate readiness.
            "initial_idle": (provider == "claude" and event_name == "SessionStart"
                             and payload.get("source") == "startup"
                             and payload.get("prompt_id") is None),
            "captured_at": int(time.time()),
        }
        # Sequence allocation and publication share one cross-process lock.
        # Otherwise concurrent hooks can publish sequence 2 before sequence 1,
        # making the bridge treat the later event as a permanent sequence gap.
        with _locked_queue(state_dir, provider):
            sequence = _locked_next_sequence(state_dir, provider)
            event["sequence"] = sequence
            content = json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(content) > MAX_EVENT_BYTES:
                return
            _publish_event(state_dir, provider, sequence, content, queue_locked=True)
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return


def _capture_codex_notify(state_dir: Path, payload: dict[str, Any]) -> None:
    """Capture Codex's official end-of-turn notification using metadata only."""
    if payload.get("type") != CODEX_NOTIFY_TYPE:
        return
    session_id = _safe_id(payload.get("thread-id"))
    turn_id = _safe_id(payload.get("turn-id"))
    if session_id is None or turn_id is None:
        return

    project_name = None
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        try:
            project_name = Path(cwd).name[:160] or None
        except (OSError, ValueError):
            pass

    agent_process = find_agent_process("codex", os.getppid())
    parent_pid, created = agent_process if agent_process is not None else (None, None)
    runtime_path = state_dir / "runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        instance_id = _safe_id(runtime.get("instance_id")) if isinstance(runtime, dict) else None
        if instance_id is None:
            return
        event = {
            "instance_id": instance_id,
            "provider": "codex",
            "event_name": "response_completed",
            "session_id": session_id,
            "turn_id": turn_id,
            "agent_id": None,
            "question_id": None,
            "project_name": project_name,
            "parent_pid": parent_pid,
            "parent_created": created,
            "resume_reason": "unknown",
            "captured_at": int(time.time()),
        }
        with _locked_queue(state_dir, "codex"):
            sequence = _locked_next_sequence(state_dir, "codex")
            event["sequence"] = sequence
            content = json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(content) <= MAX_EVENT_BYTES:
                _publish_event(state_dir, "codex", sequence, content, queue_locked=True)
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return


def _forward_codex_notify(raw_argument: str, backup_path: Path | None) -> None:
    """Preserve a pre-existing Codex notify callback by direct argv dispatch."""
    if backup_path is None or not backup_path.is_file():
        return
    try:
        config = tomllib.loads(backup_path.read_text(encoding="utf-8"))
        command = config.get("notify")
        if command is None:
            return
        if (not isinstance(command, list) or not command
                or any(not isinstance(item, str) or not item for item in command)):
            return
        reporter_path = Path(__file__).resolve()
        from agent_beacon.adapters.configure import _is_beacon_notify
        if _is_beacon_notify(command):
            return
        try:
            recursive = (len(command) >= 3
                         and os.path.normcase(os.path.abspath(command[0]))
                         == os.path.normcase(os.path.abspath(sys.executable))
                         and os.path.normcase(os.path.abspath(command[1]))
                         == os.path.normcase(os.path.abspath(reporter_path))
                         and command[2] == "codex-notify")
        except (OSError, ValueError):
            recursive = False
        if recursive:
            return
        subprocess.run([*command, raw_argument], check=False)
    except Exception:
        # Preserve Codex's fail-open behavior without exposing payload or command details.
        return


def _capture_antigravity(state_dir: Path, payload: dict[str, Any]) -> None:
    from agent_beacon.adapters.antigravity import metadata

    session_id = _safe_id(payload.get("conversationId"))
    if session_id is None:
        return
    event = metadata(payload, payload.get("hook_event_name"))
    owner = find_agent_process("antigravity", os.getppid())
    if event is None or owner is None:
        return
    try:
        runtime = json.loads((state_dir / "runtime.json").read_text(encoding="utf-8"))
        instance_id = _safe_id(runtime.get("instance_id")) if isinstance(runtime, dict) else None
        if instance_id is None:
            return
        event.update(instance_id=instance_id, provider="antigravity", event_name="antigravity",
                     session_id=session_id, turn_id=None, parent_pid=owner[0], parent_created=owner[1])
        with _locked_queue(state_dir, "antigravity"):
            sequence = _locked_next_sequence(state_dir, "antigravity")
            event["sequence"] = sequence
            raw = json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(raw) <= MAX_EVENT_BYTES:
                _publish_event(state_dir, "antigravity", sequence, raw, queue_locked=True)
    except (OSError, ValueError, TypeError):
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("provider", choices=(*EVENT_MAP, "codex-notify"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--notify-backup", type=Path)
    parser.add_argument("notification", nargs="?")
    parser.add_argument("--event")
    provider = None
    try:
        arguments, extra_arguments = parser.parse_known_args(argv)
        provider = arguments.provider
        if provider == "codex-notify":
            value = arguments.notification
            if value is None and len(extra_arguments) == 1:
                value = extra_arguments[0]
            elif extra_arguments:
                return 0
            if isinstance(value, str):
                try:
                    parsed = json.loads(value) if len(value.encode("utf-8")) <= MAX_INPUT else None
                    if isinstance(parsed, dict):
                        _capture_codex_notify(Path(arguments.state_dir), parsed)
                except Exception:
                    # The chained user callback must still run if our capture fails.
                    pass
                _forward_codex_notify(value, arguments.notify_backup)
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict):
            if provider == "antigravity":
                payload["hook_event_name"] = arguments.event
            _capture(arguments.provider, Path(arguments.state_dir), payload)
    except (SystemExit, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        # Hooks must fail open and never leak payload data in diagnostics.
        pass
    finally:
        if provider == "codex":
            # Codex requires a JSON object for a successful Stop hook.
            try:
                sys.stdout.write("{}\n")
            except OSError:
                pass
        elif provider == "antigravity":
            # "allow" delegates to the normal permission policy; never auto-approve.
            output = {"decision": "allow"} if arguments.event == "PreToolUse" else {"decision": "stop"} if arguments.event == "Stop" else {}
            try:
                sys.stdout.write(json.dumps(output) + "\n")
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
