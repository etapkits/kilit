import unittest

from etakit_kilit.config import Config
from etakit_kilit.session import BoardSession


def session(**overrides) -> BoardSession:
    values = dict(
        server_url="https://etakit.okul.local",
        enrollment_key="anahtar",
        idle_seconds=600,
        lock_countdown_seconds=10,
        offline_grace_seconds=45,
        heartbeat_seconds=10,
        command_wait_seconds=20,
    )
    values.update(overrides)
    return BoardSession(Config(**values), now=0)


class SessionTest(unittest.TestCase):
    def test_starts_locked_and_shows_a_qr_only_after_approval(self):
        board = session()
        view = board.view()
        self.assertTrue(view.grab)
        self.assertEqual("", view.qr_payload)
        self.assertEqual("Sunucuya bağlanılıyor.", view.message)
        board.on_link_lost(now=1, reason="Sunucu sertifikası doğrulanamadı.")
        self.assertEqual("Sunucu sertifikası doğrulanamadı.", board.view().message)

        board.on_registered("pending", "AB7K", "Fen", now=1)
        view = board.view()
        self.assertEqual("AB7K", view.device_code)
        self.assertEqual("Fen", view.board_name)
        self.assertEqual("", view.qr_payload)
        self.assertEqual("Yönetici onayı bekleniyor.", view.message)
        self.assertEqual([], board.peek_acks())

        board.on_command("cmd-lock", "lock", now=2)
        self.assertEqual(["cmd-lock"], board.peek_acks())
        self.assertTrue(board.view().grab)

        board.on_link_ok("approved", "AB7K")
        view = board.view()
        self.assertTrue(view.qr_payload.startswith("etakit:1:AB7K:"))
        self.assertEqual("Kilidi açmak için karekodu okutun.", view.message)
        self.assertEqual(view.qr_payload, board.qr_to_report())

    def test_unlock_opens_the_desktop_and_lock_waits_out_the_countdown(self):
        board = self._approved()
        board.on_command("open-1", "unlock", now=5)
        view = board.view()
        self.assertEqual("unlocked", view.phase)
        self.assertTrue(view.show_lock_button)
        self.assertFalse(view.grab)
        self.assertEqual("", view.qr_payload)
        self.assertEqual("unlocked", board.reported_state())
        self.assertEqual(["open-1"], board.peek_acks())

        board.on_command("lock-1", "lock", now=6)
        view = board.view()
        self.assertTrue(view.show_countdown)
        self.assertEqual("unlocked", board.reported_state())
        self.assertEqual("Tahta 10 saniye içinde kilitlenecek.", view.countdown_text)
        self.assertEqual([], [item for item in board.peek_acks() if item == "lock-1"])

        board.tick(15.9)
        self.assertEqual("countdown", board.view().phase)
        board.tick(16)
        view = board.view()
        self.assertEqual("locked", view.phase)
        self.assertTrue(view.grab)
        self.assertIn("lock-1", board.peek_acks())
        self.assertTrue(view.qr_payload.startswith("etakit:1:AB7K:"))
        self.assertEqual("locked", board.reported_state())

    def test_idle_needs_an_x_sample(self):
        board = self._approved()
        board.on_command("open-1", "unlock", now=0)
        board.tick(1000)
        self.assertEqual("unlocked", board.view().phase)

    def test_corner_button_and_idle_lock_immediately(self):
        board = self._approved()
        board.on_command("open-1", "unlock", now=0)
        board.user_lock(now=3)
        self.assertEqual("locked", board.view().phase)
        self.assertEqual("locked", board.reported_state())

        board.on_command("open-2", "unlock", now=4)
        board.note_x_idle(80, now=4)
        board.tick(20)
        self.assertEqual("unlocked", board.view().phase)
        board.note_x_idle(80 + 600, now=604)
        board.tick(604)
        self.assertEqual("locked", board.view().phase)

    def test_unlocked_board_locks_when_the_server_stays_away(self):
        board = self._approved()
        board.on_command("open-1", "unlock", now=10)
        board.on_link_lost(now=12)
        board.tick(56)
        self.assertEqual("unlocked", board.view().phase)
        self.assertIn("ulaşılamıyor", board.view().message)
        board.tick(57)
        self.assertEqual("locked", board.view().phase)

        board.on_link_ok()
        board.tick(58)
        self.assertEqual("locked", board.view().phase)

    def test_locked_board_stays_locked_offline(self):
        board = self._approved()
        payload = board.view().qr_payload
        board.on_link_lost(now=1)
        board.tick(90)
        view = board.view()
        self.assertEqual("locked", view.phase)
        self.assertTrue(view.grab)
        self.assertNotEqual(payload, view.qr_payload)
        self.assertTrue(view.warn)

    def test_shutdown_is_reported_and_repeated_until_the_board_powers_off(self):
        board = self._approved()
        board.on_command("open-1", "unlock", now=1)
        board.on_command("down-1", "shutdown", now=2)
        self.assertEqual("shutting_down", board.reported_state())
        self.assertTrue(board.wants_poweroff())
        self.assertTrue(board.view().grab)
        self.assertEqual("Tahta kapanıyor.", board.view().message)
        self.assertIn("down-1", board.peek_acks())
        board.on_command("open-2", "unlock", now=3)
        self.assertEqual("shutting_down", board.view().phase)

    def test_rejected_board_never_shows_a_qr(self):
        board = session()
        board.on_registered("rejected", "AB7K", "Fen", now=1)
        board.tick(30)
        view = board.view()
        self.assertEqual("", view.qr_payload)
        self.assertEqual("Bu tahta reddedildi.", view.message)
        self.assertTrue(view.grab)

    def test_emergency_pin_opens_for_five_minutes_then_locks(self):
        from etakit_kilit.config import hash_emergency_pin

        board = session(
            emergency_pin_hash=hash_emergency_pin("1234"),
            emergency_seconds=300,
            emergency_max_attempts=5,
            emergency_lockout_seconds=300,
        )
        self.assertEqual("unconfigured", session().try_emergency_pin("1234", 0))
        self.assertEqual("denied", board.try_emergency_pin("9999", 1))
        self.assertEqual("ok", board.try_emergency_pin("1234", 2))
        self.assertEqual("unlocked", board.view().phase)
        self.assertEqual("unlocked", board.reported_state())
        self.assertEqual([], board.peek_acks())
        board.tick(301)
        self.assertEqual("unlocked", board.view().phase)
        board.tick(302)
        self.assertEqual("locked", board.view().phase)

    def test_emergency_unlock_stays_open_while_the_server_is_down(self):
        from etakit_kilit.config import hash_emergency_pin

        board = session(
            emergency_pin_hash=hash_emergency_pin("1234"),
            emergency_seconds=300,
            offline_grace_seconds=45,
        )
        board.on_link_lost(now=0)
        self.assertEqual("ok", board.try_emergency_pin("1234", 120))
        board.on_registered("pending", "AB7K", "Fen", now=121)
        board.tick(122)
        self.assertEqual("unlocked", board.view().phase)
        board.tick(419)
        self.assertEqual("unlocked", board.view().phase)
        board.tick(420)
        self.assertEqual("locked", board.view().phase)

    def test_emergency_pin_locks_out_after_repeated_failures(self):
        from etakit_kilit.config import hash_emergency_pin

        board = session(
            emergency_pin_hash=hash_emergency_pin("1234"),
            emergency_max_attempts=2,
            emergency_lockout_seconds=300,
        )
        self.assertEqual("denied", board.try_emergency_pin("0000", 0))
        self.assertEqual("locked", board.try_emergency_pin("0000", 1))
        self.assertEqual("locked", board.try_emergency_pin("1234", 2))
        self.assertTrue(board.view().grab)
        self.assertEqual("ok", board.try_emergency_pin("1234", 301))

    def _approved(self) -> BoardSession:
        board = session()
        board.on_registered("approved", "AB7K", "Fen", now=0)
        board.mark_qr_reported(board.qr_to_report(), 25)
        return board


if __name__ == "__main__":
    unittest.main()
