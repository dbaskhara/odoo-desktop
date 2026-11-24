"""Per-account data kept on this computer only.

Pins, private notes, the last-visit snapshot, the daily plan, a history of
changes (for the weekly summary), the running timer and which notifications
were already shown.
"""

import datetime
import hashlib
import json

from . import settings

HISTORY_DAYS = 60
PLAN_DAYS = 30


class LocalStore:
    def __init__(self, session):
        key = f"{session.base_url}|{session.database}|{session.uid}".encode("utf-8")
        self.path = settings.data_dir() / f"data-{hashlib.sha1(key).hexdigest()[:12]}.json"
        self.snapshot = {}
        self.saved_at = ""
        self.pins = set()
        self.notes = {}
        self.plan = {}  # date -> [task ids]
        self.plan_done = {}  # date -> [task ids ticked that day]
        self.history = []  # [{date, kind, task_id, name, project, detail}]
        self.timer = {}  # {task_id, name, project_id, started}
        self.notified = {}  # date -> [keys already notified]
        self.checked_mentions = set()  # message ids ticked by hand on the Mentions page
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get("snapshot"), dict):
            self.snapshot = data["snapshot"]
        self.saved_at = str(data.get("saved_at") or "")
        self.pins = {int(i) for i in data.get("pins", []) if str(i).isdigit()}
        self.checked_mentions = {int(i) for i in data.get("checked_mentions", []) if str(i).isdigit()}
        if isinstance(data.get("notes"), dict):
            self.notes = {str(k): str(v) for k, v in data["notes"].items() if v}
        for name in ("plan", "plan_done", "notified"):
            value = data.get(name)
            if isinstance(value, dict):
                setattr(self, name, {str(k): list(v) for k, v in value.items() if isinstance(v, list)})
        if isinstance(data.get("history"), list):
            self.history = [entry for entry in data["history"] if isinstance(entry, dict)]
        if isinstance(data.get("timer"), dict):
            self.timer = data["timer"]

    def save(self):
        data = {
            "saved_at": self.saved_at,
            "pins": sorted(self.pins),
            "checked_mentions": sorted(self.checked_mentions),
            "notes": self.notes,
            "plan": self.plan,
            "plan_done": self.plan_done,
            "timer": self.timer,
            "notified": self.notified,
            "history": self.history,
            "snapshot": self.snapshot,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    # Snapshot, pins, notes ----------------------------------------------

    def remember_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.saved_at = datetime.datetime.now().isoformat(timespec="minutes")
        self.save()

    def toggle_pin(self, task_id):
        if task_id in self.pins:
            self.pins.discard(task_id)
        else:
            self.pins.add(task_id)
        self.save()
        return task_id in self.pins

    def note(self, task_id):
        return self.notes.get(str(task_id), "")

    def set_note(self, task_id, text):
        text = text.strip()
        if text == self.note(task_id):
            return
        if text:
            self.notes[str(task_id)] = text
        else:
            self.notes.pop(str(task_id), None)
        self.save()

    # Mentions ticked by hand -----------------------------------------------

    def mention_checked(self, message_id):
        return int(message_id) in self.checked_mentions

    def set_mentions_checked(self, message_ids, checked=True):
        ids = {int(i) for i in message_ids}
        if checked:
            self.checked_mentions |= ids
        else:
            self.checked_mentions -= ids
        self.save()

    # History of changes ---------------------------------------------------

    def add_history(self, changes, today):
        """Append `insights.Change` items seen today; forget entries older than HISTORY_DAYS."""
        if changes:
            stamp = today.isoformat()
            for change in changes:
                self.history.append(
                    {
                        "date": stamp,
                        "kind": change.kind,
                        "task_id": change.task_id,
                        "name": change.name,
                        "project": change.project,
                        "detail": change.detail,
                    }
                )
        limit = (today - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
        self.history = [entry for entry in self.history if str(entry.get("date", "")) >= limit]

    def history_between(self, start, end):
        return [entry for entry in self.history if start.isoformat() <= str(entry.get("date", "")) <= end.isoformat()]

    # Today plan -----------------------------------------------------------

    def today_plan(self, today, open_ids):
        """Task ids planned for today. The first call each day carries over what was left."""
        key = today.isoformat()
        if key not in self.plan:
            carried = []
            earlier = sorted(day for day in self.plan if day < key)
            if earlier:
                last = earlier[-1]
                finished = set(self.plan_done.get(last, []))
                carried = [i for i in self.plan[last] if i not in finished and i in open_ids]
            self.plan[key] = carried
            limit = (today - datetime.timedelta(days=PLAN_DAYS)).isoformat()
            self.plan = {day: ids for day, ids in self.plan.items() if day >= limit}
            self.plan_done = {day: ids for day, ids in self.plan_done.items() if day >= limit}
            self.save()
        return list(self.plan[key])

    def in_plan(self, today, task_id):
        return task_id in self.plan.get(today.isoformat(), [])

    def toggle_plan(self, today, task_id):
        ids = self.plan.setdefault(today.isoformat(), [])
        if task_id in ids:
            ids.remove(task_id)
        else:
            ids.append(task_id)
        self.save()
        return task_id in ids

    def plan_is_done(self, today, task_id):
        return task_id in self.plan_done.get(today.isoformat(), [])

    def toggle_plan_done(self, today, task_id):
        ids = self.plan_done.setdefault(today.isoformat(), [])
        if task_id in ids:
            ids.remove(task_id)
        else:
            ids.append(task_id)
        self.save()
        return task_id in ids

    # Timer ------------------------------------------------------------------

    def start_timer(self, task_id, name, project_id, now=None):
        now = now or datetime.datetime.now()
        self.timer = {
            "task_id": int(task_id),
            "name": name,
            "project_id": int(project_id or 0),
            "started": now.isoformat(timespec="seconds"),
        }
        self.save()

    def timer_seconds(self, now=None):
        if not self.timer:
            return 0
        now = now or datetime.datetime.now()
        try:
            started = datetime.datetime.fromisoformat(self.timer["started"])
        except (KeyError, ValueError):
            return 0
        return max(int((now - started).total_seconds()), 0)

    def clear_timer(self):
        self.timer = {}
        self.save()

    # Notifications already shown ------------------------------------------

    def should_notify(self, today, key):
        """True the first time `key` is asked for on this day."""
        stamp = today.isoformat()
        seen = self.notified.setdefault(stamp, [])
        if key in seen:
            return False
        seen.append(key)
        self.notified = {stamp: seen}
        self.save()
        return True
