import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from etakit_kilit.client import ApiError, DeviceClient


class ClientTest(unittest.TestCase):
    def test_register_heartbeat_qr_poll_and_ack(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                seen["path"] = self.path
                seen["auth"] = self.headers.get("Authorization")
                seen["body"] = json.loads(raw) if raw else {}
                if self.path == "/api/device/register":
                    self._json(201, {
                        "board_id": "board-1",
                        "device_token": "jeton",
                        "device_code": "AB7K",
                        "approval": "pending",
                        "name": "Fen",
                    })
                    return
                if self.path == "/api/device/heartbeat":
                    self._json(200, {"ok": True, "approval": "approved", "device_code": "AB7K"})
                    return
                if self.path == "/api/device/qr":
                    self._json(200, {"ok": True, "ttl_seconds": 25})
                    return
                if self.path == "/api/device/commands/cmd-1/ack":
                    self._json(200, {"ok": True})
                    return
                self._json(404, {"message": "yok"})

            def do_GET(self):
                seen["poll"] = self.path
                self._json(200, {"command": {"id": "cmd-1", "type": "unlock"}})

            def _json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        client = DeviceClient(base)

        registered = client.register("anahtar", "machine-1", "Fen")
        self.assertEqual("jeton", registered["device_token"])
        self.assertEqual("anahtar", seen["body"]["enrollment_key"])

        beat = client.heartbeat("jeton", "locked")
        self.assertEqual("Bearer jeton", seen["auth"])
        self.assertEqual("approved", beat["approval"])

        qr = client.report_qr("jeton", "etakit:1:AB7K:" + ("a" * 32))
        self.assertEqual(25, qr["ttl_seconds"])

        command = client.poll_commands("jeton", 0)
        self.assertEqual("/api/device/commands?wait=0", seen["poll"])
        self.assertEqual("unlock", command["command"]["type"])
        client.ack("jeton", command["command"]["id"])
        self.assertEqual("/api/device/commands/cmd-1/ack", seen["path"])
        client.register("anahtar", "machine-1", "Fen", "10-A")
        self.assertEqual("10-A", seen["body"]["name"])
        self.assertNotIn("device_code", seen["body"])

    def test_unauthorized_register_raises(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                body = json.dumps({"message": "Kayıt anahtarı geçersiz."}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = DeviceClient(f"http://127.0.0.1:{server.server_port}")

        with self.assertRaises(ApiError) as caught:
            client.register("yanlis", "machine-1", "Fen")
        self.assertEqual(401, caught.exception.status)
        self.assertEqual("Kayıt anahtarı geçersiz.", caught.exception.message)

    def test_register_page_url_still_targets_the_device_api(self):
        from etakit_kilit.client import device_base

        root = "https://etakits.teknovip.net"
        self.assertEqual(device_base(root + "/"), device_base(root + "/api/device/register"))
        self.assertEqual(root + "/api/device", device_base(root))


if __name__ == "__main__":
    unittest.main()
