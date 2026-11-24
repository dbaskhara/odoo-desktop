"""Version of the app, and a check for a newer release on GitHub.

The check is one anonymous request to the public "latest release" address,
which redirects to the page of the newest version (the GitHub API is not used:
its anonymous quota is shared by everyone behind the same office address).
Nothing about the
user, the Odoo server or the tasks is sent. The app never downloads or
installs anything by itself: it only shows a notice with a link.
"""

import re
from urllib.error import URLError
from urllib.request import Request, urlopen

VERSION = "5.0"
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
