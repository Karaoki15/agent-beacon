"""Small authenticated LAN discovery for previously paired phones only."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
import time
from collections import OrderedDict

from .pairing import PairingManager

DISCOVERY_PORT = 47665
PROTOCOL = "agent-beacon-discovery-v1"
logger = logging.getLogger(__name__)
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_host(host: str) -> bool:
    try:
        address = ipaddress.IPv4Address(host)
        return any(address in network for network in PRIVATE_NETWORKS)
    except ipaddress.AddressValueError:
        return False


class LanDiscovery:
    def __init__(self, pairing: PairingManager, http_port: int, instance_id: str,
                 *, port: int = DISCOVERY_PORT) -> None:
        self.pairing, self.http_port, self.instance_id = pairing, http_port, instance_id
        self.port = port
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._peers: OrderedDict[str, float] = OrderedDict()

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if os.name == "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._thread = threading.Thread(target=self._run, name="agent-beacon-discovery", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        sock = self._socket
        assert sock is not None
        while not self._stop.is_set():
            try:
                data, peer = sock.recvfrom(1025)
                if len(data) > 1024 or not private_host(peer[0]):
                    continue
                now = time.monotonic()
                if now - self._peers.get(peer[0], float("-inf")) < 0.25:
                    continue
                self._peers[peer[0]] = now
                self._peers.move_to_end(peer[0])
                while len(self._peers) > 64:
                    self._peers.popitem(last=False)
                payload = json.loads(data.decode("ascii"))
                if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
                    continue
                # Let routing select this interface's local IPv4 address; do not
                # advertise a VPN or a remembered address from another network.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                    route.connect(peer)
                    host = route.getsockname()[0]
                if not private_host(host):
                    continue
                reply = self.pairing.discovery_reply(payload.get("device_id"), payload.get("nonce"),
                                                     payload.get("proof"), host, self.http_port, self.instance_id)
                if reply is not None:
                    sock.sendto(json.dumps(reply, separators=(",", ":")).encode("ascii"), peer)
            except socket.timeout:
                continue
            except (ValueError, UnicodeError, RecursionError):
                continue
            except OSError as error:
                if getattr(error, "winerror", None) == 10054:
                    continue  # A phone closed its UDP socket before the reply.
                if not self._stop.is_set():
                    logger.warning("LAN discovery socket failed (%s)", type(error).__name__)
                return
