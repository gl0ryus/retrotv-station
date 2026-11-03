import sys, os, json, shutil, logging, threading, time, urllib.request, faulthandler
faulthandler.dump_traceback_later(50, exit=True, file=open("/tmp/sim/stacks.txt", "w"))
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH("/tmp/sim/rt.log", mode="w")
import retro_station as rs, retrotv_cards as cards, retrotv_remote as remote
cards.CARDS_DIR = "/tmp/sim/cards"
shutil.copy(os.path.expanduser("~/retrotv_manifest.json"), "/tmp/sim/manifest.json")
import harness
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
g = api("/api/guide")
print("guide:", [(c["number"], c["name"], [(p["time"], p["name"][:16], p["playable"]) for p in c["programs"][:3]]) for c in g["channels"]])
ch = next(c for c in g["channels"] if c["number"] == 4)
p = next(p for p in ch["programs"] if p["playable"])
print("tune:", api("/api/tune", {"channel": 4, "id": p["id"]}))
time.sleep(8); show_state("on ch4")
s2 = api("/api/state"); print("channel:", s2["channel"], "now id matches:", (s2["now"] or {}).get("show", {}).get("id") == p["id"])
time.sleep(5)
print("main:", api("/api/main", {}))
time.sleep(10); show_state("back"); print("channel:", api("/api/state")["channel"])
player.proc.kill(); os._exit(0)
