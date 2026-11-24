"""Version of the app, and a check for a newer release on GitHub.

The check is one anonymous request to the public "latest release" address,
which redirects to the page of the newest version (the GitHub API is not used:
its anonymous quota is shared by everyone behind the same office address).
Nothing about the
user, the Odoo server or the tasks is sent. The app never downloads or
installs anything by itself: it only shows a notice with a link.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

VERSION = "6.0"
REPOSITORY = "dbaskhara/odoo-desktop"
DOWNLOAD_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
CHECK_EVERY_HOURS = 24


def parse(version):
    """'v4.10.1' -> (4, 10, 1). Unknown text -> ()."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(version or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(candidate, current=VERSION):
    new, old = parse(candidate), parse(current)
    if not new or not old:
        return False
    size = max(len(new), len(old))
    return new + (0,) * (size - len(new)) > old + (0,) * (size - len(old))


def fetch_latest(timeout=6):
    """Latest released version as text ('4.5'), or '' when it cannot be read."""
    request = Request(DOWNLOAD_PAGE, method="HEAD", headers={"User-Agent": f"OdooDesktop/{VERSION}"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https address
            landed = response.geturl()  # .../releases/tag/v4.5
    except (URLError, OSError, ValueError):
        return ""
    prefix = f"https://github.com/{REPOSITORY}/releases/tag/"
    tag = landed[len(prefix):] if landed.startswith(prefix) else ""
    return ".".join(str(part) for part in parse(tag))


def download(version, folder=None, timeout=90):
    """Download the exe of a release from the app's GitHub releases. Returns the file path."""
    if not parse(version):
        raise OSError("unknown version")
    number = ".".join(str(part) for part in parse(version))
    url = f"https://github.com/{REPOSITORY}/releases/download/v{number}/OdooDesktop.exe"
    target_dir = Path(folder or tempfile.gettempdir()) / "OdooDesktop-update"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "OdooDesktop.exe"
    request = Request(url, headers={"User-Agent": f"OdooDesktop/{VERSION}"})
    with urlopen(request, timeout=timeout) as response, open(target, "wb") as out:  # noqa: S310 - fixed https address
        shutil.copyfileobj(response, out, 1 << 16)
    with open(target, "rb") as check:
        head = check.read(2)
    if head != b"MZ" or target.stat().st_size < 1_000_000:
        target.unlink(missing_ok=True)
        raise OSError("the downloaded file is not the app")
    return str(target)


def restart_script(new, current):
    """A batch file that waits for the app to close, swaps the exe and starts it again."""
    return "\r\n".join([
        "@echo off",
        "set /a tries=0",
        ":again",
        "set /a tries+=1",
        "ping -n 2 127.0.0.1 >nul",
        f'copy /y "{new}" "{current}" >nul 2>&1',
        "if errorlevel 1 if %tries% lss 30 goto again",
        f'start "" "{current}"',
        f'del "{new}" >nul 2>&1',
        'del "%~f0" >nul 2>&1',
        "",
    ])  # fmt: skip


def replace_and_restart(new, current):
    script = Path(new).with_name("update.cmd")
    script.write_text(restart_script(new, current), encoding="ascii", errors="replace")
    subprocess.Popen(  # noqa: S603, S607
        ["cmd", "/c", str(script)], creationflags=0x08000000 | 0x00000008, close_fds=True,
    )  # CREATE_NO_WINDOW | DETACHED_PROCESS
