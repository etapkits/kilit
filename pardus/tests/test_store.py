import os
import tempfile
import unittest
from pathlib import Path

from etakit_kilit.store import StateStore


class StoreTest(unittest.TestCase):
    def test_machine_id_stays_after_the_token_is_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            first = store.machine_id()
            store.save_device("jeton", "AB7K", "board-1", "Fen")
            store.clear_token()

            self.assertEqual(first, store.machine_id())
            saved = store.load_device()
            self.assertEqual("", saved.token)
            self.assertEqual("AB7K", saved.device_code)
            self.assertEqual("Fen", saved.name)
            if os.name != "nt":
                mode = Path(directory, "device.json").stat().st_mode & 0o777
                self.assertEqual(0o666, mode)


if __name__ == "__main__":
    unittest.main()
