import sys, os, json, shutil, logging, threading, time, urllib.request
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH("/tmp/sim/rt.log", mode="w")
import harness
rs = harness.everything()
from retrotv import cards
from retrotv import remote
cards.CARDS_DIR = "/tmp/sim/cards"
shutil.copy(os.path.expanduser("~/retrotv_manifest.json"), "/tmp/sim/manifest.json")
harness.guard(rs)
rs.MANIFEST_FILE, rs.SERIES_FILE, rs.REMOTE_STATE_FILE = "/tmp/sim/manifest.json", "/tmp/sim/series.json", "/tmp/sim/remote.json"
rs.MPV_SOCKET = "/tmp/h-mpv.sock"
rs.PLAYER_CMD = ["/usr/bin/mpv", "--profile=retrotv", "--really-quiet", "--idle=yes", "--vo=null", "--ao=null", f"--input-ipc-server={rs.MPV_SOCKET}"]
rs.SPECIALS = {}
rs.Lineup._prep_worker = lambda self: None
rs.CacheManager._worker = lambda self: None
def load(self):
    with open(rs.MANIFEST_FILE) as f: self.entries = json.load(f)
    for src, e in self.entries.items(): self._describe(src, e)
rs.CacheManager._load_manifest = load
idx = rs.MediaIndexer(); cache = rs.CacheManager(idx); lineup = rs.Lineup(cache); player = rs.Player()
st = rs.Station(cache, lineup, player)
remote.start(st, 8098)
threading.Thread(target=st.run, daemon=True).start()
def api(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8098" + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))
def show_state(tag):
    s = api("/api/state")
    n = s["now"] or {}
    print(f"[{tag}] now={n.get('kind')} {n.get('label','')[:50]!r} show={(n.get('show') or {}).get('name')} vol={s['volume']} lineup={[ (r['start'], r['name'][:20], r['requested']) for r in s['lineup'][:3]]}")
time.sleep(15); show_state("start")
page = urllib.request.urlopen("http://127.0.0.1:8098/", timeout=5).read(); print("page bytes", len(page))
font = urllib.request.urlopen("http://127.0.0.1:8098/fonts/ArchivoBlack-Regular.ttf", timeout=5).read(); print("font bytes", len(font))
hits = api("/api/search?q=seinfeld"); print("search hits", len(hits), [h["episode"] for h in hits[:3]])
cached = [h for h in api("/api/search?q=scrubs") if h["cached"]]
if cached:
    print("play next:", api("/api/play", {"id": cached[0]["id"]}))
show_state("after request")
print(api("/api/volume", {"change": -10})); print(api("/api/volume", {"mute": True})); print(api("/api/volume", {"mute": True}))
time.sleep(5); print(api("/api/skip", {"show": False})); time.sleep(3); show_state("after skip item")
time.sleep(5); print(api("/api/skip", {"show": True})); time.sleep(12); show_state("after skip show")
time.sleep(20); show_state("later")
player.proc.kill(); os._exit(0)
