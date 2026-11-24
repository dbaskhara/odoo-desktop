import tests_env  # noqa: F401  (must come first)

import datetime
import json
import tkinter as tk
import unittest
from unittest.mock import patch

from odoo_login import insights, winshell
from odoo_login.actions import RISKY_EXTENSIONS, human_size, safe_filename
from odoo_login.dashboard import DashboardView
from odoo_login.odoo_client import (
    Attachment,
    Mention,
    OdooClient,
    OdooConnectionError,
    OdooSession,
    Reply,
    TaskDetail,
    TaskInfo,
    TeamTask,
)
from odoo_login.store import LocalStore

TODAY = datetime.date(2026, 11, 24)  # a Tuesday


def _reply(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def _args(urlopen, index=-1):
    return json.loads(urlopen.call_args_list[index].args[0].data)["params"]["args"]


class WriteCallsTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)
        self.session = OdooSession(base_url="https://odoo.example.com", database="main", uid=7, api_key="key")

    @patch("odoo_login.odoo_client.urlopen")
    def test_set_stage_writes_only_the_stage(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = _reply(True)
        self.client.set_stage_many(self.session, [55, 56], 12)
        self.assertEqual(_args(urlopen)[3:], ["project.task", "write", [[55, 56], {"stage_id": 12}], {}])
        self.client.set_stage(self.session, 55, 12)
        self.assertEqual(_args(urlopen)[3:], ["project.task", "write", [[55], {"stage_id": 12}], {}])

    @patch("odoo_login.odoo_client.urlopen")
    def test_post_message_is_a_comment_and_refuses_empty_text(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = _reply(901)
        self.client.post_message(self.session, 55, "  On it.  ")
        args = _args(urlopen)
        self.assertEqual(args[3:6], ["project.task", "message_post", [[55]]])
        self.assertEqual(args[6], {"body": "On it.", "message_type": "comment", "subtype_xmlid": "mail.mt_comment"})
        with self.assertRaisesRegex(OdooConnectionError, "Write a message"):
            self.client.post_message(self.session, 55, "   ")
        self.assertEqual(urlopen.call_count, 1)

    @patch("odoo_login.odoo_client.urlopen")
    def test_log_time_creates_a_timesheet_line(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = _reply(33)
        self.client.log_time(self.session, 55, 4, 1.256, "", "2026-11-24")
        args = _args(urlopen)
        self.assertEqual(args[3:5], ["account.analytic.line", "create"])
        self.assertEqual(
            args[5][0], {"task_id": 55, "name": "/", "unit_amount": 1.26, "date": "2026-11-24", "project_id": 4}
        )
        with self.assertRaisesRegex(OdooConnectionError, "more than zero"):
            self.client.log_time(self.session, 55, 4, 0, "x", "2026-11-24")

    @patch("odoo_login.odoo_client.urlopen")
    def test_create_task_assigns_me_and_checks_the_deadline(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = _reply([77])
        self.assertEqual(self.client.create_task(self.session, 4, " New thing ", "2026-12-01"), 77)
        self.assertEqual(
            _args(urlopen)[5][0],
            {"name": "New thing", "project_id": 4, "user_ids": [[6, 0, [7]]], "date_deadline": "2026-12-01"},
        )
        with self.assertRaisesRegex(OdooConnectionError, "YYYY-MM-DD"):
            self.client.create_task(self.session, 4, "x", "next week")
        with self.assertRaisesRegex(OdooConnectionError, "title"):
            self.client.create_task(self.session, 4, "  ")

    @patch("odoo_login.odoo_client.urlopen")
    def test_odoo_error_message_is_shown(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"data": {"message": "You are not allowed to modify this task."}}}
        ).encode()
        with self.assertRaisesRegex(OdooConnectionError, "not allowed to modify"):
            self.client.set_stage(self.session, 55, 12)

    @patch("odoo_login.odoo_client.urlopen")
    def test_mentions_are_answered_only_by_a_later_message_of_mine(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.side_effect = [
            _reply([{"id": 7, "partner_id": [70, "Me"]}]),
            _reply([
                {"id": 3, "model": "project.task", "res_id": 10, "record_name": "Fix login", "author_id": [5, "Jeff"],
                 "date": "2026-11-23 09:00:00", "body": "<p>ping</p>"},
                {"id": 2, "model": "project.task", "res_id": 11, "record_name": "Docs", "author_id": [5, "Jeff"],
                 "date": "2026-11-22 09:00:00", "body": "<p>ping</p>"},
                {"id": 1, "model": "crm.lead", "res_id": 4, "record_name": "Acme", "author_id": [6, "Sam"],
                 "date": "2026-11-21 09:00:00", "body": "<p>hello</p>"},
            ]),
            _reply([
                {"id": 9, "res_id": 10, "date": "2026-11-22 08:00:00"},  # before the mention: not an answer
                {"id": 8, "res_id": 11, "date": "2026-11-22 10:00:00"},
            ]),
        ]  # fmt: skip
        mentions = self.client.fetch_mentions(self.session)
        self.assertEqual([(m.record, m.answered) for m in mentions], [("Fix login", False), ("Docs", True), ("Acme", False)])
        domain = _args(urlopen, 1)[5][0]
        self.assertIn(["partner_ids", "in", [70]], domain)

    @patch("odoo_login.odoo_client.urlopen")
    def test_team_tasks_carry_assignee_names_and_skip_done_stages(self, urlopen):
        fields = {n: {} for n in ("name", "project_id", "stage_id", "date_deadline", "state", "user_ids")}

        def row(task_id, stage, users):
            return {"id": task_id, "name": f"T{task_id}", "project_id": [4, "Website"], "stage_id": stage,
                    "date_deadline": False, "state": "01_in_progress", "user_ids": users}  # fmt: skip

        urlopen.return_value.__enter__.return_value.read.side_effect = [
            _reply(fields),
            _reply([row(1, [10, "In Progress"], [7, 8]), row(2, [11, "Done"], [8]), row(3, [10, "In Progress"], [])]),
            _reply([{"id": 10, "fold": False}, {"id": 11, "fold": False}]),
            _reply([{"id": 7, "display_name": "Demi"}, {"id": 8, "display_name": "Yazid"}]),
        ]
        team = self.client.fetch_team_tasks(self.session, 4)
        self.assertEqual([(t.task.id, t.assignees) for t in team], [(1, ("Demi", "Yazid")), (3, ())])


class StoreAndInsightsTests(unittest.TestCase):
    uid = 900

    def setUp(self):
        StoreAndInsightsTests.uid += 1
        self.session = OdooSession("https://odoo.example.com", "main", self.uid, "key")
        self.store = LocalStore(self.session)

    def test_today_plan_carries_over_unfinished_open_tasks(self):
        monday = TODAY - datetime.timedelta(days=1)
        for task_id in (1, 2, 3):
            self.store.toggle_plan(monday, task_id)
        self.store.toggle_plan_done(monday, 1)
        # 1 was ticked, 3 is no longer open: only 2 comes along.
        self.assertEqual(self.store.today_plan(TODAY, {1, 2}), [2])
        self.assertEqual(LocalStore(self.session).today_plan(TODAY, {1, 2}), [2])
        self.assertTrue(self.store.toggle_plan(TODAY, 9))
        self.assertEqual(self.store.today_plan(TODAY, set()), [2, 9])

    def test_timer_history_and_notify_once(self):
        start = datetime.datetime(2026, 11, 24, 9, 0, 0)
        self.store.start_timer(5, "Fix", 4, now=start)
        self.assertEqual(self.store.timer_seconds(now=start + datetime.timedelta(minutes=75)), 4500)
        self.assertEqual(LocalStore(self.session).timer["task_id"], 5)
        self.store.clear_timer()
        self.assertEqual(self.store.timer_seconds(), 0)

        change = insights.Change("stage", 5, "Fix", "Website", "Ready  →  Done")
        self.store.add_history([change], TODAY - datetime.timedelta(days=90))
        self.store.add_history([change], TODAY)
        self.assertEqual([e["date"] for e in self.store.history], [TODAY.isoformat()])

        self.assertTrue(self.store.should_notify(TODAY, "due:5"))
        self.assertFalse(self.store.should_notify(TODAY, "due:5"))
        self.assertTrue(self.store.should_notify(TODAY + datetime.timedelta(days=1), "due:5"))

    def test_week_summary_and_calendar_helpers(self):
        self.assertEqual(insights.week_range(TODAY), (datetime.date(2026, 11, 23), datetime.date(2026, 11, 29)))
        grid = insights.month_grid(2026, 11)
        self.assertEqual((grid[0][0], grid[5][6]), (datetime.date(2026, 10, 26), datetime.date(2026, 12, 6)))
        self.assertEqual(insights.shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(insights.format_duration(4500), "1:15")

        history = [
            {"date": "2026-11-24", "kind": "stage", "task_id": 1, "name": "A", "project": "P", "detail": "Ready  →  Review"},
            {"date": "2026-11-24", "kind": "new", "task_id": 2, "name": "B", "project": "P", "detail": "Stage: Ready"},
            {"date": "2026-11-10", "kind": "new", "task_id": 3, "name": "Old", "project": "P", "detail": ""},
        ]
        closed = [TaskInfo(4, "Shipped", 1, "P", "Done", done=True)]
        late = [TaskInfo(5, "Late one", 1, "P", "Ready", deadline="2026-11-20")]
        span, sections = insights.week_summary(TODAY, history, closed, late)
        self.assertEqual(span, "23 Nov to 29 Nov 2026")
        counts = {title: len(items) for title, items in sections}
        self.assertEqual(
            counts,
            {"Finished this week": 1, "Moved to another stage": 1, "New for you": 1, "Deadline changed": 0,
             "Slipped (overdue now)": 1},
        )  # fmt: skip


class HelperTests(unittest.TestCase):
    def test_file_helpers(self):
        self.assertEqual(human_size(532), "532 B")
        self.assertEqual(human_size(48_200), "48 KB")
        self.assertEqual(human_size(2_500_000), "2.5 MB")
        self.assertEqual(safe_filename('..\\evil/na:me?.pdf '), "_evil_na_me_.pdf")
        self.assertEqual(safe_filename("  ...  "), "file")
        self.assertIn(".exe", RISKY_EXTENSIONS)
        self.assertIn(".ps1", RISKY_EXTENSIONS)

    def test_credential_target_is_stable(self):
        self.assertEqual(
            winshell.credential_target("https://Odoo.Example.com", "Main", " Me@Example.com "),
            "OdooDesktop:https://odoo.example.com|Main|me@example.com",
        )


class FakeClient:
    def __init__(self):
        self.calls = []
        self.tasks = [
            TaskInfo(10, "Fix login", 1, "Website", "In Progress", deadline="2026-11-20", updated="2026-11-23"),
            TaskInfo(11, "Write docs", 1, "Website", "Ready", deadline="2026-11-24", updated="2026-11-01"),
            TaskInfo(12, "Ship build", 2, "Mobile", "In Review", deadline="2026-11-26", updated="2026-11-24"),
        ]
        self.stage = "In Progress"

    def fetch_my_tasks(self, _session):
        return self.tasks

    def fetch_task_detail(self, _session, task_id):
        return TaskDetail(
            id=task_id, name="Fix login", project="Website", stage=self.stage, project_id=1, stage_id=10,
            stages=((10, "In Progress"), (11, "In Review"), (12, "Done")),
            attachments=(Attachment(5, "screenshot.png", "image/png", 48_200), Attachment(6, "run.bat", "text/plain", 20)),
        )  # fmt: skip

    def set_stage(self, _session, task_id, stage_id):
        self.calls.append(("set_stage", task_id, stage_id))
        self.stage = {10: "In Progress", 11: "In Review", 12: "Done"}[stage_id]

    def set_stage_many(self, _session, task_ids, stage_id):
        self.calls.append(("set_stage_many", list(task_ids), stage_id))

    def fetch_stages(self, _session, project_id):
        if project_id == 1:
            return ((10, "In Progress"), (11, "In Review"), (12, "Done"))
        return ((20, "Ready"), (21, "In Review"), (22, "Done"))

    def post_message(self, _session, task_id, text):
        self.calls.append(("post_message", task_id, text))

    def log_time(self, _session, task_id, project_id, hours, description, date):
        self.calls.append(("log_time", task_id, project_id, hours, description, date))

    def create_task(self, _session, project_id, name, deadline=""):
        self.calls.append(("create_task", project_id, name, deadline))
        return 99

    def download_attachment(self, _session, attachment_id):
        return b"data"


class DashboardV4Tests(unittest.TestCase):
    uid = 700

    def setUp(self):
        DashboardV4Tests.uid += 1
        self.root = tk.Tk()
        self.root.withdraw()
        self.client = FakeClient()
        self.session = OdooSession("https://odoo.example.com", "main", self.uid, "key")
        self.notes = []
        self.overdue = []
        self.view = DashboardView(
            self.root, self.client, self.session, "demo@example.com",
            notifier=lambda title, text: self.notes.append((title, text)),
            on_overdue=lambda overdue, count: self.overdue.append((overdue, count)),
        )  # fmt: skip
        self.view._today = lambda: TODAY
        self.view.refresh = lambda: None  # no background threads in tests
        self.view.load_sync()

    def tearDown(self):
        self.root.destroy()

    def test_move_stage_reply_and_time_are_sent_to_odoo(self):
        self.view.open_task_sync(10)
        self.assertEqual(list(self.view.stage_box.cget("values")), ["In Progress", "In Review", "Done"])
        self.view.do_set_stage("In Review")
        self.assertEqual(self.view.detail.stage, "In Review")

        self.view.reply_text.insert("1.0", "On it.")
        self.view.do_post_message("On it.")
        self.assertEqual(self.view.reply_text.get("1.0", "end").strip(), "")

        self.view.store.start_timer(10, "Fix login", 1)
        self.view.do_log_time(1.5, "debugging")
        self.assertEqual(self.view.store.timer, {})

        self.assertEqual(self.view.do_create_task(2, "New thing", "2026-12-01"), 99)
        self.assertEqual(
            self.client.calls,
            [
                ("set_stage", 10, 11),
                ("post_message", 10, "On it."),
                ("log_time", 10, 1, 1.5, "debugging", "2026-11-24"),
                ("create_task", 2, "New thing", "2026-12-01"),
            ],
        )
        with self.assertRaisesRegex(OdooConnectionError, "does not exist"):
            self.view.do_set_stage("Nowhere")

    def test_several_tasks_selected_in_focus_move_to_one_stage(self):
        tree = self.view.focus_tree
        tree.selection_set(("10", "11", "12"))
        self.view._show_detail()
        self.assertEqual(self.view.selected_task_ids(), [10, 11, 12])
        self.assertIsNone(self.view.selected_task_id())
        self.assertEqual(self.view.detail_title.get(), "3 tasks selected")
        self.assertEqual(self.view.move_button.cget("text"), "Move 3 tasks to stage")

        tasks = [self.view._task(i) for i in (10, 11, 12)]
        names, by_project = self.view.shared_stages(tasks)
        self.assertEqual(names, ["In Review", "Done"])  # only stages both projects have
        # Task 12 is already In Review: it is left alone. Each project gets its own stage id.
        self.assertEqual(self.view.do_move_tasks(tasks, "In Review", by_project), 2)
        self.assertEqual(self.client.calls, [("set_stage_many", [10, 11], 11)])
        self.assertEqual(self.view.do_move_tasks(tasks, "Done", by_project), 3)
        self.assertEqual(self.client.calls[1:], [("set_stage_many", [10, 11], 12), ("set_stage_many", [12], 22)])

        tree.selection_set(("11",))
        self.view._show_detail()
        self.assertEqual(self.view.selected_task_id(), 11)
        self.assertEqual(self.view.move_button.cget("text"), "Move to stage")

    def test_update_notice_shows_only_for_a_newer_release(self):
        from odoo_login import updates

        self.assertEqual(updates.parse("v4.10.1"), (4, 10, 1))
        self.assertEqual(updates.parse("nightly"), ())
        self.assertTrue(updates.is_newer("4.10", "4.9"))
        self.assertTrue(updates.is_newer("5", "4.9.3"))
        self.assertFalse(updates.is_newer("4.4", "4.4.0"))
        self.assertFalse(updates.is_newer("", "4.4"))

        self.assertEqual(self.view.new_version, "")  # no checker given: tests never call GitHub
        self.assertFalse(self.view.update_label.winfo_manager())
        self.view._update_result(updates.VERSION)
        self.assertEqual(self.view.new_version, "")
        self.view._update_result("99.0")
        self.assertEqual(self.view.new_version, "99.0")
        self.assertIn("Version 99.0 is available", self.view.update_label.cget("text"))
        self.assertTrue(self.view.update_label.winfo_manager())
        self.assertEqual(self.notes[-1][0], "Odoo Desktop update")
        self.assertEqual(self.view.update_button.cget("text"), "Download 99.0")
        self.view.rebuild()  # the notice survives a theme switch
        self.assertTrue(self.view.update_label.winfo_manager())

    @patch("odoo_login.updates.urlopen")
    def test_latest_release_is_read_from_github(self, urlopen):
        from odoo_login import updates

        response = urlopen.return_value.__enter__.return_value
        response.geturl.return_value = "https://github.com/dbaskhara/odoo-desktop/releases/tag/v4.7"
        self.assertEqual(updates.fetch_latest(), "4.7")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://github.com/dbaskhara/odoo-desktop/releases/latest")
        self.assertEqual(request.get_method(), "HEAD")
        self.assertIsNone(request.data)  # nothing is sent
        response.geturl.return_value = "https://example.com/releases/tag/v9.9"  # not our repository: ignored
        self.assertEqual(updates.fetch_latest(), "")
        urlopen.side_effect = OSError("offline")
        self.assertEqual(updates.fetch_latest(), "")

    def test_attachments_are_listed_and_saved_with_a_safe_name(self):
        self.view.open_task_sync(10)
        body = self.view.task_text.get("1.0", "end")
        self.assertIn("ATTACHMENTS   2", body)
        self.assertIn("screenshot.png   ·   48 KB", body)
        with patch.object(self.view, "attachment_dir", return_value=tests_env.Path(tests_env._tmp.name)):
            path = self.view.save_attachment(6, "..\\run.bat")
        self.assertEqual(path.name, "6-_run.bat")
        self.assertEqual(path.read_bytes(), b"data")
        self.assertIn(path.suffix, RISKY_EXTENSIONS)  # shown in Explorer, never started

    def test_today_plan_suggestions_and_ticks(self):
        self.assertEqual([t.id for t in self.view.today_suggestions()], [10, 11])  # overdue, then due today
        self.view.toggle_today(10)
        self.assertEqual(self.view.today_ids(), [10])
        self.assertEqual([t.id for t in self.view.today_suggestions()], [11])
        self.view.tick_today(10)
        self.assertIn("1 of 1 done", self.view.today_sub.get())

    def test_calendar_counts_deadlines_and_filters_focus_by_day(self):
        self.view.shift_calendar(0)
        self.assertIn("November 2026", self.view.cal_title.get())
        self.assertIn("3 deadline(s)", self.view.cal_title.get())
        self.view.show_day("2026-11-26")
        self.assertEqual([t.id for t in self.view.visible_tasks], [12])
        self.assertEqual(self.view.current_tab, "focus")
        self.view.nav_clicked("focus")
        self.assertEqual(len(self.view.visible_tasks), 3)

    def test_notifications_for_due_today_new_tasks_and_replies(self):
        self.assertEqual(self.overdue[-1], (1, 3))
        self.assertEqual([title for title, _ in self.notes], ["1 task(s) due today"])
        self.view.load_sync()  # same tasks again: nothing new, due-today not repeated
        self.assertEqual(len(self.notes), 1)

        self.client.tasks = self.client.tasks + [TaskInfo(13, "Brand new", 2, "Mobile", "Ready")]
        self.view.load_sync()
        self.assertEqual(self.notes[-1], ("New task for you", "Brand new\nMobile"))

        self.view._notify_replies([Reply(12, "Jeff", "2026-11-24 10:00", "<p>Looks good</p>")])
        self.assertEqual(self.notes[-1], ("Jeff replied", "Ship build\nLooks good"))

    def test_mentions_team_and_summary_pages(self):
        self.view._handle_side_result(
            "mentions",
            (
                Mention(3, "project.task", 10, "Fix login", "Jeff", "2026-11-23 16:00", "<p>ping</p>", False),
                Mention(2, "project.task", 11, "Docs", "Jeff", "2026-11-22 16:00", "<p>ping</p>", True),
            ),
        )
        # Checked is decided by hand: a reply of mine does not tick the message.
        self.assertEqual(self.view.tab_text["mentions"].get(), "Mentions (2)")
        self.assertEqual(self.view.mentions_tree.get_children(), ("3", "2"))
        self.assertEqual(self.view.mentions_tree.set("2", "status"), "To check (replied)")
        self.view.mentions_tree.selection_set("3")
        self.view.toggle_checked_mentions()
        self.assertEqual(self.view.tab_text["mentions"].get(), "Mentions (1)")
        self.assertEqual(self.view.mentions_tree.get_children(), ("2",))
        self.assertTrue(self.view.store.mention_checked(3))
        self.view.unchecked_only.set(False)
        self.view._render_mentions()
        self.assertEqual(len(self.view.mentions_tree.get_children()), 2)
        self.view.mentions_tree.selection_set("3")
        self.view.toggle_checked_mentions()
        self.assertEqual(self.view.tab_text["mentions"].get(), "Mentions (2)")

        late = TaskInfo(1, "Late", 4, "Website", "Ready", deadline="2026-11-01")
        fine = TaskInfo(2, "Fine", 4, "Website", "Ready", deadline="2026-12-20")
        self.view.team_project = "Website"
        self.view._handle_side_result(
            "team", ("Website", (TeamTask(fine, ("Demi",)), TeamTask(late, ("Yazid", "Demi")), TeamTask(fine, ())))
        )
        self.assertEqual([name for name, _tasks in self.view.team_groups()], ["Demi", "Yazid", "Unassigned"])
        self.assertIn("3 open task(s), 1 overdue", self.view.team_sub.get())

        self.view._handle_side_result("closed", (TaskInfo(4, "Shipped", 1, "Website", "Done", done=True),))
        text = self.view.summary_text()
        self.assertIn("Weekly summary, 23 Nov to 29 Nov 2026", text)
        self.assertIn("Finished this week (1)", text)
        self.assertIn("  - Shipped", text)
        self.assertIn("Slipped (overdue now) (1)", text)


if __name__ == "__main__":
    unittest.main()
