import tests_env  # noqa: F401  (must come first)

import datetime
import tkinter as tk
import unittest

from odoo_login.dashboard import DashboardView
from odoo_login.odoo_client import OdooSession, TaskInfo
from odoo_login.store import LocalStore

TODAY = datetime.date(2026, 11, 24)


def sample_tasks():
    return [
        TaskInfo(10, "Fix login", 1, "Website", "In Progress", deadline="2026-11-20", updated="2026-11-23"),
        TaskInfo(11, "Write docs", 1, "Website", "Ready", deadline="", updated="2026-11-01"),
        TaskInfo(12, "Ship build", 2, "Mobile", "In Review", deadline="2026-11-26", updated="2026-11-24"),
    ]


class FakeClient:
    def __init__(self):
        self.tasks = sample_tasks()

    def fetch_my_tasks(self, _session):
        return self.tasks


class DashboardViewTests(unittest.TestCase):
    uid = 100

    def setUp(self):
        DashboardViewTests.uid += 1  # fresh local store per test
        self.root = tk.Tk()
        self.root.withdraw()
        self.client = FakeClient()
        self.session = OdooSession(
            base_url="https://odoo.example.com", database="main", uid=self.uid, api_key="key"
        )
        self.view = self._view()

    def _view(self):
        view = DashboardView(self.root, self.client, self.session, "demo@example.com")
        view._today = lambda: TODAY
        return view

    def tearDown(self):
        self.root.destroy()

    def test_focus_groups_tasks_by_urgency(self):
        self.view.load_sync()
        tree = self.view.focus_tree
        self.assertEqual(tree.get_children(), ("g:overdue", "g:week", "g:none"))
        self.assertEqual(tree.get_children("g:overdue"), ("10",))
        self.assertEqual(tree.item("10", "values")[2], "4 days late")
        self.assertEqual(self.view.stat_vars["open"].get(), "3")
        self.assertEqual(self.view.stat_vars["overdue"].get(), "1")
        self.assertEqual(self.view.stat_vars["stale"].get(), "1")

    def test_search_and_project_filters(self):
        self.view.load_sync()
        self.view.search_var.set("docs")
        self.assertEqual([t.id for t in self.view.visible_tasks], [11])
        self.view.search_var.set("")
        self.view.focus_on_project("Mobile")
        self.assertEqual([t.id for t in self.view.visible_tasks], [12])
        self.assertEqual(self.view.current_tab, "focus")

    def test_pin_and_private_note_stay_on_this_computer(self):
        self.view.load_sync()
        self.view.focus_tree.selection_set("11")
        self.view._show_detail()
        self.view.toggle_pin()
        self.view._show_detail()
        self.view.note_text.insert("1.0", "ask Jeff first")
        self.view._save_note()
        self.assertTrue(self.view.focus_tree.item("11", "text").endswith("Write docs"))
        self.assertNotEqual(self.view.focus_tree.item("11", "text"), "Write docs")

        saved = LocalStore(self.session)
        self.assertEqual(saved.pins, {11})
        self.assertEqual(saved.note(11), "ask Jeff first")
        self.view.pinned_only.set(True)
        self.view.render_focus()
        self.assertEqual([t.id for t in self.view.visible_tasks], [11])

    def test_first_visit_has_no_changes_then_next_visit_lists_them(self):
        self.view.load_sync()
        self.assertEqual(self.view.changes, [])
        self.view.destroy()

        tasks = sample_tasks()
        self.client.tasks = [
            TaskInfo(10, "Fix login", 1, "Website", "In Review", deadline="2026-11-20", updated="2026-11-24"),
            tasks[2],
            TaskInfo(13, "New thing", 2, "Mobile", "Ready"),
        ]
        self.view = self._view()
        self.view.load_sync()
        kinds = sorted((c.kind, c.task_id) for c in self.view.changes)
        self.assertEqual(kinds, [("gone", 11), ("new", 13), ("stage", 10)])
        self.assertEqual(len(self.view.changes_tree.get_children()), 3)
        self.assertEqual(self.view.tab_text["changes"].get(), "What changed (3)")
        # A refresh in the same session keeps comparing with the previous visit.
        self.view.load_sync()
        self.assertEqual(len(self.view.changes), 3)

    def test_done_stage_tasks_are_hidden_and_never_overdue(self):
        self.client.tasks = sample_tasks() + [
            TaskInfo(20, "Old fix", 1, "Website", "Done", deadline="2026-10-01", updated="2026-10-02", done=True),
        ]
        self.view.load_sync()

        self.assertFalse(self.view.focus_tree.exists("20"))
        self.assertEqual(self.view.stat_vars["open"].get(), "3")
        self.assertEqual(self.view.stat_vars["overdue"].get(), "1")
        self.assertEqual(self.view.stat_vars["stale"].get(), "1")
        self.assertEqual(self.view.health[0].open, 2)
        self.assertIn("1 in a done stage are hidden", self.view.status_var.get())

        self.view.show_done.set(True)
        self.view.render_focus()
        self.assertEqual(self.view.focus_tree.get_children("g:done"), ("20",))
        self.assertEqual(self.view.focus_tree.item("20", "values")[2], "01 Oct")

    def test_project_health_and_export(self):
        self.view.load_sync()
        self.assertEqual([p.name for p in self.view.health], ["Website", "Mobile"])
        self.assertEqual(self.view.health[0].status, "Late")
        rows = self.view.export_rows()
        self.assertEqual(rows[0][:3], ["Urgency", "Task", "Project"])
        self.assertEqual([r[1] for r in rows[1:]], ["Fix login", "Ship build", "Write docs"])
        self.assertIn("id=10", rows[1][-1])


if __name__ == "__main__":
    unittest.main()
