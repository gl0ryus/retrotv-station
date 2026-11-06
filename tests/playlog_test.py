# The play log (retrotv/playlog.py) and its page and API, on copies. Runs on the Pi: python3 -I tests/playlog_test.py
import json, os, re, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
TMP = tempfile.mkdtemp(prefix="playlog_test")
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(TMP + "/rt.log", mode="w")   # not the station's own log
import harness
rs = harness.everything()
from retrotv import playlog, remote

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


rs.PLAY_LOG_FILE = TMP + "/plays.jsonl"
now = time.time()
playlog.record("act", "Blade II (2002) (act 1/6)", now - 3600)
playlog.record("commercial", "Toaster Strudel 1995.mp4", now - 3500)
playlog.record("act", "Blade II (2002) (act 2/6)", now - 3000)
plays = playlog.recent(24)
check([p["label"] for p in plays] == ["Blade II (2002) (act 1/6)", "Toaster Strudel 1995.mp4", "Blade II (2002) (act 2/6)"],
      f"plays come back oldest first: {plays}")
check(plays[0]["kind"] == "act" and abs(plays[0]["t"] - (now - 3600)) < 1, "with their kind and start time")
check(len(playlog.recent(0.9)) == 1, "only those in the time asked for")

with open(rs.PLAY_LOG_FILE, "a") as f:
    f.write('{"t": 17')   # a line cut short by a power cut
playlog.record("card", "[weather]")
check(len(playlog.recent(24)) == 4, "a broken line is skipped, and later plays still go in")

# Trimmed to PLAY_LOG_DAYS, once a day
with open(rs.PLAY_LOG_FILE, "a") as f:
    f.write(json.dumps({"t": now - 30 * 86400, "kind": "act", "label": "Old Show (act 1/2)"}) + "\n")
playlog._trimmed = 0.0
playlog.record("act", "New Show (act 1/2)")
with open(rs.PLAY_LOG_FILE) as f:
    text = f.read()
check("Old Show" not in text and "New Show" in text and "Blade II" in text, "plays older than two weeks are dropped")

rs.PLAY_LOG_FILE = "/proc/nowhere/plays.jsonl"
playlog.record("act", "Unwritable (act 1/2)")
check(playlog.recent(24) == [], "a log that can't be written or read is no trouble")
rs.PLAY_LOG_FILE = TMP + "/plays.jsonl"


# Over HTTP, as the page gets it
class Station:
    live = None


remote.start(Station(), 8096)
got = json.load(urllib.request.urlopen("http://127.0.0.1:8096/api/plays?hours=24", timeout=5))
check(len(got) == 5 and got[-1]["label"] == "New Show (act 1/2)", f"/api/plays: {len(got)}")
check(len(json.load(urllib.request.urlopen("http://127.0.0.1:8096/api/plays?hours=nonsense", timeout=5))) == 5, "a bad hours is the default")
page = urllib.request.urlopen("http://127.0.0.1:8096/log", timeout=5).read()
check(b"/api/plays" in page and b"Play log" in page, "the page is served")


# The page's grouping (its rows()), run in node: what's one airing and what's a repeat
def airings(plays):
    text = page.decode()
    script = (re.search(r"const isShow = .*?;\n", text).group(0) + re.search(r"const RESUME_SECONDS = .*?;\n", text).group(0)
              + re.search(r"function rows\(\) \{.*?\n\}\n", text, re.S).group(0)
              + "const plays = JSON.parse(process.argv[1]);\nconsole.log(JSON.stringify(rows()));\n")
    done = subprocess.run(["node", "-e", script, json.dumps(plays)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return [a for a in json.loads(done.stdout) if a["show"]]


t0 = now - 3 * 86400
day = [{"t": t0, "kind": "act", "label": "Blade II (2002) (act 1/3)"},
       {"t": t0 + 600, "kind": "commercial", "label": "Toaster Strudel 1995.mp4"},
       {"t": t0 + 700, "kind": "act", "label": "Blade II (2002) (act 2/3)"},
       {"t": t0 + 900, "kind": "card", "label": "[alert]"},
       {"t": t0 + 1000, "kind": "act", "label": "Blade II (2002) (act 2/3)"},   # picked up again after the alert
       {"t": t0 + 1600, "kind": "act", "label": "Blade II (2002) (act 3/3)"},
       {"t": t0 + 2400, "kind": "act", "label": "Gumbasia (1953)"},
       {"t": t0 + 2460, "kind": "card", "label": "[right back]"},
       {"t": t0 + 2500, "kind": "act", "label": "Gumbasia (1953)"},             # a show without breaks, picked up again
       {"t": t0 + 9000, "kind": "act", "label": "Gumbasia (1953)"},             # two hours on: aired again
       {"t": t0 + 86400, "kind": "act", "label": "Blade II (2002) (act 1/3)"}]  # the next day: aired again
got = airings(day)
blade = [a for a in got if a["name"] == "Blade II (2002)"]
check(len(blade) == 2 and blade[0]["parts"] == [1, 2, 3], f"a show picked up after an alert is one airing: {blade}")
check(all(len(a["others"]) == 1 for a in blade), "and the next day's airing is a repeat of it")
short = [a for a in got if a["name"] == "Gumbasia (1953)"]
check(len(short) == 2 and all(len(a["others"]) == 1 for a in short), f"the same for a show without breaks: {short}")
print(f"play log checks passed: {checks}")
