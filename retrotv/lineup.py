"""
What airs when: Show and MusicBlock, and the Lineup that plans them from the weekly schedule.
"""
import datetime
import json
import random
import threading
import time

from retrotv import cards
from retrotv import screens
from retrotv import events
from retrotv import meta
from retrotv.config import (
    COUNTDOWN_BLOCKS, INTRO_SECONDS, LINEUP_FILE, LINEUP_HOURS, MAIN_CATEGORIES, MEDIA_ROOT, MID_BREAK_SECONDS,
    MIN_CUT_MINUTES, MIN_END_BREAK_SECONDS, MUSIC_MIN_FILL, NEW_YEAR_COUNTDOWN, SHORT_SHOW_MINUTES, SIGN_OFF_HOUR,
    SLOT_MINUTES, STATIC_SECONDS, TIGHT_END_SECONDS, logger, td)
from retrotv.jsonio import write_json
from retrotv.rules import (
    act_targets, artist_key, base_category, broadcast_date, broadcast_hour, cache_path, is_lottery, mix_at,
    music_credit, near_sign_off, next_boundary, off_air, on_grid, plan_breaks, sign_on_after, slot_end, tight_plan)

STRETCHED = object()   # what _pick returns when it filled the time left by stretching the show before
UNFILLED = object()    # ... and when the slot's block has nothing on the Pi for it yet, far enough ahead to wait
STRETCH_MAX = td(minutes=SLOT_MINUTES)   # the most time a show's breaks take on (Lineup._stretch)


class Show:
    def __init__(self, src, path, entry, in_order=False):
        self.src, self.path = src, path
        self.category = entry["category"]
        self.duration = entry["duration"] or 1800.0
        self.name, self.episode = cards.parse_title(src, self.category, MEDIA_ROOT)
        self.aired = meta.air_date(src)
        self.year = meta.era_year(src, self.category, entry.get("series"), entry.get("order"))
        self.series = entry.get("series")
        self.breaks = entry.get("breaks")  # None until analysed
        self.in_order = in_order           # aired as the next episode of its series
        self.requested = False             # asked for from the remote
        self.marathon = None               # the marathon block it airs in
        self.marathon_intro = False        # first show of that block
        self.start = self.end = None       # see Lineup.retime()
        self.mid_target = 0.0
        self.end_target = MIN_END_BREAK_SECONDS
        self.sign_off_before = None        # set when the station is off the air before this show
        self.window: tuple | None = None   # (opens, closes) of the schedule slot it was picked for
        self.overrun = False               # it may run past the slot's close
        self.keep_breaks = None            # how many mid-show breaks a tight slot leaves it, None for all
        self.cut = False                   # stops at self.duration, before the file ends (see cut_to)
        self.stretch = False               # its breaks fill its slot to the close: nothing else from it fits after

    def title(self):
        return f"{self.name} · {self.episode}" if self.episode else self.name

    def osd_title(self):
        """The title shown as the show starts, with its original air date on a second line."""
        if not self.aired:
            return "${media-title}"
        episode = self.episode
        short = self.aired.strftime("%b %-d, %Y")
        if episode.startswith(short):
            episode = episode[len(short):].lstrip(" ·")
        title = cards.esc(f"{self.name} · {episode}" if episode else self.name).replace("$", "$$")
        # Scaled relative to the title: OSD font sizes aren't in the cards' units
        return "${osd-ass-cc/0}" + title + "\\N{\\fscx72\\fscy72\\b0\\i1}Originally aired " + meta.long_date(self.aired)

    def even_breaks(self):
        """Fallback when the show airs before its breaks were analysed."""
        return [round(b, 2) for b in act_targets(self.duration, self.category, self.src)]

    def kept_breaks(self):
        """Where it breaks for commercials: all its breaks, or as many as a tight slot leaves room for, evenly spread."""
        breaks = self.breaks if self.breaks is not None else self.even_breaks()
        if self.cut:
            breaks = [b for b in breaks if b < self.duration - 120]
        if self.keep_breaks is None or len(breaks) <= self.keep_breaks:
            return breaks
        step = len(breaks) / (self.keep_breaks + 1)
        return [breaks[int(step * (i + 1))] for i in range(self.keep_breaks)]

    def cut_to(self, seconds):
        """Airs only the first `seconds` of the file."""
        self.duration, self.cut = seconds, True

    def plan_slot(self, start):
        self.start = start
        self.end, _, _ = slot_end(self.duration, self.category, self.src, start)
        mids = len(act_targets(self.duration, self.category, self.src))
        self.keep_breaks = None
        if self.window and self.end > self.window[1] and not off_air(self.window[1]):   # sign-off can wait for it
            keep, tight_end = tight_plan(self.duration, mids, start, self.window[1])
            if tight_end <= self.window[1]:   # it fits by the close on fewer, shorter breaks
                mids, self.end = keep, tight_end
                self.keep_breaks = mids
            # Otherwise it runs over either way: it keeps its breaks and ends on the grid like any show, so what follows
            # starts on the :00/:30 (Dan, 2026-11-06: a 42 min X-Play XL in the 10-10:30 PM slot put late night at 10:42)
        if self.stretch and self.window and self.window[1] > self.end:
            self.end = self.window[1]   # to its slot's close (Lineup._stretch)
        fill = (self.end - start).total_seconds() - self.duration - INTRO_SECONDS
        if mids and self.stretch:
            self.mid_target = max(MID_BREAK_SECONDS[0], fill / (mids + 1))   # the extra time shared evenly by its breaks
        elif mids:
            self.mid_target = min(MID_BREAK_SECONDS[1], max(MID_BREAK_SECONDS[0], fill / (mids + 1.5)))
        self.end_target = fill - mids * self.mid_target + INTRO_SECONDS


class MusicBlock:
    """A run of cached music videos that airs like a show: back to back, a break after each video in
    break_after, the next show on the grid after its closing break. Has what the lineup, cards, guide and
    breaks read from a Show; Station.air_music plays it."""
    category = "music"

    def __init__(self, name, minutes, videos, break_after, countdown=(), lead=None):
        self.src = f"music:{name}:{id(self)}"
        self.window: tuple | None = None   # as on a Show
        self.overrun = False
        self.srcs = [src for src, _ in videos]
        self.videos = [(src, cache_path(e), e["duration"]) for src, e in videos]
        self.name, self.episode, self.minutes = name, "", minutes
        self.countdown, self.lead = list(countdown), lead   # the Weekly Countdown it opens with, if any
        self.duration = sum(d for _, _, d in self.videos) + (
            screens.COUNTDOWN_SECONDS + STATIC_SECONDS if self.countdown else 0)
        self.break_after = set(break_after)
        years = sorted(y for y in (music_credit(src)[2] for src in self.srcs) if y)
        self.year = years[len(years) // 2] if years else None   # breaks lean to the videos' era
        self.path = self.aired = self.series = self.marathon = None
        self.breaks = []                   # nothing for the break finder to analyse
        self.in_order = self.requested = self.marathon_intro = False
        self.start = self.end = None
        self.mid_target = 0.0
        self.end_target = MIN_END_BREAK_SECONDS
        self.sign_off_before = None

    def title(self):
        return self.name

    def osd_title(self):
        return "${media-title}"

    def plan_slot(self, start):
        self.start = start
        mids = len(self.break_after)
        need = self.duration + mids * MID_BREAK_SECONDS[0] + MIN_END_BREAK_SECONDS + INTRO_SECONDS
        self.end = next_boundary(start + td(seconds=need))
        fill = (self.end - start).total_seconds() - self.duration - INTRO_SECONDS
        if mids:
            self.mid_target = min(MID_BREAK_SECONDS[1], max(MID_BREAK_SECONDS[0], fill / (mids + 1.5)))
        self.end_target = fill - mids * self.mid_target + INTRO_SECONDS


def _record(show):
    """What Lineup.restore needs to bring a planned show back (see Lineup.save)."""
    def when(t):
        return t.isoformat() if t else None
    base = {"start": when(show.start), "window": [when(t) for t in show.window] if show.window else None,
            "overrun": bool(show.overrun)}
    if isinstance(show, MusicBlock):
        return {**base, "music": show.name, "minutes": show.minutes, "srcs": show.srcs,
                "break_after": sorted(show.break_after), "countdown": show.countdown, "lead": show.lead}
    return {**base, "src": show.src, "in_order": show.in_order, "stretch": show.stretch, "requested": show.requested,
            "marathon": show.marathon, "cut": show.duration if show.cut else None}


class Lineup:
    """The broadcast day's shows, chosen ahead of time (to sign-off) so cards and the guide can say what's coming, and
    kept on disk so a restart carries on with them."""
    def __init__(self, cache):
        self.cache = cache
        self.shows = []
        self.last = None   # the show most recently handed to the station
        self.lock = threading.RLock()
        self.wake = threading.Event()
        threading.Thread(target=self._prep_worker, daemon=True).start()

    def retime(self, first_start):
        """Lays the shows out on the clock from first_start, moving any that would start off the air."""
        t = first_start
        with self.lock:
            prev = self.last
            for show in self.shows:
                show.sign_off_before = None
                if near_sign_off(t):
                    show.sign_off_before = t
                    t = sign_on_after(t)
                show.plan_slot(t)
                if show.marathon:
                    show.marathon_intro = not (prev and prev.marathon == show.marathon and not show.sign_off_before)
                t = show.end
                prev = show
        return t

    def plan(self, first_start, until=None):
        """Schedules shows to the end of the broadcast day (sign-off), and at least LINEUP_HOURS (or to `until`) from
        first_start, so the whole day is decided once and a restart can pick it back up (save, restore)."""
        end = self.retime(first_start)
        if self._prune():
            end = self.retime(first_start)
        sign_off = first_start.replace(hour=SIGN_OFF_HOUR, minute=0, second=0, microsecond=0)
        if sign_off <= first_start:
            sign_off += td(days=1)
        horizon = max(first_start + td(hours=LINEUP_HOURS), sign_off, until or sign_off)
        while end < horizon or not self.shows:
            start = sign_on_after(end) if near_sign_off(end) else end
            with self.lock:
                prev = self.shows[-1] if self.shows else self.last
            show = self._pick(start, prev, first_start + td(hours=LINEUP_HOURS))
            if show is STRETCHED:   # the show before now runs to its slot's close: on to the next slot
                end = self.retime(first_start)
                continue
            if show is UNFILLED or not show:
                break   # the rest waits: a later plan() goes on from here, once the refill has caught up
            with self.lock:
                self.shows.append(show)
            end = self.retime(first_start)
        self.save()
        self.wake.set()

    @staticmethod
    def _out_of_slot(show):
        window = getattr(show, "window", None)
        if not window or not show.start:
            return False
        return (show.start < window[0] - td(minutes=1) or show.start >= window[1]
                or (not getattr(show, "overrun", False) and show.end > window[1] and not off_air(window[1])))

    def _prune(self):
        """A skip, a request or a late start can push what's planned out of its slot: from the first show that
        no longer belongs there, the lineup gets picked again. True if it changed."""
        with self.lock:
            first = next((i for i, s in enumerate(self.shows) if self._out_of_slot(s)), None)
            gone = [s for s in self.shows[first:] if not getattr(s, "requested", False)] if first is not None else []
        for show in gone:
            self.drop(show)
        return bool(gone)

    def replan(self):
        """The schedule changed: everything not yet on the air gets picked again (and saved: see plan)."""
        for show in self.snapshot():
            if not getattr(show, "requested", False):
                self.drop(show)

    def _pick(self, start, prev=None, near=None):
        """The next show at `start`: the schedule's, or the old odds outside it. Within a slot, when nothing from its
        block fits, the show before is stretched (STRETCHED); failing that, past `near` the time is left for a later
        plan (UNFILLED), since what the block needs may not be on the Pi yet; before it, a show from outside fills in."""
        with self.lock:
            planned = {s.src for s in self.shows}
        on_air = self.cache.schedule_slot(start)
        if on_air:
            group, opens, closes = on_air
            show = self._from_slot(group, opens, closes, start, planned)
            if not show and self._stretch(prev, opens, closes, start):
                return STRETCHED
            if not show and near is not None and start > near:
                return UNFILLED
            if not show:
                show = self._filler(start, prev, planned)
                if show:
                    logger.info(f"Schedule: nothing from {group['name']} fits at {cards.clock(start)}, filling with {show.title()}")
            if show:
                show.window = (opens, closes)
                if not group.get("overflow", True) and isinstance(show, Show):
                    self._cut_to_slot(show, start)
            return show
        return self._filler(start, prev, planned)

    def _from_slot(self, group, opens, closes, start, planned):
        """The group's next show that ends by the time its slot closes, giving up commercials if it has to. A group
        that gets cut off (see _cut_to_slot) airs its next show however long it is. Otherwise, at the top of the slot
        a show whose turn it is airs anyway and runs over, and so does whatever runs over least if nothing else can
        air: it's what's scheduled, and whatever follows joins late. Later in the slot, nothing that fits: None (see
        _pick, which stretches the show before)."""
        points = self._fixed_points(start)
        at_top = start - opens < td(minutes=1)
        left = ((min(points) if points else closes) - start).total_seconds()
        if not group.get("overflow", True):
            if left < MIN_CUT_MINUTES * 60:
                return None
            return self._next_from_group(group, closes, start, planned, lambda src, e: True)

        def fits(src, e):
            duration = e["duration"] or 1800.0
            return (not self._crosses((src, e, 1.0, False), start, points)
                    or duration + INTRO_SECONDS + TIGHT_END_SECONDS <= left)
        show = self._next_from_group(group, closes, start, planned, fits, at_top)
        if not show and at_top:
            show = self._next_from_group(group, closes, start, planned, None)
        if show and getattr(show, "duration", 0) + INTRO_SECONDS + TIGHT_END_SECONDS > left:
            show.overrun = True
        return show

    def _stretch(self, prev, opens, closes, start):
        """Nothing from the slot's group fits the time left: the show before it, if it's from the same slot and not on
        the air yet, takes the time instead, its commercial breaks stretched to the slot's close, as a real station
        pads a program to its slot (Dan, 2026-11-06). Only for up to STRETCH_MAX: a longer gap isn't a program's
        padding. True if it did."""
        with self.lock:
            planned = prev is not None and any(s is prev for s in self.shows)
        if (not planned or not isinstance(prev, Show) or prev.cut or prev.window != (opens, closes)
                or start <= opens or start >= closes or closes - start > STRETCH_MAX):
            return False
        prev.stretch = True
        logger.info(f"Schedule: nothing from the slot fits at {cards.clock(start)}, "
                    f"{prev.title()} runs to {cards.clock(closes)} with longer breaks")
        return True

    def _cut_to_slot(self, show, start):
        """A show from a group that can't run over stops where the slot ends."""
        points = self._fixed_points(start)
        if not points:
            return
        room = (min(points) - start).total_seconds() - INTRO_SECONDS - TIGHT_END_SECONDS
        if show.duration > room:
            show.cut_to(max(60.0, room))
            show.overrun = False
            logger.info(f"Schedule: {show.title()} gets cut off at {cards.clock(min(points))}")

    def _next_from_group(self, group, closes, start, planned, fits, must_air=False):
        items = group["items"]
        if not items:
            return None
        if group["mode"] == "shuffle":
            found = self.cache.next_in_pool([i for i in items if not i.startswith("music:")], start, planned, fits)
            return self._take((found[0], found[1], 1.0, found[2])) if found else None
        turn = self.cache.turns.get(group["id"], 0)
        order = [(turn + i) % len(items) for i in range(len(items))]
        if fits is None:
            # Running over either way: whichever show's next episode runs over least
            options = [(i, self.cache.next_in_item(items[i], start, planned, None)) for i in order
                       if not items[i].startswith("music:")]
            options = [o for o in options if o[1]]
            if not options:
                return None
            at, found = min(options, key=lambda o: o[1][1]["duration"] or 1800.0)
            self.cache.set_turn(group["id"], (at + 1) % len(items))
            return self._group_show(group, items[at], found)
        for at in order:
            item = items[at]
            if item.startswith("music:"):
                show = self._music_for_slot(item[6:], closes, start)
            else:
                found = self.cache.next_in_item(item, start, planned, fits, must_air)
                show = self._group_show(group, item, found) if found else None
            if show:
                self.cache.set_turn(group["id"], (at + 1) % len(items))
                return show
        return None

    def _group_show(self, group, item, found):
        src, e, in_order = found
        show = self._take((src, e, 1.0, in_order))
        if item == "marathon:":
            show.marathon = group["name"]
        return show

    def _music_for_slot(self, name, closes, start):
        minutes = int((closes - start).total_seconds() // (SLOT_MINUTES * 60)) * SLOT_MINUTES
        if not on_grid(start) or minutes < SLOT_MINUTES:
            return None
        return self._build_music(name, minutes, start, start + td(minutes=minutes))

    def _filler(self, start, prev, planned):
        """When the schedule has nothing that fits (a few minutes left in a slot, or nothing cached yet): the old
        odds, leaving out G4, which keeps to its nights. Nothing that aired within REPEAT_DAYS (see show_candidates)
        while anything else can go: when nothing fresh fits, a short rerun (a cartoon or short film, under
        SHORT_SHOW_MINUTES) fills the gap rather than a fresh show running past the slot, and a longer rerun airs only
        when nothing fresh is cached at all."""
        mix = mix_at(*broadcast_hour(start))
        points = self._fixed_points(start)
        cands, fitting = self._filler_options(mix, start, planned, points)
        if not any(fitting.values()):
            again, again_fitting = self._filler_options(mix, start, planned, points, rerun=True)
            shorts = {c: [x for x in xs if (x[1]["duration"] or 1800.0) < SHORT_SHOW_MINUTES * 60]
                      for c, xs in again_fitting.items()}
            if not any(cands.values()):
                cands, fitting = again, again_fitting
            elif any(shorts.values()):
                fitting = shorts
        # The mix is airtime: each category's odds are divided by how long its pick would run
        weights = [w / self._mean_slot(fitting[c] or cands[c], start, prev) for c, w in mix.items()]
        category = random.choices(list(mix), weights=weights)[0]
        order = [category] + [c for c in MAIN_CATEGORIES if c != base_category(category)]
        if category == "kids":
            order.insert(1, "videos")
        fallback = None
        for c in order:
            if fitting[c]:
                return self._choose(fitting[c], start, prev)
            fallback = fallback or cands[c]
        if not fallback:
            return None
        show = self._choose(fallback, start, prev)
        show.overrun = True
        return show

    def _filler_options(self, mix, start, planned, points, rerun=False):
        """{category: what the filler could air} and {category: those of them that end before the slot does}, what
        has never aired first in each. rerun: see show_candidates."""
        cands, fitting = {}, {}
        for c in set(mix) | set(MAIN_CATEGORIES):
            options = [x for x in self.cache.show_candidates(c, start, planned, rerun=rerun) if x[1]["category"] != "g4"]
            fit = [x for x in options if not self._crosses(x, start, points)]
            cands[c] = [x for x in options if x[0] not in self.cache.aired] or options
            fitting[c] = [x for x in fit if x[0] not in self.cache.aired] or fit
        return cands, fitting

    def _fixed_points(self, start):
        """Times a show starting at `start` shouldn't run across: midnight on New Year's Eve (for the
        countdown) and the end of its slot on the schedule."""
        points = []
        if NEW_YEAR_COUNTDOWN and start.month == 12 and start.day == 31:
            points.append(datetime.datetime.combine(start.date() + td(days=1), datetime.time()))
        on_air = self.cache.schedule_slot(start)
        if on_air:
            points.append(on_air[2])
        return [p for p in points if p > start]

    @staticmethod
    def _crosses(cand, start, points):
        if not points:
            return False
        src, e, _, _ = cand
        end, _, _ = slot_end(e["duration"] or 1800.0, e["category"], src, start)
        return any(start < p < end for p in points)

    @staticmethod
    def _odds(cands, start, prev=None):
        """[(weight, slot seconds)] for each candidate: favours shows whose length fits the half-hour grid,
        and after a short segment, the next segment of the same series."""
        grid = on_grid(start)
        out = []
        for src, e, w, in_order in cands:
            end, extra, aligned = slot_end(e["duration"] or 1800.0, e["category"], src, start)
            if not grid:
                w *= 8.0 if aligned else 1.0  # off the grid: prefer a show that gets us back on it
            elif aligned and e["category"] != "movies":
                w /= 1.0 + extra / 300.0
            if prev and prev.duration < SHORT_SHOW_MINUTES * 60 and in_order and e.get("series") == prev.series:
                w *= 4.0
            out.append((w, (end - start).total_seconds()))
        return out

    def _mean_slot(self, cands, start, prev=None):
        """How long a pick from cands would run on average, in half hours (one when there's nothing to pick)."""
        odds = self._odds(cands, start, prev)
        total = sum(w for w, _ in odds)
        return sum(w * secs for w, secs in odds) / total / (SLOT_MINUTES * 60) if total else 1.0

    def _choose(self, cands, start, prev=None):
        """Weighted pick (see _odds)."""
        weights = [w for w, _ in self._odds(cands, start, prev)]
        return self._take(random.choices(cands, weights=weights)[0])

    def _take(self, cand):
        src, e, _, in_order = cand
        self.cache.reserve(src)
        return Show(src, cache_path(e), e, in_order)

    @staticmethod
    def _music_target(minutes, end):
        """(mid breaks, seconds of video) for a music block of `minutes` ending at `end`: the slot less
        its breaks, ~150 s per break, the closing one a little longer for the Up Next card."""
        mids = max(1, minutes // 20 - 1)
        target = minutes * 60 - INTRO_SECONDS - mids * 150 - 160
        if is_lottery(end):
            target -= events.LOTTERY_SECONDS + STATIC_SECONDS
        return mids, target

    def _build_music(self, name, minutes, start, end):
        """Picks videos to fill the slot (fewest plays first, one video per artist where it can) and puts
        a break about every 20 minutes at the video boundary nearest an even split."""
        with self.lock:
            planned = {s for show in self.shows for s in getattr(show, "srcs", ())}
        pool = [(s, e) for s, e in self.cache.music_pool(start) if s not in planned]
        mids, target = self._music_target(minutes, end)
        chart = []
        if name in COUNTDOWN_BLOCKS:
            try:
                chart = screens.weekly_chart()
            except Exception:
                logger.exception("Could not get the Weekly Countdown")
        if chart:
            target -= screens.COUNTDOWN_SECONDS + STATIC_SECONDS
        random.shuffle(pool)
        artist = {s: music_credit(s)[0] for s, _ in pool}
        picked, total, artists = [], 0.0, set()
        lead = None
        ranks = {artist_key(a): rank for rank, a, _, _ in chart}
        charted = [(ranks[artist_key(artist[s])], e["plays"], s, e) for s, e in pool
                   if artist[s] and artist_key(artist[s]) in ranks and e["duration"] <= target]
        if charted:   # the countdown's best placed artist with a video cached opens the block
            rank, _, s, e = min(charted, key=lambda c: (c[0], c[1]))
            pool.remove((s, e))
            picked.append((s, e))
            total += e["duration"]
            artists.add(artist[s])
            lead = (rank, *music_credit(s)[:2])
        while True:
            cands = [(s, e) for s, e in pool if e["duration"] <= target - total]
            if not cands:
                break
            weights = [(0.1 if artist[s] in artists else 1.0) / (1 + 2 * e["plays"]) for s, e in cands]
            s, e = random.choices(cands, weights=weights)[0]
            pool.remove((s, e))
            picked.append((s, e))
            total += e["duration"]
            artists.add(artist[s])
        if total < target * MUSIC_MIN_FILL:
            logger.info(f"Not enough music videos cached for {name} ({total / 60:.0f} of {target / 60:.0f} min)")
            return None
        cumulative, break_after = 0.0, []
        bounds = []
        for i, (_, e) in enumerate(picked[:-1]):
            cumulative += e["duration"]
            bounds.append((cumulative, i))
        for k in range(1, mids + 1):
            goal = total * k / (mids + 1)
            free = [(abs(c - goal), i) for c, i in bounds if i not in break_after]
            if free:
                break_after.append(min(free)[1])
        for s, _ in picked:
            self.cache.reserve(s)
        return MusicBlock(name, minutes, picked, sorted(break_after), chart, lead)

    def request(self, src):
        """Puts a cached show next in line (after any others asked for). Returns its planned start."""
        e = self.cache.entry(src)
        if not e:
            return None
        with self.lock:
            first_start = self.shows[0].start if self.shows else None
            existing = next((s for s in self.shows if s.src == src), None)
            if existing:
                self.shows.remove(existing)
                show = existing
            else:
                self.cache.reserve(src)
                show = Show(src, cache_path(e), e)
            show.requested, show.marathon, show.in_order = True, None, False
            at = next((i for i, s in enumerate(self.shows) if not s.requested), len(self.shows))
            self.shows.insert(at, show)
            if first_start:
                self.retime(first_start)
        logger.info(f"Requested from the remote: {show.title()}")
        self.save()
        self.wake.set()
        return show

    def next_show(self, start):
        self.plan(start)
        with self.lock:
            if not self.shows:
                return None
            self.last = self.shows.pop(0)
        self.save()
        return self.last

    # --- The day's plan on disk (LINEUP_FILE), so a restart picks it back up instead of choosing again ---

    def save(self):
        """Writes the show on now (lineup.last, with its start) and those planned after it. Never raises."""
        with self.lock:
            last, shows = self.last, list(self.shows)
        first = next((s.start for s in [last] + shows if s is not None and s.start), None)
        if first is None:
            return
        try:
            write_json(LINEUP_FILE, {"day": broadcast_date(first).isoformat(), "saved": time.time(),
                                     "last": _record(last) if last is not None else None,
                                     "shows": [_record(s) for s in shows]})
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"Could not save the day's plan: {e}")

    def restore(self, now):
        """Brings back today's saved plan: the shows to come, and the one that was on, returned (with its start) for
        the station to rejoin by the clock (Station.resume). None when there's no plan for today's broadcast day.
        A show whose file has left the Pi is left out; the rest keep their places on the clock."""
        try:
            with open(LINEUP_FILE) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(saved, dict) or saved.get("day") != broadcast_date(now).isoformat():
            return None
        last = self._revive(saved["last"]) if saved.get("last") else None
        shows = [s for s in (self._revive(r) for r in saved.get("shows") or []) if s is not None]
        if last is not None and last.start:
            last.plan_slot(last.start)
            first = last.end
        else:
            first = next((s.start for s in shows if s.start), now)
        with self.lock:
            self.shows, self.last = shows, last
        self.retime(first)
        logger.info(f"Picked the day's plan back up: {len(shows)} shows to come"
                    + (f", {last.title()} on since {cards.clock(last.start)}" if last is not None and last.start else ""))
        self.wake.set()
        return last

    def _revive(self, r):
        """A Show or MusicBlock from a saved record, reserved again in the cache, or None if its files are gone."""
        def when(t):
            return datetime.datetime.fromisoformat(t) if t else None
        try:
            show: Show | MusicBlock
            if "music" in r:
                videos = [(s, self.cache.entry(s)) for s in r["srcs"]]
                if not videos or any(e is None for _, e in videos):
                    return None
                show = MusicBlock(r["music"], r["minutes"], videos, r["break_after"],
                                  [tuple(c) for c in r.get("countdown") or []], tuple(r["lead"]) if r.get("lead") else None)
                for s, _ in videos:
                    self.cache.reserve(s)
            else:
                e = self.cache.entry(r["src"])
                if e is None:
                    return None
                show = Show(r["src"], cache_path(e), e, r.get("in_order", False))
                show.stretch, show.requested, show.marathon = bool(r.get("stretch")), r.get("requested", False), r.get("marathon")
                if r.get("cut"):
                    show.cut_to(r["cut"])
                self.cache.reserve(r["src"])
            window = r.get("window")
            show.window = (when(window[0]), when(window[1])) if window else None
            show.overrun = bool(r.get("overrun"))
            show.start = when(r.get("start"))
            return show
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Could not bring back a planned show ({e}): {r}")
            return None

    def upcoming(self, first_start):
        self.retime(first_start)
        with self.lock:
            return list(self.shows)

    def snapshot(self):
        with self.lock:
            return list(self.shows)

    def take(self, show):
        """Hands a planned show to the station out of turn (rejoining it partway through)."""
        with self.lock:
            if show in self.shows:
                self.shows.remove(show)
            self.last = show
        self.save()

    def drop(self, show):
        """Forgets a planned show whose slot went by while the set was on another channel."""
        with self.lock:
            if show in self.shows:
                self.shows.remove(show)
        for src in getattr(show, "srcs", [show.src]):
            self.cache.release(src)
        self.save()

    def drop_before(self, t):
        for show in self.snapshot():
            if show.start and show.start < t and not show.sign_off_before:
                self.drop(show)

    def _prep_worker(self):
        """Finds act breaks for planned shows in the background, long before they air."""
        while True:
            self.wake.wait(timeout=300)
            self.wake.clear()
            while True:
                with self.lock:
                    todo = next((s for s in self.shows if s.breaks is None), None)
                if not todo:
                    break
                t0 = time.time()
                breaks = plan_breaks(todo.path, todo.duration, todo.category, todo.src)
                todo.breaks = breaks
                self.cache.set_breaks(todo.src, breaks)
                if breaks:
                    marks = ", ".join(str(td(seconds=int(b))) for b in breaks)
                    logger.info(f"Act breaks for {todo.title()}: {marks} (found in {time.time() - t0:.0f}s)")
