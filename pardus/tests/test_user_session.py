import os
import tempfile
import unittest

from etakit_kilit.app import consume_settings_request, is_user_session, write_settings_request


class UserSessionTest(unittest.TestCase):
    def test_greeter_does_not_start_the_lock(self):
        self.assertFalse(is_user_session({"XDG_SESSION_CLASS": "greeter", "USER": "ogrenci"}, uid=1000))
        self.assertFalse(is_user_session({"USER": "lightdm"}, uid=110))
        self.assertFalse(is_user_session({}, uid=110))

    def test_menu_settings_request_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_settings_request(directory)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(consume_settings_request(directory))
            self.assertFalse(os.path.isfile(path))
            self.assertFalse(consume_settings_request(directory))

    def test_logged_in_user_starts_the_lock(self):
        self.assertTrue(is_user_session({"XDG_SESSION_CLASS": "user", "USER": "ogrenci"}, uid=1000))
        self.assertTrue(is_user_session({"USER": "ogretmen"}, uid=1001))


if __name__ == "__main__":
    unittest.main()
