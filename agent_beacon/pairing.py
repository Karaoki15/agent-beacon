"""Bounded, single-use LAN pairing and read-only device credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

MAX_CODES = 8
MAX_DEVICES = 32
MAX_STORE_BYTES = 16 * 1024
CODE_TTL_SECONDS = 120
_DEVICE_NAME = re.compile(r"^.{1,48}$", re.DOTALL)


def _valid_device_name(value: Any) -> bool:
    if not isinstance(value, str) or not _DEVICE_NAME.fullmatch(value):
        return False
    return all(unicodedata.category(char) not in {"Cc", "Cf"} for char in value)


class PairingError(ValueError):
    """Invalid pairing request or bounded capacity reached."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class DeviceStoreError(RuntimeError):
    """Persistent device store is invalid or could not be safely updated."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class PairingManager:
    def __init__(self, path: str | Path | None = None, *, clock=time.monotonic) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._codes: dict[str, float] = {}
        self._path = Path(path) if path is not None else None
        self._devices: dict[str, dict[str, str]] = {}
        if self._path is not None and self._path.exists():
            self._devices = self._load()

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            raw = self._path.read_bytes()
            if len(raw) > MAX_STORE_BYTES:
                raise ValueError("store too large")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(payload, dict) or set(payload) != {"devices"} or not isinstance(payload["devices"], list):
                raise ValueError("invalid top-level schema")
            if len(payload["devices"]) > MAX_DEVICES:
                raise ValueError("too many devices")
            result: dict[str, dict[str, str]] = {}
            for item in payload["devices"]:
                if not isinstance(item, dict) or set(item) != {"device_id", "device_name", "token_sha256"}:
                    raise ValueError("invalid device record")
                device_id, name, digest = item["device_id"], item["device_name"], item["token_sha256"]
                if (not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", device_id)
                        or not _valid_device_name(name)
                        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or device_id in result):
                    raise ValueError("invalid device fields")
                result[device_id] = {"device_name": name, "token_sha256": digest}
            return result
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError) as error:
            raise DeviceStoreError("device store could not be loaded safely") from error

    def _persist(self, devices: dict[str, dict[str, str]]) -> None:
        if self._path is None:
            return
        payload = {"devices": [
            {"device_id": key, **value}
            for key, value in sorted(devices.items())
        ]}
        data = (json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        temporary = self._path.with_name(self._path.name + ".tmp-" + secrets.token_hex(8))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DeviceStoreError("device store could not be updated safely") from error

    def mint_code(self) -> str:
        with self._lock:
            now = self._clock()
            self._codes = {code: expiry for code, expiry in self._codes.items() if expiry > now}
            if len(self._codes) >= MAX_CODES:
                raise PairingError("Pairing code limit reached.")
            code = secrets.token_urlsafe(32)
            self._codes[code] = now + CODE_TTL_SECONDS
            return code

    def pair(self, code: Any, device_name: Any, instance_id: str) -> dict[str, str]:
        if not isinstance(code, str) or len(code) != 43 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
            raise PairingError("Malformed pairing code.", 400)
        if not _valid_device_name(device_name) or not device_name.strip():
            raise PairingError("device_name must contain 1 to 48 printable characters.", 400)
        name = device_name.strip()
        with self._lock:
            now = self._clock()
            self._codes = {key: expiry for key, expiry in self._codes.items() if expiry > now}
            expiry = self._codes.get(code)
            if expiry is None or expiry <= now:
                raise PairingError("Invalid or expired pairing code.")
            if len(self._devices) >= MAX_DEVICES:
                raise PairingError("Paired device limit reached.")
            device_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            updated = dict(self._devices)
            updated[device_id] = {"device_name": name, "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest()}
            self._persist(updated)
            self._devices = updated
            del self._codes[code]
            return {"device_id": device_id, "device_token": token, "instance_id": instance_id}

    def authorized(self, token: str) -> bool:
        if not isinstance(token, str) or not token.isascii():
            return False
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._lock:
            return any(secrets.compare_digest(digest, item["token_sha256"]) for item in self._devices.values())

    def devices(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"device_id": device_id, "device_name": value["device_name"]}
                    for device_id, value in sorted(self._devices.items())]

    def import_into_empty_registry(self, path: Path) -> int:
        """Explicit local migration; never replace already enrolled devices."""
        candidate = PairingManager(path)
        with self._lock:
            if self._path is None or self._devices:
                raise DeviceStoreError("destination must be a persistent, empty registry")
            if not candidate._devices:
                raise DeviceStoreError("source has no paired devices")
            self._persist(candidate._devices)
            self._devices = {key: dict(value) for key, value in candidate._devices.items()}
            return len(self._devices)

    def discovery_reply(self, device_id: str, nonce: str, proof: str,
                        host: str, port: int, instance_id: str) -> dict[str, Any] | None:
        """Prove a new endpoint to an already paired phone without exposing its token."""
        if not all(isinstance(value, str) for value in (device_id, nonce, proof)):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", nonce) or not re.fullmatch(r"[0-9a-f]{64}", proof):
            return None
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return None
            key = bytes.fromhex(device["token_sha256"])
            prefix = "agent-beacon-discovery-v1\n"
            request = f"{prefix}probe\n{device_id}\n{nonce}".encode("ascii")
            if not hmac.compare_digest(hmac.new(key, request, "sha256").hexdigest(), proof):
                return None
            message = f"{prefix}reply\n{device_id}\n{nonce}\n{host}\n{port}\n{instance_id}".encode("ascii")
            return {"protocol": "agent-beacon-discovery-v1", "nonce": nonce, "host": host,
                    "port": port, "instance_id": instance_id,
                    "proof": hmac.new(key, message, "sha256").hexdigest()}

    def revoke(self, device_id: Any) -> bool:
        if not isinstance(device_id, str):
            raise PairingError("Invalid device_id.")
        with self._lock:
            if device_id not in self._devices:
                return False
            updated = dict(self._devices)
            del updated[device_id]
            self._persist(updated)
            self._devices = updated
            return True
