"""
Small pieces the station's modules share: the User-Agent it sends, fetching JSON, and saving JSON safely.
Standard library only, so the news job can use it too.
"""
import json
import os
import threading
import urllib.request

USER_AGENT = "GL0RYUS-RetroTV/1.0 (personal hobby TV station)"


def get_json(url, timeout=15, accept="application/json", check=None):
    """JSON from a URL. Raises on any failure (OSError for the network, ValueError for a reply that isn't JSON).
    check(data) runs before the request closes, so a reply it refuses counts against that service on the health page."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
        if check:
            check(data)
    return data


def write_json(path, data, indent=None, sync=False):
    """Writes through a temp file, so a crash or a reader at the wrong moment never sees half a file. Each thread
    gets its own temp file, so two writing at once can't clobber each other's. sync: on the disk before it's in place."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=indent)
            if sync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
