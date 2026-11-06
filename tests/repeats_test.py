# What keeps the station from repeating itself: a show counts as aired once it's on (so a restart can't put it back on),
# episodes and movies wait REPEAT_DAYS while there's something else to air, and a commercial airs once a broadcast day
# while others fit. On copies, no network: python3 -I tests/repeats_test.py
import datetime, json, os, random, sys, tempfile, threading, time, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
TMP = tempfile.mkdtemp(prefix="repeats_test")
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(TMP + "/rt.log", mode="w")   # not the station's own log
import harness
rs = harness.everything()
harness.offline()
harness.guard(rs)
rs.AIRED_FILE, rs.MANIFEST_FILE, rs.SERIES_FILE = TMP + "/aired.json", TMP + "/manifest.json", TMP + "/series.json"
rs.PROMO_AT = 0.0   # no promo lower third: Station.promo isn't under test

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


NOW = time.time()
DAY = 86400
ROOT = rs.MEDIA_ROOT


def entry(category="videos", duration=1320.0, aired_ago=None, plays=0):
    """A cached file's manifest entry; aired_ago: days since it last aired (None: never)."""
    last = NOW - aired_ago * DAY if aired_ago is not None else 0
    return {"file": f"x{random.random()}.mp4", "category": category, "size": 1, "duration": duration,
            "plays": plays if aired_ago is None else max(plays, 1), "last_played": last, "breaks": None,
            "series": None, "order": None, "cached": 0}


def cache_with(entries):
    """A CacheManager over these entries, without its threads or the station's files."""
    cm = object.__new__(rs.CacheManager)
    cm.lock = threading.RLock()
    cm.entries = dict(entries)
    cm.aired = {src: e["last_played"] for src, e in entries.items() if e["last_played"]}
    cm.reserved, cm.blocked, cm.series_pos = set(), set(), {}
    cm.refilling = True   # mark_played's refill check: not under test
    cm.wake, cm.archive_wake = threading.Event(), threading.Event()
    cm.indexer = types.SimpleNamespace(has_after=lambda key, pos: False, info=lambda src: (None, None))
    return cm


# --- A shuffle group (next_in_pool): what hasn't aired in a month, then the longest waiting ---
when = datetime.datetime(2026, 11, 6, 20, 0)
movies = {f"{ROOT}/movies/Film {n} (1990).mkv": entry("movies", 5400.0, ago) for n, ago in
          (("A", 10), ("B", 40), ("C", None))}
A, B, C = sorted(movies)
cm = cache_with(movies)
cm._episodes = lambda item, when: sorted(movies)
src, _, _ = cm.next_in_pool(["movies/"], when, set(), lambda s, e: True)
check(src in (B, C), f"a shuffle group passes over a movie from 10 days ago: {src}")
check(cm.next_in_pool(["movies/"], when, set(), lambda s, e: s == A) is None,
      "it doesn't take the rerun because only the rerun fits: the slot fills some other way")
check(cm.next_in_pool(["movies/"], when, {B, C}, lambda s, e: True)[0] == A,
      "with the fresh ones already in the lineup, the rerun goes")
recent = {A: movies[A], f"{ROOT}/movies/Film D (1990).mkv": entry("movies", 5400.0, 5)}
cm = cache_with(recent)
cm._episodes = lambda item, when: sorted(recent)
check(cm.next_in_pool(["movies/"], when, set(), lambda s, e: True)[0] == A,
      "when everything aired this month, the one that has waited longest goes")
cm = cache_with({A: entry("movies", 5400.0, 0.25)})
cm._episodes = lambda item, when: [A]
check(cm.next_in_pool(["movies/"], when, set(), lambda s, e: True) is None, "never within RERUN_HOURS")

# --- show_candidates: a series offers its next episode that hasn't aired this month ---
eps = {f"{ROOT}/shows/Test Show/Season 01/Test Show - S01E0{n}.mkv": entry(aired_ago=ago) for n, ago in
       ((1, 3), (2, None), (3, None))}
for n, (src, e) in enumerate(sorted(eps.items()), 1):
    e["series"], e["order"] = "test show", [1, n]
cm = cache_with(eps)   # S1E1 was cut off by a restart: it went on the air, its series' place never moved
picked = [src for src, _, _, in_order in cm.show_candidates("videos", when) if in_order]
check(picked == [sorted(eps)[1]], f"the series goes on with S1E2, not S1E1 again: {picked}")
picked = [src for src, _, _, in_order in cm.show_candidates("videos", when, rerun=True) if in_order]
check(picked == [sorted(eps)[0]], f"with rerun, the old 12 hour rule: S1E1 again: {picked}")


# --- The filler: grid first for short gaps, but never a long rerun while fresh shows are cached ---
def filler_pick(entries, gap_minutes, runs=25):
    """What the filler airs at 14:00 on a Friday with gap_minutes left in the slot, over a few runs."""
    start = datetime.datetime(2026, 11, 6, 14, 0)
    picks = set()
    for seed in range(runs):
        random.seed(seed)
        cm = cache_with(entries)
        cm.schedule_slot = lambda t: ({"name": "Test"}, start, start + datetime.timedelta(minutes=gap_minutes))
        lineup = object.__new__(rs.Lineup)
        lineup.cache = cm
        show = lineup._filler(start, None, set())
        picks.add((show.src, show.overrun) if show else None)
    return picks


def short_film(name, minutes, ago):
    return f"{ROOT}/Short Films/{name}.mp4", entry(duration=minutes * 60.0, aired_ago=ago)


fresh_short, stale_short = short_film("Fresh Short (1950)", 3, None), short_film("Stale Short (1951)", 3, 2)
fresh_long, stale_long = short_film("Fresh Long (1960)", 22, 45), short_film("Stale Long (1961)", 22, 2)
lineup = object.__new__(rs.Lineup)
gap_start = datetime.datetime(2026, 11, 6, 14, 0)
points = [gap_start + datetime.timedelta(minutes=10)]
check(not lineup._crosses((*fresh_short, 1.0, False), gap_start, points)
      and lineup._crosses((*fresh_long, 1.0, False), gap_start, points), "(a 3 minute short fits a 10 minute gap, 22 doesn't)")
got = filler_pick(dict([fresh_short, stale_short, fresh_long]), 10)
check(got == {(fresh_short[0], False)}, f"a fresh short that fits goes first: {got}")
got = filler_pick(dict([stale_short, fresh_long]), 10)
check(got == {(stale_short[0], False)}, f"no fresh short: a short rerun holds the grid, not a fresh show running over: {got}")
got = filler_pick(dict([stale_long, fresh_long]), 30)
check(got == {(fresh_long[0], False)}, f"a 22 minute rerun never beats one last aired over a month ago: {got}")
got = filler_pick(dict([stale_long, short_film("Long Fresh Feature (1970)", 50, 45)]), 30)
check(got == {(f"{ROOT}/Short Films/Long Fresh Feature (1970).mp4", True)},
      f"a fresh show running over beats a long rerun that fits: {got}")
got = filler_pick(dict([stale_long]), 30)
check(got == {(stale_long[0], False)}, f"with nothing fresh cached at all, a rerun beats dead air: {got}")
got = filler_pick(dict([short_film("Just Aired (1950)", 3, 0.2)]), 10)
check(got == {None}, f"never within RERUN_HOURS: {got}")

# --- A show counts as aired once its first act is on, before it's over ---
src = f"{ROOT}/shows/Test Show/Season 01/Test Show - S01E04.mkv"
e = entry()
e["breaks"] = [440.0, 880.0]
cm = cache_with({src: e})
st = object.__new__(rs.Station)
st.cache, st.anniversary, st.current_show, st.epoch = cm, None, None, 0
st.player = types.SimpleNamespace(epoch=0)
queued = []
st.emit = lambda item: queued.append(item) or len(queued) < 2   # the second act never makes it (a restart, say)
st._open = st.mid_break = st.end_break = st._close = lambda *a: None
st.snipe = lambda show, length=None: (None, [])
show = rs.Show(src, rs.cache_path(e), e)
show.plan_slot(datetime.datetime(2026, 11, 6, 15, 0))
st.air(show)
check(len(queued) == 2 and NOW - 5 < cm.aired.get(src, 0) <= time.time(), f"aired from its first act: {cm.aired}")
check(cm.entries[src]["plays"] == 0, "its plays (and its series' place) still wait for the end")
with open(rs.AIRED_FILE) as f:
    check(json.load(f).get(src) == cm.aired[src], "and it's saved, so a restart knows")


# --- Commercials: once a broadcast day while others fit ---
class AdCache:
    def __init__(self, ads):
        self.ads = ads

    def commercial_pool(self, hour):
        return list(self.ads.items())

    def reserve(self, src):
        pass


def ads_picked(ads, at, seconds=70.0, runs=40):
    st = object.__new__(rs.Station)
    st.cache = AdCache(ads)
    picked = set()
    for seed in range(runs):
        random.seed(seed)
        picked |= {item.label for item in st.fill(seconds, at, fine=False)}
    return picked


morning = datetime.datetime(2026, 11, 6, 10, 0)   # the broadcast day began at sign-on, 9 AM
signed_on = datetime.datetime(2026, 11, 6, rs.SIGN_ON_HOUR).timestamp()


def ad(name, aired_at):
    e = entry("commercials", 30.0)
    e["last_played"] = aired_at
    return f"{ROOT}/commercials/{name}.mp4", e


today = dict(ad(f"today {n}", signed_on + 1800 + n) for n in range(3))
before = dict([ad("last night 1 AM", signed_on - 8 * 3600), ad("yesterday", signed_on - DAY), ad("never", 0)])
got = ads_picked({**today, **before}, morning)
check(got and got <= {os.path.basename(s) for s in before},
      f"a break passes over the ads that already aired today (1 AM is still last night): {got}")
got = ads_picked(today, morning)
check(len(got) >= 2 and got <= {os.path.basename(s) for s in today}, f"when every ad has aired today, they air again: {got}")
late = datetime.datetime(2026, 11, 7, 1, 0)   # 1 AM: still the 6th's broadcast day
got = ads_picked({**today, **dict([ad("not since yesterday", signed_on - 3600)])}, late, seconds=40.0)
check(got == {"not since yesterday.mp4"}, f"after midnight the day still runs from the evening's sign-on: {got}")

print(f"repeat checks passed: {checks}")
