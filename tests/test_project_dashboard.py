import json
import unittest
from unittest.mock import patch

from odoo_login.odoo_client import OdooClient, OdooSession, to_local_date


def _reply(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


FIELDS_18 = {
    name: {"type": "char"}
    for name in (
        "name",
        "project_id",
        "stage_id",
        "date_deadline",
        "priority",
        "write_date",
        "create_date",
        "state",
        "user_ids",
    )
}


class FetchMyTasksTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)
        self.session = OdooSession(base_url="https://odoo.example.com", database="main", uid=7, api_key="key")

    @patch("odoo_login.odoo_client.urlopen")
    def test_reads_open_tasks_of_current_user_across_projects(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.side_effect = [
            _reply(FIELDS_18),
            _reply(
                [
                    {
                        "id": 55,
                        "name": "Fix login",
                        "project_id": [1, "Website"],
                        "stage_id": [10, "In Progress"],
                        "date_deadline": "2026-12-01",
                        "priority": "1",
                        "write_date": "2026-11-20 08:00:00",
                        "create_date": "2026-11-01 08:00:00",
                        "state": "01_in_progress",
                    },
                    {
                        "id": 56,
                        "name": "Private",
                        "project_id": False,
                        "stage_id": False,
                        "date_deadline": False,
                        "priority": "0",
                        "write_date": False,
                        "create_date": False,
                        "state": "01_in_progress",
                    },
                ]
            ),
            _reply([{"id": 10, "fold": False}]),
        ]

        tasks = self.client.fetch_my_tasks(self.session)

        self.assertEqual((tasks[0].name, tasks[0].project, tasks[0].stage), ("Fix login", "Website", "In Progress"))
        self.assertEqual(tasks[0].deadline, "2026-12-01")
        self.assertTrue(tasks[0].starred)
        self.assertEqual((tasks[1].project, tasks[1].stage, tasks[1].deadline), ("Private tasks", "No stage", ""))

        params = json.loads(urlopen.call_args_list[1].args[0].data)["params"]
        self.assertEqual(params["args"][:5], ["main", 7, "key", "project.task", "search_read"])
        domain = params["args"][5][0]
        self.assertIn(["user_ids", "in", [7]], domain)
        self.assertIn(["state", "not in", ["1_done", "1_canceled"]], domain)

    @patch("odoo_login.odoo_client.urlopen")
    def test_older_odoo_without_state_field_uses_is_closed(self, urlopen):
        fields = {name: {} for name in ("name", "project_id", "stage_id", "date_deadline", "is_closed")}
        urlopen.return_value.__enter__.return_value.read.side_effect = [_reply(fields), _reply([]), _reply([])]

        self.client.fetch_my_tasks(self.session)
        self.client.fetch_my_tasks(self.session)  # field list is cached: 3 calls, not 4

        self.assertEqual(urlopen.call_count, 3)
        params = json.loads(urlopen.call_args_list[1].args[0].data)["params"]
        domain, wanted = params["args"][5]
        self.assertIn(["is_closed", "=", False], domain)
        self.assertNotIn("state", wanted)
        self.assertNotIn("write_date", wanted)

    @patch("odoo_login.odoo_client.urlopen")
    def test_tasks_in_a_finished_stage_are_marked_done(self, urlopen):
        def row(task_id, stage):
            return {"id": task_id, "name": f"T{task_id}", "project_id": [1, "Website"], "stage_id": stage,
                    "date_deadline": "2026-11-01", "priority": "0", "write_date": False, "create_date": False,
                    "state": "01_in_progress"}  # fmt: skip

        urlopen.return_value.__enter__.return_value.read.side_effect = [
            _reply(FIELDS_18),
            _reply([row(1, [10, "In Progress"]), row(2, [11, "Done"]), row(3, [12, "Archive"]), row(4, False)]),
            _reply([{"id": 10, "fold": False}, {"id": 11, "fold": False}, {"id": 12, "fold": True}]),
            _reply([row(5, [12, "Archive"])]),
        ]

        tasks = {task.id: task.done for task in self.client.fetch_my_tasks(self.session)}
        # 2: stage is named Done; 3: stage is folded in Odoo.
        self.assertEqual(tasks, {1: False, 2: True, 3: True, 4: False})

        # Stage answers are remembered: the second load asks Odoo only for the tasks.
        self.assertTrue(self.client.fetch_my_tasks(self.session)[0].done)
        self.assertEqual(urlopen.call_count, 4)

    def test_dates_are_converted_to_local_day(self):
        self.assertEqual(to_local_date("2026-11-25"), "2026-11-25")
        self.assertEqual(to_local_date(False), "")
        self.assertEqual(len(to_local_date("2026-11-25 12:00:00")), 10)


if __name__ == "__main__":
    unittest.main()
