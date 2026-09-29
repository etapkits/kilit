import tempfile
import unittest

from etakit_kilit.config import Config
from etakit_kilit.limit import format_session_left, remember_login, remaining_seconds, restart_login
from etakit_kilit.session import BoardSession
from etakit_kilit.ui import corner_origin, corner_width


class LimitTest(unittest.TestCase):
    def test_the_clock_starts_once_per_login(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(1_000, remember_login(directory, "42", 1_000))
            self.assertEqual(1_000, remember_login(directory, "42", 9_000))
            self.assertEqual(9_000, remember_login(directory, "43", 9_000))

    def test_remaining_time_switches_to_seconds_under_one_minute(self):
        self.assertIsNone(remaining_seconds(0, 1_000, 1_000))
        self.assertEqual("2 dk", format_session_left(120))
        self.assertEqual("1 dk", format_session_left(61))
        self.assertEqual("59 sn", format_session_left(59))
        clock = {"now": 1_000.0}
        board = BoardSession(
            Config(server_url="https://panel.okul.local", enrollment_key="anahtar", session_seconds=120),
            now=0,
            login_at=1_000,
            unix_clock=lambda: clock["now"],
        )
        self.assertEqual(120, board.view().session_left)
        self.assertFalse(board.session_expired())
        clock["now"] = 1_120
        self.assertTrue(board.session_expired())
        self.assertEqual("0 sn", format_session_left(board.view().session_left))

    def test_every_unlock_restarts_the_clock(self):
        clock = {"now": 1_000.0}
        saved = []
        board = BoardSession(
            Config(server_url="https://panel.okul.local", enrollment_key="anahtar", session_seconds=120),
            now=0,
            login_at=1_000,
            unix_clock=lambda: clock["now"],
            on_login_restart=saved.append,
        )
        board.on_registered("approved", "ABC123", "Tahta", now=0)
        board.on_command("c1", "unlock", now=0)
        clock["now"] = 1_100
        board.user_lock(now=100)
        self.assertEqual(20, board.view().session_left)
        board.on_command("c2", "unlock", now=110)
        self.assertEqual(120, board.view().session_left)
        self.assertEqual([1_000.0, 1_100], saved)

    def test_restarted_login_survives_a_relaunch(self):
        with tempfile.TemporaryDirectory() as directory:
            remember_login(directory, "42", 1_000)
            restart_login(directory, "42", 5_000)
            self.assertEqual(5_000, remember_login(directory, "42", 9_000))

    def test_timer_sits_beside_the_lock_button(self):
        width = corner_width(True)
        x, y = corner_origin(0, 0, 1920, 1032, width, 36)
        self.assertGreater(width, 90)
        self.assertEqual(1920 - width - 8, x)
        self.assertEqual(988, y)
        self.assertLess(y + 36, 1032)


if __name__ == "__main__":
    unittest.main()
