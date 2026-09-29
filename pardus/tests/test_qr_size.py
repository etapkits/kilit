import unittest

from etakit_kilit.ui import corner_origin, qr_pixel_size


class QrSizeTest(unittest.TestCase):
    def test_qr_stays_above_the_screen_keyboard(self):
        self.assertEqual(280, qr_pixel_size(1080))
        self.assertLessEqual(250 + qr_pixel_size(768), int(768 * 0.60))
        self.assertGreaterEqual(qr_pixel_size(768), 200)

    def test_lock_button_sits_above_the_taskbar(self):
        x, y = corner_origin(0, 0, 1920, 1032)
        self.assertEqual(1822, x)
        self.assertEqual(988, y)
        self.assertLess(y + 36, 1032)


if __name__ == "__main__":
    unittest.main()
