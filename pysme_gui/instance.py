"""One interface in the browser at a time.

Without a window of its own, the program keeps running when its tab is closed.
Starting it again then shows the one that runs, with everything still in it,
instead of leaving another behind.
"""

import json
import os
import urllib.request
from urllib.parse import parse_qs, urlsplit

from . import envmanager


def _path():
    return os.path.join(envmanager.app_home(), "running.json")


def _recorded():
    try:
        with open(_path(), encoding="utf-8") as handle:
            return str(json.load(handle)["url"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def running():
    """The address of the interface that is running, or None."""
    url = _recorded()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.hostname != "127.0.0.1":
        return None
    token = parse_qs(parts.query).get("token", [""])[0]
    request = urllib.request.Request(
        f"{parts.scheme}://{parts.netloc}/api/state", headers={"X-PySME-Token": token}
    )
    # Never through a proxy: the address is this computer's.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return url if response.status == 200 else None
    except (OSError, ValueError):
        return None


def register(url):
    """Note that this interface runs, unless another one already does."""
    if running() is not None:
        return
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # The address holds the access token, so only this user may read the file;
    # on a shared computer it would otherwise open this session to the others.
    temp = f"{path}.{os.getpid()}.tmp"
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"url": url, "pid": os.getpid()}, stream)
    os.replace(temp, path)


def unregister(url):
    if _recorded() == url:
        try:
            os.remove(_path())
        except OSError:
            pass
