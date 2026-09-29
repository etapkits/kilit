import os
import tempfile
import unittest

from etakit_kilit.client import ApiError
from etakit_kilit.config import Config
from etakit_kilit.link import LinkWorker
from etakit_kilit.session import BoardSession
from etakit_kilit.store import StateStore


class FakeClient:
    def __init__(self):
        self.heartbeats = []
        self.qrs = []
        self.acks = []
        self.registered = 0
        self.commands = []
        self.fail_register = None
        self.fail_heartbeat = None
        self.fail_settings = None
        self.visible_name = ""
        self.server_policy = {"configured": False}
        self.base = ""

    def retarget(self, server_url, verify_tls):
        from etakit_kilit.client import device_base

        self.base = device_base(server_url)

    def register(self, enrollment_key, machine_id, hostname, name=""):
        self.registered += 1
        self.registered_at = self.base
        self.board_name = name
        if self.fail_register:
            raise self.fail_register
        return {
            "device_token": "jeton",
            "device_code": "AB7K",
            "board_id": "board-1",
            "approval": "approved",
            "name": name or hostname or "Fen",
        }

    def fetch_settings(self, token):
        if self.fail_settings:
            raise self.fail_settings
        return dict(self.server_policy)

    def heartbeat(self, token, state):
        if self.fail_heartbeat:
            raise self.fail_heartbeat
        self.heartbeats.append((token, state))
        return {
            "ok": True,
            "approval": "approved",
            "device_code": "AB7K",
            "name": self.visible_name,
        }

    def report_qr(self, token, code):
        self.qrs.append(code)
        return {"ok": True, "ttl_seconds": 25}

    def poll_commands(self, token, wait):
        command = self.commands.pop(0) if self.commands else None
        return {
            "command": command,
            "name": self.visible_name,
            "device_code": "AB7K",
            "approval": "approved",
        }

    def ack(self, token, command_id):
        if getattr(self, "fail_ack", None):
            raise self.fail_ack
        self.acks.append(command_id)


class LinkTest(unittest.TestCase):
    def test_an_old_server_token_registers_on_the_new_server(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                server_url="https://yeni.okul.local",
                enrollment_key="anahtar",
                state_dir=directory,
            )
            store = StateStore(directory)
            store.save_device("eski-jeton", "AB7K", "board-1", "10-A", "https://eski.okul.local")
            board = BoardSession(config, now=0)
            client = FakeClient()
            client.base = "https://eski.okul.local/api/device"
            worker = LinkWorker(config, board, store, client, clock=lambda: 50)

            self.assertEqual("", worker.token)
            self.assertTrue(worker.flush_reports())
            self.assertEqual(1, client.registered)
            self.assertEqual("https://yeni.okul.local/api/device", client.registered_at)
            self.assertEqual("jeton", store.load_device().token)
            self.assertTrue(store.token_belongs("https://yeni.okul.local"))
            self.assertFalse(store.token_belongs("https://eski.okul.local"))

    def test_register_reports_locked_and_the_qr_then_acks_unlock(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(server_url="https://etakit.okul.local", enrollment_key="anahtar", state_dir=directory)
            board = BoardSession(config, now=0)
            client = FakeClient()
            powered = []
            worker = LinkWorker(
                config,
                board,
                StateStore(directory),
                client,
                poweroff=lambda: powered.append(True) or True,
                clock=lambda: 100,
            )

            self.assertTrue(worker.flush_reports())
            self.assertEqual(1, client.registered)
            self.assertEqual([("jeton", "locked")], client.heartbeats)
            self.assertEqual(1, len(client.qrs))
            self.assertFalse(board.qr_to_report())

            client.commands.append({"id": "open-1", "type": "unlock"})
            worker.poll_once()
            self.assertEqual("unlocked", board.view().phase)
            self.assertTrue(worker.flush_reports())
            self.assertEqual(("jeton", "unlocked"), client.heartbeats[-1])
            self.assertEqual(["open-1"], client.acks)

            client.commands.append({"id": "down-1", "type": "shutdown"})
            worker.poll_once()
            worker._last_power = 0
            self.assertTrue(worker.flush_reports())
            self.assertEqual(["open-1", "down-1"], client.acks)
            self.assertEqual([True], powered)
            self.assertEqual("shutting_down", client.heartbeats[-1][1])

    def test_shutdown_powers_off_even_when_the_server_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(server_url="https://etakit.okul.local", enrollment_key="anahtar", state_dir=directory)
            board = BoardSession(config, now=0)
            client = FakeClient()
            powered = []
            now = [100.0]
            worker = LinkWorker(
                config,
                board,
                StateStore(directory),
                client,
                poweroff=lambda: powered.append(True) or True,
                clock=lambda: now[0],
            )
            self.assertTrue(worker.flush_reports())

            client.commands.append({"id": "down-1", "type": "shutdown"})
            worker.poll_once()
            client.fail_heartbeat = OSError("ağ yok")
            client.fail_ack = OSError("ağ yok")

            self.assertFalse(worker.flush_reports())
            self.assertEqual([], powered)

            now[0] = 120.0
            self.assertFalse(worker.flush_reports())
            self.assertEqual([True], powered)

    def test_shutdown_waits_for_the_ack_before_powering_off(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(server_url="https://etakit.okul.local", enrollment_key="anahtar", state_dir=directory)
            board = BoardSession(config, now=0)
            client = FakeClient()
            powered = []
            worker = LinkWorker(
                config,
                board,
                StateStore(directory),
                client,
                poweroff=lambda: powered.append(list(client.acks)) or True,
                clock=lambda: 100,
            )
            self.assertTrue(worker.flush_reports())

            client.commands.append({"id": "down-1", "type": "shutdown"})
            worker.poll_once()
            self.assertTrue(worker.flush_reports())
            self.assertEqual([["down-1"]], powered)

    def test_bad_enrollment_key_stays_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(server_url="https://etakit.okul.local", enrollment_key="yanlis", state_dir=directory)
            board = BoardSession(config, now=0)
            client = FakeClient()
            client.fail_register = ApiError(401, "Kayıt anahtarı geçersiz.")
            worker = LinkWorker(config, board, StateStore(directory), client, clock=lambda: 1)

            self.assertFalse(worker.flush_reports())
            view = board.view()
            self.assertTrue(view.grab)
            self.assertEqual("Kayıt anahtarı geçersiz.", view.message)
            self.assertEqual("", worker.token)

    def test_rejected_token_is_cleared_and_the_next_flush_registers_again(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.save_device("eski", "AB7K", "board-1", "Fen")
            config = Config(
                server_url="https://etakit.okul.local",
                enrollment_key="anahtar",
                state_dir=directory,
                heartbeat_seconds=10,
            )
            board = BoardSession(config, now=0)
            client = FakeClient()
            client.fail_heartbeat = ApiError(401, "Tahta kimliği geçersiz.")
            worker = LinkWorker(config, board, store, client, clock=lambda: 50)

            self.assertFalse(worker.flush_reports())
            self.assertEqual("", store.load_device().token)
            client.fail_heartbeat = None
            self.assertTrue(worker.flush_reports())
            self.assertEqual("jeton", store.load_device().token)
            self.assertEqual("locked", board.view().phase)
            self.assertEqual("AB7K", board.view().device_code)
            self.assertTrue(board.view().qr_payload.startswith("etakit:1:AB7K:"))

    def test_a_renamed_board_shows_the_new_name_on_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                server_url="https://panel.okul.local",
                enrollment_key="anahtar",
                state_dir=directory,
                heartbeat_seconds=10,
            )
            board = BoardSession(config, now=0)
            client = FakeClient()
            worker = LinkWorker(config, board, StateStore(directory), client, clock=lambda: 50)

            self.assertTrue(worker.flush_reports())
            self.assertEqual("", client.board_name)
            client.visible_name = "10-A"
            worker._last_hb = 0
            self.assertTrue(worker.flush_reports())
            self.assertEqual("10-A", board.view().board_name)

            client.visible_name = "9/B"
            worker.poll_once()
            self.assertEqual("9/B", board.view().board_name)

    def test_a_server_name_is_written_into_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "kilit.conf")
            config = Config(
                server_url="https://panel.okul.local",
                enrollment_key="anahtar",
                state_dir=directory,
                heartbeat_seconds=10,
                board_name="Fen",
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(config.render())
            board = BoardSession(config, now=0)
            client = FakeClient()
            client.visible_name = "10-A"
            worker = LinkWorker(
                config,
                board,
                StateStore(directory),
                client,
                clock=lambda: 50,
                config_path=path,
            )
            self.assertEqual("Fen", board.view().board_name)
            self.assertTrue(worker.flush_reports())
            self.assertEqual("10-A", Config.load(path).board_name)
            self.assertEqual("10-A", board.view().board_name)

    def test_missing_server_policy_keeps_the_local_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                server_url="https://panel.okul.local",
                enrollment_key="anahtar",
                state_dir=directory,
                idle_seconds=600,
                lock_countdown_seconds=10,
            )
            board = BoardSession(config, now=0)
            client = FakeClient()
            worker = LinkWorker(config, board, StateStore(directory), client, clock=lambda: 50)
            self.assertTrue(worker.flush_reports())
            self.assertEqual(600, config.idle_seconds)
            self.assertEqual(10, config.lock_countdown_seconds)

            client.server_policy = {
                "configured": True,
                "idle_seconds": 120,
                "lock_countdown_seconds": 5,
                "offline_grace_seconds": 60,
                "emergency_seconds": 1800,
                "session_seconds": 2400,
                "emergency_pin_hash": "",
            }
            worker._last_policy = 0
            self.assertTrue(worker.flush_reports())
            self.assertEqual(120, config.idle_seconds)
            self.assertEqual(5, config.lock_countdown_seconds)
            self.assertEqual(2400, config.session_seconds)

            client.server_policy = {"configured": False}
            worker._last_policy = 0
            self.assertTrue(worker.flush_reports())
            self.assertEqual(600, config.idle_seconds)
            self.assertEqual(10, config.lock_countdown_seconds)
            self.assertEqual(0, config.session_seconds)

            client.fail_settings = ApiError(500, "SQLSTATE[42S02]: Base table or view not found: lock_settings")
            worker._last_policy = 0
            self.assertTrue(worker.flush_reports())
            self.assertEqual(600, config.idle_seconds)
            self.assertNotIn("SQLSTATE", board.view().message)


if __name__ == "__main__":
    unittest.main()
