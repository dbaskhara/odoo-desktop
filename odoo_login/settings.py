"""Remembers the server, database, email and theme between runs. Never the password."""

import json
import os
from pathlib import Path

LOGIN_FIELDS = ("url", "database", "email")
FIELDS = LOGIN_FIELDS + ("theme", "notify", "tray", "stay", "updates")
FLAG_DEFAULTS = {"notify": True, "tray": True, "stay": False, "updates": True}


def settings_path():
    override = os.environ.get("ODOO_DESKTOP_SETTINGS")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "OdooDesktop" / "settings.json"


def data_dir():
    """Folder for per-account data (pins, notes, last-visit snapshot)."""
    return settings_path().parent


def load():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in FIELDS if isinstance(data.get(key), str) and data[key]}


def _write(values):
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass


def save(values):
    """Merge the given fields into what is already saved."""
    current = load()
    for key in FIELDS:
        if key in values:
            current[key] = str(values[key]).strip()
    _write({key: value for key, value in current.items() if value})


def clear():
    """Forget the sign-in fields but keep the theme choice."""
    current = load()
    _write({key: value for key, value in current.items() if key not in LOGIN_FIELDS})


def flag(name):
    """On/off preference: notify (notifications), tray (keep running when closed), stay (stay signed in)."""
    value = load().get(name)
    if value in ("1", "0"):
        return value == "1"
    return FLAG_DEFAULTS[name]


def set_flag(name, value):
    save({name: "1" if value else "0"})
