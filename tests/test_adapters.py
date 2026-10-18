from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_beacon.adapters import reporter
from agent_beacon.adapters import configure
from agent_beacon.adapters.bridge import HookBridge
from agent_beacon.adapters.process import process_info
from agent_beacon.core import BeaconCore


class HookAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary.name) / ".beacon"
        self.core = BeaconCore(question_reminder_seconds=60)
        self.identities = {"codex": ("codex.exe", "created-codex"),
                           "claude": ("claude.exe", "created-claude")}
        self.bridge = HookBridge(
            self.core, ["codex", "claude"], self.state_dir,
            identity_probe=lambda pid: self.identities.get("claude" if pid == 202 else "codex"),
        )

    def tearDown(self) -> None:
        self.bridge.close()
        self.temporary.cleanup()

    def emit(self, provider: str, payload: dict, *, pid: int | None = None) -> None:
        process_id = pid or (202 if provider == "claude" else 101)
        identity = self.identities[provider]
        with patch.object(reporter, "find_agent_process", return_value=(process_id, identity[1])):
            reporter._capture(provider, self.state_dir, payload)

    def poll(self) -> dict:
        self.bridge.poll_once()
        return self.core.snapshot()

    def test_codex_hook_files_drive_session_question_and_resume(self) -> None:
        base = {"session_id": "codex-session", "cwd": r"C:\private\project-a", "prompt": "secret prompt"}
        self.emit("codex", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        resumed = self.poll()
        self.assertEqual("working", resumed["task_state"])
        self.assertEqual("working", resumed["state"])
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-1",
                             "tool_name": "request_user_input"})
        self.assertEqual("awaiting_user", self.poll()["state"])
        observed = self.core._observed[("codex", "codex-session")]
        deadline = observed["_question_deadline"]
        self.assertIsNotNone(deadline)

        # Generic activity is not proof of a human answer.
        self.emit("codex", {**base, "hook_event_name": "PostToolUse", "turn_id": "turn-1",
                             "tool_name": "Bash", "tool_input": {"command": "secret"},
                             "tool_output": "secret"})
        resumed = self.poll()
        self.assertEqual("working", resumed["task_state"])
        self.assertEqual("awaiting_user", resumed["state"])
        self.assertEqual(deadline, observed["_question_deadline"])
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        self.assertIsNone(observed["_question_deadline"])

        # A tool response without structured answers is not human proof.
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-1",
                             "tool_name": "request_user_input", "tool_input": {"question": "private"}})
        self.assertEqual("awaiting_user", self.poll()["state"])
        updated_deadline = observed["_question_deadline"]
        self.emit("codex", {**base, "hook_event_name": "PostToolUse", "turn_id": "turn-1",
                             "tool_name": "request_user_input", "tool_output": "private answer"})
        resumed = self.poll()
        self.assertEqual("working", resumed["task_state"])
        self.assertEqual("awaiting_user", resumed["state"])
        self.assertEqual(updated_deadline, observed["_question_deadline"])

    def test_codex_exact_question_tool_names_wait_but_substring_lookalikes_do_not(self) -> None:
        base = {"cwd": r"C:\private\project"}
        question_tool_names = ("request_user_input", "request_user_input_async")
        for index, tool_name in enumerate(question_tool_names, start=1):
            session_id = f"question-session-{index}"
            self.emit("codex", {**base, "session_id": session_id, "hook_event_name": "UserPromptSubmit",
                                 "turn_id": f"turn-{index}"})
            self.poll()
            self.emit("codex", {**base, "session_id": session_id, "hook_event_name": "PreToolUse",
                                 "turn_id": f"turn-{index}", "tool_name": tool_name})
            snapshot = self.poll()
            row = next(item for item in snapshot["sessions"] if item["session_id"] == session_id)
            self.assertEqual("awaiting_user", row["state"])
            self.assertFalse(row["question_reminder_active"])

        self.emit("codex", {**base, "session_id": "ordinary-tool-session",
                             "hook_event_name": "UserPromptSubmit", "turn_id": "ordinary-turn"})
        self.poll()
        self.emit("codex", {**base, "session_id": "ordinary-tool-session",
                             "hook_event_name": "PreToolUse", "turn_id": "ordinary-turn",
                             "tool_name": "mcp__server__request_user_input_async"})
        snapshot = self.poll()
        row = next(item for item in snapshot["sessions"] if item["session_id"] == "ordinary-tool-session")
        self.assertEqual("working", row["state"])
        self.assertEqual("working", row["display_state"])
        self.assertFalse(row["question_reminder_active"])

    def test_claude_elicitation_answer_needs_matching_id_and_following_work(self) -> None:
        base = {"session_id": "claude-session", "cwd": "/private/project-b",
                "prompt_id": "prompt-1", "message": "private question"}
        self.emit("claude", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "UserPromptSubmit"})
        resumed = self.poll()
        self.assertEqual("working", resumed["task_state"])
        self.assertEqual("working", resumed["state"])
        self.emit("claude", {**base, "hook_event_name": "Elicitation", "elicitation_id": "elicitation-1",
                              "requested_schema": {"secret": "form"}})
        self.assertEqual("awaiting_user", self.poll()["state"])
        observed = self.core._observed[("claude", "claude-session")]
        deadline = observed["_question_deadline"]
        self.emit("claude", {**base, "hook_event_name": "ElicitationResult", "elicitation_id": "wrong-id",
                              "action": "accept", "content": {"secret": "answer"}})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "PostToolUse", "tool_name": "MCP tool"})
        resumed = self.poll()
        self.assertEqual("working", resumed["task_state"])
        self.assertEqual("awaiting_user", resumed["state"])
        self.assertEqual(deadline, observed["_question_deadline"])

        # A new explicit question invalidates old proof; only its matching
        # ElicitationResult followed by a tool event clears the reminder.
        self.emit("claude", {**base, "hook_event_name": "Elicitation", "elicitation_id": "elicitation-2"})
        self.assertEqual("awaiting_user", self.poll()["state"])
        self.emit("claude", {**base, "hook_event_name": "ElicitationResult", "elicitation_id": "elicitation-2",
                              "action": "accept", "content": {"secret": "answer"}})
        self.poll()
        self.assertEqual("awaiting_user", self.core.snapshot()["state"])
        self.emit("claude", {**base, "hook_event_name": "PostToolUse", "tool_name": "Read"})
        self.assertEqual("working", self.poll()["task_state"])
        self.assertIsNone(observed["_question_deadline"])

    def test_claude_idle_prompt_completes_only_the_correlated_prompt(self) -> None:
        base = {"session_id": "claude-session", "cwd": "/private/project-b"}
        self.emit("claude", {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "prompt-1"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "Elicitation", "prompt_id": "prompt-1",
                              "elicitation_id": "question-1"})
        self.assertEqual("awaiting_user", self.poll()["state"])
        observed = self.core._observed[("claude", "claude-session")]
        self.assertIsNotNone(observed["_question_deadline"])

        self.emit("claude", {**base, "hook_event_name": "Notification", "notification_type": "idle_prompt",
                              "prompt_id": "prompt-1", "message": "PRIVATE-NOTIFICATION"})
        serialized = next((self.state_dir / "events").glob("claude-*.json")).read_text(encoding="ascii")
        self.assertNotIn("PRIVATE-NOTIFICATION", serialized)
        completed = self.poll()
        self.assertEqual("ready", completed["state"])
        self.assertIsNone(observed["_question_deadline"])

        self.emit("claude", {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "prompt-2"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "Notification", "notification_type": "idle_prompt",
                              "prompt_id": "prompt-1"})
        self.poll()
        self.assertEqual("prompt-2", observed["turn_id"])
        self.assertEqual("working", observed["state"])

        self.emit("claude", {**base, "hook_event_name": "Notification", "notification_type": "idle_prompt"})
        self.assertFalse(list((self.state_dir / "events").glob("claude-*.json")))

    def test_provider_stop_completes_correlated_main_session_and_clears_reminder(self) -> None:
        cases = (
            ("codex", "turn_id", "turn-1"),
            ("claude", "prompt_id", "prompt-1"),
        )
        for provider, correlation_field, correlation_id in cases:
            with self.subTest(provider=provider):
                session_id = f"{provider}-stop-session"
                base = {"session_id": session_id, "cwd": r"C:\\private\\project"}
                start = {**base, "hook_event_name": "UserPromptSubmit", correlation_field: correlation_id}
                self.emit(provider, start)
                self.poll()
                question = {**base, "hook_event_name": "PreToolUse", correlation_field: correlation_id,
                            "tool_name": "request_user_input"}
                if provider == "claude":
                    question = {**base, "hook_event_name": "Elicitation", "prompt_id": correlation_id,
                                "elicitation_id": "question-1"}
                self.emit(provider, question)
                self.assertEqual("awaiting_user", self.poll()["state"])
                observed = self.core._observed[(provider, session_id)]
                self.assertIsNotNone(observed["_question_deadline"])

                stop = {**base, "hook_event_name": "Stop", correlation_field: correlation_id,
                        "stop_hook_active": False}
                self.emit(provider, stop)
                completed = self.poll()
                row = next(item for item in completed["sessions"] if item["session_id"] == session_id)
                self.assertEqual("ready", row["state"])
                self.assertTrue(row["available"])
                self.assertIsNone(observed["_question_deadline"])

                # Regular healthy bridge polls keep a confirmed idle session
                # green beyond the observer lease while its exact process lives.
                current_time = [self.core._sources[provider]["_monotonic_at"]]
                with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
                    for _ in range(4):
                        current_time[0] += 10
                        self.bridge.poll_once()
                        row = next(item for item in self.core.snapshot()["sessions"]
                                   if item["session_id"] == session_id)
                        self.assertEqual("ready", row["state"])
                        self.assertTrue(row["available"])

                # A subsequent prompt is a new turn and returns the session to working.
                next_turn = "turn-2" if provider == "codex" else "prompt-2"
                self.emit(provider, {**base, "hook_event_name": "UserPromptSubmit",
                                     correlation_field: next_turn})
                active = self.poll()
                row = next(item for item in active["sessions"] if item["session_id"] == session_id)
                self.assertEqual("working", row["state"])

    def test_stop_does_not_complete_missing_child_or_stale_turn(self) -> None:
        base = {"session_id": "codex-session", "cwd": r"C:\\private\\project"}
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-2"})
        self.poll()

        # An earlier turn's terminal signal cannot mark the current turn ready.
        self.emit("codex", {**base, "hook_event_name": "Stop", "turn_id": "turn-1"})
        stale = self.poll()
        self.assertEqual("working", stale["state"])
        self.assertEqual("turn-2", self.core._observed[("codex", "codex-session")]["turn_id"])

        # Missing correlation is dropped by the reporter.
        self.emit("codex", {**base, "hook_event_name": "Stop"})
        self.assertFalse(list((self.state_dir / "events").glob("codex-*.json")))

        # Subagent completion is excluded, so the main turn remains working.
        self.emit("codex", {**base, "hook_event_name": "Stop", "turn_id": "turn-2",
                             "agent_id": "child-agent"})
        self.assertFalse(list((self.state_dir / "events").glob("codex-*.json")))
        self.assertEqual("working", self.core.snapshot()["state"])

    def test_stop_continuation_requires_user_prompt(self) -> None:
        base = {"session_id": "codex-session", "cwd": r"C:\\private\\project"}
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "Stop", "turn_id": "turn-1",
                             "stop_hook_active": False})
        self.assertEqual("ready", self.poll()["state"])

        # Neither delayed post-hooks nor late pre-hooks restart a stopped turn.
        self.emit("codex", {**base, "hook_event_name": "PostToolUse", "turn_id": "turn-1",
                             "tool_name": "Bash"})
        self.assertEqual("ready", self.poll()["state"])
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-1",
                             "tool_name": "Bash"})
        self.assertEqual("ready", self.poll()["state"])

        # Neither a late tool start from an earlier turn nor child activity
        # can change the current main-thread state.
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-2"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-1",
                             "tool_name": "Bash"})
        self.assertEqual("working", self.poll()["state"])
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-2",
                             "tool_name": "Bash", "agent_id": "child-agent"})
        self.assertFalse(list((self.state_dir / "events").glob("codex-*.json")))
        self.assertEqual("working", self.core.snapshot()["state"])

    def test_codex_interrupt_marks_only_matching_turn_ready_and_clears_question(self) -> None:
        base = {"session_id": "codex-session", "cwd": "C:\\project"}
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        observed = self.core._observed[("codex", "codex-session")]
        self.emit("codex", {**base, "hook_event_name": "PermissionRequest", "turn_id": "turn-1",
                             "tool_name": "Bash", "permission_mode": "default"})
        permission_check = self.poll()
        self.assertEqual("working", permission_check["state"])
        self.assertIsNone(observed["_question_deadline"])
        self.emit("codex", {**base, "hook_event_name": "PreToolUse", "turn_id": "turn-1",
                             "tool_name": "request_user_input"})
        self.poll()
        self.assertIsNotNone(observed["_question_deadline"])
        self.emit("codex", {**base, "hook_event_name": "Interrupt", "turn_id": "turn-1"})
        stopped = self.poll()
        self.assertEqual("ready", observed["state"])
        self.assertEqual("ready", stopped["state"])
        self.assertIsNone(observed["_question_deadline"])

        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-2"})
        self.poll()
        other = {"session_id": "codex-other", "cwd": "C:\\project"}
        self.emit("codex", {**other, "hook_event_name": "UserPromptSubmit", "turn_id": "other-turn"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "Interrupt", "turn_id": "turn-1"})
        self.poll()
        self.assertEqual("turn-2", observed["turn_id"])
        self.assertEqual("working", observed["state"])
        self.assertIsNone(observed["_question_deadline"])
        self.assertEqual("working", self.core._observed[("codex", "codex-other")]["state"])

    def test_codex_interrupt_cannot_bootstrap_unknown_turn_to_ready(self) -> None:
        base = {"session_id": "codex-session", "cwd": "C:\\project"}
        self.emit("codex", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "Interrupt", "turn_id": "unseen-turn"})
        snapshot = self.poll()
        observed = self.core._observed[("codex", "codex-session")]
        self.assertIsNone(observed["turn_id"])
        self.assertIsNone(observed["state"])
        self.assertFalse(snapshot["available"])

    def test_tool_progress_reconciles_midturn_and_after_observer_gap(self) -> None:
        base = {"session_id": "claude-session", "cwd": "/private/project-b",
                "prompt_id": "prompt-1"}
        self.emit("claude", {**base, "hook_event_name": "PostToolUse", "tool_name": "Read"})
        first = self.poll()
        self.assertEqual("working", first["task_state"])
        observed = self.core._observed[("claude", "claude-session")]
        self.assertEqual("prompt-1", observed["turn_id"])

        self.bridge._lose_continuity("claude")
        self.assertIsNone(self.core.snapshot()["sessions"][0]["state"])
        self.emit("claude", {**base, "hook_event_name": "PostToolUse", "tool_name": "Read"})
        recovered = self.poll()
        self.assertEqual("working", recovered["task_state"])
        self.assertEqual("working", self.core._observed[("claude", "claude-session")]["state"])

    def test_stale_elicitation_result_cannot_change_current_process_owner(self) -> None:
        self.identities["stale-claude"] = ("claude.exe", "created-stale")
        self.bridge.identity_probe = lambda pid: {
            101: self.identities["codex"],
            202: self.identities["claude"],
            303: self.identities["stale-claude"],
        }.get(pid)
        base = {"session_id": "claude-session", "cwd": "/private/project-b"}
        self.emit("claude", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "prompt-1"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "Elicitation", "prompt_id": "prompt-1",
                              "elicitation_id": "question-1"})
        self.poll()
        self.emit("claude", {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "prompt-2"})
        self.poll()
        owner_before = self.bridge._processes["claude"]["claude-session"]
        with patch.object(reporter, "find_agent_process", return_value=(303, "created-stale")):
            reporter._capture("claude", self.state_dir,
                              {**base, "hook_event_name": "ElicitationResult", "prompt_id": "prompt-1",
                               "elicitation_id": "question-1", "action": "accept"})
        self.poll()
        self.assertEqual(owner_before, self.bridge._processes["claude"]["claude-session"])
        current = self.core._observed[("claude", "claude-session")]
        self.assertEqual("prompt-2", current["turn_id"])
        self.assertEqual("working", current["state"])
        self.emit("claude", {**base, "hook_event_name": "PostToolUse", "prompt_id": "prompt-1",
                              "tool_name": "Read"})
        self.poll()
        self.assertEqual("prompt-2", current["turn_id"])
        self.assertEqual("working", current["state"])

    def test_payload_only_persists_allowlisted_metadata(self) -> None:
        payload = {
            "hook_event_name": "UserPromptSubmit", "session_id": "codex-session", "turn_id": "turn-1",
            "cwd": r"C:\private\client-name", "prompt": "TOP-SECRET-PROMPT",
            "last_assistant_message": "TOP-SECRET-ANSWER", "transcript_path": "C:\private\transcript.jsonl",
            "tool_input": {"password": "TOP-SECRET-TOOL-INPUT"},
        }
        self.emit("codex", payload)
        event_file = next((self.state_dir / "events").glob("codex-*.json"))
        serialized = event_file.read_text(encoding="ascii")
        self.assertNotIn("TOP-SECRET", serialized)
        self.assertNotIn("transcript", serialized)
        self.assertNotIn("C:\\private", serialized)
        event = json.loads(serialized)
        self.assertEqual("client-name", event["project_name"])

    def test_old_instance_event_is_discarded(self) -> None:
        self.emit("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "old-session",
                             "turn_id": "turn-1", "cwd": "C:\\old"})
        event_file = next((self.state_dir / "events").glob("codex-*.json"))
        event = json.loads(event_file.read_text(encoding="ascii"))
        event["instance_id"] = "previous-server-instance"
        event_file.write_text(json.dumps(event), encoding="ascii")
        self.poll()
        self.assertFalse(any(item["session_id"] == "old-session" for item in self.core.snapshot()["sessions"]))
        self.assertFalse(event_file.exists())

    def test_session_end_closes_known_session_after_its_process_exits(self) -> None:
        base = {"session_id": "codex-session", "cwd": "C:\\project"}
        self.emit("codex", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()

        self.emit("codex", {**base, "hook_event_name": "SessionEnd"})
        self.identities.pop("codex")
        snapshot = self.poll()

        observed = self.core._observed[("codex", "codex-session")]
        self.assertFalse(observed["active"])
        self.assertIsNone(observed["state"])
        self.assertFalse(any(item["session_id"] == "codex-session" and item["active"]
                             for item in snapshot["sessions"]))

    def test_stale_session_end_cannot_close_a_reopened_session_owner(self) -> None:
        self.identities["old-codex"] = ("codex.exe", "created-old")
        self.bridge.identity_probe = lambda pid: {
            101: self.identities.get("codex"),
            202: self.identities.get("claude"),
            303: self.identities.get("old-codex"),
        }.get(pid)
        base = {"session_id": "codex-session", "cwd": "C:\\project"}
        self.emit("codex", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()

        with patch.object(reporter, "find_agent_process", return_value=(303, "created-old")):
            reporter._capture("codex", self.state_dir,
                              {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-2"})
        self.poll()
        current = self.core._observed[("codex", "codex-session")]
        self.assertEqual("turn-2", current["turn_id"])
        self.assertEqual((303, "created-old"), self.bridge._processes["codex"]["codex-session"])

        with patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")):
            reporter._capture("codex", self.state_dir,
                              {**base, "hook_event_name": "SessionEnd"})
        self.poll()

        self.assertTrue(current["active"])
        self.assertEqual("turn-2", current["turn_id"])
        self.assertEqual("working", current["state"])
        self.assertEqual((303, "created-old"), self.bridge._processes["codex"]["codex-session"])

    def test_missing_turn_id_is_dropped_without_inventing_completion(self) -> None:
        base = {"session_id": "codex-session", "cwd": "C:\\project"}
        self.emit("codex", {**base, "hook_event_name": "SessionStart"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit"})
        self.poll()
        self.assertEqual(1, len(self.core.snapshot()["sessions"]))
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "Stop"})
        self.assertFalse(list((self.state_dir / "events").glob("codex-*.json")))
        self.assertEqual("working", self.core.snapshot()["state"])

    def test_codex_official_notify_completes_only_its_current_turn_without_text(self) -> None:
        base = {"session_id": "codex-session", "cwd": r"C:\private\project"}
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1"})
        self.poll()
        self.emit("codex", {**base, "hook_event_name": "UserPromptSubmit", "turn_id": "turn-2"})
        self.poll()

        notification = {
            "type": "agent-turn-complete", "thread-id": "codex-session", "turn-id": "turn-1",
            "cwd": r"C:\private\project", "input-messages": ["PRIVATE-PROMPT"],
            "last-assistant-message": "PRIVATE-ANSWER",
        }
        with patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")):
            reporter._capture_codex_notify(self.state_dir, notification)
        event_file = next((self.state_dir / "events").glob("codex-*.json"))
        serialized = event_file.read_text(encoding="ascii")
        self.assertNotIn("PRIVATE-", serialized)
        self.assertNotIn("input-messages", serialized)
        self.assertNotIn("last-assistant-message", serialized)
        self.poll()
        self.assertEqual("working", self.core.snapshot()["state"])
        self.assertEqual("turn-2", self.core._observed[("codex", "codex-session")]["turn_id"])

        notification["turn-id"] = "turn-2"
        with patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")):
            self.assertEqual(0, reporter.main(["codex-notify", "--state-dir", str(self.state_dir),
                                               json.dumps(notification)]))
        ready = self.poll()
        codex_session = next(item for item in ready["sessions"] if item["source"] == "codex")
        self.assertEqual("ready", codex_session["state"])
        self.assertEqual("ready", ready["state"])
        sources = {item["source"]: item for item in ready["sources"]}
        self.assertEqual("observed_sessions", sources["codex"]["coverage"])
        self.assertEqual("observed_sessions", sources["claude"]["coverage"])

    def test_codex_notify_forwards_original_json_argument_without_shell(self) -> None:
        backup = self.state_dir / "config.toml.agent-beacon.bak"
        backup.write_text('notify = ["legacy-notifier", "--flag"]\n', encoding="utf-8")
        raw_argument = '{"type":"agent-turn-complete","last-assistant-message":"DO-NOT-LOG"}'
        with patch.object(reporter.subprocess, "run") as run:
            reporter._forward_codex_notify(raw_argument, backup)
        run.assert_called_once_with(["legacy-notifier", "--flag", raw_argument], check=False)
        self.assertNotIn("DO-NOT-LOG", backup.read_text(encoding="utf-8"))

        notification = {"type": "agent-turn-complete", "thread-id": "codex-session",
                        "turn-id": "turn-1", "input-messages": ["PRIVATE"]}
        raw_argument = json.dumps(notification)
        with patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")), \
                patch.object(reporter.subprocess, "run", side_effect=OSError("callback unavailable")):
            self.assertEqual(0, reporter.main([
                "codex-notify", "--state-dir", str(self.state_dir),
                "--notify-backup", str(backup), raw_argument,
            ]))
        event_files = list((self.state_dir / "events").glob("codex-*.json"))
        self.assertEqual(1, len(event_files))
        self.assertNotIn("PRIVATE", event_files[0].read_text(encoding="ascii"))

    def test_installer_dry_run_and_idempotent_merge_preserve_user_config(self) -> None:
        home = Path(self.temporary.name) / "user"
        codex_dir = home / ".codex"
        claude_dir = home / ".claude"
        codex_dir.mkdir(parents=True)
        claude_dir.mkdir(parents=True)
        original_codex = b'# private user config\nmodel = "example"\nnotify = ["legacy-tool", "--keep"]\n'
        original_hooks = {"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "keep-me"}]}]}}
        original_claude = {"theme": "dark", "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "keep-me"}]}]}}
        codex_config = codex_dir / "config.toml"
        codex_config.write_bytes(original_codex)
        (codex_dir / "hooks.json").write_text(json.dumps(original_hooks), encoding="utf-8")
        claude_settings = claude_dir / "settings.json"
        claude_settings.write_text(json.dumps(original_claude), encoding="utf-8")

        with patch.object(Path, "home", return_value=home):
            configure.install_user(["codex", "claude"], self.state_dir, apply=False)
            self.assertEqual(original_codex, codex_config.read_bytes())
            self.assertEqual(original_claude, json.loads(claude_settings.read_text(encoding="utf-8")))
            configure.install_user(["codex", "claude"], self.state_dir, apply=True)
            installed = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            backup = Path(installed["notify"][6])
            self.assertEqual(original_codex, backup.read_bytes())
            self.assertEqual("codex-notify", installed["notify"][2])
            updated_state = self.state_dir / "new-state"
            configure.install_user(["codex", "claude"], updated_state, apply=True)
            self.assertEqual(original_codex, backup.read_bytes())
            upgraded = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            self.assertEqual(str(backup), upgraded["notify"][6])
            codex_hooks = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))["hooks"]
            claude_hooks = json.loads(claude_settings.read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(2, sum(len(item["hooks"]) for item in codex_hooks["Stop"]))
            self.assertEqual(2, sum(len(item["hooks"]) for item in claude_hooks["Stop"]))
            self.assertEqual("keep-me", codex_hooks["Stop"][0]["hooks"][0]["command"])
            self.assertEqual("keep-me", claude_hooks["Stop"][0]["hooks"][0]["command"])

    def test_installer_refuses_recursive_notify_backup(self) -> None:
        home = Path(self.temporary.name) / "recursive-user"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        config = codex_dir / "config.toml"
        command = [str(Path(sys.executable).resolve()),
                   str(Path(configure.__file__).with_name("reporter.py").resolve()), "codex-notify",
                   "--state-dir", str(self.state_dir.resolve())]
        config.write_text("notify = " + json.dumps(command) + "\n", encoding="utf-8")
        with patch.object(Path, "home", return_value=home), self.assertRaises(ValueError):
            configure.install_user(["codex"], self.state_dir, apply=False)

    def test_codex_toml_merge_ignores_multiline_string_decoys_and_preserves_other_values(self) -> None:
        original = ('description = """example only\nnotify = ["decoy"]\n"""\n'
                    '# notify = ["comment"]\n'
                    'notify = [\n "legacy-notifier",\n "--keep", # comment\n]\n'
                    '[project]\nname = "private"\n')
        command = [str(Path(sys.executable).resolve()), "reporter.py", "codex-notify",
                   "--state-dir", str(self.state_dir.resolve()), "--notify-backup", "C:/backup"]
        updated = configure._merge_codex_toml(original, command)
        self.assertEqual(command, tomllib.loads(updated)["notify"])
        before = tomllib.loads(original)
        after = tomllib.loads(updated)
        self.assertEqual({key: val for key, val in before.items() if key != "notify"},
                         {key: val for key, val in after.items() if key != "notify"})
        self.assertIn('notify = ["decoy"]', updated)
        self.assertIn('[project]\nname = "private"', updated)

    def test_installer_uses_content_addressed_backup_despite_stale_legacy_backup(self) -> None:
        home = Path(self.temporary.name) / "stale-user"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        config = codex_dir / "config.toml"
        original = b'model = "current"\nnotify = ["legacy", "--current"]\n'
        config.write_bytes(original)
        stale = codex_dir / "config.toml.agent-beacon.bak"
        stale.write_bytes(b'notify = ["stale"]\n')
        with patch.object(Path, "home", return_value=home):
            configure.install_user(["codex"], self.state_dir, apply=True)
        installed = tomllib.loads(config.read_text(encoding="utf-8"))
        backup = Path(installed["notify"][6])
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual(b'notify = ["stale"]\n', stale.read_bytes())

    def test_unrelated_reporter_hooks_are_preserved(self) -> None:
        unrelated = {"type": "command", "command": "python",
                     "args": ["C:/other/reporter.py", "codex", "--state-dir", "C:/other/state"]}
        merged = configure._merge_hooks({"hooks": {"Stop": [{"hooks": [unrelated]}]}},
                                        "claude", self.state_dir)
        groups = merged["hooks"]["Stop"]
        self.assertEqual(unrelated, groups[0]["hooks"][0])
        self.assertEqual(2, sum(len(group["hooks"]) for group in groups))

    def test_claude_notification_install_is_idempotent_and_preserves_user_handlers(self) -> None:
        old_state = self.state_dir / "old-state"
        managed = configure._claude_handler("claude", old_state)
        user_handler = {"type": "command", "command": "user-notifier"}
        settings = {"hooks": {"Notification": [
            {"matcher": "idle_prompt", "hooks": [managed, user_handler, managed]},
            {"matcher": "other", "hooks": [managed]},
        ]}}
        with patch.object(configure, "_claude_version", return_value=(2, 1, 193)):
            once = configure._merge_hooks(settings, "claude", self.state_dir)
            twice = configure._merge_hooks(once, "claude", self.state_dir)

        groups = twice["hooks"]["Notification"]
        idle_group = next(group for group in groups if group.get("matcher") == "idle_prompt")
        managed_handlers = [handler for group in groups for handler in group["hooks"]
                            if configure._managed_handler(handler, "claude")]
        self.assertEqual(1, len(managed_handlers))
        self.assertEqual(1, sum(handler == user_handler for group in groups
                                for handler in group["hooks"]))
        self.assertEqual(str(self.state_dir.resolve()), idle_group["hooks"][0]["args"][3])

    def test_concurrent_settings_edit_is_not_overwritten(self) -> None:
        home = Path(self.temporary.name) / "concurrent-user"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        config = codex_dir / "config.toml"
        config.write_text('model = "before"\n', encoding="utf-8")
        original_replace_if_current = configure._atomic_replace_if_current

        def edit_during_apply(path: Path, expected: bytes | None, content: bytes) -> None:
            if path == config:
                config.write_text('model = "user edit"\n', encoding="utf-8")
            original_replace_if_current(path, expected, content)

        with patch.object(Path, "home", return_value=home), \
                patch.object(configure, "_atomic_replace_if_current", side_effect=edit_during_apply), \
                self.assertRaisesRegex(ValueError, "settings changed during install"):
            configure.install_user(["codex"], self.state_dir, apply=True)
        self.assertEqual('model = "user edit"\n', config.read_text(encoding="utf-8"))

    def test_concurrent_capture_sequences_are_unique(self) -> None:
        def capture(index: int) -> None:
            reporter._capture("codex", self.state_dir,
                              {"hook_event_name": "UserPromptSubmit", "session_id": f"session-{index}",
                               "turn_id": f"turn-{index}", "cwd": "/workspace"})

        # Install the process finder once: patching it independently inside
        # worker threads would race unittest.mock's global restoration.
        with patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(capture, range(24)))
        events = [json.loads(path.read_text(encoding="ascii"))
                  for path in (self.state_dir / "events").glob("codex-*.json")]
        sequences = [event["sequence"] for event in events]
        self.assertEqual(24, len(set(sequences)))
        self.assertEqual(list(range(1, 25)), sorted(sequences))

    def test_concurrent_queue_publishers_cannot_exceed_capacity(self) -> None:
        barrier = threading.Barrier(2)

        def capture(index: int) -> None:
            barrier.wait(timeout=2)
            reporter._capture("codex", self.state_dir,
                              {"hook_event_name": "UserPromptSubmit", "session_id": f"queued-{index}",
                               "turn_id": f"turn-{index}", "cwd": "/workspace"})

        with patch.object(reporter, "MAX_QUEUE_FILES", 1), \
                patch.object(reporter, "find_agent_process", return_value=(101, "created-codex")):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(capture, range(2)))
        event_files = list((self.state_dir / "events").glob("codex-*.json"))
        self.assertEqual(1, len(event_files))

    def test_bridge_worker_survives_poll_error_and_marks_source_unknown(self) -> None:
        second_poll = threading.Event()
        calls = 0

        def poll_once() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("poll failure with no payload")
            second_poll.set()
            self.bridge._stop.set()

        self.bridge.poll_once = poll_once
        self.bridge.start()
        self.assertTrue(second_poll.wait(timeout=2))
        self.bridge._thread.join(timeout=2)
        self.assertFalse(self.bridge._thread.is_alive())
        self.assertGreaterEqual(calls, 2)
        sources = {source["source"]: source for source in self.core.snapshot()["sources"]}
        self.assertFalse(sources["codex"]["healthy"])
        self.assertFalse(sources["claude"]["healthy"])

    def test_timed_out_close_keeps_bridge_ownership_until_worker_exits(self) -> None:
        poll_started = threading.Event()
        finish_poll = threading.Event()

        def blocked_poll() -> None:
            poll_started.set()
            finish_poll.wait(timeout=2)

        self.bridge.poll_once = blocked_poll
        self.bridge.start()
        self.assertTrue(poll_started.wait(timeout=2))
        self.bridge.close(timeout=0)
        try:
            with self.assertRaises(OSError):
                HookBridge(self.core, ["codex"], self.state_dir, identity_probe=lambda _: None)
        finally:
            finish_poll.set()
        self.bridge._thread.join(timeout=2)
        self.assertFalse(self.bridge._thread.is_alive())
        self.bridge.close()
        replacement = HookBridge(self.core, ["codex"], self.state_dir, identity_probe=lambda _: None)
        replacement.close()

    def test_native_process_identity_probe_smoke(self) -> None:
        info = process_info(os.getpid())
        self.assertIsNotNone(info)
        self.assertTrue(info[0])
        self.assertTrue(info[1])

    def test_generated_commands_are_path_safe_and_executable(self) -> None:
        output = self.state_dir / "generated configs"
        with patch.object(configure, "_claude_version", return_value=(2, 1, 193)):
            self.assertEqual(0, configure.main(["codex", "claude", "--output-dir", str(output),
                                                "--state-dir", str(self.state_dir)]))
        codex = json.loads((output / "codex-hooks.json").read_text(encoding="utf-8"))
        notify = tomllib.loads((output / "codex-notify.toml").read_text(encoding="utf-8"))
        claude = json.loads((output / "claude-settings-hooks.json").read_text(encoding="utf-8"))
        self.assertIn("commandWindows", codex["hooks"]["Stop"][0]["hooks"][0])
        self.assertIn("Interrupt", codex["hooks"])
        self.assertIn("codex-notify", notify["notify"])
        self.assertIn("ElicitationResult", claude["hooks"])
        self.assertEqual("idle_prompt", claude["hooks"]["Notification"][0]["matcher"])

        claude_handler = claude["hooks"]["Stop"][0]["hooks"][0]
        result = subprocess.run([claude_handler["command"], *claude_handler["args"]],
                                input="{}", text=True, capture_output=True, timeout=5, check=False)
        self.assertEqual(0, result.returncode)
        if os.name == "nt":
            codex_command = codex["hooks"]["Stop"][0]["hooks"][0]["commandWindows"]
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", codex_command],
                                    input="{}", text=True, capture_output=True, timeout=5, check=False)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
