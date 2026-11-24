import tests_env  # noqa: F401  (must come first)

import datetime
import io
import json
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from odoo_login import ai, ai_other, richtext, updates
from odoo_login.dashboard import DashboardView
from odoo_login.odoo_client import Mention, OdooConnectionError, OdooSession, TaskDetail, TaskInfo, TaskMessage
from odoo_login.odoo_extra import Conversation, Meeting, OdooClient, Row, TimeLine
from odoo_login.power import unique_labels
from odoo_login.store import LocalStore

TODAY = datetime.date(2026, 11, 24)
CHECKLIST = '<p>Steps</p><ul class="o_checklist"><li>Reproduce</li><li class="o_checked">Fix</li></ul><ul><li>note</li></ul>'


def _reply(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def _fields(*names):
    return _reply({name: {"type": "char"} for name in names})


def _call(urlopen, index):
    return json.loads(urlopen.call_args_list[index].args[0].data)["params"]["args"][3:]


class ClientWriteTests(unittest.TestCase):
    def setUp(self):
        self.client = OdooClient(timeout=3)
        self.session = OdooSession(base_url="https://odoo.example.com", database="main", uid=7, api_key="key")

    def answers(self, urlopen, *replies):
        urlopen.return_value.__enter__.return_value.read.side_effect = list(replies)

    def test_checklist_html_changes_only_the_ticks(self):
        changed = richtext.set_checklist(CHECKLIST, [True, False])
        self.assertEqual(
            changed,
            '<p>Steps</p><ul class="o_checklist"><li class="o_checked">Reproduce</li><li class="">Fix</li></ul><ul><li>note</li></ul>',
        )
        self.assertEqual([kind for kind, _text in richtext.html_to_blocks(changed)], ["p", "done", "check", "li"])
        self.assertIsNone(richtext.set_checklist(CHECKLIST, [True]))  # the list changed meanwhile: touch nothing
        self.assertEqual(richtext.set_checklist(CHECKLIST, [False, True]), CHECKLIST)

    @patch("odoo_login.odoo_client.urlopen")
    def test_update_task_writes_only_what_changed(self, urlopen):
        self.answers(urlopen, _reply(True))
        self.client.update_task(self.session, 10, deadline="", starred=True, user_ids=[7, 8])
        self.assertEqual(
            _call(urlopen, -1)[:3],
            ["project.task", "write", [[10], {"date_deadline": False, "priority": "1", "user_ids": [[6, 0, [7, 8]]]}]],
        )
        self.assertFalse(self.client.update_task(self.session, 10))
        with self.assertRaisesRegex(OdooConnectionError, "YYYY-MM-DD"):
            self.client.update_task(self.session, 10, deadline="tomorrow")

    @patch("odoo_login.odoo_client.urlopen")
    def test_checklist_is_read_again_before_it_is_written(self, urlopen):
        self.answers(urlopen, _reply([{"id": 10, "description": CHECKLIST}]), _reply(True))
        self.client.set_checklist(self.session, 10, [True, True])
        self.assertIn('<li class="o_checked">Reproduce</li>', _call(urlopen, -1)[2][1]["description"])
        self.answers(urlopen, _reply([{"id": 10, "description": "<p>rewritten</p>"}]))
        with self.assertRaisesRegex(OdooConnectionError, "changed in Odoo"):
            self.client.set_checklist(self.session, 10, [True, True])

    @patch("odoo_login.odoo_client.urlopen")
    def test_mark_channel_read_moves_my_own_marker(self, urlopen):
        self.answers(
            urlopen,
            _reply([{"id": 7, "partner_id": [70, "Demi"]}]),
            _reply([555]),
            _reply([31]),
            _fields("seen_message_id", "fetched_message_id", "new_message_separator"),
            _reply(True),
        )
        self.assertTrue(self.client.mark_channel_read(self.session, 6))
        model, method, args, _kwargs = _call(urlopen, -1)
        self.assertEqual((model, method), ("discuss.channel.member", "write"))
        self.assertEqual(args, [[31], {"seen_message_id": 555, "fetched_message_id": 555, "new_message_separator": 556}])

    @patch("odoo_login.odoo_client.urlopen")
    def test_time_off_request_and_planned_activity(self, urlopen):
        with self.assertRaisesRegex(OdooConnectionError, "before the first"):
            self.client.request_time_off(self.session, 1, "2026-12-05", "2026-12-01")
        self.answers(urlopen, _reply(44))
        self.assertEqual(self.client.request_time_off(self.session, 1, "2026-12-01", "2026-12-02", " rest "), 44)
        self.assertEqual(
            _call(urlopen, -1)[2],
            [{"holiday_status_id": 1, "request_date_from": "2026-12-01", "request_date_to": "2026-12-02", "name": "rest"}],
        )
        self.answers(urlopen, _reply([90]), _reply([{"id": 1, "res_id": 4}]), _reply(77))
        self.assertEqual(self.client.plan_activity(self.session, "crm.lead", 3, "Send proposal", "2026-11-25"), 77)
        self.assertEqual(
            _call(urlopen, -1)[2],
            [{"res_model_id": 90, "res_id": 3, "user_id": 7, "summary": "Send proposal", "date_deadline": "2026-11-25",
              "activity_type_id": 4}],
        )  # fmt: skip
        with self.assertRaisesRegex(OdooConnectionError, "what has to be done"):
            self.client.plan_activity(self.session, "crm.lead", 3, " ", "2026-11-25")


class StoreAndUpdateTests(unittest.TestCase):
    def test_tasks_are_kept_for_offline_use(self):
        session = OdooSession("https://odoo.example.com", "main", 2900, "key")
        store = LocalStore(session)
        store.save_tasks([TaskInfo(10, "Fix login", 1, "Website", "Ready", deadline="2026-11-26")])
        store.tasks_cache.append({"id": "broken"})  # an entry from another version is skipped, not fatal
        store.save()
        tasks, saved = LocalStore(session).cached_tasks()
        self.assertEqual([(t.id, t.name, t.deadline) for t in tasks], [(10, "Fix login", "2026-11-26")])
        self.assertTrue(saved)

    @patch("odoo_login.updates.urlopen")
    def test_update_download_refuses_what_is_not_the_app(self, urlopen):
        import tempfile

        folder = tempfile.mkdtemp()
        urlopen.return_value.__enter__.return_value = io.BytesIO(b"<html>not found</html>")
        with self.assertRaisesRegex(OSError, "not the app"):
            updates.download("9.9", folder)
        urlopen.return_value.__enter__.return_value = io.BytesIO(b"MZ" + b"\0" * 1_100_000)
        path = updates.download("v9.9", folder)
        self.assertTrue(path.endswith("OdooDesktop.exe"))
        self.assertEqual(
            urlopen.call_args.args[0].full_url,
            "https://github.com/dbaskhara/odoo-desktop/releases/download/v9.9/OdooDesktop.exe",
        )
        with self.assertRaises(OSError):
            updates.download("../evil", folder)
        script = updates.restart_script(r"C:\Temp\new.exe", r"C:\Apps\OdooDesktop.exe")
        self.assertIn(r'copy /y "C:\Temp\new.exe" "C:\Apps\OdooDesktop.exe"', script)
        self.assertIn(r'start "" "C:\Apps\OdooDesktop.exe"', script)


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        return self.message


def _claude_message(text="Hello", stop="end_turn"):
    block = MagicMock()
    block.type, block.text = "text", text
    thinking = MagicMock()
    thinking.type = "thinking"
    message = MagicMock()
    message.content, message.stop_reason = [thinking, block], stop
    return message


class AIConnectionTests(unittest.TestCase):
    def _client(self, message):
        import anthropic

        client = MagicMock()
        client.messages.stream.return_value = FakeStream(message)
        client.beta.messages.stream.return_value = FakeStream(message)
        return patch("odoo_login.ai._claude_client", return_value=(anthropic, client)), client

    def test_claude_uses_the_sdk_with_a_fallback_on_models_that_have_one(self):
        patcher, client = self._client(_claude_message("Summary."))
        with patcher:
            self.assertEqual(ai.ask("claude", "key", "", "system", "prompt"), "Summary.")
        kwargs = client.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual((kwargs["fallbacks"], kwargs["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "prompt"}])
        self.assertNotIn("thinking", kwargs)  # adaptive by default: never sent
        self.assertFalse(client.messages.stream.called)

        patcher, client = self._client(_claude_message("Ok"))
        with patcher:
            ai.ask("claude", "key", "claude-haiku-5-5", "system", "prompt")
        self.assertTrue(client.messages.stream.called)
        self.assertNotIn("fallbacks", client.messages.stream.call_args.kwargs)
        self.assertFalse(client.beta.messages.stream.called)

    def test_claude_refusal_and_missing_key_are_plain_messages(self):
        patcher, _client = self._client(_claude_message("", stop="refusal"))
        with patcher, self.assertRaisesRegex(ai.AIError, "declined"):
            ai.ask("claude", "key", "", "system", "prompt")
        with self.assertRaisesRegex(ai.AIError, "No API key"):
            ai.ask("claude", "", "", "system", "prompt")
        with self.assertRaisesRegex(ai.AIError, "Choose a model"):
            ai.ask("gpt", "key", "", "system", "prompt")

    @patch("odoo_login.ai_other.urlopen")
    def test_gpt_and_gemini_requests_and_answers(self, urlopen):
        read = urlopen.return_value.__enter__.return_value.read
        read.return_value = json.dumps({"choices": [{"message": {"content": " Hi "}}]}).encode()
        self.assertEqual(ai.ask("gpt", "sk-test", "gpt-x", "system", "prompt"), "Hi")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
        self.assertEqual(json.loads(request.data)["messages"][1], {"role": "user", "content": "prompt"})

        read.return_value = json.dumps({"candidates": [{"content": {"parts": [{"text": "Halo"}]}}]}).encode()
        self.assertEqual(ai.ask("gemini", "g-key", "gemini-x", "system", "prompt"), "Halo")
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1beta/models/gemini-x:generateContent"))
        self.assertNotIn("g-key", request.full_url)  # the key travels in a header, never in the address
        self.assertEqual(request.get_header("X-goog-api-key"), "g-key")

        read.return_value = json.dumps({"models": [
            {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-b", "supportedGenerationMethods": ["embedContent"]},
        ]}).encode()  # fmt: skip
        self.assertEqual(ai.list_models("gemini", "g-key"), ["gemini-a"])
        read.return_value = json.dumps({"data": [{"id": "gpt-x"}, {"id": "text-embedding-3"}, {"id": "whisper-1"}]}).encode()
        self.assertEqual(ai.list_models("gpt", "sk-test"), ["gpt-x"])
        read.return_value = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        with self.assertRaisesRegex(ai.AIError, "SAFETY"):
            ai.ask("gemini", "g-key", "gemini-x", "system", "prompt")


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.tasks = [
            TaskInfo(10, "Fix login", 1, "Website", "In Progress", deadline="2026-11-20", updated="2026-11-23"),
            TaskInfo(11, "Write docs", 1, "Website", "Ready", deadline="2026-11-26", updated="2026-11-01"),
        ]

    def fetch_my_tasks(self, _session):
        if self.fail:
            raise OdooConnectionError("Could not reach Odoo. Check the URL and your network connection.")
        return self.tasks

    def fetch_task_detail(self, _session, task_id):
        return TaskDetail(
            id=task_id, name="Fix login", project="Website", stage="In Progress", description=CHECKLIST,
            messages=(TaskMessage("Jeff", "2026-11-23 16:00", "<p>Before Friday please.</p>"),),
        )  # fmt: skip

    def fetch_task_edit_data(self, _session, _task_id):
        return {
            "deadline": "2026-11-20", "starred": False, "user_ids": [7], "tag_ids": [],
            "users": [(7, "Demi"), (8, "Jeff"), (9, "Jeff")], "tags": [(1, "Bug")],
        }  # fmt: skip

    def update_task(self, _session, task_id, **changes):
        self.calls.append(("update_task", task_id, changes))

    def set_checklist(self, _session, task_id, states):
        self.calls.append(("set_checklist", task_id, states))

    def decide_time_off(self, _session, leave_id, approve=True):
        self.calls.append(("decide_time_off", leave_id, approve))

    def set_ticket_stage(self, _session, ticket_id, stage_id):
        self.calls.append(("set_ticket_stage", ticket_id, stage_id))

    def plan_activity(self, _session, model, res_id, summary, date):
        self.calls.append(("plan_activity", model, res_id, summary, date))


class DashboardV6Tests(unittest.TestCase):
    uid = 3900

    def setUp(self):
        DashboardV6Tests.uid += 1
        self.root = tk.Tk()
        self.root.withdraw()
        self.client = FakeClient()
        self.session = OdooSession("https://odoo.example.com", "main", self.uid, "key")
        self.notes = []
        self.view = self._view()
        self.view.load_sync()

    def _view(self):
        view = DashboardView(
            self.root, self.client, self.session, "demo@example.com",
            notifier=lambda title, text: self.notes.append((title, text)),
        )  # fmt: skip
        view._today = lambda: TODAY
        view.refresh = lambda: None
        view.show_more = lambda key, reload=False: None  # no background reload in tests
        view.load_activities = lambda quiet=False: None
        return view

    def tearDown(self):
        self.root.destroy()

    def test_overview_tiles_show_dashes_until_odoo_answers(self):
        self.assertEqual(self.view.odoo_strip(), {})
        self.assertEqual(self.view.odoo_vars["activities"][0].get(), "—")
        self.view._handle_side_result(
            "activities", (Row("1", ("2 days late", "Call", "Ring", "Acme"), "crm.lead", 3, "overdue"),)
        )
        self.view._handle_side_result("today_meetings", (Meeting(1, "Stand-up", "2026-11-24", "23:59"),))
        self.view._handle_side_result(
            "week_hours", (TimeLine("2026-11-24", 1.5, "Website", "Fix login", ""), TimeLine("2026-11-23", 2, "Website", "Docs", ""))
        )
        strip = self.view.odoo_strip()
        self.assertEqual(strip["activities"], ("1 to do", "1 late, 0 due today"))
        self.assertEqual(strip["hours"], ("1:30 today", "3:30 logged this week"))
        self.assertEqual(strip["meetings"], ("1 meeting", "next 23:59  Stand-up"))
        self.assertEqual(self.view.odoo_vars["hours"][0].get(), "1:30 today")

    def test_new_direct_messages_notify_once_and_reading_clears_the_count(self):
        first = (Conversation(6, "Jeff", "chat", 1), Conversation(5, "# general", "channel", 4))
        self.view._handle_side_result("conversations", first)
        self.assertEqual(self.notes, [])  # what was already unread at start is not news
        later = (Conversation(6, "Jeff", "chat", 3), Conversation(5, "# general", "channel", 9))
        self.view._handle_side_result("conversations", later)
        self.assertEqual([title for title, _text in self.notes], ["Message from Jeff"])  # channels stay quiet
        self.view._handle_side_result("conversations", later)
        self.assertEqual(len(self.notes), 1)
        self.view._handle_side_result("channel_read", 6)
        self.assertEqual(self.view.tab_text["discuss"].get(), "Discuss (9)")

    def test_search_finds_tasks_pages_conversations_and_odoo_matches(self):
        self.view.conversations = (Conversation(6, "Jeff", "chat", 0),)
        self.view.mentions = (Mention(3, "project.task", 10, "Fix login", "Sam", "2026-11-23 16:00", "<p>ping</p>", False),)
        kinds = lambda text: [(kind, target) for _label, kind, target in self.view.search_results(text)]  # noqa: E731
        self.assertEqual(kinds("login web"), [("task", 10)])  # every word must match
        self.assertEqual(kinds("sam"), [("record", ("project.task", 10))])  # a mention
        self.assertEqual(kinds("jeff"), [("conversation", 6)])
        self.assertIn(("page", "timesheets"), kinds("timesh"))
        self.assertEqual(kinds(""), [])
        self.view._search_remote = ((10, "Fix login", "Website"), (55, "Login page redesign", "Mobile"))
        self.assertEqual(kinds("login")[-1], ("task", 55))  # a task of mine is not listed twice
        self.view.open_search_result("page", "calendar")
        self.assertEqual(self.view.current_tab, "calendar")
        self.assertEqual(self.view.visible_pages()[0], "overview")

    def test_offline_shows_the_saved_tasks(self):
        self.view.destroy()
        self.client.fail = True
        self.view = self._view()  # same account, Odoo unreachable
        self.assertEqual([t.id for t in self.view.tasks], [10, 11])  # shown at once from the saved copy
        self.view._went_offline("Could not reach Odoo.")
        self.assertTrue(self.view.offline)
        self.assertIn("Offline: showing the tasks saved", self.view.status_var.get())
        self.assertEqual(self.view.stat_vars["open"].get(), "2")
        self.client.fail = False
        self.view.load_sync()
        self.assertFalse(self.view.offline)

    def test_edit_task_sends_only_the_changes_and_checklist_sends_all_ticks(self):
        self.assertEqual(list(unique_labels([(8, "Jeff"), (9, "Jeff"), (7, "Demi")])), ["Jeff  (#8)", "Jeff  (#9)", "Demi"])
        self.view.open_task_sync(10)
        answers = iter([
            {"deadline": "2026-11-28", "priority": "High", "users": ["Demi", "Jeff  (#9)"], "tags": []},
            {"items": ["1.  Reproduce", "2.  Fix"]},
            {"deadline": "2026-11-20", "priority": "Normal", "users": ["Demi"], "tags": []},
        ])  # fmt: skip
        self.view._form = lambda *args, **kwargs: next(answers)
        self.view._busy = lambda work: (True, work())
        self.view.edit_task_clicked()
        self.assertEqual(self.view.checklist_items(), [("Reproduce", False), ("Fix", True)])
        self.view.checklist_clicked()
        self.view.edit_task_clicked()  # nothing changed: nothing is written
        self.assertEqual(
            self.client.calls,
            [
                ("update_task", 10, {"deadline": "2026-11-28", "starred": True, "user_ids": [7, 9]}),
                ("set_checklist", 10, [True, True]),
            ],
        )
        self.assertEqual(self.view.status_var.get(), "Nothing was changed.")

    def test_more_from_odoo_offers_actions_that_fit_the_view(self):
        labels = lambda: [label for label, _command in self.view.more_actions()]  # noqa: E731
        self.assertEqual(labels(), [])
        self.view.more_key = "timeoff"
        self.assertEqual(labels(), ["Request time off", "Approve", "Refuse"])
        self.view.more_key = "crm"
        self.assertEqual(labels(), ["Plan activity"])
        self.view.do_decide_time_off(9, True)
        self.view.do_ticket_stage(4, 12)
        self.view.do_plan_activity("crm.lead", 3, "Send proposal", "2026-11-25")
        self.assertEqual(
            self.client.calls,
            [("decide_time_off", 9, True), ("set_ticket_stage", 4, 12),
             ("plan_activity", "crm.lead", 3, "Send proposal", "2026-11-25")],
        )  # fmt: skip

    def test_ai_asks_before_sending_and_can_fill_the_reply_box(self):
        self.view.open_task_sync(10)
        errors, asked, sent = [], [], []
        self.view._error = errors.append
        self.assertFalse(self.view.ai_run("Summary", "prompt", "The task"))  # no key saved: nothing is sent
        self.assertIn("not set up", errors[0])

        self.view.ai_ready = lambda: True
        self.view._confirm = lambda title, message: asked.append(title) or False
        self.assertFalse(self.view.ai_run("Summary", "prompt", "The task"))  # the user said no
        self.view._confirm = lambda title, message: asked.append(title) or True
        self.view._ask_provider = lambda system, prompt: sent.append(prompt) or "Thanks, on it."
        self.view.ai_draft_task_reply()
        self.view.ai_summarise_task()
        self.assertEqual(asked, ["Send to Claude (Anthropic)"] * 2)  # asked again only after a refusal
        for _ in range(100):
            if self.view.results.qsize() >= 2:
                break
            self.root.after(20, self.root.quit)
            self.root.mainloop()
        self.assertIn("Before Friday please.", sent[0])
        self.assertIn("Task: Fix login", sent[0])
        kind, (title, text, on_use, use_label) = self.view.results.get_nowait()
        self.assertEqual((kind, text, use_label), ("ai_done", "Thanks, on it.", "Put in reply box"))
        on_use("Edited reply")
        self.assertEqual(self.view.reply_text.get("1.0", "end").strip(), "Edited reply")
        self.assertIn("Fix login | Website | In Progress | 2026-11-20 | 1", self.view.tasks_text())


if __name__ == "__main__":
    unittest.main()
