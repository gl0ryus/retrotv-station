"""
The make-believe channels on the remote's guide, and their listings.
"""
import datetime
import math
import random
import re
import threading
import time

from retrotv import cards
from retrotv.config import (
    CHANNELS, GUIDE_HOURS, LATE_NIGHT_HOUR, MAIN_CHANNEL, MEDIA_ROOT, SEASON_CHANNEL_NAMES, TALK_SERIES, td)
from retrotv.overlays import half_hour_floor
from retrotv.rules import broadcast_hour, holiday_season, holiday_tag, rating, sign_on_after


class Channels:
    """The make-believe channels on the remote's guide: a few hours of cached shows each, on the half-hour
    grid. Listings stay put once made; a listing the main channel has since claimed is swapped out."""
    def __init__(self, station):
        self.station = station
        self.lock = threading.Lock()
        self.rows = {}    # channel number -> [listing]
        self.recent = {}  # NAS path -> when it was last listed, so channels don't repeat too soon

    @staticmethod
    def listed():
        season = holiday_season(datetime.date.today())
        out = []
        for number, name, kind in CHANNELS:
            if kind == "season":
                if not season:
                    continue
                name = SEASON_CHANNEL_NAMES.get(season, name)
            out.append((number, name, kind))
        return out

    @staticmethod
    def fits(kind, src, e, season):
        cat, r, d = e["category"], rating(src), e["duration"]
        if cat == "music":
            return False   # music videos air in the main channel's music blocks
        if kind == "late":
            return r == "late"
        if r == "late" or d < 60:
            return False
        if kind == "kids":
            return cat == "videos" and r == "kids"
        if kind == "g4":
            return cat == "g4"
        if kind == "movies":
            return cat == "movies"
        if kind == "talk":
            return e.get("series") in TALK_SERIES
        if kind == "sitcoms":
            return (cat == "videos" and bool(e.get("series")) and r == "" and e.get("series") not in TALK_SERIES
                    and 15 * 60 <= d <= 35 * 60)
        if kind == "tapes":
            return cat == "videos" and bool(re.search(r"\bwoc\b|\bvhs\b|\btape\b|recording", src, re.I))
        if kind == "season":
            return bool(season) and holiday_tag(src) == season
        return False

    def _listing(self, kind, t, exclude):
        if kind == "late" and broadcast_hour(t)[1] < LATE_NIGHT_HOUR:
            on = t.replace(hour=LATE_NIGHT_HOUR, minute=0, second=0, microsecond=0)
            return {"src": None, "name": f"Signs on at {cards.clock(on)}", "episode": "", "start": t, "end": on}
        season = holiday_season(t.date())
        with self.station.cache.lock:
            pool = [(s, dict(e)) for s, e in self.station.cache.entries.items()
                    if s not in exclude and e["category"] != "commercials" and self.fits(kind, s, e, season)]
        if not pool:
            return {"src": None, "name": "No signal", "episode": "", "start": t, "end": t + td(minutes=30)}
        now = time.time()
        fresh = [x for x in pool if now - self.recent.get(x[0], 0) > 12 * 3600] or pool
        fresh.sort(key=lambda x: (x[1]["plays"], self.recent.get(x[0], 0)))
        src, e = random.choice(fresh[:8])
        self.recent[src] = now
        minutes = 15 if e["duration"] <= 14 * 60 else math.ceil(e["duration"] / 1800) * 30
        name, episode = cards.parse_title(src, e["category"], MEDIA_ROOT)
        return {"src": src, "name": name, "episode": episode, "start": t, "end": t + td(minutes=minutes),
                "duration": e["duration"]}

    def listing(self, number, src):
        """The listing for src on a channel, preferring the one on now."""
        now = datetime.datetime.now()
        with self.lock:
            rows = [x for x in self.rows.get(number, []) if x["src"] == src]
        return next((x for x in rows if x["start"] <= now < x["end"]), rows[0] if rows else None)

    def after(self, number, src):
        """The listing that follows src's on a channel."""
        with self.lock:
            row = self.rows.get(number, [])
            for i, x in enumerate(row[:-1]):
                if x["src"] == src:
                    return row[i + 1] if row[i + 1]["src"] else None
        return None

    def main_row(self, start):
        """The main channel's own lineup: the show on now and what's planned after it."""
        st = self.station
        shows = [st.interrupted or st.current_show or st.lineup.last] + st.lineup.snapshot()
        out, seen = [], set()
        for show in shows:
            if not show or not show.start or not show.end or show.end <= start or id(show) in seen:
                continue
            seen.add(id(show))
            if show.sign_off_before and show.sign_off_before < show.start:
                out.append({"src": None, "name": "Off the air", "episode": f"Back at {cards.clock(show.start)}",
                            "start": show.sign_off_before, "end": show.start})
            out.append({"src": show.src, "name": f"{show.name} Marathon" if show.marathon_intro else show.name,
                        "episode": show.episode, "start": show.start, "end": show.end, "duration": show.duration})
        if not out and not st.on_air:
            out.append({"src": None, "name": "Off the air", "episode": "", "start": start,
                        "end": sign_on_after(datetime.datetime.now())})
        return out

    def guide(self):
        now = datetime.datetime.now()
        start = half_hour_floor(now)
        end = start + td(hours=GUIDE_HOURS)
        main = self.main_row(start)
        claimed = {x["src"] for x in main if x["src"]}
        with self.lock:
            for number, name, kind in self.listed():
                row = [x for x in self.rows.get(number, []) if x["end"] > start]
                others = {x["src"] for n, r in self.rows.items() if n != number for x in r if x["src"]}
                # A listing that hasn't started yet but now airs on the main channel gets swapped out
                for i, x in enumerate(row):
                    if x["src"] in claimed and x["start"] > now:
                        swap = self._listing(kind, x["start"], claimed | others | {y["src"] for y in row})
                        if swap["src"] and swap["end"] <= x["end"]:
                            swap["end"] = x["end"]
                            row[i] = swap
                t = row[-1]["end"] if row else start
                while t < end:
                    x = self._listing(kind, t, claimed | others | {y["src"] for y in row})
                    row.append(x)
                    t = x["end"]
                self.rows[number] = row
            listed = [(number, name, self.rows[number]) for number, name, _ in self.listed()]
        with self.station.cache.lock:
            cached = set(self.station.cache.entries)

        def program(x):
            return {"id": x["src"], "name": x["name"], "episode": x["episode"], "time": cards.clock(x["start"]),
                    "start": x["start"].timestamp(), "end": x["end"].timestamp(),
                    "minutes": round(x.get("duration", 0) / 60), "playable": bool(x["src"] and x["src"] in cached)}
        channels = [{"number": MAIN_CHANNEL[0], "name": MAIN_CHANNEL[1], "main": True,
                     "programs": [program(x) for x in main if x["end"] > start]}]
        for number, name, row in listed:
            channels.append({"number": number, "name": name, "main": False, "programs": [program(x) for x in row]})
        return {"start": start.timestamp(), "end": end.timestamp(), "now": now.timestamp(), "channels": channels}


NO_STATIC = ("act", "id", "bumper", "offair", "poweroff", "dark", "signon")
QUIET_AFTER = ("offair", "poweroff", "dark", "signon", "anthem")
