import tests_env  # noqa: F401  (must come first)

import tkinter as tk
import unittest
from unittest.mock import patch

from odoo_login.app import LoginWindow
from odoo_login.odoo_client import LoginResult, TaskInfo


class LoginToDashboardTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LoginWindow(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_successful_login_replaces_form_with_dashboard(self):
        self.app._pending = ("demo@example.com", "secret-key")
        result = LoginResult(uid=7, database="main", base_url="https://odoo.example.com")

        with patch.object(
            self.app.client,
            "fetch_my_tasks",
            return_value=[TaskInfo(1, "Fix login", 1, "Website", "Ready")],
        ):
            dashboard = self.app.show_dashboard(result)
            dashboard.load_sync()
            self.root.update_idletasks()

        self.assertEqual(dashboard.session.uid, 7)
        self.assertEqual(dashboard.session.api_key, "secret-key")
        self.assertEqual(dashboard.session.base_url, "https://odoo.example.com")
        self.assertEqual(self.app.api_key_var.get(), "")
        self.assertFalse(self.app.container.winfo_exists())
        self.assertEqual(dashboard.focus_tree.get_children("g:none"), ("1",))


if __name__ == "__main__":
    unittest.main()
