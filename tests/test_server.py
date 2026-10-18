from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import agent_beacon.server as server_module
from agent_beacon.server import create_server
from agent_beacon.pairing import DeviceStoreError, PairingError, PairingManager


class BeaconServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        public_dir = Path(self.temp_dir.name)
        (public_dir / "index.html").write_text("<main>Beacon</main>", encoding="utf-8")
        (public_dir / "styles.css").write_text("body{}", encoding="utf-8")
        (public_dir / "app.js").write_text("void 0;", encoding="utf-8")
        (public_dir / "vendor").mkdir()
        (public_dir / "vendor" / "qr.js").write_text("var qrcode = function() {};", encoding="utf-8")
        self.server = create_server("127.0.0.1", 0, 30, public_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        request_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if headers:
            request_headers.update(headers)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def get_token(self) -> str:
        status, _, body = self.request("GET", "/api/control")
        self.assertEqual(200, status)
        return json.loads(body)["viewer_token"]

    def test_static_allowlist_and_query_paths(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(b"<main>Beacon</main>", body)
        self.assertEqual(404, self.request("GET", "/other.txt")[0])
        qr_status, qr_headers, qr_body = self.request("GET", "/vendor/qr.js")
        self.assertEqual(200, qr_status)
        self.assertIn("javascript", qr_headers["Content-Type"])
        self.assertIn(b"qrcode", qr_body)
        self.assertEqual(400, self.request("GET", "/?token=secret")[0])
        self.assertEqual(404, self.request("GET", "/%2e%2e/secret")[0])

    def test_apk_download_serves_only_the_fixed_public_artifact(self) -> None:
        project_root = Path(self.temp_dir.name)
        artifact_dir = project_root / "dist"
        artifact_dir.mkdir()
        artifact = artifact_dir / "AgentBeacon-debug.apk"
        expected = b"APK fixture bytes\x00\x01"
        artifact.write_bytes(expected)
        with patch.object(server_module, "PROJECT_ROOT", project_root):
            status, headers, body = self.request("GET", "/downloads/AgentBeacon-debug.apk")
        self.assertEqual(200, status)
        self.assertEqual(expected, body)
        self.assertEqual(str(len(expected)), headers["Content-Length"])
        self.assertEqual("application/vnd.android.package-archive", headers["Content-Type"])
        self.assertEqual('attachment; filename="AgentBeacon-debug.apk"', headers["Content-Disposition"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual(400, self.request("GET", "/downloads/AgentBeacon-debug.apk?path=../../secret")[0])
        self.assertEqual(404, self.request("GET", "/downloads/../../secret")[0])

    def test_apk_download_missing_and_oversized_artifacts_are_not_exposed(self) -> None:
        project_root = Path(self.temp_dir.name)
        with patch.object(server_module, "PROJECT_ROOT", project_root):
            missing_status, _, missing_body = self.request("GET", "/downloads/AgentBeacon-debug.apk")
            self.assertEqual(404, missing_status)
            self.assertNotIn(str(project_root).encode(), missing_body)

            artifact_dir = project_root / "dist"
            artifact_dir.mkdir()
            (artifact_dir / "AgentBeacon-debug.apk").write_bytes(b"1234")
            with patch.object(server_module, "MAX_APK_BYTES", 3):
                oversized_status, _, oversized_body = self.request("GET", "/downloads/AgentBeacon-debug.apk")
        self.assertEqual(404, oversized_status)
        self.assertNotIn(str(project_root).encode(), oversized_body)

    def test_bootstrap_is_local_only_and_status_requires_bearer(self) -> None:
        self.assertEqual(403, self.request("GET", "/api/control", host=f"192.168.1.5:{self.port}")[0])
        self.assertEqual(401, self.request("GET", "/api/status")[0])
        control = json.loads(self.request("GET", "/api/control")[2])
        self.assertFalse(control["lan_enabled"])
        token = self.get_token()
        status, _, body = self.request("GET", "/api/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, status)
        snapshot = json.loads(body)
        self.assertIsNone(snapshot["state"])
        self.assertEqual(1, snapshot["schema_version"])

    def test_manual_requires_local_peer_and_exact_origin(self) -> None:
        body = json.dumps({"agent_id": "demo", "session_id": "s1", "state": "working", "sequence": 1})
        base_headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(403, self.request("POST", "/api/manual", headers={**base_headers, "Origin": "http://evil.test"}, body=body)[0])
        self.assertEqual(403, self.request("POST", "/api/manual", headers=base_headers, body=body, host=f"10.1.2.3:{self.port}")[0])
        status, _, response_body = self.request("POST", "/api/manual", headers=base_headers, body=body)
        self.assertEqual(200, status)
        self.assertEqual("working", json.loads(response_body)["state"])

    def test_malformed_json_duplicate_fields_and_body_limits_are_rejected(self) -> None:
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        duplicate = b'{"agent_id":"a","agent_id":"b","session_id":"s","state":"ready","sequence":1}'
        self.assertEqual(400, self.request("POST", "/api/manual", headers=headers, body=duplicate)[0])
        non_finite = b'{"agent_id":"a","session_id":"s","state":"ready","sequence":NaN}'
        self.assertEqual(400, self.request("POST", "/api/manual", headers=headers, body=non_finite)[0])
        oversized = b" " * 8193
        self.assertEqual(413, self.request("POST", "/api/manual", headers=headers, body=oversized)[0])

    def test_non_ascii_bearer_and_oversized_content_length_fail_cleanly(self) -> None:
        self.assertEqual(401, self.request("GET", "/api/status", headers={"Authorization": "Bearer café"})[0])
        response = self.request(
            "POST",
            "/api/clear",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Length": "9" * 500,
            },
            body=b"{}",
        )
        self.assertEqual(413, response[0])

    def test_duplicate_host_or_origin_headers_are_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.putrequest("GET", "/api/control")
        connection.putheader("Host", f"127.0.0.1:{self.port}")
        connection.putheader("Host", f"127.0.0.1:{self.port}")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(400, response.status)
        response.read()
        connection.close()

    def test_duplicate_content_type_headers_are_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.putrequest("POST", "/api/clear")
        connection.putheader("Host", f"127.0.0.1:{self.port}")
        connection.putheader("Origin", f"http://127.0.0.1:{self.port}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Type", "text/plain")
        connection.putheader("Content-Length", "2")
        connection.endheaders(b"{}")
        response = connection.getresponse()
        self.assertEqual(400, response.status)
        response.read()
        connection.close()

    def test_bind_address_rejects_nonlocal_interfaces(self) -> None:
        with self.assertRaises(ValueError):
            create_server("192.168.1.5", 0)

    def test_control_reports_wildcard_bind_as_lan_enabled(self) -> None:
        lan_server = create_server("0.0.0.0", 0, 30, Path(self.temp_dir.name))
        lan_thread = threading.Thread(target=lan_server.serve_forever, daemon=True)
        lan_thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", lan_server.server_port, timeout=2)
            connection.request("GET", "/api/control", headers={"Host": f"localhost:{lan_server.server_port}"})
            response = connection.getresponse()
            control = json.loads(response.read())
            connection.close()
            self.assertEqual(200, response.status)
            self.assertTrue(control["lan_enabled"])
        finally:
            lan_server.shutdown()
            lan_server.server_close()
            lan_thread.join(timeout=2)

    def test_live_server_port_cannot_be_claimed_by_a_second_instance(self) -> None:
        # Windows SO_REUSEADDR otherwise allows a second listener and can
        # direct clients to either server instance on the same local port.
        with self.assertRaises(OSError):
            create_server("127.0.0.1", self.port, 30, Path(self.temp_dir.name))

    def test_total_connection_deadline_closes_slow_client_and_releases_slot(self) -> None:
        error_handled = threading.Event()
        with patch("agent_beacon.server.MAX_CONNECTION_SECONDS", 0.1), patch.object(
            self.server, "handle_error", side_effect=lambda *_args: error_handled.set()
        ):
            slow_client = socket.create_connection(("127.0.0.1", self.port), timeout=1)
            slow_client.settimeout(1)
            slow_client.sendall(b"GET /")
            try:
                self.assertEqual(b"", slow_client.recv(1))
            except (ConnectionResetError, OSError):
                pass
            finally:
                slow_client.close()
            self.assertTrue(error_handled.wait(1))

        self.assertEqual(200, self.request("GET", "/")[0])

    def test_new_server_instance_rotates_instance_id_and_viewer_token(self) -> None:
        restarted = create_server("127.0.0.1", 0, 30, Path(self.temp_dir.name))
        try:
            self.assertNotEqual(self.server.core.instance_id, restarted.core.instance_id)
            self.assertNotEqual(self.server.viewer_token, restarted.viewer_token)
        finally:
            restarted.server_close()

    def test_invalid_origin_authorization_and_nested_json_fail_as_client_errors(self) -> None:
        self.assertEqual(403, self.request("GET", "/api/control", headers={"Origin": "http://[invalid"})[0])
        self.assertEqual(401, self.request("GET", "/api/status", headers={"Authorization": "Bearer é"})[0])
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        nested = b'{"agent_id":"a","session_id":"s","state":"ready","sequence":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
        self.assertEqual(400, self.request("POST", "/api/manual", headers=headers, body=nested)[0])

    def test_native_pairing_authentication_and_local_device_controls(self) -> None:
        origin = f"http://127.0.0.1:{self.port}"
        local_headers = {"Content-Type": "application/json", "Origin": origin}
        status, _, body = self.request("POST", "/api/pairing", headers=local_headers, body=b"{}")
        self.assertEqual(200, status)
        code = json.loads(body)["code"]
        pair_body = json.dumps({"code": code, "device_name": "Phone"})
        status, _, body = self.request("POST", "/api/pair", headers={"Content-Type": "application/json"}, body=pair_body)
        self.assertEqual(200, status)
        paired = json.loads(body)
        self.assertEqual(self.server.core.instance_id, paired["instance_id"])
        token = paired["device_token"]
        status, _, body = self.request("GET", "/api/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(200, status)
        self.assertEqual(403, self.request("POST", "/api/manual", headers={"Content-Type": "application/json"}, body=b"{}")[0])
        self.assertEqual(403, self.request("GET", "/api/devices", host=f"192.168.1.20:{self.port}")[0])
        self.assertEqual(200, self.request("GET", "/api/devices")[0])
        revoke = json.dumps({"device_id": paired["device_id"]})
        self.assertEqual(200, self.request("POST", "/api/revoke", headers=local_headers, body=revoke)[0])
        self.assertEqual(401, self.request("GET", "/api/status", headers={"Authorization": f"Bearer {token}"})[0])
        self.assertEqual(409, self.request("POST", "/api/pair", headers={"Content-Type": "application/json"}, body=pair_body)[0])

    def test_pair_endpoint_checks_any_supplied_origin(self) -> None:
        code = self.server.pairing.mint_code()
        body = json.dumps({"code": code, "device_name": "Browser"})
        headers = {"Content-Type": "application/json", "Origin": "http://evil.example"}
        self.assertEqual(403, self.request("POST", "/api/pair", headers=headers, body=body)[0])


class PairingManagerTests(unittest.TestCase):
    def test_codes_expire_are_single_use_and_bounded(self) -> None:
        now = [10.0]
        manager = PairingManager(clock=lambda: now[0])
        code = manager.mint_code()
        result = manager.pair(code, "Phone", "instance-a")
        self.assertEqual("instance-a", result["instance_id"])
        with self.assertRaises(PairingError):
            manager.pair(code, "Second", "instance-a")
        expired = manager.mint_code()
        now[0] += 121
        with self.assertRaises(PairingError):
            manager.pair(expired, "Phone", "instance-a")
        for _ in range(8):
            manager.mint_code()
        with self.assertRaises(PairingError):
            manager.mint_code()

    def test_concurrent_pair_attempts_consume_code_once(self) -> None:
        manager = PairingManager()
        code = manager.mint_code()
        successes: list[dict[str, str]] = []
        def consume() -> None:
            try:
                successes.append(manager.pair(code, "Phone", "instance"))
            except PairingError:
                pass
        workers = [threading.Thread(target=consume) for _ in range(8)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertEqual(1, len(successes))

    def test_persistent_store_contains_only_digests_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "devices.json"
            first = PairingManager(store)
            code = first.mint_code()
            device = first.pair(code, "Trusted Phone", "instance-1")
            raw = store.read_text(encoding="utf-8")
            self.assertNotIn(device["device_token"], raw)
            second = PairingManager(store)
            self.assertTrue(second.authorized(device["device_token"]))
            self.assertEqual(device["device_id"], second.devices()[0]["device_id"])
            self.assertTrue(second.revoke(device["device_id"]))
            self.assertFalse(PairingManager(store).authorized(device["device_token"]))

    def test_malformed_store_fails_closed_and_write_failure_preserves_prior_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "devices.json"
            store.write_text('{"devices":[{"device_id":"invalid"}]}', encoding="utf-8")
            with self.assertRaises(DeviceStoreError):
                PairingManager(store)
            deeply_nested = b'{"devices":[' + b"[" * 1200 + b"0" + b"]" * 1200 + b"]}"
            store.write_bytes(deeply_nested)
            with self.assertRaises(DeviceStoreError):
                PairingManager(store)
            store.unlink()
            manager = PairingManager(store)
            code = manager.mint_code()
            with patch("agent_beacon.pairing.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(DeviceStoreError):
                    manager.pair(code, "Phone", "instance")
            self.assertEqual([], manager.devices())
            self.assertFalse(store.exists())
            self.assertEqual([], list(Path(temp).glob("devices.json.tmp-*")))


if __name__ == "__main__":
    unittest.main()
