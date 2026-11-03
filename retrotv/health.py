"""
What the station depends on and how each thing is doing, for the health page (http://retrotv.local/health).

Every web request goes through one watched urlopen, so a service added later shows up by itself, and a reply that can't
be read counts as a failure the same as no reply. The station and the news job each keep their own file in DATA_DIR, and
the page reads both. Nothing is recorded until install() is called, so tests and simulations leave no trace here.

BadFiles is the log of video files the player couldn't load, and of which are kept off the air until reviewed.
"""
import atexit
import copy
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from retrotv.jsonio import write_json
from retrotv.paths import DATA_DIR

logger = logging.getLogger("RetroTV")

BAD_FILES_FILE = os.path.join(DATA_DIR, "bad_files.json")
FLUSH_SECONDS = 300       # how often the results are saved when nothing has changed (the SD card thanks you)
FAILS_TO_DOWN = 3         # failures in a row before a service counts as down rather than having trouble
FAILS_KEPT = 200
DAY = 86400
ALERTS_PATH = "/alerts"
JOB_SLACK_SECONDS = 300   # beyond 3 of its waits, how long a background job's run may take before it counts as stuck
NAS_CHECK_SECONDS = 300   # the page only looks at the NAS this often, and only while someone's looking at the page
NAS_CHECK_TIMEOUT = 5
HOT_C = 80.0
MAX_BAD_FILES_KEPT = 500
THROTTLED_NOW = 0x5       # under-voltage, throttled (vcgencmd get_throttled)
THROTTLED_SINCE_BOOT = 0x50000


def _service(id, group, name, provider, hosts, used_for, when_down, every=None, on_air_only=False):
    return {"id": id, "group": group, "name": name, "provider": provider, "hosts": hosts, "used_for": used_for,
            "when_down": when_down, "every": every, "on_air_only": on_air_only}


# Everything the station calls out to. every: minutes within which it should have been tried, for the ones that run on a
# timer; the rest are only asked when their card comes up, so a long gap there means nothing.
SERVICES = [
    _service("nws", "Safety", "Emergency alerts", "National Weather Service", [],
             "Severe weather cut-ins and crawls", "No new alerts can reach the air", every=5),
    _service("weather", "Weather", "Weather forecast", "Open-Meteo", ["api.open-meteo.com"],
             "Weather card and G|TV Text, refreshed every 10 minutes", "The backup takes over after 3 failures in a row", every=30),
    _service("weather-backup", "Weather", "Backup weather forecast", "National Weather Service", ["api.weather.gov"],
             "Takes over when Open-Meteo fails, and is tested every 6 hours",
             "With both down, a saved forecast airs for up to 3 hours, labeled with its time, then no weather card", every=420),
    _service("scores", "Sports", "Score cards", "ESPN", ["site.api.espn.com"],
             "Score cards and the scores crawl", "Scores stop updating"),
    _service("logos", "Sports", "Team logos", "ESPN", ["espncdn.com"],
             "Logos on the score card, each downloaded once", "A new team shows its letters instead of a logo"),
    _service("stocks", "Stocks & crypto", "Stock prices", "Yahoo Finance", ["query1.finance.yahoo.com"],
             "S&P 500 on the markets card", "A saved chart for up to 4 days, then none"),
    _service("crypto", "Stocks & crypto", "Crypto prices", "CoinGecko", ["api.coingecko.com"],
             "Bitcoin and Ethereum on the markets card", "A saved chart for up to 6 hours, then none"),
    _service("facts", "Quips & headlines", "Useless facts", "Useless Facts", ["uselessfacts.jsph.pl"],
             "Text bumps", "Bumps use their other sources"),
    _service("dad", "Quips & headlines", "Dad jokes", "icanhazdadjoke", ["icanhazdadjoke.com"],
             "Text bumps", "Bumps use their other sources"),
    _service("jokes", "Quips & headlines", "Late-night jokes", "JokeAPI", ["v2.jokeapi.dev"],
             "Text bumps after dark", "Bumps use their other sources"),
    _service("news", "Quips & headlines", "Odd news headlines", "UPI", ["rss.upi.com"],
             "Headline bumps and G|TV Text", "No new headlines; recent ones keep airing", every=150),
    _service("claude", "Quips & headlines", "Quips, riffs and art", "Claude (Anthropic)", ["api.anthropic.com"],
             "Headline quips, show riffs, Art Break, Pop-Up Video facts, anniversary picks",
             "Nothing new gets written; what's saved keeps airing"),
    _service("launches", "Space", "Launch schedule", "Launch Library", ["ll.thespacedevs.com"],
             "Launch Watch, Who's in Space and live launch cut-ins", "Saved lists on the cards; live launches can't cut in",
             every=90, on_air_only=True),
    _service("streams", "Space", "Live launch video", "YouTube and X, through yt-dlp", [],
             "Live launch cut-ins", "The launch doesn't cut in"),
    _service("iss", "Space", "ISS position", "Where the ISS at", ["api.wheretheiss.at"],
             "The ISS map", "The map has no position"),
    _service("iss-orbit", "Space", "ISS orbit data", "CelesTrak", ["celestrak.org"],
             "Passes on the sky card", "The sky card loses its ISS passes"),
    _service("lastfm", "Music", "Listening charts", "Last.fm", ["ws.audioscrobbler.com"],
             "The Last.fm chart cards", "Saved charts for up to a day, then none"),
    _service("wikipedia", "Music", "Pop-up facts and movie posters", "Wikipedia", ["en.wikipedia.org"],
             "Pop-Up Video facts, and finding movie posters", "No facts for new music videos, no posters for new movies"),
    _service("art", "Programming", "Show posters", "TVmaze and Wikimedia", ["tvmaze.com", "upload.wikimedia.org"],
             "Posters on the NEXT, LATER and TONIGHT lower thirds, and the marathon ad's picture",
             "New shows go without; pictures already saved keep airing"),
    _service("history", "Quiz & history", "Today in history", "Wikimedia", ["api.wikimedia.org"],
             "Today in History cards", "A saved day for up to a day, then none"),
    _service("opentdb", "Quiz & history", "Quiz questions", "Open Trivia DB", ["opentdb.com"],
             "Pop Quiz", "The quiz keeps the questions it has"),
    _service("trivia", "Quiz & history", "Holiday quiz questions", "The Trivia API", ["the-trivia-api.com"],
             "Themed Pop Quiz questions", "Falls back on ordinary questions"),
    _service("archive", "Programming", "Old cartoons and commercials", "archive.org", ["archive.org"],
             "Vintage commercials, cartoons and movies", "Airs what's already downloaded, then the library"),
    _service("clips", "Behind the scenes", "Promo clips", "This Pi (ffmpeg)", [],
             "The moving pictures on the NEXT, LATER and TONIGHT lower thirds", "Those lower thirds use the show's poster, or no picture"),
    _service("news-job", "Behind the scenes", "Two-hourly writing job", "This Pi (retrotv-news)", [],
             "Runs the headline, riff, sky, anniversary, art and pop-up steps", "Those stop being refreshed", every=150),
]
BY_ID = {s["id"]: s for s in SERVICES}
BY_HOST = {host: s["id"] for s in SERVICES for host in s["hosts"]}

_lock = threading.Lock()
_state = {}
_file = None           # this process's results; None until install()
_flushed = 0.0


def _state_file(process):
    return os.path.join(DATA_DIR, f"health_{process}.json")


def install(process):
    """Starts recording, for a process named "station" or "news". Called once from its main()."""
    global _file, _state
    if _file:
        return
    _file = _state_file(process)
    _state = _read(_file)
    real = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        service = _service_of(getattr(url, "full_url", url))
        try:
            response = real(url, *args, **kwargs)
        except Exception as e:
            if service:
                record(service, False, e)
            raise
        return _Watched(response, service) if service else response
    urllib.request.urlopen = urlopen
    atexit.register(_flush, True)


def _service_of(url):
    """The service id of a URL, the host itself for one not listed yet, None for this network."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return None
    if host == "api.weather.gov" and parsed.path.startswith(ALERTS_PATH):
        return "nws"    # the same host as the backup forecast, and a separate question
    try:
        if ipaddress.ip_address(host).is_private:
            return None
    except ValueError:
        pass
    while host:
        if host in BY_HOST:
            return BY_HOST[host]
        host = host.partition(".")[2]
    return urllib.parse.urlparse(url).hostname.lower()


class _Watched:
    """A response whose result is known when its with-block ends: a reply that can't be parsed there is a failure."""

    def __init__(self, response, service):
        self._response, self._service = response, service

    def __getattr__(self, name):
        return getattr(self._response, name)

    def __iter__(self):
        return iter(self._response)

    def __enter__(self):
        self._response.__enter__()
        return self

    def __exit__(self, kind, error, trace):
        if error is None:
            record(self._service, True)
        elif isinstance(error, Exception):
            record(self._service, False, error)
        return self._response.__exit__(kind, error, trace)


def _clean(error):
    """An error as it can be shown on a page anyone at home can open: short, and with no keys in it."""
    text = re.sub(r"sk-ant-[\w-]+", "[key]", str(error))
    text = re.sub(r"(?i)\b(api_key|apikey|key|token)=[^&\s\"']+", r"\1=[key]", text)
    return text[:200]


def record(service, ok, error: object = ""):
    """Notes how a try at a service went."""
    if not _file:
        return
    now = time.time()
    with _lock:
        s = _state.setdefault(service, {"ok_at": 0, "fail_at": 0, "streak": 0, "error": "", "fails": []})
        was_failing = s["streak"] > 0
        if ok:
            s["ok_at"], s["streak"] = now, 0
            s.pop("fallback", None)
        else:
            s["fail_at"], s["streak"], s["error"] = now, s["streak"] + 1, _clean(error)
            s["fails"] = [t for t in s["fails"] if t > now - DAY][-FAILS_KEPT:] + [now]
        changed = was_failing != (s["streak"] > 0)
    _flush(changed)


def fallback(service, text):
    """While a failing service has something standing in for it, it counts as trouble rather than down. None: nothing is."""
    if not _file:
        return
    with _lock:
        s = _state.get(service)
        if not s or s.get("fallback") == text:
            return
        if text:
            s["fallback"] = text
        else:
            s.pop("fallback", None)
    _flush(True)


def _flush(force=False):
    global _flushed
    if not _file or not (force or time.time() - _flushed > FLUSH_SECONDS):
        return
    with _lock:
        _flushed = time.time()
        snapshot = copy.deepcopy(_state)
    try:
        write_json(_file, snapshot)
    except OSError as e:
        logger.warning(f"Could not save the health results: {e}")


def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _merged():
    """Both processes' results, the latest try winning where both have used a service."""
    merged = {}
    with _lock:
        mine = json.loads(json.dumps(_state))
    for process in ("station", "news"):
        path = _state_file(process)
        for service, s in (mine if path == _file else _read(path)).items():
            have = merged.get(service)
            if not have or max(s["ok_at"], s["fail_at"]) > max(have["ok_at"], have["fail_at"]):
                merged[service] = {**s, "fails": s["fails"] + (have["fails"] if have else [])}
            else:
                have["fails"] = have["fails"] + s["fails"]
    return merged


def judge(service, s, now, on_air):
    """(status, message) for a service: ok, warn (trouble), down, or idle (not used yet)."""
    tried = max(s["ok_at"], s["fail_at"]) if s else 0
    if not tried:
        return "idle", "Not used yet"
    every = service.get("every")
    if every and now - tried > every * 60 and (on_air or not service.get("on_air_only")):
        return "down", f"Should be checked every {every} minutes, and hasn't been"
    if s["fail_at"] > s["ok_at"]:
        if s.get("fallback"):
            return "warn", s["fallback"]
        return ("down" if s["streak"] >= FAILS_TO_DOWN else "warn"), s["error"]
    return "ok", ""


def service_rows(on_air, now=None):
    now = now or time.time()
    state = _merged()
    rows = []
    unlisted = [_service(k, "Not listed yet", k, k, [k], "Something new the station called", "Unknown") for k in state if k not in BY_ID]
    for service in SERVICES + unlisted:
        s = state.get(service["id"])
        status, message = judge(service, s, now, on_air)
        row = {k: service[k] for k in ("id", "group", "name", "provider", "used_for", "when_down")}
        row.update(status=status, message=message, ok_at=s["ok_at"] if s else 0, fail_at=s["fail_at"] if s else 0,
                   error=s["error"] if s else "", fails_today=sum(t > now - DAY for t in s["fails"]) if s else 0)
        rows.append(row)
    return rows


# --- Background jobs ---

_beats = {}   # job name -> (when it last finished a run, seconds it usually waits between runs)


def repeat(name, step, wait):
    """Runs step() over and over in a background thread. step returns the seconds until its next run, or None for
    `wait`; an exception is logged and waited out. Each finished run is a heartbeat: a job that stops finishing its
    runs (stuck on a network call or a disk) shows as down on the health page."""
    def loop():
        while True:
            after = None
            try:
                after = step()
            except Exception:
                logger.exception(f"{name} failed")
            _beats[name] = (time.time(), wait)
            time.sleep(wait if after is None else after)
    _beats[name] = (time.time(), wait)
    threading.Thread(target=loop, daemon=True, name=name).start()


def jobs_row(now=None):
    """The health page's row for the background jobs: down if one hasn't finished a run in a good while."""
    now = now or time.time()
    stuck = [name for name, (last, wait) in sorted(_beats.items()) if now - last > 3 * wait + JOB_SLACK_SECONDS]
    if stuck:
        return {"id": "jobs", "name": "Background jobs", "status": "down", "message": "Stuck: " + ", ".join(stuck)}
    return {"id": "jobs", "name": "Background jobs", "status": "ok" if _beats else "idle",
            "message": ", ".join(sorted(_beats)) if _beats else "None running"}


# --- The Pi itself ---

_nas = {"at": 0.0, "ok": None, "checking": False}


def _nas_row(root):
    def look():
        ok = False
        try:
            ok = os.path.isdir(root)
        finally:
            _nas.update(ok=ok, checking=False)
    if time.time() - _nas["at"] > NAS_CHECK_SECONDS and not _nas["checking"]:
        _nas.update(at=time.time(), checking=True)
        thread = threading.Thread(target=look, daemon=True)
        thread.start()
        thread.join(NAS_CHECK_TIMEOUT)
    if _nas["checking"]:
        return "down", "Not answering"
    return ("ok", "Reachable") if _nas["ok"] else ("down", "Not reachable")


def _disk_row(path, must_mount, min_free_gb):
    if must_mount and not os.path.ismount(path):
        return "down", "Not mounted"
    try:
        disk = os.statvfs(path)
    except OSError as e:
        return "down", _clean(e)
    free = disk.f_bavail * disk.f_frsize / 1e9
    total = disk.f_blocks * disk.f_frsize / 1e9
    return ("warn" if free < min_free_gb else "ok"), f"{free:.0f} GB free of {total:.0f} GB"


def _power_row():
    notes, status = [], "ok"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read()) / 1000
        notes.append(f"{temp:.0f}°C")
        if temp >= HOT_C:
            status = "warn"
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        flags = int(out.partition("=")[2].strip(), 16)
        if flags & THROTTLED_NOW:
            status = "down"
            notes.append("short of power or throttling right now")
        elif flags & THROTTLED_SINCE_BOOT:
            status = "warn"
            notes.append("short of power or throttled since it booted")
        else:
            notes.append("no power trouble")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return status, ", ".join(notes) or "Can't tell"


def report(on_air, on_air_row, nas_root, disks, min_free_gb, bad_files, more=()):
    """Everything the health page shows. disks: (id, name, path, must be mounted); more: further rows about the Pi."""
    box = [{"id": "on-air", "name": "On the air", "status": on_air_row[0], "message": on_air_row[1]}]
    status, message = _nas_row(nas_root)
    box.append({"id": "nas", "name": "NAS", "status": status, "message": message})
    for id, name, path, must_mount in disks:
        status, message = _disk_row(path, must_mount, min_free_gb)
        box.append({"id": id, "name": name, "status": status, "message": message})
    status, message = _power_row()
    box.append({"id": "power", "name": "Temperature and power", "status": status, "message": message})
    box += list(more)
    return {"time": time.time(), "box": box, "services": service_rows(on_air), "bad_files": bad_files.rows()}


# --- Video files that wouldn't load ---

class BadFiles:
    """A record per file: when, what the player said, what was done about it. status: moved (in _for_review and kept off
    the air until cleared), missing (already gone), held (not moved: probably the drive's fault), left (not ours to move)."""

    def __init__(self, path=BAD_FILES_FILE):
        self.path = path
        self.lock = threading.Lock()
        records = _read(path).get("files", []) if os.path.exists(path) else []
        self.records = [r for r in records if isinstance(r, dict) and r.get("id")]

    def _save(self):
        try:
            write_json(self.path, {"files": self.records}, indent=1)
        except OSError as e:
            logger.error(f"Could not save the bad file log: {e}")

    def add(self, src, label, error, status, original="", moved_to=""):
        """Logs a failure; the same file failing the same way again only raises its count."""
        now = time.time()
        with self.lock:
            record = next((r for r in self.records if r["src"] == src and r["status"] == status and not r.get("cleared")), None)
            if record:
                record.update(when=now, count=record["count"] + 1, error=error)
            else:
                record = {"id": secrets.token_hex(6), "src": src, "label": label, "error": error, "status": status, "when": now,
                          "count": 1, "original": original, "moved_to": moved_to, "probe": "", "cleared": False}
                self.records.append(record)
                self.records = self.records[-MAX_BAD_FILES_KEPT:]
            self._save()
            return record["id"]

    def note_probe(self, id, probe):
        with self.lock:
            for record in self.records:
                if record["id"] == id:
                    record["probe"] = probe
                    self._save()

    def clear(self, id):
        """Takes a file off the page: it can air again if the station copies it back. The file in _for_review stays."""
        with self.lock:
            for record in self.records:
                if record["id"] == id:
                    record["cleared"] = True
                    self._save()
                    return record["src"]
        return None

    def blocked(self):
        with self.lock:
            return {r["src"] for r in self.records if r["status"] == "moved" and not r.get("cleared")}

    def moved_since(self, when):
        with self.lock:
            return sum(1 for r in self.records if r["status"] == "moved" and r["when"] > when)

    def rows(self):
        with self.lock:
            return sorted(({k: v for k, v in r.items() if k != "cleared"} for r in self.records if not r.get("cleared")),
                          key=lambda r: -r["when"])
