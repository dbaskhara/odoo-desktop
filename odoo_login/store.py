"""Per-account data kept on this computer only: pins, private notes, last-visit snapshot."""

import datetime
import hashlib
import json

from . import settings


class LocalStore:
    def __init__(self, session):
        key = f"{session.base_url}|{session.database}|{session.uid}".encode("utf-8")
        self.path = settings.data_dir() / f"data-{hashlib.sha1(key).hexdigest()[:12]}.json"
        self.snapshot = {}
        self.saved_at = ""
        self.pins = set()
        self.notes = {}
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
        if isinstance(data.get("notes"), dict):
            self.notes = {str(k): str(v) for k, v in data["notes"].items() if v}

    def save(self):
        data = {
            "saved_at": self.saved_at,
            "pins": sorted(self.pins),
            "notes": self.notes,
            "snapshot": self.snapshot,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

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
