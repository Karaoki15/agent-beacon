"""Experimental metadata-only translation of Antigravity's documented hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

EVENTS = frozenset({"SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop"})
MAX_COUNTER = 4_503_599_627_370_495


def counter(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNTER else None


def metadata(payload: dict[str, Any], hook: str) -> dict[str, Any] | None:
    if not isinstance(hook, str) or hook not in EVENTS:
        return None
    # No transcript contents, tool arguments/results, model messages or error
    # text are read or persisted. Explicit transient runs are out of scope.
    if "transcriptPath" in payload and not payload.get("transcriptPath"):
        return None
    paths = payload.get("workspacePaths")
    project = None
    if isinstance(paths, list) and paths and isinstance(paths[0], str):
        project = Path(paths[0]).name[:160] or None
    result: dict[str, Any] = {"native_event": hook, "project_name": project}
    if hook in {"PreInvocation", "PostInvocation"}:
        invocation, steps = counter(payload.get("invocationNum")), counter(payload.get("initialNumSteps"))
        if invocation is None or steps is None:
            result["native_event"] = "Unconfirmed"
        else:
            result.update(invocation=invocation, steps=steps)
    elif hook in {"PreToolUse", "PostToolUse"}:
        step = counter(payload.get("stepIdx"))
        tool = payload.get("toolCall")
        if step is None or not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            result["native_event"] = "Unconfirmed"
        else:
            result.update(step=step, question=tool["name"] == "ask_question")
    elif hook == "Stop":
        execution, idle = counter(payload.get("executionNum")), payload.get("fullyIdle")
        if execution is None or not isinstance(idle, bool):
            result["native_event"] = "Unconfirmed"
        else:
            result.update(execution=execution, fully_idle=idle)
    return result


def transition(position: dict[str, Any], event: dict[str, Any]) -> tuple[str, str] | None:
    """Fence native progress duplicates; return lifecycle and resume reason.

    Antigravity has a conversation ID and native progress counters, rather
    than Codex's common turn ID. Do not pretend invocation completion is idle.
    """
    hook = event["native_event"]
    if hook == "SessionStart":
        return None if position else ("session_started", "unknown")
    if hook == "Unconfirmed":
        return "session_unknown", "unknown"
    terminal = position.get("terminal", False)
    if hook in {"PreInvocation", "PostInvocation"}:
        if terminal and hook == "PostInvocation":
            return None
        value = (event["steps"], event["invocation"])
        previous = position.get("invocation", (-1, -1))
        if value < previous or (value == previous and (hook == "PreInvocation" or terminal)):
            return None
        position["invocation"] = value
        position["terminal"] = False
        return "resumed", "unknown"
    if hook in {"PreToolUse", "PostToolUse"}:
        if terminal:
            return None
        step = event["step"]
        previous = position.get("step", -1)
        if step < previous:
            return None
        if step == previous and hook == "PreToolUse":
            return None
        if step == previous and hook == "PostToolUse" and position.get("tool_finished"):
            return None
        position.update(step=step, tool_finished=hook == "PostToolUse", terminal=False)
        if event["question"] and hook == "PreToolUse":
            position["question"] = step
            return "awaiting_user", "unknown"
        if event["question"] and hook == "PostToolUse" and position.get("question") == step:
            position.pop("question", None)
            return "resumed", "user"
        return "resumed", "unknown"
    if hook == "Stop":
        execution = event["execution"]
        previous = position.get("execution", -1)
        if execution < previous or (execution == previous and position.get("idle_confirmed")):
            return None
        position.update(execution=execution, idle_confirmed=event["fully_idle"], terminal=event["fully_idle"])
        if event["fully_idle"]:
            position.pop("question", None)
            return "response_completed", "unknown"
        return "resumed", "unknown"
    raise ValueError("unsupported native Antigravity event")
