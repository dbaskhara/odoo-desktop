import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from odoo_login import settings
from odoo_login.app import LoginWindow


class LoginWindowLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("ODOO_DESKTOP_SETTINGS")
        os.environ["ODOO_DESKTOP_SETTINGS"] = str(Path(self.tmp.name) / "settings.json")
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        if self._old is None:
            os.environ.pop("ODOO_DESKTOP_SETTINGS", None)
        else:
            os.environ["ODOO_DESKTOP_SETTINGS"] = self._old
        self.tmp.cleanup()

    def _open(self):
        app = LoginWindow(self.root)
        self.root.update_idletasks()
        return app

    def test_sign_in_button_keeps_its_size_after_a_failed_sign_in(self):
        # The card must grow with the error message instead of squeezing the button out.
        app = self._open()
        self.root.deiconify()
        self.root.update()
        height = app.login_button.winfo_height()
        self.assertGreaterEqual(height, 40)
        for message in (
            "Sign-in failed. Check your email and password or API key.",
            "Odoo rejected the request: " + "Access Denied. " * 18,
        ):
            app.results.put((False, message))
            app._poll_result()
            self.root.update()
            self.assertEqual(app.login_button.winfo_height(), height, message[:30])
            self.assertTrue(app.login_button.winfo_ismapped())
            self.assertEqual(app.login_button.cget("text"), "Sign in")
            self.assertEqual(str(app.login_button.cget("state")), "normal")
            bottom = app.login_button.winfo_rooty() - self.root.winfo_rooty() + height
            self.assertLessEqual(bottom, self.root.winfo_height())
        app._toggle_help()
        self.root.update()
        self.assertEqual(app.login_button.winfo_height(), height)

    def test_window_size_and_empty_fields(self):
        app = self._open()
        min_width, min_height = self.root.minsize()
        self.assertGreaterEqual(min_width, 760)
        self.assertGreaterEqual(min_height, 560)
        # Placeholders are drawn over the entries, never stored in the values.
        self.assertEqual(app.url_var.get(), "")
        self.assertEqual(app.email_var.get(), "")

    def test_missing_fields_show_error_without_connecting(self):
        app = self._open()
        app.url_var.set("hub.example.com")
        app.sign_in()
        self.assertIn("email", app.status_var.get())
        self.assertIn("password or API key", app.status_var.get())
        self.assertEqual(str(app.login_button.cget("state")), "normal")

    def test_remembers_server_database_and_email_but_not_api_key(self):
        settings.save({"url": "hub.example.com", "database": "Main", "email": "me@example.com", "api_key": "x"})
        app = self._open()
        self.assertEqual(app.url_var.get(), "hub.example.com")
        self.assertEqual(app.database_var.get(), "Main")
        self.assertEqual(app.email_var.get(), "me@example.com")
        self.assertEqual(app.api_key_var.get(), "")
        self.assertNotIn("api_key", settings.load())

    def test_api_key_show_hide_toggle(self):
        app = self._open()
        entry = app.api_key_field.entry
        self.assertEqual(entry.cget("show"), "•")
        app.api_key_field._toggle_secret()
        self.assertEqual(entry.cget("show"), "")
        app.api_key_field._toggle_secret()
        self.assertEqual(entry.cget("show"), "•")


if __name__ == "__main__":
    unittest.main()
