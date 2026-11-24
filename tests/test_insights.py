import datetime
import unittest

from odoo_login import insights
from odoo_login.odoo_client import TaskInfo

TODAY = datetime.date(2026, 11, 24)


def task(id, deadline="", updated="2026-11-24", project="Website", stage="In Progress", name=None):
    return TaskInfo(
        id=id,
        name=name or f"Task {id}",
        project_id=1 if project == "Website" else 2,
        project=project,
        stage=stage,
        deadline=deadline,
        updated=updated,
    )


class InsightsTests(unittest.TestCase):
    def test_buckets_by_deadline(self):
        self.assertEqual(insights.bucket_of(task(1, "2026-11-20"), TODAY), "overdue")
        self.assertEqual(insights.bucket_of(task(1, "2026-11-24"), TODAY), "today")
        self.assertEqual(insights.bucket_of(task(1, "2026-12-01"), TODAY), "week")
        self.assertEqual(insights.bucket_of(task(1, "2026-12-02"), TODAY), "later")
        self.assertEqual(insights.bucket_of(task(1, ""), TODAY), "none")

    def test_deadline_text_is_relative(self):
        self.assertEqual(insights.deadline_text(task(1, "2026-11-21"), TODAY), "3 days late")
        self.assertEqual(insights.deadline_text(task(1, "2026-11-25"), TODAY), "Tomorrow")
        self.assertEqual(insights.deadline_text(task(1, "2026-11-28"), TODAY), "in 4 days")
        self.assertEqual(insights.deadline_text(task(1, ""), TODAY), "—")

    def test_stale_after_seven_days_without_change(self):
        self.assertTrue(insights.is_stale(task(1, updated="2026-11-17"), TODAY))
        self.assertFalse(insights.is_stale(task(1, updated="2026-11-18"), TODAY))
        self.assertFalse(insights.is_stale(task(1, updated=""), TODAY))

    def test_diff_reports_new_moved_rescheduled_and_gone(self):
        old = insights.snapshot([task(1, "2026-11-30", stage="Ready"), task(2, "2026-11-30"), task(3)])
        new = insights.snapshot([task(1, "2026-11-30", stage="In Review"), task(2, "2026-12-05"), task(4)])
        changes = {(c.kind, c.task_id): c for c in insights.diff(old, new)}
        self.assertEqual(set(changes), {("new", 4), ("stage", 1), ("deadline", 2), ("gone", 3)})
        self.assertIn("Ready", changes[("stage", 1)].detail)
        self.assertIn("In Review", changes[("stage", 1)].detail)
        self.assertEqual(insights.diff(new, new), [])

    def test_project_health_ranks_late_projects_first(self):
        tasks = [
            task(1, "2026-11-10"),
            task(2, "2026-11-12"),
            task(3, "2026-12-20"),
            task(4, "2026-12-20", project="Mobile"),
            task(5, "", project="Mobile"),
        ]
        health = insights.project_health(tasks, TODAY)
        self.assertEqual([p.name for p in health], ["Website", "Mobile"])
        self.assertEqual(health[0].status, "Late")
        self.assertEqual((health[0].open, health[0].overdue, health[0].later), (3, 2, 1))
        self.assertEqual(health[1].status, "On track")


if __name__ == "__main__":
    unittest.main()
