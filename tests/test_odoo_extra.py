import tests_env  # noqa: F401  (must come first)

import datetime
import json
import tkinter as tk
import unittest
from unittest.mock import patch

from odoo_login.dashboard import DashboardView
from odoo_login.odoo_client import OdooConnectionError, OdooSession, TaskInfo, TaskMessage
from odoo_login.odoo_extra import Conversation, Meeting, OdooClient, Row, TimeLine, due_text
from odoo_login.odoo_pages import hours_text

TODAY = datetime.date(2026, 11, 24)  # a Tuesday


def _reply(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def _error(message):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"data": {"message": message}}}).encode()


def _fields(*names):
    return _reply({name: {"type": "char"} for name in names})


def _call(urlopen, index):
    """(model, method, args, kwargs) of the RPC number `index`."""
    return json.loads(urlopen.call_args_list[index].args[0].data)["params"]["args"][3:]


class ExtraCallsTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)
        self.session = OdooSession(base_url="https://odoo.example.com", database="main", uid=7, api_key="key")

    def answers(self, urlopen, *replies):
        urlopen.return_value.__enter__.return_value.read.side_effect = list(replies)

    def test_due_text(self):
        self.assertEqual(due_text("2026-11-21", TODAY), ("3 days late", "overdue"))
        self.assertEqual(due_text("2026-11-24", TODAY), ("Today", "today"))
        self.assertEqual(due_text("2026-11-27", TODAY), ("in 3 days", ""))
        self.assertEqual(due_text("2027-01-05", TODAY), ("05 Jan 2027", ""))
        self.assertEqual(due_text(False, TODAY), ("", ""))
        self.assertEqual((hours_text(1.5), hours_text(0), hours_text(0.25)), ("1:30", "", "0:15"))

    @patch("odoo_login.odoo_client.urlopen")
    def test_activities_are_mine_and_only_known_fields_are_read(self, urlopen):
        self.answers(
            urlopen,
            _fields("res_model", "res_id", "res_name", "activity_type_id", "summary", "date_deadline"),  # no "note"
            _reply([
                {"id": 1, "res_model": "project.task", "res_id": 10, "res_name": "Fix login",
                 "activity_type_id": [4, "Call"], "summary": "Call the customer", "date_deadline": "2026-11-20"},
                {"id": 2, "res_model": "crm.lead", "res_id": 3, "res_name": "Acme deal",
                 "activity_type_id": False, "summary": False, "date_deadline": "2026-11-24"},
            ]),
        )  # fmt: skip
        rows = self.client.fetch_activities(self.session, TODAY)
        self.assertEqual(rows[0], Row("1", ("4 days late", "Call", "Call the customer", "Fix login"), "project.task", 10, "overdue"))
        self.assertEqual(rows[1].cells, ("Today", "To do", "", "Acme deal"))
        model, method, args, kwargs = _call(urlopen, 1)
        self.assertEqual((model, method, args[0]), ("mail.activity", "search_read", [["user_id", "=", 7]]))
        self.assertNotIn("note", args[1])

        self.answers(urlopen, _reply(55))
        self.client.mark_activity_done(self.session, 1, " done by phone ")
        self.assertEqual(
            _call(urlopen, -1), ["mail.activity", "action_feedback", [[1]], {"feedback": "done by phone"}]
        )

    @patch("odoo_login.odoo_client.urlopen")
    def test_timesheets_of_the_week(self, urlopen):
        self.answers(
            urlopen,
            _fields("date", "unit_amount", "project_id", "task_id", "name"),
            _reply([{"id": 1, "date": "2026-11-23", "unit_amount": 1.5, "project_id": [1, "Website"],
                     "task_id": [10, "Fix login"], "name": "debug"}]),
        )  # fmt: skip
        lines = self.client.fetch_timesheets(self.session, "2026-11-23", "2026-11-29")
        self.assertEqual(lines, (TimeLine("2026-11-23", 1.5, "Website", "Fix login", "debug"),))
        domain = _call(urlopen, 1)[2][0]
        self.assertIn(["user_id", "=", 7], domain)
        self.assertIn(["date", "<=", "2026-11-29"], domain)

    @patch("odoo_login.odoo_client.urlopen")
    def test_conversations_unread_first_and_my_name_removed_from_chats(self, urlopen):
        self.answers(
            urlopen,
            _reply([{"id": 7, "partner_id": [70, "Demi"]}]),
            _fields("channel_id", "message_unread_counter"),
            _reply([{"id": 1, "channel_id": [5, "general"], "message_unread_counter": 0},
                    {"id": 2, "channel_id": [6, "Demi, Jeff"], "message_unread_counter": 3}]),
            _fields("name", "display_name", "channel_type"),
            _reply([{"id": 5, "name": "general", "display_name": "general", "channel_type": "channel"},
                    {"id": 6, "name": False, "display_name": "Demi, Jeff", "channel_type": "chat"}]),
            _reply([{"id": 7, "name": "Demi"}]),
        )  # fmt: skip
        self.assertEqual(
            self.client.fetch_conversations(self.session),
            (Conversation(6, "Jeff", "chat", 3), Conversation(5, "# general", "channel", 0)),
        )
        self.assertEqual(_call(urlopen, 2)[2][0], [["partner_id", "=", 70]])

        self.answers(urlopen, _reply(99))
        self.client.post_channel_message(self.session, 6, " hello ")
        model, method, args, kwargs = _call(urlopen, -1)
        self.assertEqual((model, method, args, kwargs["body"]), ("discuss.channel", "message_post", [[6]], "hello"))
        with self.assertRaisesRegex(OdooConnectionError, "Write a message"):
            self.client.post_channel_message(self.session, 6, "  ")

    @patch("odoo_login.odoo_client.urlopen")
    def test_meetings_use_local_time_and_keep_all_day_dates(self, urlopen):
        self.answers(
            urlopen,
            _reply([{"id": 7, "partner_id": [70, "Demi"]}]),
            _fields("name", "start", "stop", "allday", "start_date", "location"),
            _reply([
                {"id": 1, "name": "Stand-up", "start": "2026-11-24 02:30:00", "stop": "2026-11-24 03:00:00",
                 "allday": False, "start_date": False, "location": "Room 1"},
                {"id": 2, "name": "Workshop", "start": "2026-11-25 00:00:00", "stop": "2026-11-25 23:59:59",
                 "allday": True, "start_date": "2026-11-25", "location": False},
                {"id": 3, "name": "Late call", "start": "2026-11-29 20:00:00", "stop": "2026-11-29 21:00:00",
                 "allday": False, "start_date": False, "location": False},  # 03:00 the next day locally: outside
            ]),
        )  # fmt: skip
        zone = datetime.timezone(datetime.timedelta(hours=7))
        meetings = self.client.fetch_meetings(self.session, "2026-11-23", "2026-11-29", tz=zone)
        self.assertEqual(
            meetings,
            (Meeting(1, "Stand-up", "2026-11-24", "09:30", "Room 1"), Meeting(2, "Workshop", "2026-11-25", "", "")),
        )

    @patch("odoo_login.odoo_client.urlopen")
    def test_dependencies_list_only_open_blockers(self, urlopen):
        self.answers(
            urlopen,
            _fields("name", "project_id", "stage_id", "state", "depend_on_ids", "dependent_ids", "user_ids"),
            _reply([{"id": 10, "name": "Mine", "project_id": [1, "Website"], "stage_id": [1, "Ready"],
                     "state": "04_waiting_normal", "depend_on_ids": [20, 21], "dependent_ids": [22]}]),
            _reply([
                {"id": 20, "name": "API", "project_id": [1, "Website"], "stage_id": [2, "In Progress"],
                 "state": "01_in_progress", "user_ids": [8]},
                {"id": 21, "name": "Old", "project_id": [1, "Website"], "stage_id": [3, "Done"],
                 "state": "1_done", "user_ids": [8]},
                {"id": 22, "name": "Release", "project_id": [1, "Website"], "stage_id": [1, "Ready"],
                 "state": "04_waiting_normal", "user_ids": []},
            ]),
            _reply([{"id": 1, "fold": False}, {"id": 2, "fold": False}, {"id": 3, "fold": True}]),
            _reply([{"id": 8, "display_name": "Jeff"}]),
        )  # fmt: skip
        waiting, blocking = self.client.fetch_dependencies(self.session)
        self.assertEqual(waiting, (Row("10:20", ("Mine", "API", "Jeff", "In Progress", "Website"), "project.task", 20),))
        self.assertEqual(blocking, (Row("22:10", ("Release", "Nobody", "Mine", "Ready", "Website"), "project.task", 22),))

    @patch("odoo_login.odoo_client.urlopen")
    def test_missing_apps_are_detected_and_time_off_survives_partial_access(self, urlopen):
        self.answers(urlopen, _reply([{"id": 1, "model": "mail.activity"}, {"id": 2, "model": "crm.lead"}]))
        self.assertEqual(self.client.available_models(self.session), {"mail.activity", "crm.lead"})
        self.answers(urlopen, _error("Access Denied"))
        self.assertIn("helpdesk.ticket", self.client.available_models(self.session))  # unknown: assume all

        self.answers(
            urlopen,
            _error("You are not allowed to access 'Time Off Type'"),
            _fields("holiday_status_id", "request_date_from", "request_date_to", "number_of_days", "state"),
            _reply([{"id": 4, "holiday_status_id": [1, "Paid leave"], "request_date_from": "2026-12-02",
                     "request_date_to": "2026-12-04", "number_of_days": 3.0, "state": "validate"}]),
            _error("Object hr.employee.public doesn't exist"),
        )  # fmt: skip
        rows = self.client.fetch_time_off(self.session, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cells, ("My time off", "Paid leave", "3 day(s)  ·  Approved", "02 Dec  →  04 Dec"))

        fresh = OdooClient(timeout=3)
        self.answers(urlopen, _error("no hr"), _error("no hr"), _error("no hr"))
        with self.assertRaisesRegex(OdooConnectionError, "no hr"):
            fresh.fetch_time_off(self.session, TODAY)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = [TaskMessage("Jeff", "2026-11-24 09:00", "<p>Ready?</p>")]

    def fetch_my_tasks(self, _session):
        return [TaskInfo(10, "Fix login", 1, "Website", "In Progress", deadline="2026-11-26")]

    def mark_activity_done(self, _session, activity_id, feedback=""):
        self.calls.append(("activity_done", activity_id, feedback))

    def fetch_channel_messages(self, _session, channel_id, limit=40):
        return tuple(self.messages)

    def post_channel_message(self, _session, channel_id, text):
        self.calls.append(("post", channel_id, text))
        self.messages.append(TaskMessage("Me", "2026-11-24 09:05", f"<p>{text}</p>"))


class OdooPagesTests(unittest.TestCase):
    uid = 1900

    def setUp(self):
        OdooPagesTests.uid += 1
        self.root = tk.Tk()
        self.root.withdraw()
        self.client = FakeClient()
        self.view = DashboardView(
            self.root, self.client, OdooSession("https://odoo.example.com", "main", self.uid, "key"), "demo@example.com"
        )
        self.view._today = lambda: TODAY
        self.view.refresh = lambda: None
        self.view.load_sync()

    def tearDown(self):
        self.root.destroy()

    def test_activities_page_counts_urgent_ones_and_marks_done(self):
        self.view._handle_side_result(
            "activities",
            (
                Row("1", ("4 days late", "Call", "Call the customer", "Fix login"), "project.task", 10, "overdue"),
                Row("2", ("Today", "To do", "", "Acme deal"), "crm.lead", 3, "today"),
                Row("3", ("in 3 days", "Email", "Send quote", "Acme deal"), "crm.lead", 3),
            ),
        )
        self.assertEqual(self.view.tab_text["activities"].get(), "Activities (2)")
        self.assertEqual(self.view.activities_tree.get_children(), ("1", "2", "3"))
        self.view.do_mark_activity("1", "phoned")
        self.assertEqual(self.client.calls, [("activity_done", 1, "phoned")])
        self.assertEqual(self.view.activities_tree.get_children(), ("2", "3"))
        self.assertEqual(self.view.tab_text["activities"].get(), "Activities (1)")

    def test_timesheet_week_table_sums_per_project_task_and_day(self):
        self.view.week_start = datetime.date(2026, 11, 23)
        self.view._handle_side_result(
            "timesheets",
            (
                datetime.date(2026, 11, 23),
                (
                    TimeLine("2026-11-23", 1.5, "Website", "Fix login", ""),
                    TimeLine("2026-11-23", 0.5, "Website", "Fix login", ""),
                    TimeLine("2026-11-24", 2.0, "Website", "Docs", ""),
                    TimeLine("2026-11-24", 1.0, "Mobile", "Build", ""),
                ),
            ),
        )
        table, totals = self.view.timesheet_table()
        self.assertEqual([project for project, _sums, _tasks in table], ["Mobile", "Website"])
        self.assertEqual(totals[:2], [2.0, 3.0])
        tree = self.view.timesheets_tree
        self.assertEqual(tree.item("p1", "values")[:2], ("2:00", "2:00"))  # Website: Monday, Tuesday
        self.assertEqual(tree.item("total", "values")[-1], "5:00")
        self.assertIn("5:00 logged", self.view.timesheets_sub.get())
        # A result for another week (the user moved on) is ignored.
        self.view._handle_side_result("timesheets", (datetime.date(2026, 11, 16), ()))
        self.assertEqual(len(self.view.timelines), 4)

    def test_discuss_shows_unread_and_sends_a_message(self):
        self.view._handle_side_result(
            "conversations", (Conversation(6, "Jeff", "chat", 3), Conversation(5, "# general", "channel", 0))
        )
        self.assertEqual(self.view.tab_text["discuss"].get(), "Discuss (3)")
        self.view.channel_id = 6
        self.view._handle_side_result("channel", (6, tuple(self.client.messages)))
        self.assertIn("Ready?", self.view.channel_text.get("1.0", "end"))
        self.view.do_send_channel("On my way")
        self.assertEqual(self.client.calls, [("post", 6, "On my way")])
        self.assertIn("On my way", self.view.channel_text.get("1.0", "end"))
        self.assertEqual(self.view.channel_title.get(), "Jeff")

    def test_more_page_switches_tables_and_hides_missing_apps(self):
        self.view._handle_side_result(
            "more", ("following", (Row("30", ("Their task", "Website", "Ready", "Jeff", "Tomorrow"), "project.task", 30),))
        )
        self.assertEqual(self.view.more_tree.get_children(), ("30",))
        self.assertEqual(self.view.more_tree.heading("c3", "text"), "ASSIGNED TO")
        self.view.more_rows["crm"] = (Row("3", ("Acme", "Acme Inc", "Won", "12,000", "Today", ""), "crm.lead", 3, "today"),)
        self.view.show_more("crm")
        self.assertEqual(self.view.more_tree.heading("c0", "text"), "OPPORTUNITY")
        self.assertEqual(self.view.more_tree.get_children(), ("3",))
        self.assertIn("1 line", self.view.more_sub.get())

        self.view._show_odoo_error("more", "Object helpdesk.ticket doesn't exist")
        self.assertIn("Not available on this Odoo", self.view.more_sub.get())

        self.view.available = {"mail.activity", "crm.lead"}  # no Discuss, timesheets, time off, helpdesk
        self.view._apply_availability()
        self.assertFalse(self.view.nav_rows["discuss"][0].master.winfo_manager())
        self.assertFalse(self.view.nav_rows["timesheets"][0].master.winfo_manager())
        self.assertTrue(self.view.nav_rows["activities"][0].master.winfo_manager())
        labels = list(self.view.more_box.cget("values"))
        self.assertIn("CRM pipeline", labels)
        self.assertNotIn("Helpdesk tickets", labels)
        self.assertNotIn("Time off", labels)

    def test_meetings_are_listed_on_their_calendar_day(self):
        self.view.cal_year, self.view.cal_month = 2026, 11
        self.view._handle_side_result("meetings", ((2026, 11), (Meeting(1, "Stand-up", "2026-11-24", "09:30"),)))
        self.assertEqual([m.name for m in self.view.meetings_on(TODAY)], ["Stand-up"])
        cell = self.view.cal_cells[[d for w in __import__("odoo_login.insights", fromlist=["x"]).month_grid(2026, 11) for d in w].index(TODAY)]
        texts = [child.cget("text") for child in cell.winfo_children()]
        self.assertIn("09:30  Stand-up", texts)
        self.view.rebuild()  # a theme switch keeps every page working
        self.assertEqual([m.name for m in self.view.meetings_on(TODAY)], ["Stand-up"])


if __name__ == "__main__":
    unittest.main()
