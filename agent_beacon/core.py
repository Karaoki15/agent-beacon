"""Thread-safe in-memory status aggregation for manual and observed sessions.

Provider adapters are in-process only. A generation identifies the provider
observer/bridge incarnation, not an individual CLI process or chat. Registering
a new generation invalidates that observer's scope; an individual chat or CLI
restart is represented by its session lifecycle events. Session progress has
no short TTL; only observer health expires.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
import unicodedata
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .stabilization import StateConfirmation


STATES = frozenset({"working", "awaiting_user", "ready"})
OBSERVED_SOURCES = frozenset({"codex", "claude", "antigravity"})
SOURCE_COVERAGES = frozenset({"all_sessions", "observed_sessions"})
LIFECYCLE_EVENTS = frozenset({
    "session_started", "turn_started", "awaiting_user", "resumed",
    "response_completed", "turn_stopped", "interrupted", "session_closed", "session_unknown",
})
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z", re.ASCII)
MAX_SAFE_INTEGER = 9_007_199_254_740_991
OBSERVER_TTL_SECONDS = 15.0
MAX_SOURCES = 16
MAX_RETIRED_GENERATIONS_PER_SOURCE = 64
READY_RETENTION_SECONDS = 30 * 60
CLOSED_RETENTION_SECONDS = 5 * 60


def safe_session_name(value: object) -> str | None:
    """Accept a bounded provider title, never a prompt-derived fallback."""
    if not isinstance(value, str):
        return None
    name = " ".join("".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf"}).split())
    return name[:160] or None


class CoreError(ValueError):
    """Base class for invalid core events."""


class EventConflict(CoreError):
    """The event sequence is outdated or conflicts with an existing event."""


class SessionLimitError(CoreError):
    """The bounded session store is full."""


def _validate_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise CoreError(f"{field} must be 1-64 ASCII letters, digits, '.', '_' or '-'.")
    return value


def _validate_sequence(sequence: object) -> int:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= MAX_SAFE_INTEGER:
        raise CoreError("sequence must be a positive safe integer.")
    return sequence


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BeaconCore:
    """Store bounded session state; lifecycle progress never expires by itself."""

    def __init__(
        self, ttl_seconds: float = 30, max_sessions: int = 128, *,
        question_reminder_seconds: float = 120, state_settle_seconds: float = 0,
    ) -> None:
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 86_400):
            raise ValueError("ttl_seconds must be a positive finite number no greater than 86400")
        if isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or not 1 <= max_sessions <= 128:
            raise ValueError("max_sessions must be an integer from 1 to 128")
        if (isinstance(question_reminder_seconds, bool)
                or not isinstance(question_reminder_seconds, (int, float))
                or not math.isfinite(question_reminder_seconds)
                or not 0 <= question_reminder_seconds <= 86_400):
            raise ValueError("question_reminder_seconds must be a finite number from 0 to 86400")
        if isinstance(state_settle_seconds, bool) or not isinstance(state_settle_seconds, (int, float)) or not math.isfinite(state_settle_seconds) or not 0 <= state_settle_seconds <= 3:
            raise ValueError("state_settle_seconds must be between 0 and 3")
        self._confirmation = StateConfirmation(float(state_settle_seconds))
        self._confirmation_version = 0
        self.ttl_seconds = float(ttl_seconds)
        self.max_sessions = max_sessions
        self.question_reminder_seconds = float(question_reminder_seconds)
        self.instance_id = str(uuid.uuid4())
        self._lock = threading.RLock()
        self._changes = threading.Condition(self._lock)
        self._revision = 0
        self._sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._observed: dict[tuple[str, str], dict[str, Any]] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=30)
        self._history_key: tuple[Any, ...] | None = None
        self._history_sequence = 0
        self._history_sessions: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._retired_sessions: deque[dict[str, Any]] = deque(maxlen=512)
        self._idle_sources: set[str] = set()
        self._batch_depth = 0

    @contextmanager
    def batch_updates(self):
        """Publish one received hook atomically, including source reconciliation."""
        with self._lock:
            self._batch_depth += 1
            try:
                yield
            finally:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    self._snapshot_locked()

    def _retire_session_locked(self, key: tuple[str, str]) -> None:
        item = self._observed.pop(key)
        self._history_sessions.pop(key, None)
        self._retired_sessions.append({field: item.get(field) for field in (
            "source", "session_id", "turn_id", "state", "active", "sequence",
        )})
        self._changed_locked()

    def _reclaim_closed_locked(self) -> None:
        if len(self._sessions) + len(self._observed) < self.max_sessions:
            return
        closed = [(key, item) for key, item in self._observed.items() if not item["active"]]
        if closed:
            key, _ = min(closed, key=lambda entry: entry[1].get("_closed_at", 0.0))
            self._retire_session_locked(key)

    def prune_sessions(self) -> None:
        """Retire quiet ready chats and closed metadata; never expire ongoing work."""
        with self._lock:
            now = time.monotonic()
            for key, item in list(self._observed.items()):
                source = self._sources.get(item["source"])
                fresh_source = bool(source and source["healthy"] and source["inventory_complete"]
                                    and source["generation"] == item["generation"]
                                    and source["_monotonic_at"] is not None
                                    and now - source["_monotonic_at"] < OBSERVER_TTL_SECONDS)
                ready_since = item.get("_ready_since")
                expired_ready = (item["active"] and item["state"] == "ready" and fresh_source
                                 and ready_since is not None and now - ready_since >= READY_RETENTION_SECONDS)
                expired_closed = (not item["active"] and now - item.get("_closed_at", now) >= CLOSED_RETENTION_SECONDS)
                if expired_ready or expired_closed:
                    if expired_ready:
                        self._idle_sources.add(item["source"])
                    self._retire_session_locked(key)
            for key, item in list(self._sessions.items()):
                if now - item["_monotonic_at"] >= self.ttl_seconds + CLOSED_RETENTION_SECONDS:
                    self._sessions.pop(key)
                    self._changed_locked()

    def drain_retired_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            retired = list(self._retired_sessions)
            self._retired_sessions.clear()
            return retired

    def update_session_names(self, names: dict[tuple[str, str], str]) -> None:
        """Refresh display metadata without renewing a task's ready deadline."""
        with self._lock:
            changed = False
            for key, value in names.items():
                item = self._observed.get(key)
                name = safe_session_name(value)
                if item and name and item.get("session_name") != name:
                    item["session_name"] = name
                    for event in self._history:
                        if (event.get("source"), event.get("session_id")) == key:
                            event["session_name"] = name
                    changed = True
            if changed:
                self._changed_locked()

    def record_event(
        self, *, agent_id: str, session_id: str, state: str, sequence: int, source: str = "manual",
    ) -> dict[str, Any]:
        """Record a manual event, treating an identical retry as idempotent."""
        agent_id = _validate_identifier(agent_id, "agent_id")
        session_id = _validate_identifier(session_id, "session_id")
        if source != "manual":
            raise CoreError("source must be 'manual'.")
        if not isinstance(state, str) or state not in STATES:
            raise CoreError("state must be working, awaiting_user, or ready.")
        sequence = _validate_sequence(sequence)

        key = (agent_id, session_id)
        with self._lock:
            previous = self._sessions.get(key)
            if previous is not None:
                if sequence < previous["sequence"]:
                    raise EventConflict("sequence is outdated")
                if sequence == previous["sequence"]:
                    if state == previous["state"] and source == previous["source"]:
                        return self._snapshot_locked()
                    raise EventConflict("sequence conflicts with an existing event")
            else:
                self._reclaim_closed_locked()
                if len(self._sessions) + len(self._observed) >= self.max_sessions:
                    raise SessionLimitError("session limit reached")

            self._sessions[key] = {
                "agent_id": agent_id, "session_id": session_id, "source": source,
                "sequence": sequence, "state": state, "updated_at": _utc_now(),
                "_monotonic_at": time.monotonic(),
            }
            self._changed_locked()
            return self._snapshot_locked()

    def register_source(
        self, source: str, generation: str, *, coverage: str = "all_sessions",
    ) -> dict[str, Any]:
        """Register a provider observer/bridge incarnation, not a chat or CLI process."""
        source = self._validate_source(source)
        generation = _validate_identifier(generation, "generation")
        if not isinstance(coverage, str) or coverage not in SOURCE_COVERAGES:
            raise CoreError("coverage must be 'all_sessions' or 'observed_sessions'.")
        with self._lock:
            current = self._sources.get(source)
            if current is not None and generation == current["generation"]:
                if coverage != current["coverage"]:
                    raise EventConflict("coverage cannot change within a source generation")
                return self._snapshot_locked()
            if current is not None and generation in current["retired_generations"]:
                raise EventConflict("generation has already been retired")
            if current is None and len(self._sources) >= MAX_SOURCES:
                raise SessionLimitError("source limit reached")
            if current is not None and len(current["retired_generations"]) >= MAX_RETIRED_GENERATIONS_PER_SOURCE:
                raise SessionLimitError("retired generation limit reached")

            retired = [] if current is None else [*current["retired_generations"], current["generation"]]
            self._sources[source] = {
                "source": source, "generation": generation, "healthy": False,
                "inventory_complete": False, "sequence": 0, "_monotonic_at": None,
                "coverage": coverage, "retired_generations": retired,
            }
            self._idle_sources.discard(source)
            for (item_source, _), item in self._observed.items():
                if item_source == source:
                    item["state"] = None
                    item["available"] = False
                    item["_question_deadline"] = None
            self._changed_locked()
            return self._snapshot_locked()

    def update_source(
        self, source: str, generation: str, sequence: int, healthy: bool, inventory_complete: bool,
        *, loss_reason: str = "observer_gap",
    ) -> dict[str, Any]:
        """Refresh bridge transport health and whether its session inventory is complete."""
        source = self._validate_source(source)
        generation = _validate_identifier(generation, "generation")
        sequence = _validate_sequence(sequence)
        if not isinstance(healthy, bool) or not isinstance(inventory_complete, bool):
            raise CoreError("healthy and inventory_complete must be booleans.")
        if loss_reason not in {"observer_gap", "hook_gap", "invalid_hook"}:
            raise CoreError("unsupported observer loss reason")
        with self._lock:
            current = self._require_current_generation(source, generation)
            if sequence <= current["sequence"]:
                raise EventConflict("source sequence must increase")
            now = time.monotonic()
            last_heartbeat = current["_monotonic_at"]
            continuity_lost = (
                not current["healthy"] or last_heartbeat is None
                or now - last_heartbeat >= OBSERVER_TTL_SECONDS or not healthy
            )
            if continuity_lost:
                self._mark_source_observations_unknown(source, loss_reason)
            current.update({
                "healthy": healthy, "inventory_complete": inventory_complete,
                "sequence": sequence, "_monotonic_at": now,
            })
            self._changed_locked()
            return self._snapshot_locked()

    def observe_session(
        self, source: str, generation: str, session_id: str, turn_id: str | None,
        event: str, sequence: int, project_name: str | None = None,
        parent_session_id: str | None = None,
        *, resume_reason: str = "unknown", initial_idle: bool = False, session_name: str | None = None,
        unknown_reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply a chat lifecycle event, sequenced within bridge generation and session."""
        source = self._validate_source(source)
        generation = _validate_identifier(generation, "generation")
        session_id = _validate_identifier(session_id, "session_id")
        if turn_id is not None:
            turn_id = _validate_identifier(turn_id, "turn_id")
        if not isinstance(event, str) or event not in LIFECYCLE_EVENTS:
            raise CoreError("event is not a supported lifecycle event.")
        if not isinstance(resume_reason, str) or resume_reason not in {"user", "automatic", "unknown"}:
            raise CoreError("resume_reason must be 'user', 'automatic', or 'unknown'.")
        if not isinstance(initial_idle, bool) or (initial_idle and (
                source != "claude" or event != "session_started" or turn_id is not None)):
            raise CoreError("initial_idle requires a new Claude session without a turn.")
        sequence = _validate_sequence(sequence)
        if unknown_reason not in {None, "tracking_lost", "process_unavailable"}:
            raise CoreError("unsupported unknown reason")
        if event not in {"session_started", "session_closed", "session_unknown"} and turn_id is None:
            raise CoreError("turn_id is required for turn lifecycle events.")
        if project_name is not None:
            if (not isinstance(project_name, str) or len(project_name) > 160
                    or any(separator in project_name for separator in ("/", "\\", ":"))
                    or any(unicodedata.category(character) in {"Cc", "Cf"} for character in project_name)):
                raise CoreError("project_name must be a safe basename of at most 160 characters or null.")
        if parent_session_id is not None:
            parent_session_id = _validate_identifier(parent_session_id, "parent_session_id")
            if parent_session_id == session_id:
                raise CoreError("parent_session_id cannot refer to the session itself.")

        key = (source, session_id)
        with self._lock:
            self._require_current_generation(source, generation)
            previous = self._observed.get(key)
            if previous is None:
                self._reclaim_closed_locked()
                if len(self._sessions) + len(self._observed) >= self.max_sessions:
                    raise SessionLimitError("session limit reached")
                if event not in {"session_started", "turn_started"}:
                    raise EventConflict("session must start before lifecycle events")
                previous = {
                    "source": source, "generation": generation, "session_id": session_id,
                    "turn_id": None, "sequence": 0, "state": None, "active": True,
                    "project_name": None, "parent_session_id": None, "updated_at": _utc_now(),
                    "_question_deadline": None,
                }

            if previous["generation"] != generation:
                if event not in {"session_started", "turn_started"}:
                    raise EventConflict("session belongs to a retired generation; reconcile it in the current generation")
                previous = {
                    "source": source, "generation": generation, "session_id": session_id,
                    "turn_id": None, "sequence": 0, "state": None, "active": True,
                    "project_name": None, "parent_session_id": None, "updated_at": _utc_now(),
                    "_question_deadline": None,
                }
            if sequence <= previous["sequence"]:
                raise EventConflict("session sequence must increase")
            old_state = previous["state"]
            reopening_closed = not previous["active"] and (event == "session_started" or (source == "antigravity" and event == "turn_started"))
            if not previous["active"] and not reopening_closed:
                raise EventConflict("session is closed")
            if reopening_closed:
                previous["active"] = True
                previous["turn_id"] = None
                previous["state"] = None
                previous["_question_deadline"] = None
            if event == "turn_started":
                if previous["turn_id"] is not None and turn_id == previous["turn_id"]:
                    raise EventConflict("turn_started must select a new turn")
                previous["turn_id"] = turn_id
                previous["state"] = "working"
                previous["_question_deadline"] = None
                previous["_initial_idle"] = False
            elif event in {"awaiting_user", "resumed", "response_completed", "turn_stopped", "interrupted"}:
                if previous["turn_id"] != turn_id:
                    raise EventConflict("event belongs to a different turn")
                previous["state"] = {
                    "awaiting_user": "awaiting_user", "resumed": "working",
                    "response_completed": "ready", "turn_stopped": "ready", "interrupted": None,
                }[event]
                if event == "awaiting_user":
                    previous["_question_deadline"] = (
                        time.monotonic() + self.question_reminder_seconds
                        if self.question_reminder_seconds > 0 else None
                    )
                elif event == "resumed" and resume_reason == "user":
                    previous["_question_deadline"] = None
                elif event in {"response_completed", "turn_stopped", "interrupted"}:
                    previous["_question_deadline"] = None
            elif event == "session_started":
                if previous["sequence"] != 0 and not reopening_closed:
                    raise EventConflict("session has already started")
                previous["turn_id"] = None if reopening_closed else turn_id
                previous["state"] = "ready" if initial_idle else None
                previous["_initial_idle"] = initial_idle
            elif event == "session_unknown":
                if turn_id != previous["turn_id"]:
                    raise EventConflict("unknown event belongs to a different turn")
                previous["state"] = None
                previous["_question_deadline"] = None
                previous["_initial_idle"] = False
            elif event == "session_closed":
                if turn_id is not None and turn_id != previous["turn_id"]:
                    raise EventConflict("session_closed belongs to a different turn")
                previous["active"] = False
                previous["state"] = None
                previous["_question_deadline"] = None
            if project_name is not None:
                previous["project_name"] = project_name
            previous["_unconfirmed_reason"] = unknown_reason if previous["state"] is None else None
            if parent_session_id is not None:
                previous["parent_session_id"] = parent_session_id
            name = safe_session_name(session_name)
            if name:
                previous["session_name"] = name
            if previous["state"] == "ready":
                if old_state != "ready" or event == "session_started":
                    previous["_ready_since"] = time.monotonic()
            else:
                previous["_ready_since"] = None
            if not previous["active"]:
                previous["_closed_at"] = time.monotonic()
            previous["sequence"] = sequence
            previous["updated_at"] = _utc_now()
            self._observed[key] = previous
            self._changed_locked()
            return self._snapshot_locked()

    def clear_sessions(self) -> dict[str, Any]:
        """Clear manual demo sessions while preserving provider observations."""
        with self._lock:
            if self._sessions:
                self._sessions.clear()
                self._changed_locked()
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, Any]:
        """Return a point-in-time status view with freshly calculated health leases."""
        with self._lock:
            return self._snapshot_locked()

    def wait_for_change(self, revision: int, timeout: float) -> dict[str, Any]:
        """Wait atomically for a mutation, or a bounded display/health deadline."""
        with self._changes:
            remaining = self._confirmation.remaining(time.monotonic())
            if remaining is not None:
                timeout = min(timeout, remaining)
            self._changes.wait_for(lambda: self._revision != revision, timeout=max(0.0, min(10.0, timeout)))
            return self._snapshot_locked()

    def _changed_locked(self) -> None:
        self._revision += 1
        self._changes.notify_all()

    @staticmethod
    def _validate_source(source: object) -> str:
        if not isinstance(source, str) or source not in OBSERVED_SOURCES:
            raise CoreError("unsupported observed source.")
        return source

    def _require_current_generation(self, source: str, generation: str) -> dict[str, Any]:
        current = self._sources.get(source)
        if current is None or current["generation"] != generation:
            raise EventConflict("source generation is not registered")
        return current

    def _mark_source_observations_unknown(self, source: str, reason: str = "observer_gap") -> None:
        self._idle_sources.discard(source)
        for (item_source, _), item in self._observed.items():
            if item_source == source and item["active"]:
                if item["state"] is not None or not item.get("_unconfirmed_reason"):
                    item["_unconfirmed_reason"] = reason
                item["state"] = None
                item["_question_deadline"] = None
                item["_initial_idle"] = False
                item["_ready_since"] = None

    def _snapshot_locked(self) -> dict[str, Any]:
        now_mono = time.monotonic()
        sessions: list[dict[str, Any]] = []
        known_states: list[str] = []
        display_states: list[str] = []
        remaining_leases: list[float] = []
        unknown_scope = False
        complete_inventory = False

        for item in sorted(self._sessions.values(), key=lambda row: (row["agent_id"], row["session_id"])):
            age = now_mono - item["_monotonic_at"]
            available = age < self.ttl_seconds
            if available:
                known_states.append(item["state"])
                remaining_leases.append(max(0.0, self.ttl_seconds - age))
            else:
                unknown_scope = True
            display_state = item["state"] if available else None
            if display_state is not None:
                display_states.append(display_state)
            sessions.append({
                "agent_id": item["agent_id"], "session_id": item["session_id"],
                "source": item["source"], "sequence": item["sequence"], "state": item["state"],
                "available": available, "updated_at": item["updated_at"],
                "display_state": display_state, "question_reminder_active": False,
                "question_reminder_remaining_seconds": 0.0,
                "status_reason": None if available else "signal_expired",
                "valid_for_seconds": max(0.0, self.ttl_seconds - age) if available else 0.0,
            })

        sources: list[dict[str, Any]] = []
        for source, item in sorted(self._sources.items()):
            heartbeat_at = item["_monotonic_at"]
            age = float("inf") if heartbeat_at is None else now_mono - heartbeat_at
            fresh = age < OBSERVER_TTL_SECONDS
            healthy = item["healthy"] and fresh
            idle_ready = source in self._idle_sources and healthy and item["inventory_complete"]
            enrolled = [
                observed for (session_source, _), observed in self._observed.items()
                if session_source == source and observed["generation"] == item["generation"]
                and observed["active"]
            ]
            retired_active = [
                observed for (session_source, _), observed in self._observed.items()
                if session_source == source and observed["generation"] != item["generation"]
                and observed["active"]
            ]
            in_scope = item["coverage"] == "all_sessions" or bool(enrolled) or bool(retired_active) or idle_ready
            sources.append({
                "source": source, "healthy": healthy,
                "inventory_complete": item["inventory_complete"] if fresh else False,
                "coverage": item["coverage"],
                "idle_ready": idle_ready,
                "valid_for_seconds": min(self.ttl_seconds, max(0.0, OBSERVER_TTL_SECONDS - age)) if healthy else 0.0,
            })
            if in_scope and (not healthy or not item["inventory_complete"]):
                unknown_scope = True
            elif in_scope and healthy:
                complete_inventory = True
            if in_scope and fresh:
                remaining_leases.append(max(0.0, OBSERVER_TTL_SECONDS - age))
            for (session_source, _), observed in sorted(self._observed.items()):
                if session_source != source:
                    continue
                observed = self._confirmation.select(
                    (session_source, observed["session_id"]), observed, now_mono,
                    healthy=healthy and item["inventory_complete"] and observed["generation"] == item["generation"],
                    commit=self._batch_depth == 0,
                )
                active = observed["active"]
                session_available = active and healthy and observed["state"] is not None
                if active and session_available:
                    known_states.append(observed["state"])
                if active and observed["state"] is None:
                    unknown_scope = True
                deadline = observed.get("_question_deadline")
                reminder_remaining = (
                    max(0.0, deadline - now_mono)
                    if session_available and deadline is not None else 0.0
                )
                reminder_active = (
                    session_available and observed["state"] == "working"
                    and reminder_remaining > 0
                )
                display_state = observed["state"] if session_available else None
                if reminder_active:
                    display_state = "awaiting_user"
                if active and display_state is not None:
                    display_states.append(display_state)
                status_reason = (
                    "closed" if not active else
                    "observer_restarted" if observed["generation"] != item["generation"] else
                    "observer_stale" if not fresh else
                    "observer_offline" if not healthy else
                    "awaiting_first_request" if observed.get("_initial_idle") and observed["state"] == "ready" else
                    "awaiting_activity" if observed["state"] is None and observed["turn_id"] is None else
                    observed.get("_unconfirmed_reason") or "status_unconfirmed" if observed["state"] is None else None
                )
                sessions.append({
                    "agent_id": source, "session_id": observed["session_id"], "source": source,
                    "sequence": observed["sequence"], "state": observed["state"],
                    "available": session_available, "updated_at": observed["updated_at"],
                    "turn_id": observed["turn_id"], "project_name": observed["project_name"],
                    "parent_session_id": observed["parent_session_id"], "active": active,
                    "display_state": display_state,
                    "question_reminder_active": reminder_active,
                    "question_reminder_remaining_seconds": reminder_remaining,
                    "status_reason": status_reason,
                    "session_name": observed.get("session_name"),
                    "valid_for_seconds": min(self.ttl_seconds, max(0.0, OBSERVER_TTL_SECONDS - age)) if session_available else 0.0,
                })

        if self._batch_depth == 0:
            self._confirmation.retain(set(self._observed))
            if self._confirmation.version != self._confirmation_version:
                self._confirmation_version = self._confirmation.version
                self._changed_locked()
        sessions.sort(key=lambda row: (row["source"], row["agent_id"], row["session_id"]))
        if "awaiting_user" in known_states:
            available, state, reason, partial = True, "awaiting_user", None, unknown_scope
        elif "working" in known_states:
            available, state, reason, partial = True, "working", None, unknown_scope
        elif unknown_scope:
            available, state, reason, partial = False, None, "stale" if self._sessions else "unavailable", False
        elif known_states or complete_inventory:
            available, state, reason, partial = True, "ready", None, False
        else:
            available, state, reason, partial = False, None, "unavailable", False

        task_state = state
        if "awaiting_user" in display_states:
            state = "awaiting_user"
        elif "working" in display_states:
            state = "working"
        elif not unknown_scope and (display_states or complete_inventory):
            state = "ready"
        valid_for_seconds = min(self.ttl_seconds, min(remaining_leases)) if available and remaining_leases else 0.0
        active_rows = [row for row in sessions if row.get("active", True)]
        reminder_only = state == "awaiting_user" and task_state == "working" and not any(
            row["available"] and row["state"] == "awaiting_user" for row in active_rows
        )
        projects = sorted({row["project_name"] for row in active_rows if row.get("project_name")})
        history_key = (state, available, partial, reminder_only, len(active_rows), tuple(projects))
        if self._batch_depth == 0:
            changed_chats = False
            current_keys = set()
            for row in sessions:
                key = (row["source"], row["session_id"])
                current_keys.add(key)
                row_state = row["display_state"] or "unknown"
                fingerprint = (row_state, row.get("active", True), row["status_reason"], row["question_reminder_active"])
                if self._history_sessions.get(key) == fingerprint:
                    continue
                self._history_sessions[key] = fingerprint
                changed_chats = True
                self._history_sequence += 1
                self._history.append({
                    "id": self._history_sequence, "at": _utc_now(), "state": row_state,
                    "reminder": row["question_reminder_active"], "partial": False, "chat_count": 1,
                    "project_name": row.get("project_name"), "source": row["source"],
                    "session_id": row["session_id"], "session_name": row.get("session_name"),
                    "status_reason": row["status_reason"],
                })
            self._history_sessions = {key: value for key, value in self._history_sessions.items() if key in current_keys}
            if history_key != self._history_key and not changed_chats:
                self._history_sequence += 1
                self._history.append({
                    "id": self._history_sequence, "at": _utc_now(), "state": state or "unknown",
                    "reminder": reminder_only, "partial": partial, "chat_count": len(active_rows),
                    "project_name": projects[0] if len(projects) == 1 else None,
                })
            self._history_key = history_key
        return {
            "schema_version": 1, "instance_id": self.instance_id, "revision": self._revision,
            "states_stabilized": self._confirmation.seconds >= 1,
            "ttl_seconds": self.ttl_seconds, "question_reminder_seconds": self.question_reminder_seconds,
            "available": available, "state": state, "task_state": task_state,
            "reason": reason, "partial": partial, "valid_for_seconds": valid_for_seconds,
            "sessions": sessions, "sources": sources, "server_time": _utc_now(),
            "history": [dict(event) for event in self._history],
        }
