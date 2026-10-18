"""Command-line entry point for Agent Beacon."""

from __future__ import annotations

import argparse
import ipaddress
import math
import logging
import sys
import threading
from pathlib import Path

from .server import create_server
from .pairing import DeviceStoreError


def _positive_ttl(value: str) -> float:
    try:
        ttl = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("TTL must be a positive number") from error
    if ttl <= 0 or not math.isfinite(ttl) or ttl > 86_400:
        raise argparse.ArgumentTypeError("TTL must be a positive finite number no greater than 86400")
    return ttl


def _reminder_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("question reminder must be a number") from error
    if not math.isfinite(seconds) or seconds < 0 or seconds > 86_400:
        raise argparse.ArgumentTypeError("question reminder must be a finite number from 0 to 86400")
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Agent Beacon status viewer.")
    parser.add_argument("--host", default="127.0.0.1", help="numeric IPv4 bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--ttl", type=_positive_ttl, default=30, help="session freshness TTL in seconds (default: 30)")
    parser.add_argument("--question-reminder-seconds", type=_reminder_seconds, default=120,
                        help="hold yellow after an unconfirmed resume until this many seconds from the question; 0 disables the hold (default: 120)")
    parser.add_argument("--integrations", nargs="+", choices=("codex", "claude", "antigravity"),
                        help="opt in to official local hooks for the selected provider(s)")
    parser.add_argument("--tray", action="store_true", help="run the Windows tray and its local status server")
    parser.add_argument("--device-store", type=Path, help="optional path for persistent paired-device token digests")
    arguments = parser.parse_args()

    try:
        ipaddress.IPv4Address(arguments.host)
        server = create_server(arguments.host, arguments.port, arguments.ttl,
                               question_reminder_seconds=arguments.question_reminder_seconds, state_settle_seconds=1.0,
                               device_store=arguments.device_store)
    except (ValueError, OSError, DeviceStoreError) as error:
        if isinstance(error, OSError) and (getattr(error, "winerror", None) == 10048 or error.errno in (48, 98, 10048)):
            message = f"Agent Beacon could not start: port {arguments.port} is already in use."
        else:
            message = f"Agent Beacon could not start: {error}"
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return 2

    hook_bridge = None
    if arguments.integrations:
        try:
            from .adapters.bridge import HookBridge

            hook_bridge = HookBridge(server.core, arguments.integrations, Path.cwd() / ".beacon")
            hook_bridge.start()
        except (OSError, ValueError) as error:
            server.server_close()
            if sys.stderr is not None:
                print(f"Agent Beacon integrations could not start: {error}", file=sys.stderr)
            return 2

    local_host = arguments.host if ipaddress.IPv4Address(arguments.host).is_loopback else "127.0.0.1"
    discovery = None
    if arguments.host == "0.0.0.0" and arguments.device_store is not None:
        from .discovery import LanDiscovery
        discovery = LanDiscovery(server.pairing, server.server_port, server.core.instance_id)
        try:
            discovery.start()
        except OSError as error:
            logging.getLogger(__name__).warning("LAN discovery unavailable (%s); saved-address connections remain available", type(error).__name__)
            discovery = None
    if arguments.tray:
        stop_event = threading.Event()
        server_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="agent-beacon-http",
            daemon=True,
        )
        try:
            server_thread.start()
            from .tray import run_tray
            return run_tray(port=server.server_port, host=arguments.host if ipaddress.IPv4Address(arguments.host).is_loopback else "127.0.0.1", stop_event=stop_event)
        except (OSError, ValueError) as error:
            if sys.stderr is not None:
                print(f"Agent Beacon tray could not start: {error}", file=sys.stderr)
            return 2
        finally:
            stop_event.set()
            if discovery is not None:
                discovery.close()
            if hook_bridge is not None:
                hook_bridge.close()
            if server_thread.is_alive():
                server.shutdown()
                server_thread.join(timeout=2.0)
            server.server_close()
    print(f"Agent Beacon is listening. Local controller: http://{local_host}:{server.server_port}/", flush=True)
    if not ipaddress.IPv4Address(arguments.host).is_loopback:
        print("LAN access is enabled; use the viewer URL shown in the local browser to share access.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nAgent Beacon stopped.", flush=True)
    finally:
        if discovery is not None:
            discovery.close()
        if hook_bridge is not None:
            hook_bridge.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
