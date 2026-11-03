# A week on the real saved schedule: the real Station and Lineup with a fake player, a virtual clock and a stand-in
# refill that "copies" what the schedule wants with lengths from the sampled medians (+/- a little).
# Usage on the Pi: python3 -I tests/sim_grid.py YYYY,MM,DD,HH,MM HOURS SEED [schedule.json]
# With FROZEN=<dir> it reads the station's state from a copy made by harness.freeze, so two runs can be compared
import sys, os, types, json, shutil, logging, collections, random as _r
import datetime as _dt, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SIM = "/tmp/sim"
start = _dt.datetime(*map(int, sys.argv[1].split(",")))
hours = float(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
if os.environ.get("FROZEN"):   # comparing runs: the same randomness, and no network, every time
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import harness
    harness.same_every_run(seed)


class Clock:
    t = start.timestamp()


clock = Clock()
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(SIM + "/sim_grid.log", mode="w")
time.time = lambda: clock.t


class FakeDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime.fromtimestamp(clock.t)


class FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return _dt.datetime.fromtimestamp(clock.t).date()


fake = types.SimpleNamespace(datetime=FakeDT, date=FakeDate, timedelta=_dt.timedelta, time=_dt.time)
import random
random.seed(seed)
import harness
rs = harness.everything()   # the station's modules as one: see harness.everything
from retrotv import cards
from retrotv import meta
from retrotv import events
from retrotv import screens
from retrotv import schedule
# The virtual clock, in the modules that plan and draw: the station's parts (once one file) and the screens
import importlib
for m in [importlib.import_module(f"retrotv.{part}") for part in harness.STATION_PARTS] + [meta, cards, events, screens]:
    if hasattr(m, "datetime"):
        m.datetime = fake
cards.CARDS_DIR = SIM + "/cards"
import harness
shutil.copy(harness.frozen("retrotv_manifest.json"), SIM + "/manifest.json")
for name in ("series.json", "grid_aired.json", "turns.json"):
    if os.path.exists(f"{SIM}/{name}"):
        os.remove(f"{SIM}/{name}")
harness.guard(rs)
if os.environ.get("FROZEN"):   # the same inputs as another run, to compare them: see harness.freeze
    harness.offline()
    harness.set_everywhere("INDEX_FILE", harness.frozen("retrotv_index.json"))
    harness.set_everywhere("SCHEDULE_FILE", harness.frozen("retrotv_schedule.json"))
    harness.set_everywhere("LENGTHS_FILE", harness.frozen("retrotv_data/folder_lengths.json"))
    data = SIM + "/frozen_data"   # a copy per run: the station writes what it has aired, which would change the next run
    shutil.rmtree(data, ignore_errors=True)
    shutil.copytree(harness.frozen("retrotv_data"), data, ignore=shutil.ignore_patterns("show_clips", "team_logos", "*.tmp"))
    harness.redirect(os.path.join(harness.HOME, "retrotv_data"), data)
rs.MANIFEST_FILE, rs.SERIES_FILE, rs.REMOTE_STATE_FILE = SIM + "/manifest.json", SIM + "/series.json", SIM + "/remote.json"
rs.AIRED_FILE, rs.TURNS_FILE = SIM + "/grid_aired.json", SIM + "/turns.json"
if len(sys.argv) > 4:   # an alternative schedule to try
    schedule.SCHEDULE_FILE = sys.argv[4]
rs.Lineup._prep_worker = lambda self: None
rs.CacheManager._worker = lambda self: None
rs.CacheManager._archive_worker = lambda self: None
rs.CacheManager._usb_ready = staticmethod(lambda: False)


def load(self):
    with open(rs.MANIFEST_FILE) as f:
        self.entries = {s: e for s, e in json.load(f).items() if e["category"] not in rs.MAIN_CATEGORIES}
    for src, e in self.entries.items():
        self._describe(src, e)


rs.CacheManager._load_manifest = load
rs.CacheManager._save_manifest = lambda self: None
for name, d in (("pop_quiz", 17.5), ("today_in_history", 24.0), ("launch_watch", 16.0), ("whos_in_space", 24.0)):
    setattr(screens, name, lambda *a, d=d, name=name: cards.Card("x", d, name))


class SimPlayer:
    AHEAD = 2

    def __init__(self):
        self.queue, self.log = [], []
        self.t_end = clock.t
        self.epoch, self.volume, self.muted = 0, 100, False

    def play(self, item, epoch=None):
        t0 = max(self.t_end, clock.t)
        if self.queue:
            clock.t = max(clock.t, self.queue[-1].t0)
        self._settle()
        item.t0, item.t1 = t0, t0 + item.duration + 0.03
        self.queue.append(item)
        self.t_end = item.t1
        self.log.append(item)
        return True

    def _settle(self):
        for it in list(self.queue):
            if it.t0 <= clock.t and it.started is None:
                it.started = it.t0
            if it.t1 <= clock.t:
                self.queue.remove(it)
                if it.on_end:
                    it.on_end("eof")

    def busy_until(self):
        return _dt.datetime.fromtimestamp(max(self.t_end, clock.t))

    def current(self):
        return next((i for i in self.queue if i.started), None)


idx = rs.MediaIndexer()
cache = rs.CacheManager(idx)
usual = schedule.lengths()
usual.update(json.loads(os.environ.get("USUAL", "{}")))   # e.g. USUAL='{"g4/The Screen Savers": 61}'
item_of = {}
_r.seed(seed)


def stand_in_refill(room_gb=105):
    """Adds what the schedule wants, as the real refill would until the drive is full."""
    wants = cache._wanted_shows()
    used = sum(e["size"] for e in cache.entries.values() if rs.pool_of(e) == "shows")
    for s, e in list(cache.entries.items()):
        if rs.pool_of(e) == "shows" and s not in cache.wanted and s not in cache.reserved:
            used -= e["size"]
            del cache.entries[s]
    added = 0
    for src in wants:
        if used > room_gb * rs.GB:
            break
        for item, files in cache.item_files.items():
            if item != "tape:" and item != "movies/" and src in files:
                break
        else:
            item = "tape:" if rs.is_tape(src) else "movies/"
        cat = idx.category_of(src) or "videos"
        minutes = usual.get(item, 25) * _r.uniform(0.75, 1.15)
        e = {"file": "fake.mkv", "category": cat, "size": int(minutes * 60 * 150_000), "duration": minutes * 60, "aspect": 1.33,
             "plays": 0, "last_played": 0, "cached": clock.t}
        cache._describe(src, e)
        cache.entries[src] = e
        used += e["size"]
        added += 1
    return added, len(wants)


print("first refill: added, wanted =", stand_in_refill())
lineup = rs.Lineup(cache)
player = SimPlayer()
st = rs.Station(cache, lineup, player)
end = start.timestamp() + hours * 3600
last_refill = clock.t
while clock.t < end:
    st.step()
    if clock.t - last_refill > 86400:
        stand_in_refill()
        last_refill = clock.t

# --- What aired ---
doc = cache.schedule
groups = {g["id"]: g for g in doc["groups"]}
files_of = {}
aired, seen_shows = [], set()
for it in player.log:
    show = getattr(it, "show", None)
    if show is None or id(show) in seen_shows or it.kind not in ("act", "video", "music"):
        continue
    seen_shows.add(id(show))
    aired.append((_dt.datetime.fromtimestamp(it.t0), show))

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
per_group, off_slot, filler, repeats, standards, overruns = collections.Counter(), [], [], [], [], []
srcs = collections.Counter()
xplay = collections.defaultdict(list)
g4_days = collections.defaultdict(collections.Counter)
minutes_by_group = collections.Counter()
for t, show in aired:
    on_air = cache.schedule_slot(t)
    name = show.title()
    if isinstance(show, rs.MusicBlock):
        per_group["music block"] += 1
        continue
    if on_air is None:
        off_slot.append((t, name))
        continue
    group, opens, closes = on_air
    src = show.src
    srcs[src] += 1
    mine = any(src in cache._episodes(i, t) for i in group["items"])
    minutes_by_group[group["name"]] += (show.end - show.start).total_seconds() / 60
    if mine:
        per_group[group["name"]] += 1
    elif not show.requested:
        filler.append((f"{t:%a %H:%M}", group["name"], name[:50], f"{(closes - t).total_seconds() / 60:.0f} min left"))
    if not rs.allowed(src, rs.broadcast_hour(t)[1]) and not show.requested:
        standards.append((f"{t:%a %H:%M}", name[:50]))
    if show.end > closes and not getattr(show, "overrun", False):
        overruns.append((f"{t:%a %H:%M}", name[:40], f"past {closes:%H:%M}"))
    if show.category == "g4":
        g4_days[f"{t:%a}"][os.path.relpath(src, rs.PATHS["g4"]).split(os.sep)[0]] += 1
    if src.startswith(rs.PATHS["g4"] + "/X-Play"):
        xplay[f"{t:%a %m-%d}"].append(f"{t:%H:%M:%S}")
print("=====SUMMARY=====")
print(f"{len(aired)} shows in {hours:.0f} h")
print("by group:", dict(per_group))
print("repeats:", [(os.path.basename(s)[:40], n) for s, n in srcs.items() if n > 1][:12])
print("filler:", len(filler), *filler[:30], sep="\n  ")
print("standards breaks:", standards[:10])
print("ran past their slot:", len(overruns), overruns[:10])
print("X-Play:", dict(xplay))
print("G4 by day:", {d: dict(c) for d, c in g4_days.items()})
late = [(t, s) for t, s in aired if not isinstance(s, rs.MusicBlock) and cache.schedule_slot(t) and (t - cache.schedule_slot(t)[1]).total_seconds() < 1800 and t.second + t.minute % 30 * 60 not in (0,)]
# A skip or a request shifts what's planned: nothing may stay outside its slot
t0 = _dt.datetime.fromtimestamp(clock.t)
first = rs.sign_on_after(t0).replace(hour=18, minute=0, second=0, microsecond=0)
lineup.plan(first, until=first + _dt.timedelta(hours=6))
before = [(s.title(), s.start) for s in lineup.snapshot()]
lineup.plan(first + _dt.timedelta(minutes=47), until=first + _dt.timedelta(hours=6))
bad = [s.title() for s in lineup.snapshot() if lineup._out_of_slot(s)]
print("after a 47 minute shift, out of their slots:", bad, "| planned:", len(before), "->", len(lineup.snapshot()))
lineup.replan()
print("replan leaves:", len(lineup.snapshot()), "planned; still reserved shows:", sum(1 for s in cache.reserved if s in cache.entries and rs.pool_of(cache.entries[s]) == "shows"))
sat = [(t, s) for t, s in aired if t.weekday() in (5, 6) and not isinstance(s, rs.MusicBlock)]
print("== weekend ==")
for t, s in sat[:44]:
    print(f"{t:%a %H:%M:%S}  {s.start:%H:%M}-{s.end:%H:%M} {s.title()[:64]}")
print("== cut off at the end of their slot ==")
for t, sh in aired:
    if getattr(sh, "cut", False):
        last = [it for it in player.log if getattr(it, "show", None) is sh and it.kind == "act"][-1]
        following = next((f"{nt:%H:%M:%S}" for nt, ns in aired if nt > t), "?")
        print(f"{t:%a %H:%M} slot closes {sh.window[1]:%H:%M}, stops at {sh.duration / 60:.0f} min, ends {sh.end:%H:%M:%S}, end={last.options.get('end')}, next at {following}: {sh.title()[:36]}")
print("== shows that gave up commercials ==")
for t, sh in aired:
    if isinstance(sh, rs.MusicBlock) or sh.keep_breaks is None:
        continue
    print(f"{t:%a %H:%M} {sh.start:%H:%M}-{sh.end:%H:%M:%S} {sh.duration / 60:.0f} min, kept {len(sh.kept_breaks())} of {len(sh.even_breaks())} breaks: {sh.title()[:44]}")
print("== g4 hour ==")
for i, (t, sh) in enumerate(aired):
    if not isinstance(sh, rs.MusicBlock) and sh.category == "g4" and t.hour == 18:
        nt, ns = aired[i + 1]
        print(f"{t:%a %H:%M} {sh.duration / 60:.0f} min {sh.title()[:40]} | next at {nt:%H:%M:%S}: {ns.title()[:30]}, ends {ns.end:%H:%M}")
late = [(f"{t:%a %H:%M:%S}", s.title()[:30]) for t, s in aired if not isinstance(s, rs.MusicBlock) and t.minute % 30 and t.minute % 30 <= 6 and cache.schedule_slot(t) and (t - cache.schedule_slot(t)[1]).total_seconds() < 400]
print("slots that started late:", late)
print("== first day ==")
first = aired[0][0].date()
for t, s in aired:
    if t.date() in (first, first + _dt.timedelta(days=1)):
        print(f"{t:%a %H:%M:%S}  {s.start:%H:%M}-{s.end:%H:%M} {s.title()[:64]}")
