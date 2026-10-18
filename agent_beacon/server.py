"""Small, bounded HTTP API and static-file server for Agent Beacon."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import secrets
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .core import BeaconCore, CoreError, EventConflict, SessionLimitError
from .pairing import DeviceStoreError, PairingError, PairingManager

MAX_BODY_BYTES = 8 * 1024
MAX_CONNECTIONS = 32
READ_TIMEOUT_SECONDS = 5
MAX_CONNECTION_SECONDS = 15
EVENT_STREAM_SECONDS = 10.0
MAX_EVENT_STREAMS = 8
MAX_APK_BYTES = 32 * 1024 * 1024
APK_FILENAME = "AgentBeacon-debug.apk"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APK_RELATIVE_PATH = Path("dist") / APK_FILENAME
STATIC_FILES = {
    "/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css",
    "/app.js": "app.js", "/vendor/qr.js": "vendor/qr.js",
    "/setup.html": "setup.html", "/setup.js": "setup.js", "/setup.css": "setup.css",
}


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


class BeaconHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR permits two live listeners to claim the same
    # address, which can route requests to the wrong Beacon instance. Keep
    # the normal POSIX restart behavior while making Windows binds exclusive.
    allow_reuse_address = os.name != "nt"
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address: tuple[str, int], core: BeaconCore, static_dir: Path, device_store: str | Path | None = None) -> None:
        self.core = core
        self.static_dir = static_dir.resolve()
        self.viewer_token = secrets.token_urlsafe(32)
        self.desktop = None
        self.pairing = PairingManager(device_store)
        self._connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._event_slots = threading.BoundedSemaphore(MAX_EVENT_STREAMS)
        super().__init__(address, BeaconRequestHandler)

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self) -> tuple[socket.socket, Any]:
        sock, address = super().get_request()
        sock.settimeout(READ_TIMEOUT_SECONDS)
        return sock, address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        disconnect_timer: threading.Timer | None = None
        request_thread_entered = False
        try:
            disconnect_timer = threading.Timer(MAX_CONNECTION_SECONDS, self._disconnect_request, args=(request,))
            disconnect_timer.daemon = True
            disconnect_timer.start()
            request_thread_entered = True
            super().process_request_thread(request, client_address)
        except BaseException:
            # ThreadingMixIn closes the socket only after its handler starts.
            # If the watchdog itself cannot start, close it here as well.
            if not request_thread_entered:
                self.shutdown_request(request)
            raise
        finally:
            if disconnect_timer is not None:
                disconnect_timer.cancel()
            self._connection_slots.release()

    @staticmethod
    def _disconnect_request(request: socket.socket) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            request.close()
        except OSError:
            pass


class BeaconRequestHandler(BaseHTTPRequestHandler):
    server: BeaconHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "AgentBeacon"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        # Request paths and headers can contain credentials; keep access logs off.
        return

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _request_path(self) -> str | None:
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        return parsed.path

    def _host_parts(self) -> tuple[str, int | None] | None:
        value = self.headers.get("Host", "")
        if not value or value.count(":") > 1 or "@" in value or "/" in value:
            return None
        if ":" in value:
            hostname, port_text = value.rsplit(":", 1)
            if len(port_text) > 5 or not port_text.isascii() or not port_text.isdigit():
                return None
            port = int(port_text)
            if not 1 <= port <= 65535:
                return None
        else:
            hostname, port = value, None
        hostname = hostname.lower().rstrip(".")
        if hostname != "localhost":
            try:
                ipaddress.IPv4Address(hostname)
            except ipaddress.AddressValueError:
                return None
        expected_port = self.server.server_port
        if port is not None and port != expected_port:
            return None
        if port is None and expected_port != 80:
            return None
        return hostname, port

    def _host_is_local(self) -> bool:
        parts = self._host_parts()
        if parts is None:
            return False
        hostname = parts[0]
        if hostname == "localhost":
            return True
        return ipaddress.IPv4Address(hostname).is_loopback

    def _peer_is_local(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except (ValueError, IndexError):
            return False

    def _is_local_control_request(self) -> bool:
        return self._peer_is_local() and self._host_is_local()

    def _origin_matches_host(self, *, required: bool) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return not required
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        host_parts = self._host_parts()
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or host_parts is None
        ):
            return False
        try:
            origin_port = parsed.port or 80
        except ValueError:
            return False
        request_port = host_parts[1] or 80
        if origin_port != request_port:
            return False
        origin_host = parsed.hostname.lower().rstrip(".")
        request_host = host_parts[0]
        try:
            if origin_host != "localhost":
                origin_host = str(ipaddress.IPv4Address(origin_host))
            if request_host != "localhost":
                request_host = str(ipaddress.IPv4Address(request_host))
        except ipaddress.AddressValueError:
            return False
        return origin_host == request_host

    def _read_json(self, expected_fields: set[str]) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_error(415, "Content-Type must be application/json.")
            return None
        length_text = self.headers.get("Content-Length")
        if length_text is None or not length_text.isascii() or not length_text.isdigit():
            self._send_error(400, "A valid Content-Length is required.")
            return None
        if len(length_text) > 5:
            self._send_error(413, "Request body is too large.")
            return None
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            self._send_error(413, "Request body is too large.")
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_json_object_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            self._send_error(400, "Invalid JSON body.")
            return None
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            self._send_error(400, "JSON fields do not match the required schema.")
            return None
        return payload

    def _bearer_authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, separator, token = header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.isascii():
            return False
        return hmac.compare_digest(token, self.server.viewer_token) or self.server.pairing.authorized(token)

    def _headers_are_safe(self) -> bool:
        for name in self.headers.keys():
            values = self.headers.get_all(name, [])
            if len(values) > 1 or name.lower() == "transfer-encoding":
                return False
        return True

    def do_GET(self) -> None:
        self.close_connection = True
        if not self._headers_are_safe():
            self._send_error(400, "Duplicate or unsupported request headers.")
            return
        path = self._request_path()
        if path is None:
            self._send_error(400, "Query strings and absolute request targets are not accepted.")
            return
        host = self._host_parts()
        if host is None:
            self._send_error(400, "Invalid Host header.")
            return

        if path == "/api/control":
            if not self._is_local_control_request():
                self._send_error(403, "Local access is required.")
                return
            if not self._origin_matches_host(required=False):
                self._send_error(403, "Origin does not match this server.")
                return
            bind_ip = ipaddress.IPv4Address(self.server.server_address[0])
            from .discovery import private_host
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                             if private_host(item[4][0])}
            except OSError:
                addresses = set()
            self._send_json(200, {
                "viewer_token": self.server.viewer_token,
                "instance_id": self.server.core.instance_id,
                "lan_enabled": not bind_ip.is_loopback,
                "lan_addresses": sorted(addresses, key=lambda ip: (not ip.startswith("192.168."), ip))[:8],
                "setup_available": self.server.desktop is not None,
            })
            return
        if path == "/api/setup":
            if not self._is_local_control_request() or not self._origin_matches_host(required=False):
                self._send_error(403, "Local access is required.")
                return
            if not self._desktop_authorized():
                self._send_error(401, "Desktop viewer authorization is required.")
                return
            if self.server.desktop is None:
                self._send_error(404, "Desktop setup is not enabled for this server.")
                return
            self._send_json(200, self.server.desktop.status())
            return
        if path == "/downloads/AgentBeacon-debug.apk":
            self._send_apk()
            return
        if path == "/downloads/AgentBeacon-Setup.exe":
            self._send_apk(windows=True)
            return
        if path == "/api/status":
            if not self._bearer_authorized():
                self._send_error(401, "A valid viewer token is required.")
                return
            self._send_json(200, self.server.core.snapshot())
            return
        if path == "/api/events":
            if not self._bearer_authorized():
                self._send_error(401, "A valid viewer token is required.")
                return
            self._send_events()
            return
        if path == "/api/devices":
            if not self._is_local_control_request():
                self._send_error(403, "Local access is required.")
                return
            self._send_json(200, {"devices": self.server.pairing.devices()})
            return
        if path in STATIC_FILES:
            self._send_static(path)
            return
        self._send_error(404, "Not found.")

    @staticmethod
    def _event_key(snapshot: dict[str, Any]) -> tuple[Any, ...]:
        # Observer heartbeats and repeated same-state tools are not UI changes.
        return (
            snapshot["instance_id"], snapshot["available"], snapshot["state"],
            snapshot["task_state"], snapshot["partial"], snapshot["reason"],
            snapshot.get("history", [{}])[-1].get("id") if snapshot.get("history") else None,
            tuple((row["source"], row["session_id"], row["state"], row["available"],
                   row.get("active"), row.get("display_state"), row.get("question_reminder_active"),
                   row.get("project_name"), row.get("status_reason"), row.get("session_name")) for row in snapshot["sessions"]),
            tuple((row["source"], row["healthy"], row["inventory_complete"], row.get("coverage"), row.get("idle_ready"))
                  for row in snapshot["sources"]),
        )

    def _send_events(self) -> None:
        if not self.server._event_slots.acquire(blocking=False):
            self._send_error(503, "Live update capacity is busy; use status checks temporarily.")
            return
        try:
            heartbeat = 8.0 if self.headers.get("X-Beacon-Battery-Saver") == "1" else 4.0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            self.end_headers()
            stream_started = time.monotonic()
            deadline = stream_started + EVENT_STREAM_SECONDS
            last_key = None
            next_heartbeat = 0.0
            snapshot = self.server.core.snapshot()
            while time.monotonic() < deadline:
                if not self._bearer_authorized():
                    return
                now = time.monotonic()
                key = self._event_key(snapshot)
                if key != last_key or now >= next_heartbeat:
                    frame = {**snapshot, "stream_elapsed_ms": min(10000, int((time.monotonic() - stream_started) * 1000))}
                    payload = json.dumps(frame, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
                    if len(payload) > 128 * 1024:
                        return
                    self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
                    last_key = key
                    next_heartbeat = now + heartbeat
                wake_at = min(deadline, next_heartbeat)
                if snapshot["available"] and snapshot["valid_for_seconds"] > 0:
                    wake_at = min(wake_at, now + snapshot["valid_for_seconds"])
                for row in snapshot["sessions"]:
                    remaining = row.get("question_reminder_remaining_seconds", 0)
                    if row.get("question_reminder_active") and remaining > 0:
                        wake_at = min(wake_at, now + remaining)
                snapshot = self.server.core.wait_for_change(snapshot["revision"], max(0.001, wake_at - time.monotonic()))
        except OSError:
            # A phone may close its screen, disconnect, or cancel monitoring.
            return
        finally:
            self.server._event_slots.release()

    def do_POST(self) -> None:
        self.close_connection = True
        if not self._headers_are_safe():
            self._send_error(400, "Duplicate or unsupported request headers.")
            return
        path = self._request_path()
        if path is None:
            self._send_error(400, "Query strings and absolute request targets are not accepted.")
            return
        if self._host_parts() is None:
            self._send_error(400, "Invalid Host header.")
            return
        if path == "/api/discovery":
            from .discovery import private_host, PROTOCOL
            if not private_host(self.client_address[0]) or not self._origin_matches_host(required=False):
                self._send_error(403, "Private LAN access is required.")
                return
            payload = self._read_json({"protocol", "device_id", "nonce", "proof"})
            if payload is None:
                return
            if payload.get("protocol") != PROTOCOL:
                self._send_error(400, "Unsupported discovery protocol.")
                return
            host = self.connection.getsockname()[0]
            result = self.server.pairing.discovery_reply(payload.get("device_id"), payload.get("nonce"),
                                                        payload.get("proof"), host, self.server.server_port,
                                                        self.server.core.instance_id)
            if result is None:
                self._send_error(403, "Discovery proof not accepted.")
            else:
                self._send_json(200, result)
            return
        if path == "/api/pair":
            if not self._origin_matches_host(required=False):
                self._send_error(403, "Origin does not match this server.")
                return
            payload = self._read_json({"code", "device_name"})
            if payload is None:
                return
            try:
                result = self.server.pairing.pair(payload["code"], payload["device_name"], self.server.core.instance_id)
            except PairingError as error:
                self._send_error(error.status, str(error))
            except DeviceStoreError:
                self._send_error(503, "Device store is unavailable.")
            else:
                self._send_json(200, result)
            return

        if not self._is_local_control_request():
            self._send_error(403, "Local access is required.")
            return
        if not self._origin_matches_host(required=True):
            self._send_error(403, "A matching same-origin Origin header is required.")
            return

        if path.startswith("/api/setup/"):
            if not self._desktop_authorized():
                self._send_error(401, "Desktop viewer authorization is required.")
                return
            desktop = self.server.desktop
            if desktop is None:
                self._send_error(404, "Desktop setup is not enabled for this server.")
                return
            fields = {"plan": {"providers", "lan", "import_pairing"}, "apply": {"plan_id"}, "firewall": set(),
                      "stop": set(), "startup": {"enabled"}}
            action = path.rsplit("/", 1)[-1]
            if action not in fields:
                self._send_error(404, "Unknown setup action.")
                return
            payload = self._read_json(fields[action])
            if payload is None:
                return
            try:
                result = desktop.action(action, payload)
            except (OSError, ValueError, DeviceStoreError) as error:
                # Error types only: provider config exceptions may contain private values.
                self._send_error(409, "Setup could not complete (" + type(error).__name__ + "). Review settings or use the help prompt.")
            else:
                self._send_json(200, result)
            return

        if path == "/api/pairing":
            payload = self._read_json(set())
            if payload is None:
                return
            try:
                code = self.server.pairing.mint_code()
            except PairingError as error:
                self._send_error(error.status, str(error))
            else:
                self._send_json(200, {"code": code, "expires_in_seconds": 120})
            return
        if path == "/api/revoke":
            payload = self._read_json({"device_id"})
            if payload is None:
                return
            try:
                revoked = self.server.pairing.revoke(payload["device_id"])
            except PairingError as error:
                self._send_error(400, str(error))
            except DeviceStoreError:
                self._send_error(503, "Device store is unavailable.")
            else:
                self._send_json(200, {"revoked": revoked})
            return

        if path == "/api/manual":
            payload = self._read_json({"agent_id", "session_id", "state", "sequence"})
            if payload is None:
                return
            try:
                snapshot = self.server.core.record_event(
                    agent_id=payload["agent_id"],
                    session_id=payload["session_id"],
                    state=payload["state"],
                    sequence=payload["sequence"],
                    source="manual",
                )
            except EventConflict as error:
                status = 409
                message = str(error)
            except SessionLimitError:
                status = 409
                message = "Session limit reached."
            except CoreError as error:
                status = 400
                message = str(error)
            except (TypeError, KeyError):
                status = 400
                message = "Invalid event fields."
            else:
                self._send_json(200, snapshot)
                return
            self._send_error(status, message)
            return
        if path == "/api/clear":
            payload = self._read_json(set())
            if payload is None:
                return
            self._send_json(200, self.server.core.clear_sessions())
            return
        self._send_error(404, "Not found.")

    def _desktop_authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header.isascii() and hmac.compare_digest(header, "Bearer " + self.server.viewer_token)

    def _send_static(self, request_path: str) -> None:
        filename = STATIC_FILES[request_path]
        path = (self.server.static_dir / filename).resolve()
        try:
            if self.server.static_dir not in path.parents or not path.is_file():
                raise OSError("not a regular file")
            data = path.read_bytes()
        except OSError:
            self._send_error(404, "Not found.")
            return
        content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}[path.suffix]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_apk(self, *, windows: bool = False) -> None:
        """Serve only the bundled installer, with a bounded streamed response."""
        apk_file = None
        try:
            project_root = (Path(sys.executable).resolve().parent if windows and getattr(sys, "frozen", False) else PROJECT_ROOT.resolve())
            artifact = project_root / ("Uninstall-AgentBeacon.exe" if windows and getattr(sys, "frozen", False)
                                       else "dist/windows/AgentBeacon-Setup.exe" if windows else APK_RELATIVE_PATH)
            resolved_artifact = artifact.resolve(strict=True)
            if project_root not in resolved_artifact.parents:
                raise OSError("artifact is outside the project root")
            open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
            apk_file = os.fdopen(os.open(resolved_artifact, open_flags), "rb")
            file_info = os.fstat(apk_file.fileno())
            if not stat.S_ISREG(file_info.st_mode) or file_info.st_size > (64 * 1024 * 1024 if windows else MAX_APK_BYTES):
                raise OSError("artifact is not a bounded regular file")
        except (OSError, RuntimeError, ValueError):
            # Do not expose filesystem paths or artifact state in the response.
            if apk_file is not None:
                apk_file.close()
            self._send_error(404, "Not found.")
            return

        try:
            size = file_info.st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.microsoft.portable-executable" if windows else "application/vnd.android.package-archive")
            filename = "AgentBeacon-Setup.exe" if windows else APK_FILENAME
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            remaining = size
            while remaining:
                chunk = apk_file.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except OSError:
            # The client may disconnect while the APK is streaming.
            pass
        finally:
            apk_file.close()


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    ttl_seconds: float = 30,
    static_dir: str | Path | None = None,
    *, question_reminder_seconds: float = 120, state_settle_seconds: float = 0, device_store: str | Path | None = None,
) -> BeaconHTTPServer:
    """Create a server without starting its serve loop, useful to CLI and callers."""
    try:
        bind_ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError as error:
        raise ValueError("host must be a numeric IPv4 address") from error
    if not (bind_ip.is_loopback or bind_ip.is_unspecified):
        raise ValueError("host must be a loopback address or 0.0.0.0")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")
    core = BeaconCore(ttl_seconds=ttl_seconds, question_reminder_seconds=question_reminder_seconds,
                      state_settle_seconds=state_settle_seconds)
    public_dir = Path(static_dir) if static_dir is not None else Path(__file__).resolve().parent / "web"
    return BeaconHTTPServer((host, port), core, public_dir, device_store)
