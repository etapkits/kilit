import os
import tempfile
import unittest
from types import SimpleNamespace

from etakit_kilit.power import poweroff


class PowerTest(unittest.TestCase):
    def test_polkit_refusal_falls_back_to_the_sudo_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = os.path.join(directory, "etakit-kilit-poweroff")
            open(helper, "w").close()
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                code = 0 if command[0] == "sudo" else 1
                return SimpleNamespace(returncode=code, stderr="Access denied")

            with self.assertLogs("etakit", level="WARNING") as logs:
                self.assertTrue(poweroff(run=run, helper=helper))
            self.assertEqual(
                [["systemctl", "poweroff"], ["loginctl", "poweroff"], ["sudo", "-n", helper]],
                calls,
            )
            self.assertIn("Access denied", logs.output[0])

    def test_without_the_helper_only_the_session_commands_are_tried(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            raise OSError("yok")

        self.assertFalse(poweroff(run=run, helper="/yok/etakit-kilit-poweroff"))
        self.assertEqual([["systemctl", "poweroff"], ["loginctl", "poweroff"]], calls)


if __name__ == "__main__":
    unittest.main()
