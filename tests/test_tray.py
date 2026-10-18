from __future__ import annotations

import ctypes
import json
import threading
import time
import unittest
from unittest.mock import patch

from agent_beacon.server import create_server
from agent_beacon.tray import (
    BeaconHTTPClient,
    TrayDisplayState,
    TrayProtocolError,
    _STRINGS,
    _tray_state_text,
    _Win32Tray,
    _icon_lamp_color,
    _icon_bitmap_size,
    _icon_mask,
    _lamp_geometry,
    _rasterize_lamp,
    manual_control_payload,
    run_tray,
    validate_control,
    validate_snapshot,
)


def valid_snapshot() -> dict:
    return {
        "schema_version": 1,
        "instance_id": "instance-1",
        "revision": 2,
        "ttl_seconds": 30,
        "valid_for_seconds": 28.5,
        "available": True,
        "state": "working",
        "reason": None,
        "partial": False,
        "sessions": [
            {
                "agent_id": "demo-agent",
                "session_id": "demo-session",
                "source": "manual",
                "sequence": 1,
                "state": "working",
                "available": True,
                "updated_at": "2026-10-16T12:00:00.000Z",
            }
        ],
        "server_time": "2026-10-16T12:00:00.000Z",
        "sources": [{"source": "codex", "healthy": True}],
    }


class TrayProtocolTests(unittest.TestCase):
    def test_ready_label_describes_availability_for_the_next_task(self) -> None:
        snapshot = valid_snapshot()
        snapshot["state"] = "ready"
        self.assertEqual("Готовий до завдання", _tray_state_text(snapshot))
        self.assertNotIn("заверш", _tray_state_text(snapshot).lower())

    def test_lamp_geometry_fills_a_centered_square_icon_with_a_circle(self) -> None:
        center_x, center_y, radius = _lamp_geometry(16)
        self.assertEqual((7.5, 7.5), (center_x, center_y))
        self.assertEqual(7, radius)

        center_x, center_y, radius = _lamp_geometry(24)
        self.assertEqual((11.5, 11.5), (center_x, center_y))
        self.assertEqual(11, radius)
        self.assertEqual(48, _icon_bitmap_size(48, 48))
        self.assertEqual(32, _icon_bitmap_size(24, 32))
        self.assertEqual(16, _icon_bitmap_size(0, 0))

        for size in (16, 24, 32, 48):
            center_x, center_y, radius = _lamp_geometry(size)
            mask = {
                (x, y)
                for y in range(size)
                for x in range(size)
                if (x - center_x) ** 2 + (y - center_y) ** 2 <= radius * radius
            }
            self.assertEqual(mask, {(size - 1 - x, y) for x, y in mask})
            self.assertEqual(mask, {(x, size - 1 - y) for x, y in mask})
            padding = max(1, size // 16)
            self.assertEqual(padding, min(x for x, _ in mask))
            self.assertEqual(size - 1 - padding, max(x for x, _ in mask))

    def test_single_lamp_color_tracks_state_and_uses_gray_when_unknown(self) -> None:
        self.assertEqual((255, 85, 79), _icon_lamp_color("working"))
        self.assertEqual((255, 195, 77), _icon_lamp_color("awaiting_user"))
        self.assertEqual((98, 223, 156), _icon_lamp_color("ready"))
        self.assertEqual((128, 132, 140), _icon_lamp_color(None))

    def test_rasterized_lamp_is_round_symmetric_and_antialiased(self) -> None:
        for size in (16, 24, 32, 48):
            pixels = _rasterize_lamp(size, _icon_lamp_color("working"))
            with self.subTest(size=size):
                self.assertEqual(size * size, len(pixels))
                alpha = [pixel >> 24 for pixel in pixels]
                self.assertTrue(any(0 < value < 255 for value in alpha))
                self.assertTrue(all(alpha[0 * size + x] == 0 for x in range(size)))
                self.assertTrue(all(alpha[(size - 1) * size + x] == 0 for x in range(size)))
                self.assertTrue(all(alpha[y * size] == 0 for y in range(size)))
                self.assertTrue(all(alpha[y * size + size - 1] == 0 for y in range(size)))
                self.assertEqual(alpha, [alpha[y * size + (size - 1 - x)]
                                         for y in range(size) for x in range(size)])
                self.assertEqual(alpha, [alpha[(size - 1 - y) * size + x]
                                         for y in range(size) for x in range(size)])

    def test_rasterized_lamp_uses_premultiplied_argb_and_transparent_and_mask(self) -> None:
        for state in ("working", "awaiting_user", "ready", None):
            pixels = _rasterize_lamp(16, _icon_lamp_color(state))
            alpha = [pixel >> 24 for pixel in pixels]
            color = _icon_lamp_color(state)
            opaque_pixel = pixels[7 * 16 + 7]
            self.assertEqual(255, opaque_pixel >> 24)
            self.assertEqual(color, ((opaque_pixel >> 16) & 255,
                                     (opaque_pixel >> 8) & 255,
                                     opaque_pixel & 255))
            edge_pixel = next(pixel for pixel in pixels if 0 < pixel >> 24 < 255)
            edge_alpha = edge_pixel >> 24
            expected = tuple((channel * edge_alpha + 127) // 255 for channel in color)
            self.assertEqual(expected, ((edge_pixel >> 16) & 255,
                                        (edge_pixel >> 8) & 255,
                                        edge_pixel & 255))

            mask = _icon_mask(pixels, 16)
            self.assertEqual(32, len(mask))
            self.assertTrue(mask[0] & 0x80)  # Transparent top-left pixel uses AND bit 1.
            self.assertEqual(0, mask[7 * 2 + 0] & 0x02)  # Opaque disk pixel uses AND bit 0.

    def test_control_validation_keeps_credentials_only_in_return_value(self) -> None:
        control = validate_control({"viewer_token": "t" * 32, "instance_id": "abc"})
        self.assertEqual(control["instance_id"], "abc")
        with self.assertRaises(TrayProtocolError):
            validate_control({"viewer_token": "short", "instance_id": "abc"})

    def test_snapshot_accepts_additive_observer_fields(self) -> None:
        snapshot = valid_snapshot()
        snapshot["sessions"].append({
            "agent_id": "claude", "session_id": "sess-2", "source": "claude",
            "sequence": 4, "state": None, "available": False, "updated_at": "now",
            "turn_id": "turn-1", "project_name": "safe project", "active": True,
        })
        validated = validate_snapshot(snapshot)
        self.assertEqual(validated["sessions"][1]["source"], "claude")
        self.assertEqual(validated["sessions"][1]["project_name"], "safe project")

    def test_snapshot_preserves_safe_question_projection_fields(self) -> None:
        snapshot = valid_snapshot()
        snapshot.update(task_state="working", question_reminder_seconds=180)
        snapshot["sessions"][0].update(
            display_state="awaiting_user", question_reminder_active=True,
            question_reminder_remaining_seconds=12.5,
        )
        validated = validate_snapshot(snapshot)
        row = validated["sessions"][0]
        self.assertEqual("working", row["state"])
        self.assertEqual("awaiting_user", row["display_state"])
        self.assertTrue(row["question_reminder_active"])
        self.assertEqual(12.5, row["question_reminder_remaining_seconds"])

    def test_snapshot_rejects_inconsistent_question_projection_fields(self) -> None:
        snapshot = valid_snapshot()
        snapshot["sessions"][0].update(
            display_state="ready", question_reminder_active=True,
            question_reminder_remaining_seconds=12,
        )
        with self.assertRaises(TrayProtocolError):
            validate_snapshot(snapshot)

    def test_unavailable_unknown_snapshot_is_valid_and_has_no_active_lease(self) -> None:
        snapshot = valid_snapshot()
        snapshot.update(available=False, state=None, reason="unavailable", valid_for_seconds=0)
        validated = validate_snapshot(snapshot)
        self.assertIsNone(validated["state"])
        self.assertEqual(validated["valid_for_seconds"], 0)

    def test_snapshot_rejects_bad_numeric_types_and_identifiers(self) -> None:
        for edit in (
            lambda item: item.update(valid_for_seconds=float("nan")),
            lambda item: item.update(ttl_seconds=True),
            lambda item: item["sessions"][0].update(agent_id="bad/name"),
            lambda item: item["sessions"][0].update(available="yes"),
        ):
            snapshot = valid_snapshot()
            edit(snapshot)
            with self.subTest(snapshot=snapshot), self.assertRaises(TrayProtocolError):
                validate_snapshot(snapshot)

    def test_snapshot_rejects_closed_observed_session_claiming_availability(self) -> None:
        snapshot = valid_snapshot()
        snapshot["sessions"] = [{
            "agent_id": "codex", "session_id": "session-1", "source": "codex",
            "sequence": 4, "state": "working", "available": True,
            "updated_at": "2026-10-16T12:00:00.000Z", "turn_id": "turn-1",
            "project_name": "safe-project", "parent_session_id": None, "active": False,
        }]
        with self.assertRaises(TrayProtocolError):
            validate_snapshot(snapshot)

    def test_manual_demo_payload_increments_only_its_own_session(self) -> None:
        snapshot = validate_snapshot(valid_snapshot())
        first = manual_control_payload(snapshot, "awaiting_user")
        self.assertEqual((first["agent_id"], first["session_id"], first["sequence"]),
                         ("tray-demo", "tray-session", 1))
        snapshot["sessions"].append({
            "agent_id": "tray-demo", "session_id": "tray-session", "source": "manual",
            "sequence": 8, "state": "working", "available": True,
        })
        self.assertEqual(manual_control_payload(snapshot, "ready")["sequence"], 9)

    def test_display_lease_expires_using_monotonic_deadline(self) -> None:
        display = TrayDisplayState(validate_snapshot(valid_snapshot()), expires_at=50.0)
        self.assertIsNotNone(display.fresh_snapshot(now=49.9))
        self.assertIsNone(display.fresh_snapshot(now=50.0))

    def test_tray_session_menu_uses_projected_state_while_preserving_actual_state(self) -> None:
        snapshot = valid_snapshot()
        snapshot.update(state="awaiting_user", task_state="working", question_reminder_seconds=180)
        snapshot["sessions"][0].update(
            display_state="awaiting_user", question_reminder_active=True,
            question_reminder_remaining_seconds=10,
        )
        native = _Win32Tray.__new__(_Win32Tray)
        native.state_lock = threading.Lock()
        native.display = TrayDisplayState(validate_snapshot(snapshot), expires_at=time.monotonic() + 10)
        labels: list[str] = []
        native._append = lambda _menu, _flags, _item_id, label: labels.append(label)
        native._build_menu(None, None, None, set())
        self.assertTrue(any(label.endswith(" · Нагадування про запит") for label in labels))
        self.assertTrue(any(label == "Стан: Нагадування про запит · агент працює" for label in labels))
        self.assertEqual("working", native.display.snapshot["sessions"][0]["state"])

    def test_tray_reminder_label_yields_to_a_real_waiting_session(self) -> None:
        snapshot = valid_snapshot()
        snapshot.update(state="awaiting_user", task_state="working", question_reminder_seconds=180)
        snapshot["sessions"][0].update(
            display_state="awaiting_user", question_reminder_active=True,
            question_reminder_remaining_seconds=10,
        )
        self.assertEqual("Нагадування про запит · агент працює", _tray_state_text(snapshot))
        real_waiter = dict(snapshot["sessions"][0], agent_id="manual", session_id="waiting", source="manual", state="awaiting_user")
        snapshot["sessions"].append(real_waiter)
        self.assertEqual(_STRINGS["awaiting_user"], _tray_state_text(snapshot))
        self.assertEqual("Стан агента не підтверджено", _STRINGS["unavailable"])

    def test_windows_platform_guard_is_clear(self) -> None:
        with patch("agent_beacon.tray.os.name", "posix"):
            with self.assertRaisesRegex(OSError, "only on Windows"):
                run_tray()

    def test_partial_menu_build_destroys_attached_and_unattached_menus_once(self) -> None:
        class FakeUser32:
            def __init__(self) -> None:
                self.created = 0
                self.destroyed: list[int] = []

            def CreatePopupMenu(self) -> int:
                self.created += 1
                return self.created

            def AppendMenuW(self, menu: int, flags: int, item_id: int, label: str) -> bool:
                return menu != 3

            def DestroyMenu(self, menu: int) -> bool:
                self.destroyed.append(menu)
                return True

        native = _Win32Tray.__new__(_Win32Tray)
        native.user32 = FakeUser32()
        native._menu_handles = []
        native.state_lock = threading.Lock()
        native.display = TrayDisplayState(None, 0)

        with self.assertRaises(Exception):
            native._show_menu()

        # Destroying the root recursively destroys the attached sessions menu;
        # the demo menu failed before attachment and needs its own cleanup.
        self.assertEqual([1, 3], native.user32.destroyed)
        self.assertEqual([], native._menu_handles)

    def test_notify_uses_one_stable_tray_slot_for_all_operations(self) -> None:
        class FakeNotifyData(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint), ("hWnd", ctypes.c_void_p),
                ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
                ("uCallbackMessage", ctypes.c_uint), ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128), ("uTimeoutOrVersion", ctypes.c_uint),
            ]

        class FakeShell32:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int]] = []

            def Shell_NotifyIconW(self, operation: int, data_ref: object) -> bool:
                self.calls.append((operation, data_ref._obj.uID))
                return True

        native = _Win32Tray.__new__(_Win32Tray)
        native.NOTIFYICONDATAW = FakeNotifyData
        native.shell32 = FakeShell32()
        native.hwnd = None
        native.hicon = 123
        native.notify_version4 = False
        native._display_text = lambda: "Стан агента не підтверджено"

        native._notify(native.NIM_ADD)
        native._notify(native.NIM_MODIFY)
        native._notify(native.NIM_DELETE)
        self.assertEqual(
            [(native.NIM_ADD, 1), (native.NIM_MODIFY, 1), (native.NIM_DELETE, 1)],
            native.shell32.calls,
        )

    def test_icon_cache_builds_one_handle_and_destroys_each_cached_handle(self) -> None:
        class FakeUser32:
            def __init__(self) -> None:
                self.destroyed: list[int] = []

            def DestroyIcon(self, icon: int) -> bool:
                self.destroyed.append(icon)
                return True

        native = _Win32Tray.__new__(_Win32Tray)
        native.icon_cache = {}
        native.user32 = FakeUser32()
        native.hicon = None
        created: list[str | None] = []

        def make_icon(state: str | None) -> int:
            created.append(state)
            return 100 + len(created)

        native._make_icon = make_icon
        self.assertEqual(101, native._icon_for("working"))
        self.assertEqual(101, native._icon_for("working"))
        self.assertEqual(["working"], created)
        native.icon_cache[None] = 102
        native._destroy_icon_cache()
        self.assertEqual([101, 102], native.user32.destroyed)
        self.assertEqual({}, native.icon_cache)
        self.assertIsNone(native.hicon)

    def test_taskbar_created_readds_single_icon_and_sets_version(self) -> None:
        native = _Win32Tray.__new__(_Win32Tray)
        native.WM_TRAY = _Win32Tray.WM_TRAY
        native.WM_TIMER = _Win32Tray.WM_TIMER
        native.WM_COMMAND = _Win32Tray.WM_COMMAND
        native.WM_CLOSE = _Win32Tray.WM_CLOSE
        native.WM_DESTROY = _Win32Tray.WM_DESTROY
        native.taskbar_created = 0xC123
        native.notify_version4 = False
        native.icon_added = False
        calls: list[tuple[int, bool]] = []
        native._notify = lambda operation, version=False: calls.append((operation, version))

        self.assertEqual(0, native._window_proc(None, native.taskbar_created, 0, 0))
        self.assertEqual([(native.NIM_ADD, False), (native.NIM_SETVERSION, True)], calls)
        self.assertTrue(native.icon_added)
        self.assertTrue(native.notify_version4)


class TrayHTTPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server("127.0.0.1", 0, 30)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = BeaconHTTPClient("127.0.0.1", self.server.server_port)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_bootstrap_snapshot_and_demo_control(self) -> None:
        initial, rtt = self.client.get_snapshot()
        self.assertFalse(initial["available"])
        self.assertGreaterEqual(rtt, 0)
        updated = self.client.post_manual(initial, "working")
        self.assertTrue(updated["available"])
        self.assertEqual(updated["sessions"][0]["agent_id"], "tray-demo")
        cleared = self.client.clear_manual_sessions()
        self.assertFalse(cleared["available"])

    def test_server_accepts_question_reminder_keyword_without_changing_positional_api(self) -> None:
        server = create_server("127.0.0.1", 0, 30, question_reminder_seconds=12)
        try:
            self.assertEqual(12, server.core.question_reminder_seconds)
        finally:
            server.server_close()

    def test_snapshot_bound_accepts_maximum_valid_unicode_inventory(self) -> None:
        self.server.core.register_source("codex", "g1")
        self.server.core.update_source("codex", "g1", 1, True, True)
        for index in range(128):
            self.server.core.observe_session(
                "codex", "g1", f"session-{index}", "t" * 64, "turn_started", 1,
                project_name="😀" * 160,
            )

        raw_snapshot = json.dumps(self.server.core.snapshot(), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.assertGreater(len(raw_snapshot), 256 * 1024)
        self.assertLessEqual(len(raw_snapshot), 512 * 1024)
        snapshot, _ = self.client.get_snapshot()
        self.assertEqual(128, len(snapshot["sessions"]))
        self.assertEqual("😀" * 160, snapshot["sessions"][-1]["project_name"])

    def test_restart_refreshes_expired_process_token(self) -> None:
        self.client.bootstrap()
        self.client._token = "x" * 40
        snapshot, _ = self.client.get_snapshot()
        self.assertEqual(snapshot["instance_id"], self.server.core.instance_id)


if __name__ == "__main__":
    unittest.main()
