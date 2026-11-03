# The health page and bad files, without touching the live station's files. Runs on the Pi: python3 -I health_test.py
import glob, http.server, json, os, re, shutil, sys, tempfile, threading, time, urllib.request
ST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the station: this is its tests/ folder
sys.path.insert(0, ST)
from retrotv import station as rs
from retrotv import health
from retrotv import launches

TMP = tempfile.mkdtemp(prefix="health_test")
health.DATA_DIR = TMP
BAD_LOG = TMP + "/bad.json"
checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


# --- Every host the code calls is on the page
listed = set(health.BY_HOST)
for path in glob.glob(ST + "/retrotv/*.py"):
    if path.endswith("retrotv/health.py"):
        continue
    with open(path, encoding="utf-8") as f:
        for host in re.findall(r"https://([a-z0-9.-]+\.[a-z]+)", f.read()):
            if host != "archive.org" and host.endswith("archive.org"):
                continue
            check(host in listed or any(host.endswith("." + h) for h in listed), f"{host} ({os.path.basename(path)}) isn't on the health page")

# --- Before install(), nothing is recorded (tests and simulations)
health.record("nws", False, "x")
check(not health._state, "recorded before install")

# --- A local server for the watched urlopen. Private addresses aren't services, so it's reached by a made-up host
replies = {"/good": (200, b'{"a": 1}'), "/junk": (200, b"<html>not json"), "/gone": (500, b"oops")}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        code, body = replies["/" + self.path.rsplit("/", 1)[-1]]   # a proxy request has the whole URL
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]
health.install("station")
health.install("station")   # twice does no harm
check(health._service_of("https://ia801.us.archive.org/x") == "archive", "subdomain")
check(health._service_of("http://10.0.1.163/api") is None, "this network isn't a service")
check(health._service_of("http://localhost:80/api") is None, "nor localhost")
check(health._service_of("https://new.example.com/x") == "new.example.com", "a new host still shows up")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"127.0.0.1:{port}"}))
urllib.request.install_opener(opener)   # any host's plain http goes to the local server


def get(service_host, path):
    with urllib.request.urlopen(f"http://{service_host}{path}", timeout=5) as r:
        return json.load(r)


check(get("api.open-meteo.com", "/good") == {"a": 1}, "reply passes through")
s = health._state["weather"]
check(s["ok_at"] and not s["fail_at"] and not s["streak"], "success recorded")
try:
    get("api.open-meteo.com", "/junk")
    check(False, "junk should raise")
except ValueError:
    pass
check(s["fail_at"] >= s["ok_at"] and s["streak"] == 1, "a reply that can't be read is a failure")
check(health.judge(health.BY_ID["weather"], s, time.time(), True)[0] == "warn", "one failure is trouble")
for _ in range(2):
    try:
        get("api.open-meteo.com", "/gone")
    except OSError:
        pass
check(s["streak"] == 3 and health.judge(health.BY_ID["weather"], s, time.time(), True)[0] == "down", "three in a row is down")
check(sum(r["fails_today"] for r in health.service_rows(True) if r["id"] == "weather") == 3, "failures counted")
get("api.open-meteo.com", "/good")
check(health.judge(health.BY_ID["weather"], s, time.time(), True)[0] == "ok" and s["streak"] == 0, "recovers")
row = next(r for r in health.service_rows(True) if r["id"] == "weather")
check(row["status"] == "ok" and row["fails_today"] == 3, "still counts today's failures once recovered")

# --- Timed services: silence is a failure, but only when it should be running
now = time.time()
old = {"ok_at": now - 3600, "fail_at": 0, "streak": 0, "error": "", "fails": []}
check(health.judge(health.BY_ID["nws"], old, now, False)[0] == "down", "alerts silent for an hour, even off the air")
check(health.judge(health.BY_ID["launches"], {**old, "ok_at": now - 7200}, now, False)[0] == "ok", "launches only watched on the air")
check(health.judge(health.BY_ID["launches"], {**old, "ok_at": now - 7200}, now, True)[0] == "down", "launches silent on the air")
check(health.judge(health.BY_ID["stocks"], {**old, "ok_at": now - 90000}, now, True)[0] == "ok", "a card that hasn't come up isn't broken")
check(health.judge(health.BY_ID["stocks"], None, now, True)[0] == "idle", "never used")

# --- Keys never reach the page
cleaned = health._clean("fail https://x/?method=a&api_key=abc123&limit=3 sk-ant-api03-zzz")
check("abc123" not in cleaned and "zzz" not in cleaned and "limit=3" in cleaned, cleaned)

# --- Both processes' results are merged, the latest winning
health._flush(True)
check(os.path.exists(TMP + "/health_station.json"), "saved")
with open(TMP + "/health_news.json", "w") as f:
    json.dump({"claude": {"ok_at": now, "fail_at": 0, "streak": 0, "error": "", "fails": []},
               "weather": {"ok_at": 1, "fail_at": 2, "streak": 1, "error": "old", "fails": [now - 5]}}, f)
rows = {r["id"]: r for r in health.service_rows(True)}
check(rows["claude"]["status"] == "ok", "news job's service is seen")
check(rows["weather"]["status"] == "ok" and rows["weather"]["fails_today"] == 4, "latest wins, failures add up")
check(all(r["status"] in ("ok", "warn", "down", "idle") for r in rows.values()), "statuses")

# --- Bad files: a CacheManager on temporary folders
home, usb = TMP + "/home", TMP + "/usb"
rs.HOME_DIR, rs.CACHE_DIR, rs.USB_DIR, rs.USB_SHOWS_DIR = home, home + "/retrotv_cache", usb, usb + "/shows"
rs.REVIEW_DIRS = {usb: usb + "/_for_review", home: home + "/_for_review"}
rs.MANIFEST_FILE, rs.SERIES_FILE, rs.AIRED_FILE = TMP + "/m.json", TMP + "/s.json", TMP + "/a.json"
os.makedirs(rs.CACHE_DIR)
os.makedirs(rs.USB_SHOWS_DIR)


class FakeIndexer:
    def info(self, src):
        return None, None


def manager():
    c = rs.CacheManager.__new__(rs.CacheManager)
    c.lock = threading.RLock()
    c.entries, c.reserved, c.aired, c.series_pos = {}, set(), {}, {}
    c.refilling, c.last_refill = False, time.time()
    c.wake, c.archive_wake = threading.Event(), threading.Event()
    c.bad_files = health.BadFiles(BAD_LOG)
    c.blocked = c.bad_files.blocked()
    c.usb, c.indexer = True, FakeIndexer()
    c.unplayed_hours = lambda: 99
    c._music_short = lambda: False
    return c


def cached(c, name, folder=None, **extra):
    path = os.path.join(folder or rs.CACHE_DIR, name)
    with open(path, "wb") as f:
        f.write(b"garbage")
    src = f"/mnt/tower/.retrotv/videos/shows/X/{name}"
    c.entries[src] = {"file": name, "category": "videos", "size": 7, "duration": 60.0, "plays": 0, "last_played": 0, "cached": 0,
                      **({"dir": folder} if folder else {}), **extra}
    return src, path


c = manager()
# A corrupt file on the SD card
src, path = cached(c, "bad1.mkv")
c.set_aside(path, "Bad One", "unrecognized file format")
moved = rs.REVIEW_DIRS[home] + "/bad1.mkv"
check(not os.path.exists(path) and os.path.exists(moved), "moved, not deleted")
check(src not in c.entries and src in c.blocked, "off the air")
check(c._fetch_into_pool(src, "shows", force=True) is False, "not copied back")
rec = c.bad_files.rows()[0]
check(rec["status"] == "moved" and rec["moved_to"] == moved and rec["original"] == src and rec["label"] == "Bad One", rec)
# ...on the USB drive it stays on that drive
src2, path2 = cached(c, "bad2.mkv", rs.USB_SHOWS_DIR)
c.set_aside(path2, "Bad Two", "loading failed")
check(os.path.exists(usb + "/_for_review/bad2.mkv"), "same drive")
# The next act of the same file
c.set_aside(path2, "Bad Two act 2", "loading failed")
check(len(c.bad_files.rows()) == 2, "a later act adds nothing")
# mark_played after a set-aside does nothing and marks nothing as aired
c.mark_played(src)
check(src not in c.aired and os.path.exists(moved), "not counted as aired")
# Errors that aren't the file's fault
src3, path3 = cached(c, "fine.mkv")
for error in ("player restarted", "audio output initialization failed", "video output initialization failed", "unknown error"):
    c.set_aside(path3, "Fine", error)
check(os.path.exists(path3) and src3 in c.entries, "the file wasn't to blame")
c.set_aside("lavfi://color", "[card]", "loading failed")
c.set_aside("https://x/stream", "LIVE", "loading failed")
check(len(c.bad_files.rows()) == 2, "cards and streams aren't files")
# A file that's already gone: logged, nothing blocked, and it can come back
src4, path4 = cached(c, "gone.mkv")
os.remove(path4)
c.set_aside(path4, "Gone", "loading failed")
check(src4 not in c.entries and src4 not in c.blocked, "gone is not blocked")
check(next(r for r in c.bad_files.rows() if r["src"] == src4)["status"] == "missing", "logged as missing")
# An asset outside the cache: logged, never moved
os.makedirs(TMP + "/assets")
asset = TMP + "/assets/bumper.mp4"
open(asset, "w").close()
c.set_aside(asset, "bumper", "loading failed")
check(os.path.exists(asset) and any(r["status"] == "left" for r in c.bad_files.rows()), "assets stay")
# archive.org: set aside instead of the usual delete after airing, and another is fetched
src5, path5 = cached(c, "arch.mp4", archive="commercials", item="dial1966")
c.archive_wake.clear()
c.set_aside(path5, "Dial", "unrecognized file format")
check(os.path.exists(rs.REVIEW_DIRS[home] + "/arch.mp4") and c.archive_wake.is_set(), "archive file kept, another fetched")
check(next(r for r in c.bad_files.rows() if r["src"] == src5)["original"] == "https://archive.org/details/dial1966", "archive original")
# Three moved within the hour: the next ones stay where they are, and even an archive.org one survives its airing
src6, path6 = cached(c, "drive1.mkv", archive="commercials")
c.set_aside(path6, "Drive", "unrecognized file format")
check(os.path.exists(path6) and c.entries[src6].get("held") and src6 not in c.blocked, "held on the 4th")
c.mark_played(src6)
check(os.path.exists(path6), "a held file isn't deleted after airing")
c.set_aside(path6, "Drive", "unrecognized file format")
check(next(r for r in c.bad_files.rows() if r["src"] == src6)["count"] == 2, "repeats counted")
# The log survives a restart
c2 = manager()
check(c2.blocked == c.blocked and src in c2.blocked, "still blocked after a restart")
# Clearing: can air again; the file in _for_review stays
rid = next(r["id"] for r in c2.bad_files.rows() if r["src"] == src)
check(c2.clear_bad_file(rid) == src and src not in c2.blocked, "cleared")
check(os.path.exists(moved), "the file itself stays")
check(all(r["src"] != src for r in c2.bad_files.rows()), "off the page")
check(c2.clear_bad_file("no-such-id") is None, "an unknown id does nothing")
# A second opinion: garbage is flagged, a missing ffprobe is reported, not raised
rid = next(r["id"] for r in c2.bad_files.rows() if r["src"] == src2)
c2._probe_bad_file(rid, usb + "/_for_review/bad2.mkv")
check("can't read" in next(r for r in c2.bad_files.rows() if r["id"] == rid)["probe"], "ffprobe verdict")
# A blocked episode is skipped by the schedule
c3 = manager()
blocked_src = next(iter(c3.blocked))
c3._episodes = lambda item, when: [blocked_src, "/mnt/tower/.retrotv/videos/shows/X/next.mkv"]
c3._is_new = lambda s: True
import datetime
check(c3._item_queue("shows/X", datetime.datetime(2026, 11, 2, 20)) == ["/mnt/tower/.retrotv/videos/shows/X/next.mkv"], "blocked skipped")
# The player's end-of-file path hands failures over first, and carries on if that fails
p = rs.Player.__new__(rs.Player)
calls = []
p.on_failed = lambda url, label, error: calls.append(("failed", url, error))
item = rs.Item("/x.mkv", 10.0, "act", "X", None, lambda reason: calls.append(("end", reason)))
p._finish([(item, {"reason": "error", "file_error": "loading failed"})])
check(calls == [("failed", "/x.mkv", "loading failed"), ("end", "error")], calls)
calls.clear()
p._finish([(item, {"reason": "eof"})])
check(calls == [("end", "eof")], "a normal end isn't a failure")
calls.clear()
p.on_failed = lambda *a: 1 / 0
p._finish([(item, {"reason": "error", "file_error": "x"})])
check(calls == [("end", "error")], "on_end still runs")
# Simulations can't delete
for path in glob.glob(os.path.dirname(os.path.abspath(__file__)) + "/*.py"):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "rs.MANIFEST_FILE" in text and os.path.basename(path) != "health_test.py":
        check("harness.guard(rs)" in text or "_remove_file = staticmethod" in text, path)

# --- Background jobs: each finished run is a heartbeat; a stuck one shows as down
health._beats.clear()
check(health.jobs_row()["status"] == "idle", "no jobs yet")
runs = []
health.repeat("Quick", lambda: runs.append(1) or 0.05, 0.2)
health.repeat("Broken", lambda: 1 / 0, 0.05)
gate = threading.Event()
health.repeat("Stuck", lambda: gate.wait(), 1)
time.sleep(0.5)
check(len(runs) >= 3, f"repeats, at the pace step asks for: {len(runs)}")
check(time.time() - health._beats["Broken"][0] < 0.3, "an exception is logged and the job carries on")
row = health.jobs_row()
check(row["status"] == "ok" and "Quick" in row["message"], row)
row = health.jobs_row(now=time.time() + 3 + health.JOB_SLACK_SECONDS + 1)
check(row["status"] == "down" and "Stuck" in row["message"], row)
gate.set()
time.sleep(0.2)
check(health.jobs_row(now=time.time() + 1)["status"] == "ok", "and comes back once it finishes a run")
health._beats.clear()

shutil.rmtree(TMP)
print(f"health checks passed: {checks}")
