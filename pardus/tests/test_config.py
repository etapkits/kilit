import tempfile
import unittest
from pathlib import Path

from etakit_kilit.config import Config, ConfigError


class ConfigTest(unittest.TestCase):
    def test_reads_school_package_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text(
                "\n".join(
                    [
                        "# yorum",
                        "server_url = https://panel.okul.local/",
                        "enrollment_key = okul-anahtari",
                        "idle_seconds = 120",
                        "tls_verify = no",
                    ]
                ),
                encoding="utf-8",
            )
            config = Config.load(str(path))

        self.assertEqual("https://panel.okul.local", config.server_url)
        self.assertTrue(config.is_ready())
        self.assertFalse(config.needs_setup())
        self.assertEqual("okul-anahtari", config.enrollment_key)
        self.assertEqual(120, config.idle_seconds)
        self.assertFalse(config.tls_verify)
        self.assertEqual(10, config.lock_countdown_seconds)
        self.assertEqual("", config.emergency_pin_hash)

    def test_loads_a_plaintext_emergency_pin_as_a_hash(self):
        from etakit_kilit.config import verify_emergency_pin

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text(
                "server_url=https://panel.okul.local\nenrollment_key=anahtar\nemergency_pin=4321\n",
                encoding="utf-8",
            )
            config = Config.load(str(path))

        self.assertTrue(config.emergency_pin_hash.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_emergency_pin("4321", config.emergency_pin_hash))
        self.assertFalse(verify_emergency_pin("0000", config.emergency_pin_hash))

    def test_blank_server_waits_for_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text("server_url=\nenrollment_key=anahtar\n", encoding="utf-8")
            config = Config.load(str(path), required=False)

        self.assertFalse(config.is_ready())
        self.assertTrue(config.needs_setup())
        self.assertEqual("anahtar", config.enrollment_key)
        self.assertFalse(Config(server_url="https://etakit.okul.local", enrollment_key="anahtar").is_ready())

    def test_saves_the_server_once_and_hashes_the_pin(self):
        from etakit_kilit.config import install_config_text, verify_emergency_pin

        draft = Config(
            server_url="http://192.168.1.20/etakit/web/public",
            enrollment_key="anahtar",
            idle_seconds=120,
            tls_verify=False,
            setup_complete=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text("server_url=\nenrollment_key=\n", encoding="utf-8")
            install_config_text(draft.render("2468"), str(path), only_if_unconfigured=True)
            saved = Config.load(str(path))
            body = path.read_text(encoding="utf-8")
            with self.assertRaises(ConfigError):
                install_config_text(draft.render(), str(path), only_if_unconfigured=True)

            self.assertEqual("http://192.168.1.20/etakit/web/public", saved.server_url)
            self.assertEqual(120, saved.idle_seconds)
            self.assertFalse(saved.tls_verify)
            self.assertFalse(any(line.startswith("emergency_pin=") for line in body.splitlines()))
            self.assertTrue(verify_emergency_pin("2468", saved.emergency_pin_hash))
            self.assertFalse(saved.needs_setup())

            from etakit_kilit.config import replace_config

            changed = Config(
                server_url="https://yeni.okul.local",
                enrollment_key="yeni-anahtar",
                idle_seconds=900,
                tls_verify=True,
                board_name="10-A",
            )
            replace_config(str(path), changed.render())
            replaced = Config.load(str(path))
            self.assertEqual("https://yeni.okul.local", replaced.server_url)
            self.assertEqual("yeni-anahtar", replaced.enrollment_key)
            self.assertEqual(900, replaced.idle_seconds)
            self.assertEqual("10-A", replaced.board_name)

    def test_board_name_updates_without_changing_the_server(self):
        from etakit_kilit.config import update_board_name

        draft = Config(
            server_url="https://panel.okul.local",
            enrollment_key="anahtar",
            board_name="Fen",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text(draft.render(), encoding="utf-8")
            self.assertTrue(update_board_name(str(path), "10-A"))
            saved = Config.load(str(path))
            self.assertEqual("10-A", saved.board_name)
            self.assertEqual("https://panel.okul.local", saved.server_url)
            self.assertEqual("anahtar", saved.enrollment_key)
            self.assertFalse(update_board_name(str(path), "10-A"))

    def test_a_new_server_replaces_the_saved_address(self):
        from etakit_kilit.config import install_config_text, update_connection

        draft = Config(
            server_url="https://eski.okul.local",
            enrollment_key="eski-anahtar",
            board_name="10-A",
            idle_seconds=600,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text(draft.render(), encoding="utf-8")
            with self.assertRaises(ConfigError):
                install_config_text(
                    Config(server_url="https://yeni.okul.local", enrollment_key="yeni-anahtar").render(),
                    str(path),
                    only_if_unconfigured=True,
                )
            self.assertTrue(
                update_connection(str(path), "https://yeni.okul.local", "yeni-anahtar", False)
            )
            saved = Config.load(str(path))
            self.assertEqual("https://yeni.okul.local", saved.server_url)
            self.assertEqual("yeni-anahtar", saved.enrollment_key)
            self.assertFalse(saved.tls_verify)
            self.assertEqual("10-A", saved.board_name)
            self.assertEqual(600, saved.idle_seconds)

    def test_rejects_a_missing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kilit.conf"
            path.write_text("server_url=https://etakit.okul.local\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                Config.load(str(path))


if __name__ == "__main__":
    unittest.main()
