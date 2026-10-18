"""Windows notification-area client for a running Agent Beacon server.

The HTTP client and status validation are platform independent. Win32 APIs are
loaded only by ``run_tray`` so importing this module remains safe elsewhere.
"""

from __future__ import annotations

import ctypes
import http.client
import ipaddress
import json
import logging
import math
import os
import queue
import re
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)
VALID_STATES = frozenset({"working", "awaiting_user", "ready"})
VALID_SOURCES = frozenset({"manual", "codex", "claude", "antigravity"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]{1,64}$", re.ASCII)
MAX_SESSIONS = 128
DEMO_AGENT_ID = "tray-demo"
DEMO_SESSION_ID = "tray-session"
_STRINGS = {
    "working": "Працює",
    "awaiting_user": "Очікує на вас",
    "ready": "Готовий до завдання",
    "unavailable": "Стан агента не підтверджено",
}


def _reminder_only(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("state") != "awaiting_user" or snapshot.get("task_state") != "working":
        return False
    if any(session.get("available") and session.get("state") == "awaiting_user" for session in snapshot.get("sessions", [])):
        return False
    return any(session.get("available") and session.get("state") == "working" and
               session.get("question_reminder_active") is True and
               session.get("question_reminder_remaining_seconds", 0) > 0
               for session in snapshot.get("sessions", []))


def _tray_state_text(snapshot: dict[str, Any]) -> str:
    if _reminder_only(snapshot):
        return "Нагадування про запит · агент працює"
    return _STRINGS.get(snapshot.get("state"), _STRINGS["unavailable"])


class TrayProtocolError(ValueError):
    """Raised when a server response does not match the bounded status schema."""


def _finite_number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrayProtocolError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or (name == "ttl_seconds" and number <= 0):
        raise TrayProtocolError(f"{name} must be a finite number")
    return number


def validate_control(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TrayProtocolError("control response must be an object")
    token = value.get("viewer_token")
    instance_id = value.get("instance_id")
    if not isinstance(token, str) or not 16 <= len(token) <= 256 or not token.isascii():
        raise TrayProtocolError("control response has an invalid token")
    if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128:
        raise TrayProtocolError("control response has an invalid instance identifier")
    return {"viewer_token": token, "instance_id": instance_id}


def validate_snapshot(value: Any) -> dict[str, Any]:
    """Validate critical fields and return a private shallow copy.

    Additional keys are accepted for additive server metadata. Session labels
    used in native menus are reduced to validated identifiers only.
    """
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise TrayProtocolError("unsupported snapshot schema")
    if isinstance(value.get("schema_version"), bool):
        raise TrayProtocolError("unsupported snapshot schema")
    available = value.get("available")
    state = value.get("state")
    instance_id = value.get("instance_id")
    partial = value.get("partial")
    if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128:
        raise TrayProtocolError("instance_id is invalid")
    if not isinstance(partial, bool):
        raise TrayProtocolError("partial must be a boolean")
    if not isinstance(available, bool):
        raise TrayProtocolError("available must be a boolean")
    if state is not None and (not isinstance(state, str) or state not in VALID_STATES):
        raise TrayProtocolError("snapshot state is invalid")
    if available and state is None:
        raise TrayProtocolError("available snapshots must have a state")
    if not available and state is not None:
        raise TrayProtocolError("unavailable snapshots must have an unknown state")
    ttl = _finite_number(value.get("ttl_seconds"), "ttl_seconds")
    remaining = _finite_number(value.get("valid_for_seconds"), "valid_for_seconds")
    if ttl > 86_400 or remaining > ttl + 0.001:
        raise TrayProtocolError("valid_for_seconds exceeds ttl_seconds")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise TrayProtocolError("revision must be a non-negative integer")
    reminder_seconds = value.get("question_reminder_seconds")
    if reminder_seconds is not None:
        reminder_seconds = _finite_number(reminder_seconds, "question_reminder_seconds")
        if reminder_seconds > 86_400:
            raise TrayProtocolError("question_reminder_seconds exceeds 86400")
    task_state = value.get("task_state")
    if task_state is not None and (not isinstance(task_state, str) or task_state not in VALID_STATES):
        raise TrayProtocolError("task_state is invalid")
    if "task_state" in value and ((available and task_state is None) or (not available and task_state is not None)):
        raise TrayProtocolError("task_state does not match snapshot availability")
    sessions = value.get("sessions")
    if not isinstance(sessions, list) or len(sessions) > MAX_SESSIONS:
        raise TrayProtocolError("sessions must be a bounded list")
    safe_sessions: list[dict[str, Any]] = []
    for session in sessions:
        if not isinstance(session, dict):
            raise TrayProtocolError("session must be an object")
        agent_id = session.get("agent_id")
        session_id = session.get("session_id")
        source = session.get("source")
        session_available = session.get("available")
        session_state = session.get("state")
        sequence = session.get("sequence")
        if not isinstance(agent_id, str) or not IDENTIFIER.fullmatch(agent_id):
            raise TrayProtocolError("session agent identifier is invalid")
        if not isinstance(session_id, str) or not IDENTIFIER.fullmatch(session_id):
            raise TrayProtocolError("session identifier is invalid")
        if not isinstance(source, str) or source not in VALID_SOURCES:
            raise TrayProtocolError("session source is invalid")
        if not isinstance(session_available, bool):
            raise TrayProtocolError("session available must be a boolean")
        if session_state is not None and (not isinstance(session_state, str) or session_state not in VALID_STATES):
            raise TrayProtocolError("session state is invalid")
        if session_available and session_state is None:
            raise TrayProtocolError("available sessions must have a state")
        if source in {"codex", "claude", "antigravity"}:
            active = session.get("active")
            if not isinstance(active, bool):
                raise TrayProtocolError("observed session active must be a boolean")
            if not active and (session_available or session_state is not None):
                raise TrayProtocolError("closed observed sessions must be unknown and unavailable")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 9_007_199_254_740_991:
            raise TrayProtocolError("session sequence is invalid")
        safe_sessions.append({
            "agent_id": agent_id,
            "session_id": session_id,
            "source": source,
            "available": session_available,
            "state": session_state,
            "sequence": sequence,
        })
        display_state = session.get("display_state")
        if display_state is not None:
            if not isinstance(display_state, str) or display_state not in VALID_STATES:
                raise TrayProtocolError("session display_state is invalid")
            safe_sessions[-1]["display_state"] = display_state
        elif "display_state" in session and session_available:
            raise TrayProtocolError("available session display_state cannot be unknown")
        reminder_active = session.get("question_reminder_active")
        if reminder_active is not None:
            if not isinstance(reminder_active, bool):
                raise TrayProtocolError("question_reminder_active must be a boolean")
            safe_sessions[-1]["question_reminder_active"] = reminder_active
        reminder_remaining = session.get("question_reminder_remaining_seconds")
        if reminder_remaining is not None:
            reminder_remaining = _finite_number(reminder_remaining, "question_reminder_remaining_seconds")
            if reminder_remaining > (reminder_seconds if reminder_seconds is not None else 86_400):
                raise TrayProtocolError("question reminder remaining exceeds configured duration")
            safe_sessions[-1]["question_reminder_remaining_seconds"] = reminder_remaining
        if reminder_active is True and not (session_available and session_state == "working" and reminder_remaining is not None and reminder_remaining > 0):
            raise TrayProtocolError("active question reminder requires available working session with remaining time")
        if reminder_active is True and display_state != "awaiting_user":
            raise TrayProtocolError("active question reminder must project as awaiting_user")
        if display_state is not None:
            projection_matches = (
                display_state == session_state
                if session_state != "working"
                else display_state == "working" or (display_state == "awaiting_user" and reminder_active is True)
            )
            if not projection_matches or (not session_available and display_state is not None):
                raise TrayProtocolError("session display_state conflicts with actual state")
        project = session.get("project_name")
        if (isinstance(project, str) and len(project) <= 160
                and all(character.isprintable() and character not in "/\\:" for character in project)):
            safe_sessions[-1]["project_name"] = project
        turn_id = session.get("turn_id")
        if isinstance(turn_id, str) and IDENTIFIER.fullmatch(turn_id):
            safe_sessions[-1]["turn_id"] = turn_id
    result = dict(value)
    result["sessions"] = safe_sessions
    result["ttl_seconds"] = ttl
    result["valid_for_seconds"] = remaining
    return result


def manual_control_payload(snapshot: dict[str, Any], state: str) -> dict[str, Any]:
    if state not in VALID_STATES:
        raise TrayProtocolError("manual state is invalid")
    sequence = 1
    for session in snapshot.get("sessions", []):
        if (session.get("agent_id"), session.get("session_id")) == (DEMO_AGENT_ID, DEMO_SESSION_ID):
            sequence = session["sequence"] + 1
            break
    if sequence > 9_007_199_254_740_991:
        raise TrayProtocolError("manual sequence is exhausted")
    return {
        "agent_id": DEMO_AGENT_ID,
        "session_id": DEMO_SESSION_ID,
        "state": state,
        "sequence": sequence,
    }


def _icon_lamp_color(active_state: str | None) -> tuple[int, int, int]:
    """Return the single tray lamp color, using gray when status is unknown."""
    return {
        "working": (255, 85, 79),
        "awaiting_user": (255, 195, 77),
        "ready": (98, 223, 156),
    }.get(active_state, (128, 132, 140))


def _lamp_geometry(size: int) -> tuple[float, float, int]:
    """Return a pixel-centered round lamp geometry for a square icon bitmap."""
    center = (size - 1) / 2
    radius = max(1, size // 2 - max(1, size // 16))
    return center, center, radius


def _icon_bitmap_size(width: int, height: int) -> int:
    """Use the larger current-DPI small-icon metric for a square HICON bitmap."""
    return max(16, width, height)


def _rasterize_lamp(size: int, color: tuple[int, int, int], samples: int = 8) -> list[int]:
    """Rasterize a centered lamp as premultiplied 0xAARRGGBB pixels.

    Supersampling gives the small notification-area icon a smooth circular edge.
    Premultiplied color is required by the alpha channel consumed by Windows icons.
    """
    center_x, center_y, radius = _lamp_geometry(size)
    red, green, blue = color
    pixels: list[int] = []
    sample_count = samples * samples
    for y in range(size):
        for x in range(size):
            covered = 0
            for sample_y in range(samples):
                py = y - 0.5 + (sample_y + 0.5) / samples
                for sample_x in range(samples):
                    px = x - 0.5 + (sample_x + 0.5) / samples
                    if (px - center_x) ** 2 + (py - center_y) ** 2 <= radius * radius:
                        covered += 1
            alpha = (covered * 255 + sample_count // 2) // sample_count
            if alpha == 0:
                pixels.append(0)
                continue
            premultiplied_red = (red * alpha + 127) // 255
            premultiplied_green = (green * alpha + 127) // 255
            premultiplied_blue = (blue * alpha + 127) // 255
            pixels.append(
                (alpha << 24)
                | (premultiplied_red << 16)
                | (premultiplied_green << 8)
                | premultiplied_blue
            )
    return pixels


def _icon_mask(pixels: list[int], size: int) -> bytes:
    """Build the 1-bpp AND mask; transparent pixels have mask bit 1."""
    stride = ((size + 15) // 16) * 2
    mask = bytearray(stride * size)
    for y in range(size):
        for x in range(size):
            if pixels[y * size + x] >> 24 == 0:
                mask[y * stride + x // 8] |= 0x80 >> (x % 8)
    return bytes(mask)


class BeaconHTTPClient:
    """Short-timeout client; token stays in this object and is never logged."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 1.0):
        try:
            address = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError as error:
            raise ValueError("host must be a numeric IPv4 address") from error
        if not address.is_loopback:
            raise ValueError("tray connections must use a loopback address")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be from 1 to 65535")
        self.host = host
        self.port = port
        self.timeout = timeout
        self._token: str | None = None
        self._instance_id: str | None = None

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None,
                 body: bytes | None = None) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        request_headers = {"Host": f"{self.host}:{self.port}", "Connection": "close"}
        if headers:
            request_headers.update(headers)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(512 * 1024 + 1)
            if len(data) > 512 * 1024:
                raise TrayProtocolError("server response is too large")
            return response.status, data
        finally:
            connection.close()

    @staticmethod
    def _decode(data: bytes) -> Any:
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrayProtocolError("server returned invalid JSON") from error

    def bootstrap(self) -> None:
        status, body = self._request("GET", "/api/control")
        if status != 200:
            raise ConnectionError(f"control endpoint returned HTTP {status}")
        credentials = validate_control(self._decode(body))
        self._token = credentials["viewer_token"]
        self._instance_id = credentials["instance_id"]

    def get_snapshot(self) -> tuple[dict[str, Any], float]:
        for attempt in range(2):
            if self._token is None:
                self.bootstrap()
            started = time.monotonic()
            status, body = self._request(
                "GET", "/api/status", headers={"Authorization": f"Bearer {self._token}"}
            )
            elapsed = max(0.0, time.monotonic() - started)
            if status == 401 and attempt == 0:
                self._token = None
                self.bootstrap()
                continue
            if status != 200:
                raise ConnectionError(f"status endpoint returned HTTP {status}")
            snapshot = validate_snapshot(self._decode(body))
            if snapshot.get("instance_id") != self._instance_id:
                if attempt == 0:
                    self._token = None
                    self.bootstrap()
                    continue
                raise ConnectionError("server restarted while reading status")
            return snapshot, elapsed
        raise ConnectionError("could not refresh status credentials")

    def post_manual(self, snapshot: dict[str, Any], state: str) -> dict[str, Any]:
        if self._token is None:
            self.bootstrap()
        payload = manual_control_payload(snapshot, state)
        body = json.dumps(payload, separators=(",", ":")).encode("ascii")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Origin": f"http://{self.host}:{self.port}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        status, response = self._request("POST", "/api/manual", headers=headers, body=body)
        if status == 401:
            self._token = None
            self.bootstrap()
            payload = manual_control_payload(snapshot, state)
            body = json.dumps(payload, separators=(",", ":")).encode("ascii")
            headers["Authorization"] = f"Bearer {self._token}"
            status, response = self._request("POST", "/api/manual", headers=headers, body=body)
        if status != 200:
            raise ConnectionError(f"manual endpoint returned HTTP {status}")
        return validate_snapshot(self._decode(response))

    def clear_manual_sessions(self) -> dict[str, Any]:
        if self._token is None:
            self.bootstrap()
        body = b"{}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Origin": f"http://{self.host}:{self.port}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        status, response = self._request("POST", "/api/clear", headers=headers, body=body)
        if status == 401:
            self._token = None
            self.bootstrap()
            headers["Authorization"] = f"Bearer {self._token}"
            status, response = self._request("POST", "/api/clear", headers=headers, body=body)
        if status != 200:
            raise ConnectionError(f"clear endpoint returned HTTP {status}")
        return validate_snapshot(self._decode(response))


@dataclass(frozen=True)
class TrayDisplayState:
    snapshot: dict[str, Any] | None
    expires_at: float
    error: str | None = None

    def fresh_snapshot(self, now: float | None = None) -> dict[str, Any] | None:
        current = time.monotonic() if now is None else now
        if self.snapshot is None or current >= self.expires_at:
            return None
        return self.snapshot


class TrayWorker(threading.Thread):
    def __init__(self, host: str, port: int, publish: Callable[[TrayDisplayState], None]):
        super().__init__(name="agent-beacon-tray-http", daemon=True)
        self.client = BeaconHTTPClient(host, port)
        self.publish = publish
        self.commands: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self._snapshot: dict[str, Any] | None = None

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                try:
                    state = self.commands.get_nowait()
                except queue.Empty:
                    state = None
                if state is not None and self._snapshot is not None:
                    if state == "__clear_demo__":
                        self._snapshot = self.client.clear_manual_sessions()
                    else:
                        self._snapshot = self.client.post_manual(self._snapshot, state)
                elif state is not None:
                    self.commands.put(state)
                snapshot, rtt = self.client.get_snapshot()
                self._snapshot = snapshot
                deadline = time.monotonic() + max(0.0, snapshot["valid_for_seconds"] - rtt)
                self.publish(TrayDisplayState(snapshot, deadline))
            except Exception as error:
                # Error text is deliberately generic: never include request headers or tokens.
                LOGGER.debug("Tray status refresh failed (%s)", type(error).__name__)
                self.publish(TrayDisplayState(self._snapshot, time.monotonic(), "offline"))
            self.stop_event.wait(1.0)


def _native_loop(host: str, port: int, integrated_stop: threading.Event | None = None) -> int:
    if os.name != "nt":
        raise OSError("The Windows tray is available only on Windows.")
    native = _Win32Tray(host, port, integrated_stop)
    return native.run()


def run_tray(port: int = 8765, host: str = "127.0.0.1", *,
             stop_event: threading.Event | None = None) -> int:
    """Run the native tray loop on this thread and connect to a local server."""
    return _native_loop(host, port, stop_event)


def _get_win32():
    if os.name != "nt":
        raise OSError("The Windows tray is available only on Windows.")
    return _Win32Bindings()


class _Win32Bindings:
    """Typed Win32 imports kept behind the Windows platform guard."""

    def __init__(self) -> None:
        from ctypes import wintypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._set(self.kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
        self._set(self.user32.RegisterClassExW, wintypes.ATOM, ctypes.c_void_p)
        self._set(self.user32.UnregisterClassW, wintypes.BOOL, wintypes.LPCWSTR, wintypes.HINSTANCE)
        self._set(self.user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR,
                  wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
        self._set(self.user32.DefWindowProcW, ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                  wintypes.WPARAM, wintypes.LPARAM)
        self._set(self.user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
        self._set(self.user32.GetMessageW, ctypes.c_int, ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT)
        self._set(self.user32.TranslateMessage, wintypes.BOOL, ctypes.c_void_p)
        self._set(self.user32.DispatchMessageW, ctypes.c_ssize_t, ctypes.c_void_p)
        self._set(self.user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self._set(self.user32.PostQuitMessage, None, ctypes.c_int)
        self._set(self.user32.SetTimer, ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
        self._set(self.user32.KillTimer, wintypes.BOOL, wintypes.HWND, ctypes.c_size_t)
        self._set(self.user32.CreatePopupMenu, wintypes.HMENU)
        self._set(self.user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
        self._set(self.user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
        self._set(self.user32.TrackPopupMenu, wintypes.BOOL, wintypes.HMENU, wintypes.UINT,
                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
        self._set(self.user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
        self._set(self.user32.GetCursorPos, wintypes.BOOL, ctypes.c_void_p)
        self._set(self.user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
        self._set(self.shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p)
        self._set(self.gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.c_void_p,
                  wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
        self._set(self.gdi32.CreateBitmap, wintypes.HBITMAP, ctypes.c_int, ctypes.c_int,
                  wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        self._set(self.gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
        self._set(self.user32.CreateIconIndirect, wintypes.HICON, ctypes.c_void_p)
        self._set(self.user32.DestroyIcon, wintypes.BOOL, wintypes.HICON)
        self._set(self.user32.GetDC, wintypes.HDC, wintypes.HWND)
        self._set(self.user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)

    @staticmethod
    def _set(function: Any, restype: Any, *argtypes: Any) -> None:
        function.restype = restype
        function.argtypes = list(argtypes)


class _Win32Tray:
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_TIMER = 0x0113
    WM_APP = 0x8000
    WM_TRAY = WM_APP + 7
    WM_COMMAND = 0x0111
    WM_CONTEXTMENU = 0x007B
    NIM_ADD = 0
    NIM_MODIFY = 1
    NIM_DELETE = 2
    NIF_MESSAGE = 1
    NIF_ICON = 2
    NIF_TIP = 4
    NIF_SHOWTIP = 0x80
    NOTIFYICON_VERSION_4 = 4
    NIM_SETVERSION = 4
    ID_OPEN = 100
    ID_WORKING = 201
    ID_WAITING = 202
    ID_READY = 203
    ID_CLEAR_DEMO = 204
    ID_QUIT = 299
    TIMER_ID = 17
    TASKBAR_CREATED = 0xC000

    def __init__(self, host: str, port: int, integrated_stop: threading.Event | None):
        self.host, self.port = host, port
        self.integrated_stop = integrated_stop
        self.win = _get_win32()
        self.user32, self.shell32, self.gdi32 = self.win.user32, self.win.shell32, self.win.gdi32
        self.wintypes = self.win.wintypes
        self.state_lock = threading.Lock()
        self.closing = threading.Event()
        self.display = TrayDisplayState(None, 0.0)
        self.worker = TrayWorker(host, port, self._publish)
        self.hwnd = None
        self.hicon = None
        self.icon_added = False
        self.notify_version4 = False
        self.icon_cache: dict[str | None, Any] = {}
        self.active_icon_state: str | None | object = object()
        self.class_registered = False
        self.class_name = f"AgentBeaconTray-{os.getpid()}-{id(self):x}"
        self._menu_handles: list[Any] = []
        self.callback_ref = None
        self.dpi_size = 16
        self.taskbar_created = 0

    def _publish(self, display: TrayDisplayState) -> None:
        with self.state_lock:
            self.display = display
        if self.hwnd and not self.closing.is_set():
            self.user32.PostMessageW(self.hwnd, self.WM_TIMER, self.TIMER_ID, 0)

    def run(self) -> int:
        self._define_structures()
        try:
            self._create_window()
            self.taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")
            self.hicon = self._icon_for(None)
            self._notify(self.NIM_ADD)
            self.icon_added = True
            self._notify(self.NIM_SETVERSION, version=True)
            self.notify_version4 = True
            self.user32.SetTimer(self.hwnd, self.TIMER_ID, 500, None)
            self.worker.start()
            if self.integrated_stop is not None:
                threading.Thread(target=self._watch_integrated_stop, daemon=True).start()
            message = self.MSG()
            while True:
                result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
            return 0
        finally:
            self.closing.set()
            self.worker.stop_event.set()
            if self.worker.is_alive():
                self.worker.join(timeout=2.5)
            try:
                if self.hwnd:
                    self.user32.KillTimer(self.hwnd, self.TIMER_ID)
            except Exception:
                LOGGER.debug("Could not remove tray refresh timer")
            try:
                self._destroy_menus()
            except Exception:
                LOGGER.debug("Could not destroy tray menus")
            if self.icon_added:
                try:
                    self._notify(self.NIM_DELETE)
                except Exception:
                    LOGGER.debug("Could not remove tray icon from the notification area")
                self.icon_added = False
            self._destroy_icon_cache()
            if self.hwnd:
                try:
                    self.user32.DestroyWindow(self.hwnd)
                except Exception:
                    LOGGER.debug("Could not destroy tray message window")
                self.hwnd = None
            if self.class_registered:
                try:
                    self.user32.UnregisterClassW(self.class_name, self.instance)
                except Exception:
                    LOGGER.debug("Could not unregister tray window class")
                self.class_registered = False

    def _watch_integrated_stop(self) -> None:
        assert self.integrated_stop is not None
        self.integrated_stop.wait()
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)

    def _define_structures(self) -> None:
        w = self.wintypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", w.LONG), ("y", w.LONG)]
        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", w.HWND), ("message", w.UINT), ("wParam", w.WPARAM),
                        ("lParam", w.LPARAM), ("time", w.DWORD), ("pt", POINT), ("lPrivate", w.DWORD)]
        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                        ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]
        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                        ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
                        ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                        ("szInfo", w.WCHAR * 256), ("uTimeoutOrVersion", w.UINT),
                        ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", w.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort),
                        ("biCompression", w.DWORD), ("biSizeImage", w.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]
        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]
        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                        ("hbmMask", w.HBITMAP), ("hbmColor", w.HBITMAP)]
        self.POINT, self.MSG = POINT, MSG
        self.WNDCLASSEXW, self.NOTIFYICONDATAW = WNDCLASSEXW, NOTIFYICONDATAW
        self.BITMAPINFOHEADER, self.BITMAPINFO, self.ICONINFO = BITMAPINFOHEADER, BITMAPINFO, ICONINFO
        self.instance = self.win.kernel32.GetModuleHandleW(None)
        self._set_more_prototypes()

    def _set_more_prototypes(self) -> None:
        w = self.wintypes
        self.user32.RegisterWindowMessageW.restype = w.UINT
        self.user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
        self.user32.LoadCursorW.restype = ctypes.c_void_p
        self.user32.LoadCursorW.argtypes = [w.HINSTANCE, w.LPCWSTR]

    def _create_window(self) -> None:
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, self.wintypes.HWND, self.wintypes.UINT,
                                    self.wintypes.WPARAM, self.wintypes.LPARAM)
        self.callback_ref = WNDPROC(self._window_proc)
        wc = self.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.lpfnWndProc = ctypes.cast(self.callback_ref, ctypes.c_void_p).value
        wc.hInstance = self.instance
        wc.lpszClassName = self.class_name
        atom = self.user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            raise ctypes.WinError(ctypes.get_last_error())
        self.class_registered = True
        self.hwnd = self.user32.CreateWindowExW(0x00000080, self.class_name, "Agent Beacon", 0x80000000,
            0, 0, 0, 0, None, None, self.instance, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _window_proc(self, hwnd: Any, message: int, wparam: int, lparam: int) -> int:
        try:
            if message == self.WM_TRAY:
                event = lparam & 0xFFFF
                if event in (0x0205, 0x007B, 0x0203, 0x0406, 0x0401):
                    self._show_menu()
                elif event in (0x0202, 0x0400):
                    self._open_dashboard()
                return 0
            if message == self.WM_TIMER:
                if self.integrated_stop is not None and self.integrated_stop.is_set():
                    self.user32.PostMessageW(hwnd, self.WM_CLOSE, 0, 0)
                self._refresh_icon()
                return 0
            if self.taskbar_created and message == self.taskbar_created:
                self.icon_added = False
                self._notify(self.NIM_ADD)
                self.icon_added = True
                self._notify(self.NIM_SETVERSION, version=True)
                self.notify_version4 = True
                return 0
            if message == self.WM_COMMAND:
                command = wparam & 0xFFFF
                if command == self.ID_OPEN:
                    self._open_dashboard()
                elif command == self.ID_WORKING:
                    self.worker.commands.put("working")
                elif command == self.ID_WAITING:
                    self.worker.commands.put("awaiting_user")
                elif command == self.ID_READY:
                    self.worker.commands.put("ready")
                elif command == self.ID_CLEAR_DEMO:
                    self.worker.commands.put("__clear_demo__")
                elif command == self.ID_QUIT:
                    self.user32.DestroyWindow(hwnd)
                return 0
            if message == self.WM_CLOSE:
                self.user32.DestroyWindow(hwnd)
                return 0
            if message == self.WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
        except Exception:
            LOGGER.exception("Tray UI operation failed")
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _notify(self, operation: int, *, version: bool = False) -> None:
        data = self.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        if self.notify_version4 and operation != self.NIM_SETVERSION:
            data.uFlags |= self.NIF_SHOWTIP
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self.hicon
        state = self._display_text()[:127]
        data.szTip = f"Agent Beacon · {state}"
        if version:
            data.uFlags = 0
            data.uTimeoutOrVersion = self.NOTIFYICON_VERSION_4
        if not self.shell32.Shell_NotifyIconW(operation, ctypes.byref(data)):
            if operation == self.NIM_ADD:
                raise OSError("Windows could not register the Agent Beacon tray icon.")
            raise ctypes.WinError(ctypes.get_last_error())

    def _make_icon(self, active_state: str | None):
        # Small-icon metrics follow the Windows icon size at the current DPI.
        size = _icon_bitmap_size(self.user32.GetSystemMetrics(49), self.user32.GetSystemMetrics(50))
        self.dpi_size = size
        info = self.BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(self.BITMAPINFOHEADER)
        info.bmiHeader.biWidth = size
        info.bmiHeader.biHeight = -size
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        dc = self.user32.GetDC(None)
        if not dc:
            raise ctypes.WinError(ctypes.get_last_error())
        bits = ctypes.c_void_p()
        color_bitmap = None
        mask_bitmap = None
        try:
            color_bitmap = self.gdi32.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not color_bitmap or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            lamp_pixels = _rasterize_lamp(size, _icon_lamp_color(active_state))
            pixels = ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint32))
            for index, pixel in enumerate(lamp_pixels):
                pixels[index] = pixel
            mask_bits = _icon_mask(lamp_pixels, size)
            mask_buffer = (ctypes.c_ubyte * len(mask_bits)).from_buffer_copy(mask_bits)
            mask_bitmap = self.gdi32.CreateBitmap(size, size, 1, 1, ctypes.byref(mask_buffer))
            icon_info = self.ICONINFO(True, 0, 0, mask_bitmap, color_bitmap)
            icon = self.user32.CreateIconIndirect(ctypes.byref(icon_info))
            if not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            return icon
        finally:
            self.user32.ReleaseDC(None, dc)
            if color_bitmap:
                self.gdi32.DeleteObject(color_bitmap)
            if mask_bitmap:
                self.gdi32.DeleteObject(mask_bitmap)

    def _refresh_icon(self) -> None:
        with self.state_lock:
            display = self.display
        snapshot = display.fresh_snapshot()
        active_state = snapshot.get("state") if snapshot and snapshot.get("available") else None
        if display.error:
            active_state = None
        changed = active_state != self.active_icon_state
        if changed:
            self.hicon = self._icon_for(active_state)
            self.active_icon_state = active_state
        if changed and self.icon_added:
            self._notify(self.NIM_MODIFY)

    def _icon_for(self, active_state: str | None):
        if active_state not in self.icon_cache:
            self.icon_cache[active_state] = self._make_icon(active_state)
        return self.icon_cache[active_state]

    def _destroy_icon_cache(self) -> None:
        for icon in self.icon_cache.values():
            try:
                self.user32.DestroyIcon(icon)
            except Exception:
                LOGGER.debug("Could not destroy a cached tray icon")
        self.icon_cache.clear()
        self.hicon = None

    def _display_text(self) -> str:
        with self.state_lock:
            display = self.display
        snapshot = display.fresh_snapshot()
        if display.error or not snapshot or not snapshot.get("available"):
            return _STRINGS["unavailable"]
        return _tray_state_text(snapshot)

    def _open_dashboard(self) -> None:
        webbrowser.open(f"http://{self.host}:{self.port}/", new=2)

    def _destroy_menus(self) -> None:
        for menu in self._menu_handles:
            self.user32.DestroyMenu(menu)
        self._menu_handles.clear()

    def _append(self, menu: Any, flags: int, item_id: int, label: str) -> None:
        if not self.user32.AppendMenuW(menu, flags, item_id, label):
            raise ctypes.WinError(ctypes.get_last_error())

    def _show_menu(self) -> None:
        self._destroy_menus()
        main = self.user32.CreatePopupMenu()
        if not main:
            raise ctypes.WinError(ctypes.get_last_error())
        sessions = None
        demo = None
        attached_menus: set[Any] = set()
        try:
            sessions = self.user32.CreatePopupMenu()
            if not sessions:
                raise ctypes.WinError(ctypes.get_last_error())
            demo = self.user32.CreatePopupMenu()
            if not demo:
                raise ctypes.WinError(ctypes.get_last_error())

            self._build_menu(main, sessions, demo, attached_menus)
            self._menu_handles.append(main)
            point = self.POINT()
            self.user32.GetCursorPos(ctypes.byref(point))
            self.user32.SetForegroundWindow(self.hwnd)
            self.user32.TrackPopupMenu(main, 0x0002, point.x, point.y, 0, self.hwnd, None)
            self.user32.PostMessageW(self.hwnd, 0, 0, 0)
        except Exception:
            if main in self._menu_handles:
                self._menu_handles.remove(main)
            self.user32.DestroyMenu(main)
            for menu in (sessions, demo):
                if menu and menu not in attached_menus:
                    self.user32.DestroyMenu(menu)
            raise

    def _build_menu(self, main: Any, sessions: Any, demo: Any, attached_menus: set[Any]) -> None:
        with self.state_lock:
            display = self.display
        snapshot = display.fresh_snapshot()
        if display.error or not snapshot or not snapshot.get("available"):
            status = "Стан: не підтверджено"
        else:
            status = f"Стан: {_tray_state_text(snapshot)}"
        self._append(main, 0x0003, 0, status)
        self._append(main, 0x0002, 0, "")
        self._append(main, 0x0000, self.ID_OPEN, "Відкрити dashboard")
        menu_snapshot = display.snapshot
        if menu_snapshot and menu_snapshot.get("sessions"):
            lease_alive = display.error is None and time.monotonic() < display.expires_at
            visible_sessions = menu_snapshot["sessions"][:20]
            for session in visible_sessions:
                source = session["source"]
                row_available = lease_alive and session["available"]
                reminder_only = (session.get("state") == "working" and
                                 session.get("question_reminder_active") is True and
                                 session.get("question_reminder_remaining_seconds", 0) > 0 and
                                 _reminder_only(menu_snapshot))
                row_state = session["state"] if reminder_only else session.get("display_state", session["state"])
                state_text = ("Нагадування про запит" if reminder_only else
                              _STRINGS.get(row_state, _STRINGS["unavailable"])) if row_available else _STRINGS["unavailable"]
                project = session.get("project_name")
                project_label = ""
                if isinstance(project, str):
                    project_label = f" · {project[:48]}" if project else ""
                turn_id = session.get("turn_id")
                turn_label = f" (turn {turn_id})" if isinstance(turn_id, str) else ""
                label = (f"{source}: {session['agent_id']} / {session['session_id']}"
                         f"{turn_label}{project_label} · {state_text}")
                self._append(sessions, 0x0003, 0, label)
            if len(menu_snapshot["sessions"]) > len(visible_sessions):
                remaining = len(menu_snapshot["sessions"]) - len(visible_sessions)
                self._append(sessions, 0x0003, 0, f"Ще {remaining} · відкрийте dashboard")
        else:
            self._append(sessions, 0x0003, 0, "Немає активних сесій")
        self._append(main, 0x0010, sessions, "Сесії")
        attached_menus.add(sessions)
        self._append(demo, 0x0000, self.ID_WORKING, "Працює")
        self._append(demo, 0x0000, self.ID_WAITING, "Очікує на вас")
        self._append(demo, 0x0000, self.ID_READY, "Готовий до завдання")
        self._append(main, 0x0010, demo, "Ручний тест (tray-demo)")
        attached_menus.add(demo)
        self._append(main, 0x0000, self.ID_CLEAR_DEMO, "Очистити ручні тести")
        self._append(main, 0x0002, 0, "")
        self._append(main, 0x0000, self.ID_QUIT, "Вийти")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Attach the Windows tray to an Agent Beacon server.")
    parser.add_argument("--host", default="127.0.0.1", help="numeric loopback IPv4 address")
    parser.add_argument("--port", type=int, default=8765, help="Agent Beacon server port")
    arguments = parser.parse_args()
    try:
        return run_tray(port=arguments.port, host=arguments.host)
    except (OSError, ValueError) as error:
        import sys
        if sys.stderr is not None:
            print(f"Agent Beacon tray could not start: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
