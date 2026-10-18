from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_beacon.core import BeaconCore, CoreError, EventConflict, SessionLimitError


class BeaconCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = BeaconCore(ttl_seconds=1)

    def record(self, session: str, state: str, sequence: int = 1) -> dict:
        return self.core.record_event(
            agent_id="agent-1", session_id=session, state=state, sequence=sequence
        )

    def test_empty_and_state_aggregation(self) -> None:
        empty = self.core.snapshot()
        self.assertIsNone(empty["state"])
        self.assertFalse(empty["available"])
        self.assertEqual("unavailable", empty["reason"])

        self.record("one", "working")
        self.record("two", "awaiting_user")
        self.assertEqual("awaiting_user", self.core.snapshot()["state"])
        self.record("two", "ready", 2)
        self.assertEqual("working", self.core.snapshot()["state"])
        self.record("one", "ready", 2)
        self.assertEqual("ready", self.core.snapshot()["state"])

    def test_fresh_precedence_partial_and_stale_aggregation(self) -> None:
        current_time = [10.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.record("old", "ready")
            current_time[0] = 11.0
            self.record("fresh", "working")
            snapshot = self.core.snapshot()
            self.assertEqual("working", snapshot["state"])
            self.assertTrue(snapshot["partial"])
            self.assertFalse(next(s for s in snapshot["sessions"] if s["session_id"] == "old")["available"])

            current_time[0] = 12.0
            stale = self.core.snapshot()
            self.assertIsNone(stale["state"])
            self.assertFalse(stale["available"])
            self.assertEqual("stale", stale["reason"])

    def test_stale_and_fresh_ready_is_unavailable(self) -> None:
        current_time = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.record("old", "ready")
            current_time[0] = 0.5
            self.record("fresh", "ready")
            current_time[0] = 1.0
            snapshot = self.core.snapshot()
            self.assertFalse(snapshot["available"])
            self.assertIsNone(snapshot["state"])
            self.assertEqual("stale", snapshot["reason"])
            self.assertEqual(0.0, snapshot["valid_for_seconds"])

    def test_valid_for_seconds_uses_earliest_fresh_session_expiry(self) -> None:
        current_time = [2.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.record("first", "working")
            current_time[0] = 2.5
            self.record("second", "ready")
            current_time[0] = 2.75
            snapshot = self.core.snapshot()
            self.assertEqual("working", snapshot["state"])
            self.assertEqual(0.25, snapshot["valid_for_seconds"])

    def test_identical_retry_does_not_refresh_ttl_or_revision(self) -> None:
        current_time = [20.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.record("one", "ready")
            initial = self.core.snapshot()
            current_time[0] = 20.5
            retry = self.record("one", "ready")
            self.assertEqual(initial["revision"], retry["revision"])
            current_time[0] = 21.0
            self.assertEqual("stale", self.core.snapshot()["reason"])

    def test_conflicting_or_outdated_sequences_are_rejected(self) -> None:
        self.record("one", "working", 4)
        for state, sequence in (("working", 3), ("ready", 4)):
            with self.assertRaises(EventConflict):
                self.record("one", state, sequence)
        self.assertEqual(1, self.core.snapshot()["revision"])

    def test_validation_rejects_unsafe_identifiers_and_boolean_sequences(self) -> None:
        with self.assertRaises(CoreError):
            self.core.record_event(agent_id="bad/id", session_id="s", state="working", sequence=1)
        with self.assertRaises(CoreError):
            self.core.record_event(agent_id="a", session_id="s", state="working", sequence=True)
        with self.assertRaises(CoreError):
            self.core.record_event(agent_id="a", session_id="s", state=[], sequence=1)  # type: ignore[arg-type]
        with self.assertRaises(CoreError):
            self.core.record_event(agent_id="a", session_id="s", state="ready", sequence=9_007_199_254_740_992)
        self.assertEqual(0, self.core.snapshot()["revision"])

    def test_ttl_must_be_finite_and_positive(self) -> None:
        for ttl in (0, -1, float("inf"), float("nan"), 86_401):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                BeaconCore(ttl_seconds=ttl)

    def test_valid_for_seconds_uses_monotonic_time_and_expires_at_boundary(self) -> None:
        core = BeaconCore(ttl_seconds=1)
        current_time = [5.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]), \
                patch("agent_beacon.core._utc_now", return_value="2026-01-01T00:00:00.000Z"):
            inserted = core.record_event(
                agent_id="agent", session_id="session", state="awaiting_user", sequence=1
            )
            current_time[0] = 5.25
            after_quarter_second = core.snapshot()
            self.assertEqual(0.75, after_quarter_second["valid_for_seconds"])
            self.assertEqual(inserted["sessions"][0]["updated_at"], after_quarter_second["sessions"][0]["updated_at"])
            current_time[0] = 6.0
            expired = core.snapshot()
            self.assertEqual(0.0, expired["valid_for_seconds"])
            self.assertFalse(expired["available"])

    def test_unknown_resume_keeps_projection_yellow_until_original_deadline(self) -> None:
        core = BeaconCore(ttl_seconds=300, question_reminder_seconds=180)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
            core.observe_session("codex", "g1", "s", "t", "awaiting_user", 2)
            now[0] = 10
            core.update_source("codex", "g1", 2, True, True)
            resumed = core.observe_session("codex", "g1", "s", "t", "resumed", 3)
            row = resumed["sessions"][0]
            self.assertEqual("working", row["state"])
            self.assertEqual("awaiting_user", row["display_state"])
            self.assertTrue(row["question_reminder_active"])
            self.assertEqual(170, row["question_reminder_remaining_seconds"])
            self.assertEqual("working", resumed["task_state"])
            self.assertEqual("awaiting_user", resumed["state"])
            self.assertLessEqual(resumed["valid_for_seconds"], 170)
            source_sequence = 2
            for heartbeat_at in range(20, 180, 10):
                now[0] = float(heartbeat_at)
                source_sequence += 1
                core.update_source("codex", "g1", source_sequence, True, True)
            now[0] = 179
            source_sequence += 1
            core.update_source("codex", "g1", source_sequence, True, True)
            self.assertEqual("awaiting_user", core.snapshot()["sessions"][0]["display_state"])
            now[0] = 180
            source_sequence += 1
            core.update_source("codex", "g1", source_sequence, True, True)
            expired = core.snapshot()["sessions"][0]
            self.assertEqual("working", expired["state"])
            self.assertEqual("working", expired["display_state"])
            self.assertFalse(expired["question_reminder_active"])
            self.assertEqual(0, expired["question_reminder_remaining_seconds"])

    def test_waiting_stays_yellow_after_deadline_then_user_resume_turns_red(self) -> None:
        core = BeaconCore(ttl_seconds=300, question_reminder_seconds=2)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
            waiting = core.observe_session("codex", "g1", "s", "t", "awaiting_user", 2)
            self.assertEqual("awaiting_user", waiting["sessions"][0]["display_state"])
            now[0] = 3
            core.update_source("codex", "g1", 2, True, True)
            waiting_late = core.snapshot()["sessions"][0]
            self.assertEqual("awaiting_user", waiting_late["state"])
            self.assertEqual("awaiting_user", waiting_late["display_state"])
            self.assertEqual(0, waiting_late["question_reminder_remaining_seconds"])
            now[0] = 4
            core.update_source("codex", "g1", 3, True, True)
            resumed = core.observe_session("codex", "g1", "s", "t", "resumed", 3, resume_reason="user")
            self.assertEqual("working", resumed["task_state"])
            self.assertEqual("working", resumed["state"])
            self.assertFalse(resumed["sessions"][0]["question_reminder_active"])

    def test_user_resume_before_deadline_clears_reminder_immediately(self) -> None:
        core = BeaconCore(ttl_seconds=120, question_reminder_seconds=20)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
            core.observe_session("codex", "g1", "s", "t", "awaiting_user", 2)
            now[0] = 5
            core.update_source("codex", "g1", 2, True, True)
            resumed = core.observe_session(
                "codex", "g1", "s", "t", "resumed", 3, resume_reason="user"
            )
            row = resumed["sessions"][0]
            self.assertEqual("working", row["state"])
            self.assertEqual("working", row["display_state"])
            self.assertFalse(row["question_reminder_active"])
            self.assertEqual(0, row["question_reminder_remaining_seconds"])

    def test_offset_reminders_expire_per_session_and_fence_old_turn_events(self) -> None:
        core = BeaconCore(ttl_seconds=120, question_reminder_seconds=10)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.register_source("claude", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.update_source("claude", "g1", 1, True, True)

            # The first Codex question expires at t=10.
            core.observe_session("codex", "g1", "first", "t1", "turn_started", 1)
            core.observe_session("codex", "g1", "first", "t1", "awaiting_user", 2)
            core.observe_session("codex", "g1", "first", "t1", "resumed", 3, resume_reason="automatic")

            # A second session gets a new turn and a fresh, offset deadline at t=5.
            core.observe_session("codex", "g1", "second", "old", "turn_started", 1)
            core.observe_session("codex", "g1", "second", "old", "awaiting_user", 2)
            core.observe_session("codex", "g1", "second", "old", "resumed", 3, resume_reason="automatic")
            now[0] = 5
            core.update_source("codex", "g1", 2, True, True)
            core.update_source("claude", "g1", 2, True, True)
            core.observe_session("codex", "g1", "second", "new", "turn_started", 4)
            core.observe_session("codex", "g1", "second", "new", "awaiting_user", 5)
            core.observe_session("codex", "g1", "second", "new", "resumed", 6, resume_reason="automatic")
            core.observe_session("claude", "g1", "other-provider", "t1", "turn_started", 1)
            core.observe_session("claude", "g1", "other-provider", "t1", "awaiting_user", 2)
            core.observe_session("claude", "g1", "other-provider", "t1", "resumed", 3, resume_reason="automatic")

            now[0] = 10
            core.update_source("codex", "g1", 3, True, True)
            core.update_source("claude", "g1", 3, True, True)
            rows = {row["session_id"]: row for row in core.snapshot()["sessions"]}
            self.assertEqual("working", rows["first"]["display_state"])
            self.assertFalse(rows["first"]["question_reminder_active"])
            self.assertEqual(0, rows["first"]["question_reminder_remaining_seconds"])
            self.assertEqual("awaiting_user", rows["second"]["display_state"])
            self.assertEqual(5, rows["second"]["question_reminder_remaining_seconds"])
            self.assertEqual("awaiting_user", rows["other-provider"]["display_state"])
            self.assertEqual(5, rows["other-provider"]["question_reminder_remaining_seconds"])

            with self.assertRaises(EventConflict):
                core.observe_session("codex", "g1", "second", "old", "awaiting_user", 7)
            after_rejected_old_turn = core.snapshot()
            second = next(row for row in after_rejected_old_turn["sessions"] if row["session_id"] == "second")
            self.assertEqual("working", second["state"])
            self.assertEqual("awaiting_user", second["display_state"])
            self.assertEqual(5, second["question_reminder_remaining_seconds"])

    def test_zero_reminder_keeps_waiting_yellow_but_does_not_hold_resume(self) -> None:
        core = BeaconCore(question_reminder_seconds=0)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
            waiting = core.observe_session("codex", "g1", "s", "t", "awaiting_user", 2)
            self.assertEqual("awaiting_user", waiting["state"])
            core.update_source("codex", "g1", 2, True, True)
            resumed = core.observe_session("codex", "g1", "s", "t", "resumed", 3)
            self.assertEqual("working", resumed["state"])

    def test_active_reminder_wins_global_priority_and_completion_turns_green(self) -> None:
        core = BeaconCore(question_reminder_seconds=180)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "working", "t1", "turn_started", 1)
            core.observe_session("codex", "g1", "waiting", "t2", "turn_started", 1)
            core.observe_session("codex", "g1", "waiting", "t2", "awaiting_user", 2)
            core.observe_session("codex", "g1", "working", "t1", "response_completed", 2)
            now[0] = 1
            core.update_source("codex", "g1", 2, True, True)
            held = core.observe_session("codex", "g1", "waiting", "t2", "resumed", 3)
            self.assertEqual("awaiting_user", held["state"])
            self.assertEqual("working", held["task_state"])
            now[0] = 2
            core.update_source("codex", "g1", 3, True, True)
            complete = core.observe_session("codex", "g1", "waiting", "t2", "response_completed", 4)
            row = next(item for item in complete["sessions"] if item["session_id"] == "waiting")
            self.assertEqual("ready", row["state"])
            self.assertEqual("ready", row["display_state"])
            self.assertFalse(row["question_reminder_active"])
            self.assertEqual("ready", complete["state"])

    def test_new_question_refreshes_hold_and_new_turn_clears_it(self) -> None:
        core = BeaconCore(ttl_seconds=300, question_reminder_seconds=100)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("claude", "g1")
            core.update_source("claude", "g1", 1, True, True)
            core.observe_session("claude", "g1", "s", "t1", "turn_started", 1)
            core.observe_session("claude", "g1", "s", "t1", "awaiting_user", 2)
            now[0] = 40
            core.update_source("claude", "g1", 2, True, True)
            refreshed = core.observe_session("claude", "g1", "s", "t1", "awaiting_user", 3)
            self.assertEqual(100, refreshed["sessions"][0]["question_reminder_remaining_seconds"])
            with self.assertRaises(EventConflict):
                core.observe_session("claude", "g1", "s", "t1", "awaiting_user", 3)
            now[0] = 41
            core.update_source("claude", "g1", 3, True, True)
            new_turn = core.observe_session("claude", "g1", "s", "t2", "turn_started", 4)
            self.assertFalse(new_turn["sessions"][0]["question_reminder_active"])
            self.assertEqual("working", new_turn["sessions"][0]["display_state"])

    def test_health_loss_clears_hold_without_resurrection(self) -> None:
        core = BeaconCore(ttl_seconds=300, question_reminder_seconds=180)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
            core.observe_session("codex", "g1", "s", "t", "awaiting_user", 2)
            now[0] = 1
            core.update_source("codex", "g1", 2, False, True)
            unknown = core.snapshot()["sessions"][0]
            self.assertIsNone(unknown["state"])
            self.assertIsNone(unknown["display_state"])
            self.assertFalse(unknown["question_reminder_active"])
            now[0] = 2
            core.update_source("codex", "g1", 3, True, True)
            restored = core.snapshot()["sessions"][0]
            self.assertIsNone(restored["state"])
            self.assertFalse(restored["question_reminder_active"])

    def test_reminder_duration_validation_rejects_bool_and_nonfinite(self) -> None:
        for duration in (True, -1, float("inf"), float("nan"), 86_401):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                BeaconCore(question_reminder_seconds=duration)

    def test_resume_reason_is_validated_with_clear_error(self) -> None:
        core = BeaconCore()
        core.register_source("codex", "g1")
        core.update_source("codex", "g1", 1, True, True)
        core.observe_session("codex", "g1", "s", "t", "turn_started", 1)
        with self.assertRaisesRegex(CoreError, "resume_reason must be 'user', 'automatic', or 'unknown'"):
            core.observe_session("codex", "g1", "s", "t", "resumed", 2, resume_reason="maybe")

    def test_capacity_and_clear_are_bounded_atomic_operations(self) -> None:
        with self.assertRaises(ValueError):
            BeaconCore(max_sessions=129)
        core = BeaconCore(max_sessions=1)
        core.record_event(agent_id="a", session_id="s1", state="ready", sequence=1)
        with self.assertRaises(SessionLimitError):
            core.record_event(agent_id="a", session_id="s2", state="ready", sequence=1)
        cleared = core.clear_sessions()
        self.assertEqual([], cleared["sessions"])
        self.assertEqual(2, cleared["revision"])
        self.assertEqual(2, core.clear_sessions()["revision"])

    def test_concurrent_events_keep_single_highest_sequence(self) -> None:
        import threading

        errors: list[Exception] = []

        def submit(sequence: int) -> None:
            try:
                self.core.record_event(
                    agent_id="agent", session_id="session", state="working", sequence=sequence
                )
            except EventConflict as error:
                errors.append(error)

        threads = [threading.Thread(target=submit, args=(number,)) for number in range(1, 31)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        snapshot = self.core.snapshot()
        self.assertEqual(30, snapshot["sessions"][0]["sequence"])
        self.assertEqual(30, snapshot["revision"] + len(errors))

    def register_healthy(self, core: BeaconCore, source: str, generation: str = "g1", sequence: int = 1) -> None:
        core.register_source(source, generation)
        core.update_source(source, generation, sequence, True, True)

    def test_provider_namespace_and_turn_sequences_are_independent(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        self.register_healthy(core, "claude")
        for source in ("codex", "claude"):
            core.observe_session(source, "g1", "same-session", "turn-a", "turn_started", 1)
        core.observe_session("codex", "g1", "same-session", "turn-a", "response_completed", 2)
        core.observe_session("codex", "g1", "same-session", "turn-b", "turn_started", 3)
        with self.assertRaises(EventConflict):
            core.observe_session("codex", "g1", "same-session", "turn-a", "response_completed", 4)
        snapshot = core.snapshot()
        self.assertEqual("working", snapshot["state"])
        self.assertEqual(2, len([s for s in snapshot["sessions"] if s["session_id"] == "same-session"]))

    def test_long_running_task_survives_hours_without_turn_events(self) -> None:
        core = BeaconCore(ttl_seconds=30)
        current_time = [100.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.register_healthy(core, "codex")
            core.observe_session("codex", "g1", "overnight", "turn-1", "turn_started", 1)
            for heartbeat_sequence in range(2, 1_082):
                current_time[0] += 10
                core.update_source("codex", "g1", heartbeat_sequence, True, True)
            snapshot = core.snapshot()
            self.assertEqual("working", snapshot["state"])
            self.assertTrue(snapshot["sessions"][0]["available"])

    def test_health_gap_requires_explicit_lifecycle_reconciliation(self) -> None:
        core = BeaconCore()
        current_time = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: current_time[0]):
            self.register_healthy(core, "codex")
            core.observe_session("codex", "g1", "s", "t1", "turn_started", 1)
            current_time[0] = 20
            recovered = core.update_source("codex", "g1", 2, True, True)
            self.assertIsNone(recovered["sessions"][0]["state"])
            self.assertFalse(recovered["sessions"][0]["available"])
            self.assertFalse(recovered["available"])

            resumed = core.observe_session("codex", "g1", "s", "t1", "resumed", 2)
            self.assertEqual("working", resumed["state"])
            completed = core.observe_session("codex", "g1", "s", "t1", "response_completed", 3)
            self.assertEqual("ready", completed["state"])

    def test_explicit_unhealthy_heartbeat_breaks_continuity(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "claude")
        core.observe_session("claude", "g1", "s", "t1", "turn_started", 1)
        core.update_source("claude", "g1", 2, False, True)
        recovered = core.update_source("claude", "g1", 3, True, True)
        self.assertIsNone(recovered["sessions"][0]["state"])

    def test_observer_outage_blocks_green_but_known_waiting_wins(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        core.observe_session("codex", "g1", "waiting", "t1", "turn_started", 1)
        core.observe_session("codex", "g1", "waiting", "t1", "awaiting_user", 2)
        core.record_event(agent_id="a", session_id="waiting", state="awaiting_user", sequence=1)
        core.update_source("codex", "g1", 2, False, True)
        snapshot = core.snapshot()
        self.assertEqual("awaiting_user", snapshot["state"])
        self.assertTrue(snapshot["partial"])
        self.assertFalse(snapshot["sources"][0]["healthy"])

    def test_source_coverage_defaults_to_all_sessions_and_is_validated(self) -> None:
        core = BeaconCore()
        snapshot = core.register_source("codex", "g1")
        self.assertEqual("all_sessions", snapshot["sources"][0]["coverage"])
        with self.assertRaisesRegex(CoreError, "coverage must be"):
            core.register_source("claude", "g1", coverage="some_sessions")
        with self.assertRaisesRegex(EventConflict, "coverage cannot change"):
            core.register_source("codex", "g1", coverage="observed_sessions")

    def test_empty_observed_cohort_is_dim_and_does_not_block_other_ready(self) -> None:
        core = BeaconCore(ttl_seconds=100)
        core.register_source("codex", "g1", coverage="observed_sessions")
        core.update_source("codex", "g1", 1, False, False)
        core.record_event(agent_id="manual", session_id="ready", state="ready", sequence=1)

        snapshot = core.snapshot()

        self.assertEqual("ready", snapshot["state"])
        self.assertEqual(100, snapshot["valid_for_seconds"])
        self.assertEqual("observed_sessions", snapshot["sources"][0]["coverage"])

    def test_empty_or_closed_observed_cohort_alone_stays_dim(self) -> None:
        core = BeaconCore()
        core.register_source("codex", "g1", coverage="observed_sessions")
        core.update_source("codex", "g1", 1, True, True)
        self.assertFalse(core.snapshot()["available"])

        core.observe_session("codex", "g1", "done", "t1", "turn_started", 1)
        core.observe_session("codex", "g1", "done", "t1", "response_completed", 2)
        closed = core.observe_session("codex", "g1", "done", "t1", "session_closed", 3)
        self.assertFalse(closed["available"])
        self.assertIsNone(closed["state"])
        self.assertFalse(closed["sessions"][0]["active"])

    def test_closed_observed_cohort_does_not_block_other_ready(self) -> None:
        core = BeaconCore(ttl_seconds=100)
        core.register_source("codex", "g1", coverage="observed_sessions")
        core.update_source("codex", "g1", 1, True, True)
        core.observe_session("codex", "g1", "done", "t1", "turn_started", 1)
        core.observe_session("codex", "g1", "done", "t1", "response_completed", 2)
        core.observe_session("codex", "g1", "done", "t1", "session_closed", 3)
        ready = core.record_event(
            agent_id="manual", session_id="other", state="ready", sequence=1
        )

        self.assertEqual("ready", ready["state"])
        self.assertEqual(100, ready["valid_for_seconds"])

    def test_observed_cohort_health_loss_clears_reminder_and_blocks_ready(self) -> None:
        core = BeaconCore(ttl_seconds=300, question_reminder_seconds=180)
        core.register_source("claude", "g1", coverage="observed_sessions")
        core.update_source("claude", "g1", 1, True, True)
        core.observe_session("claude", "g1", "s", "t", "turn_started", 1)
        core.observe_session("claude", "g1", "s", "t", "awaiting_user", 2)
        held = core.observe_session("claude", "g1", "s", "t", "resumed", 3)
        self.assertTrue(held["sessions"][0]["question_reminder_active"])

        core.record_event(agent_id="manual", session_id="ready", state="ready", sequence=1)
        core.update_source("claude", "g1", 2, False, True)
        unknown = core.snapshot()
        row = next(item for item in unknown["sessions"] if item["session_id"] == "s")
        self.assertFalse(unknown["available"])
        self.assertIsNone(unknown["state"])
        self.assertIsNone(row["display_state"])
        self.assertFalse(row["question_reminder_active"])

        core.update_source("claude", "g1", 3, True, True)
        recovered = core.snapshot()
        row = next(item for item in recovered["sessions"] if item["session_id"] == "s")
        self.assertFalse(recovered["available"])
        self.assertIsNone(row["state"])
        self.assertFalse(row["question_reminder_active"])

    def test_stale_observed_cohort_lease_blocks_ready(self) -> None:
        core = BeaconCore(ttl_seconds=300)
        now = [0.0]
        with patch("agent_beacon.core.time.monotonic", side_effect=lambda: now[0]):
            core.register_source("codex", "g1", coverage="observed_sessions")
            core.update_source("codex", "g1", 1, True, True)
            core.observe_session("codex", "g1", "done", "t1", "turn_started", 1)
            core.observe_session("codex", "g1", "done", "t1", "response_completed", 2)
            now[0] = 15.0

            stale = core.snapshot()

        self.assertFalse(stale["available"])
        self.assertIsNone(stale["state"])
        row = stale["sessions"][0]
        self.assertFalse(row["available"])
        self.assertIsNone(row["display_state"])

    def test_retired_active_cohort_session_blocks_ready_until_reconciled(self) -> None:
        core = BeaconCore()
        core.register_source("codex", "g1", coverage="observed_sessions")
        core.update_source("codex", "g1", 1, True, True)
        core.observe_session("codex", "g1", "codex-session", "old-turn", "turn_started", 1)
        core.observe_session("codex", "g1", "codex-session", "old-turn", "response_completed", 2)
        self.register_healthy(core, "claude")
        core.observe_session("claude", "g1", "claude-session", "turn", "turn_started", 1)
        core.observe_session("claude", "g1", "claude-session", "turn", "response_completed", 2)

        core.register_source("codex", "g2", coverage="observed_sessions")
        core.update_source("codex", "g2", 1, True, True)
        after_restart = core.snapshot()
        old_row = next(row for row in after_restart["sessions"] if row["session_id"] == "codex-session")
        self.assertFalse(after_restart["available"])
        self.assertIsNone(after_restart["state"])
        self.assertTrue(old_row["active"])
        self.assertFalse(old_row["available"])
        self.assertIsNone(old_row["display_state"])

        core.observe_session("codex", "g2", "codex-session", "new-turn", "turn_started", 1)
        self.assertEqual("working", core.snapshot()["state"])
        reconciled = core.observe_session(
            "codex", "g2", "codex-session", "new-turn", "response_completed", 2
        )
        self.assertEqual("ready", reconciled["state"])

    def test_child_completion_does_not_finish_working_parent(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "claude")
        core.observe_session("claude", "g1", "root", "root-turn", "turn_started", 1)
        core.observe_session("claude", "g1", "child", "child-turn", "turn_started", 1, parent_session_id="root")
        core.observe_session("claude", "g1", "child", "child-turn", "response_completed", 2)
        self.assertEqual("working", core.snapshot()["state"])

    def test_closed_sessions_are_retained_but_excluded_from_aggregation(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        core.observe_session("codex", "g1", "done", "t1", "turn_started", 1)
        core.observe_session("codex", "g1", "done", "t1", "session_closed", 2)
        snapshot = core.snapshot()
        self.assertEqual("ready", snapshot["state"])
        self.assertFalse(snapshot["sessions"][0]["active"])

    def test_old_turn_cannot_close_a_new_turn(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        core.observe_session("codex", "g1", "s", "old", "turn_started", 1)
        core.observe_session("codex", "g1", "s", "new", "turn_started", 2)
        with self.assertRaises(EventConflict):
            core.observe_session("codex", "g1", "s", "old", "session_closed", 3)
        self.assertTrue(core.snapshot()["sessions"][0]["active"])

    def test_turn_stopped_marks_only_the_current_turn_ready_and_clears_reminder(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        core.observe_session("codex", "g1", "s", "t1", "turn_started", 1)
        core.observe_session("codex", "g1", "s", "t1", "awaiting_user", 2)
        stopped = core.observe_session("codex", "g1", "s", "t1", "turn_stopped", 3)
        self.assertEqual("ready", stopped["state"])
        self.assertEqual("ready", stopped["sessions"][0]["state"])
        self.assertIsNone(core._observed[("codex", "s")]["_question_deadline"])

        core.observe_session("codex", "g1", "s", "t2", "turn_started", 4)
        with self.assertRaises(EventConflict):
            core.observe_session("codex", "g1", "s", "t1", "turn_stopped", 5)
        self.assertEqual("working", core.snapshot()["state"])

    def test_closed_session_reopens_only_after_new_session_start(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "claude")
        core.observe_session("claude", "g1", "chat", "old-turn", "turn_started", 1)
        core.observe_session("claude", "g1", "chat", None, "session_closed", 2)
        with self.assertRaises(EventConflict):
            core.observe_session("claude", "g1", "chat", "new-turn", "turn_started", 3)

        restarted = core.observe_session("claude", "g1", "chat", None, "session_started", 3)
        self.assertIsNone(restarted["sessions"][0]["state"])
        self.assertIsNone(restarted["sessions"][0]["turn_id"])
        started = core.observe_session("claude", "g1", "chat", "new-turn", "turn_started", 4)
        self.assertEqual("working", started["state"])
        with self.assertRaises(EventConflict):
            core.observe_session("claude", "g1", "chat", "old-turn", "session_closed", 5)
        with self.assertRaises(EventConflict):
            core.observe_session("claude", "g1", "chat", "old-turn", "response_completed", 5)

    def test_clear_only_removes_manual_sessions(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex")
        core.observe_session("codex", "g1", "observed", "t1", "turn_started", 1)
        core.record_event(agent_id="a", session_id="demo", state="working", sequence=1)
        snapshot = core.clear_sessions()
        self.assertEqual(["observed"], [item["session_id"] for item in snapshot["sessions"]])

    def test_generation_change_invalidates_old_state_and_rejects_old_updates(self) -> None:
        core = BeaconCore()
        self.register_healthy(core, "codex", "g1")
        core.observe_session("codex", "g1", "s", "t1", "turn_started", 1)
        core.register_source("codex", "g2")
        with self.assertRaises(EventConflict):
            core.update_source("codex", "g1", 2, True, True)
        with self.assertRaises(EventConflict):
            core.register_source("codex", "g1")
        core.update_source("codex", "g2", 1, True, True)
        core.observe_session("codex", "g2", "s", "t2", "turn_started", 1)
        self.assertEqual("working", core.snapshot()["state"])

    def test_restart_has_no_persisted_provider_state(self) -> None:
        first = BeaconCore()
        self.register_healthy(first, "claude")
        first.observe_session("claude", "g1", "s", "t1", "turn_started", 1)
        restarted = BeaconCore()
        self.assertEqual([], restarted.snapshot()["sessions"])
        self.assertFalse(restarted.snapshot()["available"])

    def test_observer_limits_and_lifecycle_validation(self) -> None:
        core = BeaconCore()
        with self.assertRaises(CoreError):
            core.register_source("other", "g1")
        core.register_source("codex", "g1")
        with self.assertRaises(EventConflict):
            core.update_source("codex", "old", 1, True, True)
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", None, "resumed", 1)
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", "turn_started", 9_007_199_254_740_992)
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", [], 1)  # type: ignore[arg-type]
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1, project_name="bad\nname")
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1, project_name="C:/private/project")
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1, project_name="C:private-project")
        with self.assertRaises(CoreError):
            core.observe_session("codex", "g1", "s", "t", "turn_started", 1, parent_session_id="s")


if __name__ == "__main__":
    unittest.main()
