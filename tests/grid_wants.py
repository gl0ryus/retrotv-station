# Dry run on the Pi's real state: what the first refill would copy, and how much. Read-only: works on a copy of the
# manifest and starts no workers. Usage on the Pi: python3 -I grid_wants.py
import json, logging, os, shutil, sys, time
logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retrotv import station as rs

TMP = "/tmp/grid_wants"
os.makedirs(TMP, exist_ok=True)
for name in ("retrotv_manifest.json", "retrotv_series.json", "retrotv_aired.json", "retrotv_turns.json"):
    if os.path.exists(os.path.expanduser("~/" + name)):
        shutil.copy(os.path.expanduser("~/" + name), f"{TMP}/{name}")
import harness
harness.guard(rs)
rs.MANIFEST_FILE, rs.SERIES_FILE, rs.AIRED_FILE = f"{TMP}/retrotv_manifest.json", f"{TMP}/retrotv_series.json", f"{TMP}/retrotv_aired.json"
rs.TURNS_FILE = f"{TMP}/retrotv_turns.json"
rs.CacheManager._worker = rs.CacheManager._archive_worker = lambda self: None


def manifest(self):
    with open(rs.MANIFEST_FILE) as f:
        self.entries = json.load(f)
    for src, e in self.entries.items():
        self._describe(src, e)


rs.CacheManager._load_manifest = manifest
rs.CacheManager._save_manifest = lambda self: None
cache = rs.CacheManager(rs.MediaIndexer())
t0 = time.time()
todo = cache._wanted_shows()
print(f"usb mounted: {cache.usb}; {len(cache.wanted)} wanted over {rs.SCHEDULE_FILL_DAYS} days, {len(todo)} to copy; worked out in {time.time() - t0:.1f}s")
total, within = 0, 0
for src in todo:
    size = os.path.getsize(src) if os.path.exists(src) else 0
    total += size
    if total <= rs.USB_SHOWS_GB * rs.GB:
        within += 1
print(f"{total / 1e9:.0f} GB to copy; the first {within} fit in {rs.USB_SHOWS_GB} GB")
keep = sum(1 for s, e in cache.entries.items() if rs.pool_of(e) == "shows" and s in cache.wanted)
sweep = [e["size"] for s, e in cache.entries.items() if rs.pool_of(e) == "shows" and s not in cache.wanted]
print(f"shows already cached: {keep} wanted stay on the SD card; {len(sweep)} others ({sum(sweep) / 1e9:.0f} GB) get cleared")
for src in todo[:14]:
    print("  ", os.path.relpath(src, rs.MEDIA_ROOT)[:90])
print("group turns:", cache.turns)
