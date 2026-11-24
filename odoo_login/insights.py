"""Pure calculations on the task list: urgency, idleness, changes, project health."""

import datetime
from dataclasses import dataclass

STALE_DAYS = 7
BUCKETS = (
    ("overdue", "Overdue"),
    ("today", "Today"),
    ("week", "Next 7 days"),
    ("later", "Later"),
    ("none", "No deadline"),
    ("done", "Done"),
)
OPEN_BUCKETS = BUCKETS[:-1]


def _date(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def bucket_of(task, today):
    if getattr(task, "done", False):
        return "done"
    day = _date(task.deadline)
    if day is None:
        return "none"
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    if day <= today + datetime.timedelta(days=7):
        return "week"
    return "later"


def idle_days(task, today):
    """Days since anything changed on the task in Odoo (None if unknown)."""
    day = _date(task.updated)
    return None if day is None else max((today - day).days, 0)


def is_stale(task, today):
    if getattr(task, "done", False):
        return False
    days = idle_days(task, today)
    return days is not None and days >= STALE_DAYS


def deadline_text(task, today):
    day = _date(task.deadline)
    if day is None:
        return "—"
    if getattr(task, "done", False):
        return day.strftime("%d %b %Y" if day.year != today.year else "%d %b")
    delta = (day - today).days
    if delta < -1:
        return f"{-delta} days late"
    if delta == -1:
        return "1 day late"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta <= 7:
        return f"in {delta} days"
    return day.strftime("%d %b %Y" if day.year != today.year else "%d %b")


def idle_text(task, today):
    days = idle_days(task, today)
    if days is None:
        return "—"
    if days == 0:
        return "today"
    return f"{days} d"


# Changes since the last visit ------------------------------------------


@dataclass(frozen=True)
class Change:
    kind: str  # new | stage | deadline | gone
    task_id: int
    name: str
    project: str
    detail: str


CHANGE_LABELS = {
    "new": "New for you",
    "stage": "Stage moved",
    "deadline": "Deadline changed",
    "gone": "Closed or reassigned",
}


def snapshot(tasks):
    return {
        str(task.id): {
            "name": task.name,
            "project": task.project,
            "stage": task.stage,
            "deadline": task.deadline,
        }
        for task in tasks
    }


def diff(old, new):
    """Compare two snapshots; `old` is the previous visit, `new` is now."""
    changes = []
    for key, now in new.items():
        before = old.get(key)
        if before is None:
            changes.append(Change("new", int(key), now["name"], now["project"], f"Stage: {now['stage']}"))
            continue
        if before.get("stage") != now["stage"]:
            changes.append(
                Change("stage", int(key), now["name"], now["project"], f"{before.get('stage') or '—'}  →  {now['stage']}")
            )
        if (before.get("deadline") or "") != now["deadline"]:
            changes.append(
                Change(
                    "deadline",
                    int(key),
                    now["name"],
                    now["project"],
                    f"{before.get('deadline') or 'none'}  →  {now['deadline'] or 'none'}",
                )
            )
    for key, before in old.items():
        if key not in new:
            changes.append(
                Change(
                    "gone",
                    int(key),
                    before.get("name", ""),
                    before.get("project", ""),
                    f"Was in: {before.get('stage') or '—'}",
                )
            )
    order = {"new": 0, "stage": 1, "deadline": 2, "gone": 3}
    changes.sort(key=lambda c: (order[c.kind], c.project.lower(), c.name.lower()))
    return changes


# Project health ---------------------------------------------------------


@dataclass(frozen=True)
class ProjectHealth:
    id: int
    name: str
    open: int
    overdue: int
    today: int
    week: int
    later: int
    none: int
    stale: int
    status: str  # Late | At risk | On track


def _status(open_count, overdue, stale):
    if overdue >= 3 or (open_count and overdue / open_count >= 0.3):
        return "Late"
    if overdue or (open_count and stale / open_count >= 0.5):
        return "At risk"
    return "On track"


def project_health(tasks, today):
    groups = {}
    for task in tasks:
        if getattr(task, "done", False):
            continue
        groups.setdefault((task.project_id, task.project), []).append(task)
    result = []
    for (project_id, name), items in groups.items():
        counts = {key: 0 for key, _label in OPEN_BUCKETS}
        for task in items:
            counts[bucket_of(task, today)] += 1
        stale = sum(1 for task in items if is_stale(task, today))
        result.append(
            ProjectHealth(
                id=project_id,
                name=name,
                open=len(items),
                overdue=counts["overdue"],
                today=counts["today"],
                week=counts["week"],
                later=counts["later"],
                none=counts["none"],
                stale=stale,
                status=_status(len(items), counts["overdue"], stale),
            )
        )
    rank = {"Late": 0, "At risk": 1, "On track": 2}
    result.sort(key=lambda p: (rank[p.status], -p.overdue, -p.open, p.name.lower()))
    return result


# Calendar and week helpers ----------------------------------------------


def week_range(today):
    """Monday and Sunday of the week containing `today`."""
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=6)


def month_grid(year, month):
    """Six weeks of dates (Monday first) covering the given month."""
    first = datetime.date(year, month, 1)
    start = first - datetime.timedelta(days=first.weekday())
    return [[start + datetime.timedelta(days=week * 7 + day) for day in range(7)] for week in range(6)]


def tasks_by_day(tasks):
    days = {}
    for task in tasks:
        if task.deadline and not getattr(task, "done", False):
            days.setdefault(task.deadline, []).append(task)
    return days


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def format_duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours:d}:{rest // 60:02d}"


def week_summary(today, history, closed, open_tasks):
    """Sections of the weekly summary as [(title, [lines])]."""
    monday, sunday = week_range(today)
    span = f"{monday.strftime('%d %b')} to {sunday.strftime('%d %b %Y')}"
    entries = [e for e in history if monday.isoformat() <= str(e.get("date", "")) <= sunday.isoformat()]

    def lines(kind):
        seen, out = set(), []
        for entry in entries:
            if entry.get("kind") != kind:
                continue
            key = (entry.get("task_id"), entry.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            detail = f"  ({entry['detail']})" if entry.get("detail") and kind != "new" else ""
            out.append(f"{entry.get('name', '')}  ·  {entry.get('project', '')}{detail}")
        return out

    closed_lines = [f"{task.name}  ·  {task.project}  ·  {task.stage}" for task in closed]
    known = {task.name for task in closed}
    for entry in entries:
        if entry.get("kind") == "gone" and entry.get("name") not in known:
            known.add(entry.get("name"))
            closed_lines.append(f"{entry.get('name', '')}  ·  {entry.get('project', '')}  ·  closed or reassigned")
    slipped = [
        f"{task.name}  ·  {task.project}  ·  {deadline_text(task, today)}"
        for task in sorted(open_tasks, key=lambda t: t.deadline or "9999")
        if bucket_of(task, today) == "overdue"
    ]
    return span, [
        ("Finished this week", closed_lines),
        ("Moved to another stage", lines("stage")),
        ("New for you", lines("new")),
        ("Deadline changed", lines("deadline")),
        ("Slipped (overdue now)", slipped),
    ]
