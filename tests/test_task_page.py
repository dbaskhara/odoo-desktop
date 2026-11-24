import tests_env  # noqa: F401  (must come first)

import datetime
import json
import tkinter as tk
import unittest
from unittest.mock import patch

from odoo_login import richtext
from odoo_login.dashboard import DashboardView
from odoo_login.odoo_client import (
    OdooClient,
    OdooConnectionError,
    OdooSession,
    SubTask,
    TaskDetail,
    TaskInfo,
    TaskMessage,
)

TODAY = datetime.date(2026, 11, 24)


def _reply(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


class RichTextTests(unittest.TestCase):
    def test_html_becomes_typed_blocks(self):
        html = (
            "<h2>Goal</h2><p>Fix the <b>PDF</b> export.<br>Second line</p>"
            "<ul><li>one</li><li><p>two</p></li></ul>"
            '<ul class="o_checklist"><li class="o_checked">done it</li><li>todo</li></ul>'
            "<ol><li>a</li><li>b</li></ol><pre>x = 1\n  y</pre><style>p{color:red}</style>"
        )
        self.assertEqual(
            richtext.html_to_blocks(html),
            [
                ("h", "Goal"),
                ("p", "Fix the PDF export."),
                ("p", "Second line"),
                ("li", "one"),
                ("li", "two"),
                ("done", "done it"),
                ("check", "todo"),
                ("ol", "1. a"),
                ("ol", "2. b"),
                ("pre", "x = 1\n  y"),
            ],
        )

    def test_empty_and_plain_values(self):
        self.assertEqual(richtext.html_to_blocks(False), [])
        self.assertEqual(richtext.html_to_blocks("<p><br></p>"), [])
        self.assertEqual(richtext.html_to_text("<p>Hello &amp; welcome</p>"), "Hello & welcome")


class FetchTaskDetailTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)
        self.session = OdooSession(base_url="https://odoo.example.com", database="main", uid=7, api_key="key")
        self.fields = {
            name: {}
            for name in (
                "name", "project_id", "stage_id", "date_deadline", "priority", "write_date", "create_date",
                "state", "description", "user_ids", "tag_ids", "partner_id", "parent_id", "child_ids",
                "allocated_hours",
            )
        }  # fmt: skip

    @patch("odoo_login.odoo_client.urlopen")
    def test_reads_task_subtasks_names_and_messages(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.side_effect = [
            _reply(self.fields),
            _reply([{
                "id": 55, "name": "Fix login", "project_id": [1, "Website"], "stage_id": [10, "In Progress"],
                "date_deadline": "2026-12-01", "priority": "1", "write_date": "2026-11-20 08:00:00",
                "create_date": "2026-11-01 08:00:00", "state": "02_changes_requested",
                "description": "<p>Details</p>", "user_ids": [7, 8], "tag_ids": [3], "partner_id": [9, "Acme"],
                "parent_id": False, "child_ids": [60, 61], "allocated_hours": 4.0,
            }]),
            _reply([{"id": 10, "fold": False}]),
            _reply([
                {"id": 60, "name": "Part A", "stage_id": [11, "Done"], "state": "1_done"},
                {"id": 61, "name": "Part B", "stage_id": [10, "In Progress"], "state": "01_in_progress"},
            ]),
            _reply([
                {"id": 1, "author_id": [5, "Jeff"], "date": "2026-11-21 09:30:00", "body": "<p>Please check</p>"},
                {"id": 2, "author_id": False, "date": "2026-11-20 09:30:00", "body": ""},
            ]),
            _reply([{"id": 90, "name": "spec.pdf", "mimetype": "application/pdf", "file_size": 2048}]),
            _reply([{"id": 10, "name": "In Progress"}, {"id": 11, "name": "Done"}]),
            _reply([{"id": 7, "display_name": "Demi"}, {"id": 8, "display_name": "Yazid"}]),
            _reply([{"id": 3, "display_name": "Bug"}]),
        ]  # fmt: skip

        detail = self.client.fetch_task_detail(self.session, 55)

        self.assertEqual((detail.name, detail.project, detail.stage), ("Fix login", "Website", "In Progress"))
        self.assertEqual(detail.state, "Changes requested")
        self.assertEqual(detail.assignees, ("Demi", "Yazid"))
        self.assertEqual(detail.tags, ("Bug",))
        self.assertEqual(detail.customer, "Acme")
        self.assertEqual(detail.allocated_hours, 4.0)
        self.assertEqual([(s.name, s.closed) for s in detail.subtasks], [("Part A", True), ("Part B", False)])
        self.assertEqual(len(detail.messages), 1)
        self.assertEqual(detail.messages[0].author, "Jeff")

        read_call = json.loads(urlopen.call_args_list[1].args[0].data)["params"]["args"]
        self.assertEqual(read_call[3:5], ["project.task", "read"])
        self.assertNotIn("milestone_id", read_call[5][1])  # not on this server
        self.assertFalse(detail.done)
        self.assertEqual((detail.project_id, detail.stage_id), (1, 10))
        self.assertEqual(detail.stages, ((10, "In Progress"), (11, "Done")))
        self.assertEqual([(f.name, f.size) for f in detail.attachments], [("spec.pdf", 2048)])
        message_call = json.loads(urlopen.call_args_list[4].args[0].data)["params"]["args"]
        self.assertEqual(message_call[3], "mail.message")
        self.assertIn(["res_id", "=", 55], message_call[5][0])

    @patch("odoo_login.odoo_client.urlopen")
    def test_missing_task_raises_clear_error(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.side_effect = [_reply(self.fields), _reply([])]
        with self.assertRaisesRegex(OdooConnectionError, "no longer"):
            self.client.fetch_task_detail(self.session, 99)


class FakeClient:
    done = False

    def fetch_my_tasks(self, _session):
        return [TaskInfo(10, "Fix login", 1, "Website", "In Progress", deadline="2026-11-20", updated="2026-11-23")]

    def fetch_task_detail(self, _session, task_id):
        return TaskDetail(
            id=task_id,
            name="Fix login",
            project="Website",
            stage="In Progress",
            state="Changes requested",
            deadline="2026-11-20",
            done=self.done,
            description="<p>Users cannot sign in.</p><ul><li>check proxy</li></ul>",
            assignees=("Demi",),
            tags=("Bug",),
            subtasks=(SubTask(60, "Part A", "Done", True), SubTask(61, "Part B", "In Progress", False)),
            messages=(TaskMessage("Jeff", "2026-11-21 16:30", "<p>Please check</p>"),),
        )


class TaskPageTests(unittest.TestCase):
    uid = 500

    def setUp(self):
        TaskPageTests.uid += 1
        self.root = tk.Tk()
        self.root.withdraw()
        session = OdooSession(base_url="https://odoo.example.com", database="main", uid=self.uid, api_key="key")
        self.view = DashboardView(self.root, FakeClient(), session, "demo@example.com")
        self.view._today = lambda: TODAY
        self.view.load_sync()

    def tearDown(self):
        self.root.destroy()

    def test_task_page_shows_description_subtasks_messages_and_properties(self):
        self.view.show_tab("focus")
        self.view.open_task_sync(10)

        self.assertEqual(self.view.current_tab, "task")
        self.assertEqual(self.view.task_title.get(), "Fix login")
        body = self.view.task_text.get("1.0", "end")
        for expected in ("DESCRIPTION", "Users cannot sign in.", "check proxy", "SUB-TASKS   1/2 DONE", "Part B",
                         "MESSAGES   1", "Jeff", "Please check"):  # fmt: skip
            self.assertIn(expected, body)
        props = dict(self.view.task_properties(self.view.detail))
        self.assertEqual(props["Stage"], "In Progress  ·  Changes requested")
        self.assertEqual(props["Deadline"], "2026-11-20  (4 days late)")
        self.assertEqual(props["Assigned to"], "Demi")
        self.assertNotIn("Customer", props)

    def test_done_task_shows_plain_deadline_not_late(self):
        self.view.client.done = True
        self.view.open_task_sync(10)
        self.assertEqual(dict(self.view.task_properties(self.view.detail))["Deadline"], "2026-11-20")

    def test_note_written_on_task_page_is_saved_and_back_returns_to_previous_page(self):
        self.view.show_tab("focus")
        self.view.open_task_sync(10)
        self.view.task_note.insert("1.0", "ask Jeff")
        self.view._toggle_detail_pin()
        self.view.close_task()

        self.assertEqual(self.view.current_tab, "focus")
        self.assertEqual(self.view.store.note(10), "ask Jeff")
        self.assertIn(10, self.view.store.pins)
        self.assertNotEqual(self.view.focus_tree.item("10", "text"), "Fix login")  # shows pin and note marks


if __name__ == "__main__":
    unittest.main()
