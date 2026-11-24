import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from odoo_login.odoo_client import OdooClient, OdooConnectionError


class OdooClientTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)

    @patch("odoo_login.odoo_client.urlopen")
    def test_authenticates_with_email_api_key_and_database(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": 42}
        ).encode()

        result = self.client.login(
            "https://odoo.example.com/", "person@example.com", "secret-key", "sales"
        )

        self.assertEqual(result.uid, 42)
        self.assertEqual(result.database, "sales")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://odoo.example.com/jsonrpc")
        self.assertEqual(request.get_header("User-agent"), "OdooDesktop/1.0")
        self.assertEqual(payload["params"]["args"][:3], ["sales", "person@example.com", "secret-key"])

    @patch("odoo_login.odoo_client.urlopen")
    def test_discovers_single_database_when_database_is_blank(self, urlopen):
        database_reply = {"jsonrpc": "2.0", "id": 1, "result": ["main"]}
        auth_reply = {"jsonrpc": "2.0", "id": 1, "result": 7}
        urlopen.return_value.__enter__.return_value.read.side_effect = [
            json.dumps(database_reply).encode(),
            json.dumps(auth_reply).encode(),
        ]

        result = self.client.login("https://odoo.example.com", "a@b.com", "key", "")

        self.assertEqual(result.database, "main")
        self.assertEqual(result.uid, 7)
        self.assertEqual(urlopen.call_count, 2)

    @patch("odoo_login.odoo_client.urlopen")
    def test_blank_database_with_unreadable_database_list_requests_manual_entry(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b"<html>database list disabled</html>"

        with self.assertRaisesRegex(OdooConnectionError, "(?i)enter the database name manually"):
            self.client.login("https://odoo.example.com", "a@b.com", "key", "")

    @patch("odoo_login.odoo_client.urlopen")
    def test_blank_database_with_multiple_databases_requests_selection(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": ["one", "two"]}
        ).encode()

        with self.assertRaisesRegex(OdooConnectionError, "choose a database"):
            self.client.login("https://odoo.example.com", "a@b.com", "key", "")

    @patch("odoo_login.odoo_client.urlopen")
    def test_rejects_invalid_credentials(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": False}
        ).encode()

        with self.assertRaisesRegex(OdooConnectionError, "(?i)check your email and password.*API key"):
            self.client.login("https://odoo.example.com", "a@b.com", "bad-key", "main")

    @patch("odoo_login.odoo_client.urlopen")
    def test_reports_forbidden_api_request_instead_of_network_failure(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://odoo.example.com/jsonrpc", 403, "Forbidden", {}, BytesIO(b"blocked")
        )

        with self.assertRaisesRegex(OdooConnectionError, "HTTP 403.*security"):
            self.client._json_rpc("https://odoo.example.com/jsonrpc", {})

    def test_normalizes_server_url(self):
        self.assertEqual(OdooClient.normalize_url("odoo.example.com/"), "https://odoo.example.com")


if __name__ == "__main__":
    unittest.main()
