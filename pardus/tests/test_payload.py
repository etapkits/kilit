import re
import unittest

from etakit_kilit.payload import make_qr_payload


class PayloadTest(unittest.TestCase):
    def test_matches_the_server_code_pattern(self):
        payload = make_qr_payload("AB7K")
        self.assertRegex(payload, r"^etakit:1:AB7K:[0-9a-f]{32}$")
        self.assertRegex(payload, r"^[A-Za-z0-9:._~-]{16,512}$")
        self.assertIsNone(re.fullmatch(r"^[A-Za-z0-9:._~-]{16,512}$", "kısa"))

    def test_each_code_is_single_use(self):
        self.assertNotEqual(make_qr_payload("AB7K"), make_qr_payload("AB7K"))


if __name__ == "__main__":
    unittest.main()
