"""Imported first by UI tests: keeps settings and local data out of the real %APPDATA%."""

import atexit
import os
import tempfile
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
atexit.register(_tmp.cleanup)
os.environ["ODOO_DESKTOP_SETTINGS"] = str(Path(_tmp.name) / "settings.json")
