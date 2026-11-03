# The schedule page against a stand-in station: real library from the Pi's index, saves to /tmp, not the live file.
# On the Pi: python3 -I /tmp/schedule_test.py [PORT]   then open http://<pi>:PORT/schedule   Also runs validation checks.
import json, logging, os, sys, time
logging.basicConfig(level=logging.INFO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
from retrotv import remote
from retrotv import schedule
from retrotv import station as rs

schedule.SCHEDULE_FILE = "/tmp/schedule_test.json"
schedule.LENGTHS_FILE = "/tmp/schedule_test_lengths.json"
if os.path.exists(schedule.SCHEDULE_FILE):
    os.remove(schedule.SCHEDULE_FILE)
index = json.load(open(os.path.expanduser("~/retrotv_index.json")))["media_map"]


class Station:
    def remote_library(self):
        return {"items": items(), "rescanning": False, "live": False, "rules": {"kids_end": 21, "late_start": 22}}
    def remote_save_schedule(self, body):
        return schedule.save(body, {i["id"] for i in items()})
    def remote_rescan(self):
        return "stub"
    def remote_state(self): return {}
    def remote_guide(self): return {}
    def remote_search(self, q): return []


def items():
    return schedule.library(index, rs.MEDIA_ROOT, rs.rating, schedule.referenced_items(schedule.load()))


# Checks
t0 = time.time()
lib = items()
print(f"library: {len(lib)} items in {time.time() - t0:.1f}s")
ids = {i["id"] for i in lib}
doc = schedule.seed()
missing = sorted(schedule.referenced_items(doc) - ids)
print("seed items not in this index:", missing)
known = ids | set(missing)
good = {"groups": [{"id": "a", "name": "A", "items": ["g4/X-Play"], "mode": "in_order", "color": "g4"}],
        "slots": [{"day": 0, "start": 22, "end": 22.5, "group": "a"}],
        "blocks": [{"id": "b", "name": "Late", "days": [0, 1], "start": 22, "end": 26}]}
assert schedule.validate(good, known)["blocks"][0]["days"] == [0, 1]
schedule.validate(doc, known)


def bad(change, why):
    copy = json.loads(json.dumps(good))
    change(copy)
    try:
        schedule.validate(copy, known)
    except ValueError as e:
        print("  rejected:", why, "->", e)
        return
    raise AssertionError("accepted " + why)


bad(lambda d: d["slots"].append({"day": 0, "start": 22, "end": 23, "group": "a"}), "overlapping slots")
bad(lambda d: d["slots"][0].update(day=7), "day 7")
bad(lambda d: d["slots"][0].update(day=True), "day true")
bad(lambda d: d["slots"][0].update(start=22.25), "quarter hour")
bad(lambda d: d["slots"][0].update(end=27), "past sign-off")
bad(lambda d: d["slots"][0].update(group="nope"), "unknown group")
bad(lambda d: d["groups"][0].update(items=["../../etc/passwd"]), "path outside the library")
bad(lambda d: d["groups"][0].update(mode="x"), "bad mode")
bad(lambda d: d["groups"][0].update(overflow="no"), "overflow not on or off")
assert schedule.validate(good, known)["groups"][0]["overflow"] is True
assert schedule.validate({**good, "groups": [{**good["groups"][0], "overflow": False}]}, known)["groups"][0]["overflow"] is False
bad(lambda d: d["groups"].append(dict(d["groups"][0])), "repeated group id")
bad(lambda d: d["blocks"].append({"id": "c", "name": "X", "days": [1], "start": 23, "end": 24}), "overlapping blocks")
bad(lambda d: d["blocks"][0].update(days=[0, 0]), "repeated day")
bad(lambda d: d["blocks"][0].update(name=" "), "blank block name")
assert schedule.save({**doc, "version": 0}, known) == 1
try:
    schedule.save({**doc, "version": 0}, known)
    raise AssertionError("stale save accepted")
except schedule.Conflict as e:
    print("  conflict:", e)
assert schedule.load()["version"] == 1
os.remove(schedule.SCHEDULE_FILE)
print("checks passed")

if len(sys.argv) > 1:
    server = remote.start(Station(), int(sys.argv[1]))
    print("serving")
    time.sleep(3600)
