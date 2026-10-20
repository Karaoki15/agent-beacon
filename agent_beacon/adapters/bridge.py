"""Bounded file-queue bridge from official command hooks into BeaconCore."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from ..core import BeaconCore, CoreError, EventConflict, SessionLimitError
from .process import process_info, process_has_exited, provider_process_names
from .metadata import SessionNames

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.75
MAX_FILES_PER_POLL = 64
MAX_EVENT_BYTES = 4096
GAP_GRACE_SECONDS = 1.5
MAX_CAPTURE_SEQUENCE = 4_503_599_627_370_495
ALLOWED_EVENTS = {
    "antigravity",
    "session_started", "turn_started", "awaiting_user", "resumed",
    "response_completed", "turn_stopped", "interrupted", "session_closed", "user_prompt",
    "tool_pre", "tool_post", "human_answer_seen", "tracking_lost",
}


def process_identity(pid: int) -> tuple[str, str] | None:
    """Return process basename and OS creation identity without reading argv."""
    info = process_info(pid)
    return (info[0], info[1]) if info is not None else None


class HookBridge:
    """Tail only current-instance metadata; provider coverage stays partial."""

    def __init__(
        self,
        core: BeaconCore,
        providers: list[str] | tuple[str, ...],
        state_dir: str | Path,
        *,
        identity_probe: Callable[[int], tuple[str, str] | None] = process_identity,
    ) -> None:
        selected = tuple(sorted(set(providers)))
        if not selected or any(item not in {"codex", "claude", "antigravity"} for item in selected):
            raise ValueError("unsupported integration provider")
        self.core = core
        self.providers = selected
        self.state_dir = Path(state_dir).resolve()
        self.event_dir = self.state_dir / "events"
        self.identity_probe = identity_probe
        self.instance_id = core.instance_id
        self._generations = {provider: uuid.uuid4().hex for provider in selected}
        self._source_sequences = {provider: 0 for provider in selected}
        self._last_capture_sequences: dict[str, int | None] = {provider: None for provider in selected}
        self._gap_since: dict[str, float | None] = {provider: None for provider in selected}
        self._processes: dict[str, dict[str, tuple[int, str]]] = {provider: {} for provider in selected}
        self._session_turns: dict[str, dict[str, str | None]] = {provider: {} for provider in selected}
        self._session_states: dict[str, dict[str, str | None]] = {provider: {} for provider in selected}
        self._session_capture_sequences: dict[str, dict[str, int]] = {provider: {} for provider in selected}
        self._question_ids: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._human_answer_latch: dict[tuple[str, str, str], str] = {}
        self._uncorrelated_sessions: set[tuple[str, str]] = set()
        self._retired: OrderedDict[tuple[str, str], str | None] = OrderedDict()
        self._transient_sessions: OrderedDict[str, None] = OrderedDict()
        self._idle_processes: dict[str, OrderedDict[int, str]] = {provider: OrderedDict() for provider in selected}
        # A completed chat can outlive the short-lived provider worker that
        # reported it. Retain only the bounded terminal scope and its owner
        # identity so the bridge can keep that ready scope leased and accept a
        # later SessionEnd from the same native owner.
        self._ready_owners: dict[str, OrderedDict[str, tuple[int, str, str | None]]] = {
            provider: OrderedDict() for provider in selected
        }
        self._names = SessionNames()
        self._antigravity_positions: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)
        self._owner_lock = (self.state_dir / "bridge.lock").open("a+b")
        try:
            self._acquire_owner_lock()
        except BaseException:
            self._owner_lock.close()
            raise
        for provider in selected:
            try:
                self._last_capture_sequences[provider] = int(
                    (self.state_dir / f"{provider}.sequence").read_text(encoding="ascii")
                )
            except (OSError, ValueError):
                pass
        # Publish only after sequence baselines are ready so an immediate hook
        # event cannot be mistaken for a pre-existing counter value.
        self._prepare_runtime()
        for provider in selected:
            core.register_source(provider, self._generations[provider], coverage="observed_sessions")

    def _acquire_owner_lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._owner_lock.seek(0)
            if not self._owner_lock.read(1):
                self._owner_lock.seek(0)
                self._owner_lock.write(b"0")
                self._owner_lock.flush()
            self._owner_lock.seek(0)
            try:
                msvcrt.locking(self._owner_lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("another Agent Beacon hook bridge owns this state directory") from error
        else:
            import fcntl

            try:
                fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("another Agent Beacon hook bridge owns this state directory") from error

    def _prepare_runtime(self) -> None:
        self.event_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_dir / f".runtime-{uuid.uuid4().hex}.tmp"
        data = json.dumps({"instance_id": self.instance_id}, separators=(",", ":")).encode("ascii")
        temporary.write_bytes(data)
        os.replace(temporary, self.state_dir / "runtime.json")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="agent-beacon-hooks", daemon=True)
        self._thread.start()

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            # A slow poll must keep ownership of runtime.json and the event
            # queue until it has actually stopped. Releasing the lock after a
            # timed join would let a second bridge replace the runtime marker
            # while this worker is still consuming events.
            if self._thread.is_alive():
                return
        if not self._owner_lock.closed:
            if os.name == "nt":
                import msvcrt

                self._owner_lock.seek(0)
                msvcrt.locking(self._owner_lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_UN)
            self._owner_lock.close()

    def poll_once(self) -> None:
        self.core.prune_sessions()
        self._collect_retired()
        for provider in self.providers:
            paths = sorted(self.event_dir.glob(f"{provider}-*.json"))[:MAX_FILES_PER_POLL]
            for path in paths:
                self._collect_retired()
                with self.core.batch_updates():
                    self._consume(provider, path)
            # Consume queued lifecycle events before checking process liveness.
            # A SessionEnd hook may be the last event from a process that has
            # exited by the time this poll begins.
            self._refresh_health(provider)
            self._check_trailing_gap(provider)
        self._collect_retired()
        owners = {provider: dict(rows) for provider, rows in self._processes.items()}
        for row in self.core.snapshot()["sessions"]:
            if row["source"] == "codex":
                owners.setdefault("codex", {}).setdefault(row["session_id"], (0, ""))
        self.core.update_session_names(self._names.read(owners))

    def _collect_retired(self) -> None:
        for item in self.core.drain_retired_sessions():
            provider, session_id = item["source"], item["session_id"]
            if provider not in self.providers:
                continue
            key = (provider, session_id)
            self._retired[key] = item["turn_id"]
            self._retired.move_to_end(key)
            while len(self._retired) > 1024:
                self._retired.popitem(last=False)
            owner = self._processes[provider].pop(session_id, None)
            if item["active"] and item["state"] == "ready" and owner is not None:
                self._remember_ready_owner(provider, session_id, owner[0], owner[1], item["turn_id"])
            self._session_turns[provider].pop(session_id, None)
            self._session_states[provider].pop(session_id, None)
            self._session_capture_sequences[provider].pop(session_id, None)
            self._uncorrelated_sessions.discard(key)
            self._clear_question_state(provider, session_id)

    def _consume(self, provider: str, path: Path) -> None:
        try:
            if path.stat().st_size > MAX_EVENT_BYTES:
                self._lose_continuity(provider)
                path.unlink(missing_ok=True)
                return
            event = json.loads(path.read_text(encoding="ascii"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            self._lose_continuity(provider)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if not isinstance(event, dict):
            self._lose_continuity(provider)
            path.unlink(missing_ok=True)
            return
        try:
            if event.get("instance_id") != self.instance_id:
                path.unlink(missing_ok=True)
                return
            sequence = event.get("sequence")
            if (isinstance(sequence, bool) or not isinstance(sequence, int)
                    or not 1 <= sequence <= MAX_CAPTURE_SEQUENCE):
                raise ValueError("invalid sequence")
            previous_sequence = self._last_capture_sequences[provider]
            if previous_sequence is not None and sequence <= previous_sequence:
                path.unlink(missing_ok=True)
                return
            if previous_sequence is not None and sequence != previous_sequence + 1:
                self._lose_continuity(provider, "hook_gap")
            self._last_capture_sequences[provider] = sequence
            if event.get("provider") != provider:
                raise ValueError("provider mismatch")
            lifecycle_event = event.get("event_name")
            session_id = event.get("session_id")
            turn_id = event.get("turn_id")
            persistent = event.get("session_persistent")
            if persistent is not None and not isinstance(persistent, bool):
                raise ValueError("invalid session persistence")
            if provider == "codex" and isinstance(session_id, str):
                if persistent is False:
                    self._transient_sessions[session_id] = None
                    self._transient_sessions.move_to_end(session_id)
                    while len(self._transient_sessions) > 1024:
                        self._transient_sessions.popitem(last=False)
                elif persistent is True:
                    self._transient_sessions.pop(session_id, None)
                if session_id in self._transient_sessions:
                    # Consume its capture sequence, but do not enroll an internal
                    # non-persisted run as a second user chat. Legacy notify events
                    # carry no persistence field and follow the same exclusion.
                    return
            pid = event.get("parent_pid")
            created = event.get("parent_created")
            if (lifecycle_event not in ALLOWED_EVENTS or not isinstance(session_id, str)
                    or (turn_id is not None and not isinstance(turn_id, str))):
                raise ValueError("invalid event fields")
            if provider != "antigravity" and lifecycle_event not in {"session_started", "session_closed", "tracking_lost"} and turn_id is None:
                raise ValueError("missing turn association")
            if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(created, str):
                raise ValueError("missing process identity")
            identity = self.identity_probe(pid)
            expected_names = provider_process_names(provider)
            # Desktop workers may exit after publishing Stop but before this poll.
            # The owner can move from _processes to _ready_owners after a terminal
            # event, so accept a repeated terminal hook from that same owner too.
            # Its turn must still match the retained ready evidence exactly.
            active_owner = self._processes[provider].get(session_id)
            retained_owner = self._ready_owners[provider].get(session_id)
            if (lifecycle_event in {"tool_pre", "tool_post", "awaiting_user", "resumed"}
                    and identity is None and process_has_exited(pid)
                    and retained_owner == (pid, created, turn_id)):
                source = next(
                    (item for item in self.core.snapshot()["sources"] if item["source"] == provider), None,
                )
                if source is not None and source["healthy"]:
                    # Late same-turn hooks cannot undo a confirmed terminal
                    # event, and the retained owner proves this exited worker
                    # was enrolled. Consume them without changing ownership.
                    return
            terminal_event = lifecycle_event in {"response_completed", "turn_stopped"}
            active_terminal_owner = (
                active_owner == (pid, created)
                and turn_id is not None
                and turn_id == self._session_turns[provider].get(session_id)
            )
            retained_terminal_owner = (
                retained_owner is not None
                and retained_owner[:2] == (pid, created)
                and turn_id is not None
                and turn_id == retained_owner[2]
            )
            known_turn = self._session_turns[provider].get(session_id)
            if known_turn is None and retained_owner is not None:
                known_turn = retained_owner[2]
            if terminal_event and turn_id is not None and known_turn is not None and turn_id != known_turn:
                # A queued completion from an older turn is stale even if its
                # PID has since been reused. Discard it before process checks
                # can invalidate the current bridge lease or transfer owner.
                return
            completed_exit = (terminal_event and identity is None and process_has_exited(pid)
                              and (active_terminal_owner or retained_terminal_owner)
                              and (provider, session_id) not in self._uncorrelated_sessions)
            # An enrolled owner can publish its terminal event and exit before
            # the bridge polls. Preserve that event only under a fresh bridge
            # lease; the worker's exit does not close the native chat scope.
            if lifecycle_event == "session_closed":
                owner = self._processes[provider].get(session_id)
                retained = self._ready_owners[provider].get(session_id)
                if owner != (pid, created) and (retained is None or retained[:2] != (pid, created)):
                    raise EventConflict("session close belongs to a different process owner")
            elif not completed_exit and (identity is None or identity[0].lower() not in expected_names or identity[1] != created):
                raise ValueError("agent process identity is not verified")

            if completed_exit:
                source = next(
                    (item for item in self.core.snapshot()["sources"] if item["source"] == provider), None,
                )
                if source is None or not source["healthy"]:
                    # A terminal hook cannot restore state after this bridge's
                    # continuity lease has already expired.
                    return

            if provider == "antigravity":
                self._consume_antigravity(event, session_id, pid, created, sequence)
                return

            retired_key = (provider, session_id)
            if retired_key in self._retired:
                fresh_turn = lifecycle_event in {"user_prompt", "tool_pre", "tool_post", "turn_started"} and turn_id != self._retired[retired_key]
                if lifecycle_event == "session_closed":
                    retained = self._ready_owners[provider].get(session_id)
                    if (retained is not None and retained[:2] == (pid, created)
                            and (turn_id is None or retained[2] is None or turn_id == retained[2])):
                        self._ready_owners[provider].pop(session_id, None)
                    return
                if lifecycle_event != "session_started" and not fresh_turn:
                    return

            if lifecycle_event == "tracking_lost":
                if provider != "claude" or turn_id is not None:
                    raise ValueError("invalid tracking loss")
                if self._processes[provider].get(session_id) == (pid, created):
                    self._mark_session_unknown(provider, session_id, "tracking_lost")
                    self._uncorrelated_sessions.add((provider, session_id))
                return

            if (provider, session_id) in self._uncorrelated_sessions:
                if lifecycle_event not in {"session_started", "session_closed", "user_prompt", "tool_pre", "tool_post"}:
                    return
                if lifecycle_event in {"user_prompt", "tool_pre", "tool_post"} and turn_id == self._session_turns[provider].get(session_id):
                    return

            if lifecycle_event == "human_answer_seen":
                question = self._question_ids.get((provider, session_id))
                answer_question_id = event.get("question_id")
                if (question is not None and question[0] == turn_id
                        and question[1] is not None and question[1] == answer_question_id):
                    self._processes[provider][session_id] = (pid, created)
                    if provider == "codex":
                        lifecycle_event = "resumed"
                        event["resume_reason"] = "user"
                    else:
                        self._human_answer_latch[(provider, session_id, turn_id)] = answer_question_id
                        return
                else:
                    return

            current_turn = self._session_turns[provider].get(session_id)
            current_state = self._session_states[provider].get(session_id)
            if (current_turn == turn_id and current_state == "ready"
                    and lifecycle_event in {"tool_pre", "tool_post", "awaiting_user", "resumed"}):
                # A terminal turn stays terminal until a new human prompt.
                return
            if (lifecycle_event == "awaiting_user" and event.get("question_id") is not None
                    and self._question_ids.get((provider, session_id)) == (turn_id, event["question_id"])):
                return
            if (lifecycle_event == "session_closed" and turn_id is not None
                    and turn_id != current_turn):
                return
            resume_reason = event.get("resume_reason", "unknown")
            initial_idle = event.get("initial_idle", False)
            if not isinstance(initial_idle, bool) or (initial_idle and (
                    provider != "claude" or lifecycle_event != "session_started" or turn_id is not None)):
                raise ValueError("invalid initial idle association")
            if (lifecycle_event == "resumed" and resume_reason == "user"
                    and (current_turn != turn_id or (current_state != "awaiting_user"
                         and (provider, session_id) not in self._question_ids))):
                self._processes[provider][session_id] = (pid, created)
                return
            if lifecycle_event == "user_prompt":
                self._clear_question_state(provider, session_id)
                if current_turn == turn_id:
                    lifecycle_event = "resumed"
                    # A submitted human message ends the pending reminder,
                    # including when an async question already let work resume.
                    resume_reason = "user"
                else:
                    lifecycle_event = "turn_started"
            elif lifecycle_event in {"tool_pre", "tool_post"}:
                answer_key = (provider, session_id, turn_id)
                current_question = self._question_ids.get((provider, session_id))
                if current_turn is not None and current_turn != turn_id:
                    # A delayed tool event from an earlier turn cannot change
                    # the current turn or its process-liveness ownership.
                    return
                if (answer_key in self._human_answer_latch
                        and current_question == (turn_id, self._human_answer_latch[answer_key])):
                    lifecycle_event = "resumed"
                    resume_reason = "user"
                    self._human_answer_latch.pop(answer_key, None)
                elif current_turn == turn_id:
                    lifecycle_event = "resumed"
                    resume_reason = "unknown"
                else:
                    # A correlated tool completion can establish that work is
                    # underway when hooks start mid-turn or recover a lost
                    # observer. It never supplies proof of a human answer.
                    lifecycle_event = "turn_started"

            if lifecycle_event != "session_closed" and not completed_exit:
                self._refresh_health(provider, candidate=(session_id, pid, created))
            core_sequence = sequence * 2
            # An interrupt is evidence that the agent stopped this known turn,
            # but it cannot bootstrap a missing session/turn into a ready state.
            if lifecycle_event == "turn_stopped" and current_turn is None:
                return
            if lifecycle_event == "response_completed" and current_turn is None and session_id not in self._session_turns[provider]:
                # A late terminal hook cannot resurrect metadata already pruned.
                return
            if lifecycle_event == "turn_started" and event.get("event_name") == "user_prompt":
                closed = next((row for row in self.core.snapshot()["sessions"]
                               if row["source"] == provider and row["session_id"] == session_id
                               and not row.get("active", True)), None)
                if closed is not None:
                    # A new native user prompt may reuse a saved chat after its
                    # worker exited, without another SessionStart hook.
                    self.core.observe_session(provider, self._generations[provider], session_id, None,
                                              "session_started", core_sequence - 1)
            # Hooks can be enabled midway through an existing provider turn.
            # Establish that turn from this correlated lifecycle event so the
            # event is not silently lost while waiting for another prompt.
            if (current_turn is None and lifecycle_event != "turn_started"
                    and lifecycle_event not in {"session_started", "session_closed"}):
                self.core.observe_session(
                    provider, self._generations[provider], session_id, turn_id,
                    "turn_started", core_sequence - 1,
                    project_name=event.get("project_name"),
                )
                self._session_turns[provider][session_id] = turn_id
                self._session_states[provider][session_id] = "working"
            self.core.observe_session(
                provider,
                self._generations[provider],
                session_id,
                turn_id,
                lifecycle_event,
                core_sequence,
                project_name=event.get("project_name"),
                resume_reason=resume_reason,
                initial_idle=initial_idle,
                session_name=event.get("session_name"),
            )
            self._session_turns[provider][session_id] = turn_id if lifecycle_event != "session_closed" else None
            self._session_states[provider][session_id] = {
                "session_started": "ready" if initial_idle else None, "turn_started": "working", "awaiting_user": "awaiting_user",
                "resumed": "working", "response_completed": "ready", "turn_stopped": "ready",
                "interrupted": None,
                "session_closed": None,
            }[lifecycle_event]
            if lifecycle_event == "awaiting_user":
                self._clear_question_state(provider, session_id)
                question_id = event.get("question_id")
                if isinstance(question_id, str):
                    self._question_ids[(provider, session_id)] = (turn_id, question_id)
            elif lifecycle_event in {"turn_started", "response_completed", "turn_stopped", "interrupted", "session_closed"} or (
                    lifecycle_event == "resumed" and resume_reason == "user"):
                self._clear_question_state(provider, session_id)
            self._session_capture_sequences[provider][session_id] = sequence
            self._uncorrelated_sessions.discard((provider, session_id))
            self._retired.pop((provider, session_id), None)
            self._ready_owners[provider].pop(session_id, None)
            self._processes[provider][session_id] = (pid, created)
            if lifecycle_event == "session_closed":
                self._processes[provider].pop(session_id, None)
                self._ready_owners[provider].pop(session_id, None)
                self._session_turns[provider].pop(session_id, None)
                self._session_states[provider].pop(session_id, None)
                self._clear_question_state(provider, session_id)
        except EventConflict:
            # A delayed terminal event from an older turn must not make the
            # current turn or other sessions unknown.
            pass
        except (CoreError, SessionLimitError, TypeError, ValueError):
            self._lose_continuity(provider, "invalid_hook")
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                self._lose_continuity(provider)

    def _lose_continuity(self, provider: str, reason: str = "hook_gap") -> None:
        self._invalidate_terminal_evidence(provider)
        self._human_answer_latch = {
            key: value for key, value in self._human_answer_latch.items() if key[0] != provider
        }
        self._question_ids = {
            key: value for key, value in self._question_ids.items() if key[0] != provider
        }
        try:
            self._source_sequences[provider] += 1
            self.core.update_source(provider, self._generations[provider],
                                    self._source_sequences[provider], False, False, loss_reason=reason)
        except (CoreError, EventConflict, SessionLimitError):
            pass

    def _invalidate_terminal_evidence(self, provider: str) -> None:
        # Retained terminal evidence is valid only within an uninterrupted
        # bridge lease. A gap or malformed hook requires fresh lifecycle proof.
        self._ready_owners[provider].clear()
        self._idle_processes[provider].clear()
        for session_id in self._session_states[provider]:
            self._session_states[provider][session_id] = None

    def _consume_antigravity(self, event: dict[str, Any], session_id: str, pid: int, created: str, sequence: int) -> None:
        from .antigravity import EVENTS, counter, transition

        hook = event.get("native_event")
        if not isinstance(hook, str) or hook not in EVENTS | {"Unconfirmed"}:
            raise ValueError("invalid Antigravity event")
        fields = {"PreInvocation": ("invocation", "steps"), "PostInvocation": ("invocation", "steps"),
                  "PreToolUse": ("step",), "PostToolUse": ("step",), "Stop": ("execution",)}.get(hook, ())
        if any(counter(event.get(field)) is None for field in fields):
            raise ValueError("invalid Antigravity progress")
        if hook in {"PreToolUse", "PostToolUse"} and not isinstance(event.get("question"), bool):
            raise ValueError("invalid question flag")
        if hook == "Stop" and not isinstance(event.get("fully_idle"), bool):
            raise ValueError("invalid idle flag")
        position = self._antigravity_positions.setdefault(session_id, {})
        change = transition(position, event)
        if change is None:
            return
        self._antigravity_positions.move_to_end(session_id)
        while len(self._antigravity_positions) > 1024:
            self._antigravity_positions.popitem(last=False)
        lifecycle, resume_reason = change
        position["started"] = True
        provider = "antigravity"
        current = self._session_turns[provider].get(session_id)
        if current is None and lifecycle not in {"session_started", "resumed", "awaiting_user"}:
            return  # An idle/invalid signal cannot bootstrap a finished chat.
        self._refresh_health(provider, candidate=(session_id, pid, created))
        core_sequence = sequence * 2
        if current is None and lifecycle != "session_started":
            self.core.observe_session(provider, self._generations[provider], session_id, session_id,
                                      "turn_started", core_sequence - 1, project_name=event.get("project_name"))
            current = session_id
        self.core.observe_session(provider, self._generations[provider], session_id,
                                  current if lifecycle != "session_started" else None, lifecycle, core_sequence,
                                  project_name=event.get("project_name"), resume_reason=resume_reason)
        self._session_turns[provider][session_id] = current
        self._session_states[provider][session_id] = {"session_started": None, "session_unknown": None,
            "turn_started": "working", "resumed": "working", "awaiting_user": "awaiting_user", "response_completed": "ready"}[lifecycle]
        self._session_capture_sequences[provider][session_id] = sequence
        self._processes[provider][session_id] = (pid, created)
        self._retired.pop((provider, session_id), None)

    def _clear_question_state(self, provider: str, session_id: str) -> None:
        self._question_ids.pop((provider, session_id), None)
        self._human_answer_latch = {
            key: value for key, value in self._human_answer_latch.items()
            if key[:2] != (provider, session_id)
        }

    def _remember_ready_owner(
        self, provider: str, session_id: str, pid: int, created: str, turn_id: str | None,
    ) -> None:
        owners = self._ready_owners[provider]
        owners[session_id] = (pid, created, turn_id)
        owners.move_to_end(session_id)
        while len(owners) > 128:
            owners.popitem(last=False)

    def _check_trailing_gap(self, provider: str) -> None:
        # A bounded poll may leave valid events queued. Their allocated
        # sequences are not missing; start the trailing-gap timer only after
        # the provider queue has drained.
        last_seen = self._last_capture_sequences[provider] or 0
        expected = last_seen + 1
        has_expected_event = False
        for path in self.event_dir.glob(f"{provider}-*.json"):
            try:
                queued_sequence = int(path.name.split("-", 2)[1])
            except (IndexError, ValueError):
                continue
            if queued_sequence == expected:
                has_expected_event = True
                break
        if has_expected_event:
            self._gap_since[provider] = None
            return
        try:
            allocated = int((self.state_dir / f"{provider}.sequence").read_text(encoding="ascii"))
        except (OSError, ValueError):
            return
        if allocated <= last_seen:
            self._gap_since[provider] = None
            return
        now = time.monotonic()
        if self._gap_since[provider] is None:
            self._gap_since[provider] = now
        elif now - self._gap_since[provider] >= GAP_GRACE_SECONDS:
            self._lose_continuity(provider)
            self._last_capture_sequences[provider] = allocated
            self._gap_since[provider] = None

    def _refresh_health(self, provider: str, candidate: tuple[str, int, str] | None = None) -> None:
        prior_source = next(
            (item for item in self.core.snapshot()["sources"] if item["source"] == provider), None,
        )
        if prior_source is None or not prior_source["healthy"]:
            # Core has already expired or invalidated this bridge lease. The
            # terminal summary cannot renew readiness across that boundary.
            self._invalidate_terminal_evidence(provider)
        active = self._processes[provider]
        verified_processes: dict[str, tuple[int, str]] = {}
        probed: dict[int, tuple[str, str] | None] = {}

        def probe(pid: int) -> tuple[str, str] | None:
            if pid not in probed:
                probed[pid] = self.identity_probe(pid)
            return probed[pid]
        lost_sessions: list[str] = []
        for session_id, (pid, expected_created) in list(active.items()):
            result = probe(pid)
            name = result[0].lower() if result else ""
            actual_created = result[1] if result else None
            allowed_names = provider_process_names(provider)
            if actual_created == expected_created and name in allowed_names:
                verified_processes[session_id] = (pid, expected_created)
            else:
                lost_sessions.append(session_id)
        for session_id in lost_sessions:
            pid, created = active[session_id]
            result = probed.get(pid)
            exited = (result is not None and result[1] != created) or process_has_exited(pid)
            if self._session_states[provider].get(session_id) == "ready" and exited:
                self._remember_ready_owner(
                    provider, session_id, pid, created, self._session_turns[provider].get(session_id),
                )
                continue
            self._mark_session_unknown(provider, session_id)
        if candidate is not None:
            session_id, pid, expected_created = candidate
            result = probe(pid)
            allowed_names = provider_process_names(provider)
            if result is not None and result[0].lower() in allowed_names and result[1] == expected_created:
                verified_processes[session_id] = (pid, expected_created)
        for pid, created in list(self._idle_processes[provider].items()):
            result = probe(pid)
            allowed_names = provider_process_names(provider)
            if result is None or result[0].lower() not in allowed_names or result[1] != created:
                self._idle_processes[provider].pop(pid, None)
        verified = bool(verified_processes or self._idle_processes[provider] or self._ready_owners[provider])
        self._source_sequences[provider] += 1
        try:
            self.core.update_source(provider, self._generations[provider],
                                    self._source_sequences[provider], verified, verified)
        except (CoreError, EventConflict, SessionLimitError):
            pass
        self._processes[provider] = verified_processes

    def _mark_session_unknown(self, provider: str, session_id: str, reason: str = "process_unavailable") -> None:
        capture_sequence = self._session_capture_sequences[provider].get(session_id)
        turn_id = self._session_turns[provider].get(session_id)
        if capture_sequence is not None:
            try:
                self.core.observe_session(
                    provider, self._generations[provider], session_id, turn_id,
                    "interrupted" if turn_id is not None else "session_unknown", capture_sequence * 2 + 1,
                    unknown_reason=reason,
                )
            except (CoreError, EventConflict, SessionLimitError):
                pass
        self._processes[provider].pop(session_id, None)
        self._session_states[provider][session_id] = None
        self._clear_question_state(provider, session_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as error:
                # Keep the observer alive, but make every provider unknown until
                # a later successful poll reconciles fresh lifecycle evidence.
                logger.error("Hook bridge poll failed (%s); marking sources unknown",
                             type(error).__name__)
                for provider in self.providers:
                    try:
                        self._lose_continuity(provider)
                    except Exception as continuity_error:
                        logger.error("Could not mark hook source unknown (%s)",
                                     type(continuity_error).__name__)
            self._stop.wait(POLL_SECONDS)

