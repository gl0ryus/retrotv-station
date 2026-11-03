# Read-only: the longest show that fits a slot, and whether the next episodes of the Mon/Thu G4 group fit theirs.
import json, logging, os, shutil, sys, datetime as dt
logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retrotv import station as rs

TMP = "/tmp/fit_check"
os.makedirs(TMP, exist_ok=True)
for name in ("retrotv_manifest.json", "retrotv_series.json", "retrotv_aired.json", "retrotv_turns.json"):
    if os.path.exists(os.path.expanduser("~/" + name)):
        shutil.copy(os.path.expanduser("~/" + name), f"{TMP}/{name}")
import harness
harness.guard(rs)
rs.MANIFEST_FILE, rs.SERIES_FILE, rs.AIRED_FILE = f"{TMP}/retrotv_manifest.json", f"{TMP}/retrotv_series.json", f"{TMP}/retrotv_aired.json"
def _copied_manifest(self):   # the real one also removes files its copy doesn't list
    with open(rs.MANIFEST_FILE) as f:
        self.entries = json.load(f)
    for src, e in self.entries.items():
        self._describe(src, e)


rs.CacheManager._load_manifest = _copied_manifest
rs.TURNS_FILE = f"{TMP}/retrotv_turns.json"
rs.CacheManager._worker = rs.CacheManager._archive_worker = lambda self: None
rs.CacheManager._save_manifest = lambda self: None
cache = rs.CacheManager(rs.MediaIndexer())
start = dt.datetime(2026, 11, 2, 18, 0)
closes = start + dt.timedelta(hours=1)
src = cache.PROBE if False else "/mnt/tower/.retrotv/videos/g4/Attack of the Show/x.mkv"
best = max((m for m in range(20, 90) if rs.slot_end(m * 60.0, "g4", src, start)[0] <= closes), default=None)
print("longest g4 show that fits a 60 min slot:", best, "min")
group = next(g for g in cache.schedule["groups"] if "AOTS" in g["name"])
print("group:", group["name"], group["items"], "turn:", cache.turns.get(group["id"]))
for item in group["items"]:
    queue = cache._item_queue(item, start)
    print(item, len(queue), "new")
    for s in queue[:4]:
        e = cache.entry(s)
        mins = (e["duration"] or 0) / 60 if e else None
        print("   ", os.path.basename(s)[:60], f"{mins:.0f} min" if mins else "not cached",
              "" if not e else ("fits" if rs.slot_end(e["duration"], e["category"], s, start)[0] <= closes else "DOESN'T FIT"))
for s in cache.entries:
    pass
long = [(e["duration"] / 60, os.path.basename(s)[:50]) for s, e in cache.entries.items()
        if e["category"] == "g4" and e["duration"] and rs.slot_end(e["duration"], "g4", s, start)[0] > closes]
print("cached g4 files that don't fit an hour:", sorted(long)[:8])
