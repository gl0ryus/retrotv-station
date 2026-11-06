"""
The playout loop: what airs next, the breaks, the special screens, emergency alerts, live launches and the phone
remote's commands. main() starts it all.
"""
import datetime
import os
import random
import subprocess
import threading
import time

from retrotv import cards
from retrotv import screens
from retrotv import events
from retrotv import meta
from retrotv import remote
from retrotv import schedule
from retrotv import launches
from retrotv import health
from retrotv import live
from retrotv import weather as wx
from retrotv import art as posters
from retrotv import alerts
from retrotv import blurbs
from retrotv import scores
from retrotv.cache import CacheManager, MediaIndexer, ON_AIR, display_aspect, stretch_filter
from retrotv.channels import Channels, NO_STATIC, QUIET_AFTER
from retrotv.config import (
    ALERTS, ALERT_CHECK_SECONDS, ALERT_CRAWL_MINUTES, ALERT_TEST_HOURS, ANNIVERSARY_HOUR, ANTHEM, ARCHIVE_SHARE,
    ART_CHECK_MINUTES, BUMPER_FILE, CACHE_DIR, CLIP_GAP_SECONDS, CLIP_IDLE_SECONDS, CLIP_PRUNE_SECONDS,
    CLIP_RETRY_SECONDS, CRAWL_KINDS, ERA_WEIGHT, ERA_YEARS, FILLER_MAX_SECONDS, FRESH_SPECIALS, GLITCH_MINUTES,
    HOLIDAY_WEIGHT, INTRO_SECONDS, KIDS_END_HOUR, LATE_NIGHT_HOUR, LAUNCHES, LAUNCH_BEFORE_SECONDS,
    LAUNCH_CHECK_SECONDS, LAUNCH_QUEUE_NEXT_SECONDS, LAUNCH_START_GRACE_SECONDS, LAUNCH_WATCH_SECONDS, LIVE_STREAM,
    LOTTERY_HOUR, MAIN_CATEGORIES, MAIN_CHANNEL, MARATHON_SERIES, MEDIA_ROOT, MIN_END_BREAK_SECONDS, MIN_FREE_GB,
    MUSIC_TITLE_AT, MUSIC_TITLE_SECONDS, OFF_AIR_FILE, OFF_AIR_LOOP_MINUTES, OFF_AIR_SECONDS, PLAYER_MEMORY_MB, POPUP_VIDEO,
    PROMO_AT, PROMO_PANEL_SECONDS, REMOTE_PORT, SCORES, SCORES_CHECK_SECONDS, SCORES_IDLE_MINUTES,
    SCORES_MAX_SECONDS, SCORES_MINUTES, SCORES_TEAMS, SHOW_TITLE_SECONDS, SIGN_ON_HOUR, SLOT_MINUTES, SNIPE_SECONDS,
    SPECIALS, SPECIAL_GAP_MINUTES, STATIC_FILE, STATIC_SECONDS, STRETCH, TONIGHT_HOUR, UP_NEXT_SECONDS, USB_DIR,
    USB_SHOWS_DIR, WEATHER_LAT, WEATHER_LON, WEATHER_PLACE, WEATHER_TZ, logger, td)
from retrotv.lineup import Lineup, MusicBlock, Show
from retrotv.overlays import channel_osd, osd_msg, overlay_filter
from retrotv.player import Item, Player
from retrotv.rules import (
    allowed, blurb_level, broadcast_date, broadcast_hour, cache_path, commercial_year, heritage_month, holiday_look,
    holiday_season, holiday_tag, is_lottery, is_new_year, marathon_series, music_credit, near_sign_off,
    next_boundary, next_reminder, off_air, on_grid, popup_facts, rating, round_time, sign_on_after)


class Station:
    """Decides what airs next and hands it to the player, slightly ahead of time."""
    def __init__(self, cache, lineup, player):
        self.cache, self.lineup, self.player = cache, lineup, player
        self.live = None   # the watch page's stream, made only while someone watches, and only while mpv is drawing
        if LIVE_STREAM:
            self.live = live.Live(ready=player.showing)
            player.before_start = self.live.stop
        self.prev_kind = None
        self.last_id_hour = None
        self.good_morning = False
        self.on_air = False
        self.next_start = None      # when the next show should start (a :00/:30 boundary, or sign-on)
        self.current_show = None
        self.anniversary = None     # today's anniversary episode once it's queued, until its card airs
        self.anniversary_day = None
        self.skipping = False
        self.library_cache = (None, [])
        self.lengths_stamp = 0
        self.rescanning = False
        self.epoch = 0              # the player's skip counter when this step began
        self.detour = None          # ((number, name), src): a fake channel's show asked for from the remote
        self.rejoining = False      # go back to the main channel once the detour is queued
        self.interrupted = None     # the main channel's show that was on when we changed channels
        self.pending_channel = None # (number, name) to flash on screen with the next item
        self.channels = Channels(self)
        self.lottery_day = None
        self.next_glitch = self._glitch_time()
        # Staggered so the specials don't all come due in the first hour
        self.special_last = {k: time.time() - every * 60 * random.uniform(0.2, 0.8) for k, every in SPECIALS.items()}
        self.last_special = 0
        self.alerts = (alerts.Monitor(WEATHER_LAT, WEATHER_LON, ALERT_TEST_HOURS)
                       if ALERTS and WEATHER_LAT is not None else None)
        self.eas_pending = None     # an alert card to cut in with, set by the alert thread
        self.eas_follow = None      # what airs after that card: a live launch
        self.launches = launches.Launches()
        self.launch_live = None     # (launch, its Item, when it cut in)
        self.clips_tried = {}       # src -> when its clip was last tried, so a failure isn't retried at once
        self.clips_pruned = time.time()
        self.eas_resume = None      # (show, file position) to pick back up after it
        self.eas_from_detour = False
        self.eas_until = 0.0        # no second cut-in or crawl before this
        self.scores = scores.Scoreboard(SCORES_TEAMS) if SCORES else None
        self.scores_next = 0.0      # no score crawl before this

    @staticmethod
    def _glitch_time():
        if not GLITCH_MINUTES:
            return float("inf")
        return time.time() + random.uniform(*GLITCH_MINUTES) * 60

    def emit(self, item):
        """Plays an item, with a burst of static before it when the picture changes. False when a skip
        from the remote dropped it (everything after a skip is dropped until the next step)."""
        quiet_after = QUIET_AFTER + (("standby",) if item.kind == "standby" else ())
        # Worked out before the player's wait for a free slot (while the item before last is still playing),
        # so a fetch or probe never holds up the next file
        if item.refresh:
            self._refresh(item)
        self._stretch(item)
        if (STATIC_FILE and os.path.exists(STATIC_FILE) and self.prev_kind and self.prev_kind not in quiet_after
                and item.kind not in NO_STATIC):
            static = Item(STATIC_FILE, STATIC_SECONDS, "static", "static")
            self._stretch(static)
            self.player.play(static, self.epoch)
        if self.pending_channel:
            # After a channel change: the channel number in the corner, like a set's on-screen display
            item.options["osd-playing-msg"] = osd_msg(channel_osd(*self.pending_channel, item.show))
            item.options["osd-playing-msg-duration"] = 4000
            self.pending_channel = None
        self._maybe_glitch(item)
        ok = self.player.play(item, self.epoch)
        if ok:
            self.prev_kind = item.kind
        return ok

    def _refresh(self, item):
        """Remakes a card (the weather) with the latest data just before it's queued, since it was made when
        the break was planned, minutes earlier. Keeps the planned one if that fails or comes out a different
        length (the break around it was timed for it)."""
        try:
            card = item.refresh()
        except Exception:
            logger.exception(f"Could not refresh {item.label}")
            return
        if card and abs(card.duration - item.duration) < 0.05:
            item.url = card.url
        else:
            logger.info(f"Keeping the {item.kind} screen made when the break was planned")

    def _stretch(self, item):
        """A picture narrower than the screen fills it (see STRETCH). Cached files use the aspect saved when
        they were copied; anything else (the bumper, static) is probed once."""
        if not STRETCH or "://" in item.url or "vf-pre" in item.options:
            return
        if os.path.dirname(item.url) in (CACHE_DIR, USB_SHOWS_DIR):
            dar = self.cache.aspect(item.url)
        else:
            dar = display_aspect(item.url)
        pre = stretch_filter(dar)
        if pre:
            item.options["vf-pre"] = pre

    def _maybe_glitch(self, item):
        """Puts the hourly-ish reception glitch into this item if it comes due while it plays."""
        if item.overlay is None or item.duration < 6:
            return
        start = self.player.busy_until().timestamp()
        if self.next_glitch > start + item.duration - 3:
            return
        at = max(self.next_glitch, start + 2) - start
        self.next_glitch = self._glitch_time()
        kind = random.choice(("roll", "interference", "tear"))
        vf, af = overlay_filter(**item.overlay, glitch=(item.offset + at, kind))
        item.options["vf-append"] = vf
        if af:
            item.options["af-append"] = af
        logger.info(f"Glitch ({kind}) {at:.0f}s into {item.label}")

    @staticmethod
    def card(card, kind=None):
        return Item(card.url, card.duration, kind or card.kind, f"[{card.kind}]")

    def eta(self, items=()):
        """Estimated start of whatever is queued after the player's queue plus these items."""
        extra = sum(i.duration + (STATIC_SECONDS if i.kind not in NO_STATIC else 0) for i in items)
        return self.player.busy_until() + td(seconds=extra)

    def run(self):
        while True:
            try:
                self.step()
            except Exception:
                logger.exception("Station error, carrying on")
                time.sleep(5)

    def step(self):
        self.skipping = False
        self.epoch = self.player.epoch
        if self.eas_pending:
            self.air_alert()
            return
        if self.detour:
            self.play_detour()
            return
        if self.rejoining:
            self.rejoining = False
            if self.rejoin():
                return
        t = self.eta()
        start = t + td(seconds=INTRO_SECONDS)
        # A second or two late doesn't knock the schedule off the grid; the next break makes it up
        if self.next_start and self.next_start > start - td(seconds=10):
            start = self.next_start
        elif 0 < (start - next_boundary(start - td(seconds=20))).total_seconds() <= 20:
            start = next_boundary(start - td(seconds=20))  # e.g. a restart right on the hour
        self.next_start = None
        if near_sign_off(start):
            self.sign_off(t)
            return
        self._queue_anniversary(start)
        show = self.lineup.next_show(start)
        if not show:
            logger.warning("Nothing cached to play, standing by.")
            self.emit(self.card(cards.stand_by(holiday_look(start.date())), "standby"))
            return
        self.on_air = True
        ON_AIR.set()
        self.air(show)

    def _queue_anniversary(self, start):
        """From ANNIVERSARY_HOUR, once a day, puts the day's anniversary episode next in line (copying it from
        the NAS first if the refill didn't); air() opens it with the From the Vault card."""
        day = broadcast_date(start)
        hour = broadcast_hour(start)[1]
        if self.anniversary_day == day:
            return
        info = screens.anniversaries(day).get(day)
        if not info:
            return
        if self.cache.aired.get(info["src"], 0) > time.time() - 86400:   # already aired: a restart forgets that
            self.anniversary_day = day
            return
        self.cache.reserve(info["src"])   # held for its slot (after a restart too), never picked before it
        if hour < ANNIVERSARY_HOUR or not allowed(info["src"], hour):
            return
        self.anniversary_day, self.anniversary = day, info
        logger.info(f"Anniversary: {os.path.basename(info['src'])}, {info['years']} years ago tonight. "
                    + self.remote_play_next(info["src"]))

    def sign_off(self, t):
        signon = sign_on_after(t)
        lead = events.SIGN_ON_SECONDS + INTRO_SECONDS  # bars, the good-morning ID, then the first show
        if self.on_air:
            logger.info(f"Signing off until {cards.clock(signon)}.")
            anthem = events.anthem() if ANTHEM else None
            if anthem:
                self.emit(self.card(anthem, "anthem"))
            self.emit(self.card(cards.sign_off(signon, holiday_look(t.date()))))
            t = self.eta()
            until = signon - td(seconds=lead)
            if OFF_AIR_LOOP_MINUTES is not None:
                until = min(until, t + td(minutes=OFF_AIR_LOOP_MINUTES))
            loops = max(1, round((until - t).total_seconds() / OFF_AIR_SECONDS))
            options = {"loop-file": loops - 1} if loops > 1 else {}
            self.emit(Item(OFF_AIR_FILE, loops * OFF_AIR_SECONDS, "offair", "off the air", options))
            if OFF_AIR_LOOP_MINUTES is not None:
                self.emit(self.card(events.power_off(), "poweroff"))
            self.on_air = False
        ON_AIR.clear()  # only the off-air slate (in RAM) or a black screen from here on: copies can run flat out
        dark = (signon - td(seconds=lead) - self.eta()).total_seconds()
        if dark > 1:
            self.emit(self.card(events.dark(dark), "dark"))
        if (signon - self.eta()).total_seconds() >= lead - 2:
            self.emit(self.card(events.sign_on(), "signon"))
        self.good_morning = True
        self.next_start = signon

    def remaining_lottery(self, when):
        """True when tonight's drawing hasn't aired and it's past LOTTERY_HOUR (it missed its slot)."""
        return (LOTTERY_HOUR is not None and when.hour == LOTTERY_HOUR
                and self.lottery_day != broadcast_date(when))

    def lottery_card(self, when):
        self.lottery_day = broadcast_date(when)
        logger.info("Tonight's lottery drawing is on.")
        return self.card(events.lottery(when, holiday_look(when.date())), "lottery")

    # --- Breaks ---

    def _commercial_item(self, src, e):
        overlay = dict(duration=e["duration"], bug=False)
        vf, _ = overlay_filter(**overlay)
        item = Item(cache_path(e), e["duration"], "commercial", os.path.basename(src),
                    {"vf-append": vf} if vf else None, on_end=lambda reason, src=src: self.cache.mark_played(src))
        item.overlay = overlay
        return item

    def fill(self, seconds, when, year=None, fine=True):
        """Commercials adding up to about `seconds` (statics included), era-matched to `year`. With
        fine, the last stretch is topped up with bumps and short spots to land within a second or two."""
        hour = broadcast_hour(when)[1]
        season = holiday_season(when.date())
        pool = self.cache.commercial_pool(hour)
        random.shuffle(pool)

        pool = [(s, e) for s, e in pool if holiday_tag(s) in (None, season)]  # no Santa ads in July
        # archive.org's ads get about ARCHIVE_SHARE of the picks between them
        ours = sum(1 for _, e in pool if e.get("archive"))
        boost = ARCHIVE_SHARE / (1 - ARCHIVE_SHARE) * (len(pool) - ours) / ours if 0 < ours < len(pool) else 1.0

        def weight(src, e):
            w = (boost if e.get("archive") else 1.0) / (1 + e["plays"])
            if e["duration"] > 75:
                w *= 0.5  # trailers and long promos now and then, mostly ordinary spots
            y = commercial_year(src)
            if year and y:
                w *= ERA_WEIGHT if abs(y - year) <= ERA_YEARS else 0.25
            if season and holiday_tag(src) == season:
                w *= HOLIDAY_WEIGHT
            return w

        picked, used, left = [], set(), seconds
        while left > (FILLER_MAX_SECONDS + 10 if fine else 25):
            room = left - (8 if fine else 0)
            cands = [(s, e) for s, e in pool if s not in used and e["duration"] + STATIC_SECONDS <= room]
            if not cands:
                break
            s, e = random.choices(cands, weights=[weight(s, e) for s, e in cands])[0]
            picked.append((s, e))
            used.add(s)
            left -= e["duration"] + STATIC_SECONDS
        if fine and left > 1:
            fillers = [(s, e) for s, e in pool if s not in used and e["duration"] <= FILLER_MAX_SECONDS]
            best, best_left = [], left
            for _ in range(60):
                random.shuffle(fillers)
                pick, rest = [], left
                for s, e in fillers:
                    d = e["duration"] + STATIC_SECONDS
                    if d <= rest:
                        pick.append((s, e))
                        rest -= d
                        if rest < 0.8:
                            break
                if rest < best_left:
                    best, best_left = pick, rest
                if best_left < 0.8:
                    break
            picked += best
            left = best_left
        for s, _ in picked:
            self.cache.reserve(s)
        return [self._commercial_item(s, e) for s, e in picked]

    def mid_break(self, show):
        when = self.eta()
        season = holiday_look(when.date())
        right_back = self.card(cards.right_back(season or heritage_month(when.date())))
        back = self.card(cards.back_to(show.name, season))
        budget = show.mid_target - right_back.duration - back.duration - 2 * STATIC_SECONDS
        extra = []
        if self.remaining_lottery(when):
            extra.append(self.lottery_card(when))
        else:
            special = self.special(when + td(seconds=budget / 2), between_shows=False)
            if special:
                extra.append(special)
        budget -= sum(i.duration + STATIC_SECONDS for i in extra)
        items = self.fill(budget, when, show.year, fine=False)
        half = len(items) // 2
        items[half:half] = extra
        for item in [right_back] + items + [back]:
            if not self.emit(item):
                return

    def intro_seconds(self, show):
        return INTRO_SECONDS + ((events.MARATHON_SECONDS + STATIC_SECONDS) if show.marathon_intro else 0.0)

    def end_break(self, show):
        eta = self.eta()
        boundary = show.end
        gap = (boundary - eta).total_seconds()
        floor = INTRO_SECONDS + UP_NEXT_SECONDS[0] + STATIC_SECONDS
        if gap > show.end_target + 360:
            # The show finished early (skipped, or shorter than its file said): don't pad it out
            boundary = eta + td(seconds=MIN_END_BREAK_SECONDS)
            logger.info("Show ended early, starting the next one off the half-hour grid.")
        elif gap < floor:
            boundary = eta + td(seconds=floor)
        new_year = is_new_year(boundary)
        next_start = boundary + td(seconds=events.NEW_YEAR_SECONDS - events.NEW_YEAR_MIDNIGHT) if new_year else boundary
        self.lineup.plan(next_start)
        upcoming = self.lineup.upcoming(next_start)
        nxt = upcoming[0] if upcoming and not upcoming[0].sign_off_before else None
        if not nxt and not new_year and (boundary - eta).total_seconds() > 300:
            # Signing off next: no long padding out to the grid, just a short break before the anthem
            boundary = next_start = eta + td(seconds=MIN_END_BREAK_SECONDS)
        intro = self.intro_seconds(nxt) if nxt else 0.0
        # Everything up to the next show's intro (or, on New Year's Eve, up to the countdown)
        target = boundary - td(seconds=(events.NEW_YEAR_MIDNIGHT + STATIC_SECONDS) if new_year else intro)
        budget = (target - eta).total_seconds()

        fixed = []
        if is_lottery(boundary) or self.remaining_lottery(eta):
            fixed.append(self.lottery_card(boundary if is_lottery(boundary) else eta))
        special = self.special(eta, between_shows=True, first_start=next_start)
        if special:
            fixed.insert(0, special)
        if self.alerts and self.alerts.test_due(eta):
            test = self.alert_test_item()
            if test:
                fixed.insert(0, test)  # first to go if the break is short; it tries again next break
        up_next = (7.0 + STATIC_SECONDS) if nxt else 0.0  # the card's music bed is 7 s
        while fixed and budget - up_next - sum(i.duration + STATIC_SECONDS for i in fixed) < 20:
            dropped = fixed.pop(0)  # no room: the special waits for the next break
            if dropped.kind == "lottery":
                self.lottery_day = None
        items = self.fill(budget - up_next - sum(i.duration + STATIC_SECONDS for i in fixed), eta, show.year)
        items += fixed
        for item in items:
            if not self.emit(item):
                return
        if nxt:
            later = upcoming[1] if len(upcoming) > 1 and not upcoming[1].sign_off_before else None
            # The Up Next card stretches (or shrinks) to land the next show exactly on time
            d = (target - self.eta()).total_seconds() - STATIC_SECONDS
            d = min(UP_NEXT_SECONDS[1], max(UP_NEXT_SECONDS[0], d))
            card = cards.up_next((nxt.name, nxt.episode), (later.name, later.episode) if later else None,
                                 round_time(later.start) if later else None, duration=d,
                                 season=holiday_look(target.date()))
            if not self.emit(self.card(card)):
                return
        if new_year:
            self.emit(self.card(events.new_year(boundary.year), "newyear"))
        self.next_start = next_start

    def special(self, airs_at, between_shows, first_start=None):
        """The most overdue special screen, if any. Only one per break. Clocks show airs_at."""
        now = time.time()
        if now - self.last_special < SPECIAL_GAP_MINUTES * 60:
            return None
        due = [k for k, every in SPECIALS.items()
               if every and now - self.special_last[k] >= every * 60 and (between_shows or k != "guide")]
        due.sort(key=lambda k: (now - self.special_last[k]) / SPECIALS[k], reverse=True)
        for kind in due:
            try:
                card = self.build_special(kind, airs_at, first_start)
            except Exception:
                logger.exception(f"Could not make the {kind} screen")
                card = None
            if card:
                self.special_last[kind] = self.last_special = now
                item = self.card(card)
                if kind in FRESH_SPECIALS:
                    item.refresh = lambda kind=kind: self.build_special(kind, self.eta(), first_start)
                return item
            self.special_last[kind] = now - SPECIALS[kind] * 60 + 900  # no data right now, try again in 15 min
        return None

    def build_special(self, kind, airs_at, first_start):
        """A special screen for airs_at, or None without data."""
        season, month = holiday_look(airs_at.date()), heritage_month(airs_at.date())
        if kind == "guide":
            return cards.guide(self.guide_rows(first_start), airs_at, season)
        if kind == "weather":
            if WEATHER_LAT is None:
                return None
            data = wx.load()
            return cards.weather(data, WEATHER_PLACE, airs_at, season) if data else None
        if kind == "marathon":
            return self.marathon_ad(airs_at, season)
        if kind == "quiz":
            return screens.pop_quiz(season, month)
        if kind == "history":
            return screens.today_in_history(airs_at, season, month)
        if kind == "launch":
            return screens.launch_watch(airs_at, season)
        if kind == "space":
            return screens.whos_in_space(airs_at, season)
        if kind == "markets":
            return screens.markets(airs_at, season)
        if kind == "sky":
            return screens.tonight_sky(airs_at, season)
        if kind == "art":
            return screens.art_break(airs_at, season)
        if kind == "teletext":
            data = wx.load() if WEATHER_LAT is not None else None
            # The show on now and the lineup as planned: unlike guide_rows, this works in a mid-show break too
            listings = [(round_time(s.start), s.name, s.episode)
                        for s in [self.current_show] + self.lineup.snapshot() if s and s.start]
            return screens.teletext(airs_at, data, WEATHER_PLACE, listings, blurbs.headlines(), season)
        if kind == "reminder":
            upcoming = next_reminder(airs_at.date())
            return events.reminder(*upcoming, airs_at, season) if upcoming else None
        if kind == "blurb":
            show = self.current_show   # the show this break is in, which the bump may riff on
            return blurbs.blurb(blurb_level(airs_at), show.name if isinstance(show, Show) else None)
        return None

    def guide_rows(self, first_start):
        rows = []
        for show in self.lineup.upcoming(first_start):
            if show.sign_off_before:
                rows.append((round_time(show.sign_off_before), "OFF THE AIR",
                             f"Programming resumes at {cards.clock(show.start)}"))
            name = f"{show.name} Marathon" if show.marathon and show.marathon_intro else show.name
            rows.append((round_time(show.start), name, show.episode))
        return rows

    # --- Shows ---

    def air(self, show, join=None):
        """Airs a show in acts with breaks. join=(act, position) picks it up partway through (back from
        another channel), without the intro."""
        if isinstance(show, MusicBlock):
            return self.air_music(show, join)
        self.current_show = show
        t = show.start
        if not join:
            self._open(t)
        if show.marathon_intro and not join:
            key = marathon_series(broadcast_date(t), self.cache.indexer)
            season = holiday_look(t.date())
            self.emit(self.card(events.marathon(show.marathon, show.name, t, season)))
            logger.info(f"{show.marathon}: {show.name} ({key})")
        if self.anniversary and show.src == self.anniversary["src"] and not join:
            self.emit(self.card(screens.anniversary(self.anniversary, show.name, show.episode, t,
                                                    holiday_look(t.date()))))
            self.anniversary = None

        breaks = show.kept_breaks()
        bounds = [0.0] + breaks + [show.duration]
        first, position = join or (0, 0.0)
        logger.info(f"{'Joining' if join else 'Airing'} {show.title()} ({show.category}, {len(bounds) - 1} act(s), "
                    f"{cards.clock(show.start)}-{cards.clock(show.end)}, era {show.year or '?'}"
                    + (f", {td(seconds=int(position))} in)" if join else ")"))
        for i in range(first, len(bounds) - 1):
            last = i == len(bounds) - 2
            begin = position if (join and i == first) else bounds[i]
            options = {"force-media-title": show.title()}
            if begin:
                options["start"] = begin
            if not last or show.cut:
                options["end"] = bounds[i + 1]
            if i == first and SHOW_TITLE_SECONDS:
                options["osd-playing-msg"] = osd_msg(show.osd_title())
                options["osd-playing-msg-duration"] = SHOW_TITLE_SECONDS * 1000
            overlay: dict = dict(duration=show.duration, bug=True)
            if i == first and not join and PROMO_AT and bounds[i + 1] - begin > PROMO_AT + 3 * PROMO_PANEL_SECONDS + 15:
                overlay["promo"], overlay["art"] = self.promo(show, begin + PROMO_AT)
            if last and SNIPE_SECONDS and show.duration - begin > SNIPE_SECONDS + 15:
                overlay["snipe"], art = self.snipe(show)
                overlay["art"] = overlay.get("art", []) + art
            vf, _ = overlay_filter(**overlay)
            if vf:
                options["vf-append"] = vf
            on_end = (lambda reason, show=show: self.show_done(show)) if last else None
            label = f"{show.title()} (act {i + 1}/{len(bounds) - 1})" if len(bounds) > 2 else show.title()
            item = Item(show.path, bounds[i + 1] - begin, "act", label, options, on_end)
            item.show, item.offset, item.overlay = show, begin, overlay
            if not self.emit(item):
                break
            if last:
                self.end_break(show)
            else:
                self.mid_break(show)
            if self.player.epoch != self.epoch:
                break
        self._close(show)

    def _open(self, t):
        """What comes before a show starting at t: the station ID at the top of the hour (or the first
        show of the day, or off the grid once an hour), otherwise the bumper."""
        hour = (t.date(), t.hour)
        if self.good_morning or (t.minute == 0 if on_grid(t) else hour != self.last_id_hour):
            self.emit(self.card(events.station_id(t, self.good_morning, holiday_look(t.date()),
                                                  heritage_month(t.date()))))
            self.last_id_hour = hour
            self.good_morning = False
        elif BUMPER_FILE and os.path.exists(BUMPER_FILE):
            self.emit(Item(BUMPER_FILE, 4.5, "bumper", "bumper"))

    def _close(self, show):
        """After a show (or music block) has aired, or the remote cut into it."""
        if self.player.epoch != self.epoch:  # the remote cut in
            if self.detour or self.rejoining or self.eas_pending:
                # Changed channels (or an alert cut in): the show carries on "airing" without us, to rejoin later
                self.interrupted = show
                logger.info(f"{'Alert cut into' if self.eas_pending else 'Changed channels during'} {show.title()}")
            else:
                logger.info(f"Skipped {show.title()}")
                self.show_done(show)
            self.next_start = None
        self.current_show = None
        logger.info(f"--- {self.cache.stats_line()} ---")

    def air_music(self, block, join=None):
        """Airs a music block: the videos back to back with a break after each in block.break_after, each
        video's artist and song in a lower third. join=(video, position) picks it up partway through."""
        self.current_show = block
        if not join:
            self._open(block.start)
            if block.countdown:
                self.emit(self.card(screens.countdown(block.countdown, block.lead, block.start,
                                                      holiday_look(block.start.date()))))
        first, position = join or (0, 0.0)
        last = len(block.videos) - 1
        facts = popup_facts() if POPUP_VIDEO else {}
        logger.info(f"{'Joining' if join else 'Airing'} {block.name} ({len(block.videos)} videos, "
                    f"{block.duration / 60:.0f} min, {cards.clock(block.start)}-{cards.clock(block.end)}, "
                    f"era {block.year or '?'})")
        for i in range(first, len(block.videos)):
            src, path, duration = block.videos[i]
            artist, song, year = music_credit(src)
            begin = position if (join and i == first) else 0.0
            options = {"force-media-title": f"{artist} - {song}" if artist else song}
            if begin:
                options["start"] = begin
            overlay: dict = dict(duration=duration, bug=True)
            if duration - begin > MUSIC_TITLE_AT + 2 * MUSIC_TITLE_SECONDS + 5:
                panels = ([("Now", artist)] if artist else []) + [(str(year) if year else "Video", song)]
                overlay["promo"] = events.promo(panels, begin + MUSIC_TITLE_AT, MUSIC_TITLE_SECONDS)
            if i == last and SNIPE_SECONDS and duration - begin > SNIPE_SECONDS + 15:
                overlay["snipe"], overlay["art"] = self.snipe(block, duration)
            popped = facts.get(meta.popup_key(artist, song)) if artist else None
            if popped:
                overlay["popups"] = events.popups(popped["facts"], duration, begin)
            vf, _ = overlay_filter(**overlay)
            if vf:
                options["vf-append"] = vf

            def on_end(reason, src=src, closing=i == last):
                self.cache.mark_played(src)
                if closing:
                    self.show_done(block)
            label = f"{block.name}: {artist} - {song}" if artist else f"{block.name}: {song}"
            item = Item(path, duration - begin, "act", label, options, on_end)
            item.show, item.offset, item.overlay, item.video = block, begin, overlay, i
            logger.info(f"  {i + 1}/{len(block.videos)} {artist or '?'} - {song}" + (f" ({year})" if year else ""))
            if not self.emit(item):
                break
            if i == last:
                self.end_break(block)
            elif i in block.break_after:
                self.mid_break(block)
            if self.player.epoch != self.epoch:
                break
        self._close(block)

    # --- Other channels ---

    def play_detour(self):
        """Plays a fake channel's show (picked on the remote's guide) right away: joined in progress if
        its listing has started, like tuning in to a real channel, otherwise from the beginning."""
        (number, name), src = self.detour
        self.detour = None
        self.rejoining = True
        listing = self.channels.listing(number, src)
        now = datetime.datetime.now() + td(seconds=STATIC_SECONDS)
        e = self.cache.entry(src)
        if not e:
            return
        offset = max(0.0, (now - listing["start"]).total_seconds()) if listing else 0.0
        if offset > e["duration"] - 30:
            # The show itself is over (its slot is rounded up to the half hour): on to the channel's next one
            nxt = self.channels.after(number, src)
            if nxt and self.cache.entry(nxt["src"]):
                src, e, offset = nxt["src"], self.cache.entry(nxt["src"]), 0.0
            else:
                offset = 0.0
        show = Show(src, cache_path(e), e)
        show.start = now - td(seconds=offset)
        show.end = show.start + td(seconds=show.duration)
        overlay = dict(duration=show.duration, bug=True)
        vf, _ = overlay_filter(**overlay)
        options = {"force-media-title": show.title()}
        if offset:
            options["start"] = round(offset, 2)
        if vf:
            options["vf-append"] = vf
        item = Item(show.path, show.duration - offset, "detour", f"CH {number} {name}: {show.title()}", options,
                    on_end=lambda reason, src=src: self.cache.mark_played(src))
        item.show, item.overlay, item.channel, item.offset = show, overlay, (number, name), offset
        logger.info(f"Changed to channel {number} {name}: {show.title()}"
                    + (f", {td(seconds=int(offset))} in" if offset else ""))
        self.prev_kind = "card"  # so the channel change gets its burst of static
        self.pending_channel = (number, name)
        self.emit(item)

    def rejoin(self, flash=True):
        """Back on the main channel after a detour: picks up whatever it has on by now, the way switching
        back on a real set would. False leaves the step to carry on as usual."""
        self.pending_channel = MAIN_CHANNEL if flash else None
        t = self.eta() + td(seconds=STATIC_SECONDS)
        interrupted, self.interrupted = self.interrupted, None
        if off_air(t):
            # It signed off while we were away: back to the dark screen (no anthem)
            if interrupted:
                self.show_done(interrupted)
            self.lineup.drop_before(t)
            self.on_air = False
            return False
        current = None
        for show in ([interrupted] if interrupted else []) + self.lineup.snapshot():
            if show.start and t < show.end and not show.sign_off_before:
                current = show
                break
            if show is interrupted:
                self.show_done(show)  # it aired in full while we watched something else
            else:
                self.lineup.drop(show)
        if not current:
            return False
        self.on_air = True
        ON_AIR.set()
        if t < current.start:
            # Between shows: a break until it starts, then the step airs it on time
            self.next_start = current.start
            for item in self.fill((current.start - t).total_seconds() - INTRO_SECONDS, t, current.year):
                if not self.emit(item):
                    return True
            return False
        if current is not interrupted:
            self.lineup.take(current)
        point = self.join_point(current, t)
        if point is None:
            # In its closing break: the rest of the break, then the next show on time
            self.current_show = current
            self.end_break(current)
            self.current_show = None
        else:
            self.air(current, join=point)
        return True

    @staticmethod
    def join_point(show, t):
        """(act, position in the file) where a show is at clock time t by its slot plan, or None when it's
        in its closing break. For a music block: (video, position in that video)."""
        elapsed = (t - show.start).total_seconds()
        if isinstance(show, MusicBlock):
            clock_at = 0.0
            for i, (_, _, d) in enumerate(show.videos):
                if elapsed < clock_at + d:
                    position = max(0.0, elapsed - clock_at)
                    if d - position > 30:
                        return i, position
                    return (i + 1, 0.0) if i < len(show.videos) - 1 else None
                clock_at += d
                if i in show.break_after:
                    clock_at += show.mid_target
                    if elapsed < clock_at:
                        return i + 1, 0.0  # mid-break: back in for the next video
            return None
        breaks = show.kept_breaks()
        bounds = [0.0] + breaks + [show.duration]
        clock_at = 0.0
        for i in range(len(bounds) - 1):
            act = bounds[i + 1] - bounds[i]
            if elapsed < clock_at + act:
                position = bounds[i] + max(0.0, elapsed - clock_at)
                if bounds[i + 1] - position > 30:
                    return i, position
                # Only seconds left in this act: come back in for the next one instead
                return (i + 1, bounds[i + 1]) if i < len(bounds) - 2 else None
            clock_at += act
            if i < len(bounds) - 2:
                clock_at += show.mid_target
                if elapsed < clock_at:
                    return i + 1, bounds[i + 1]  # mid-break: back in for the next act
        return None

    def show_done(self, show):
        """Counts a show as played (once), moving its series along if it aired in order. A music block's
        videos count as they play; whatever it didn't get to is let go for the next block."""
        if not getattr(show, "done", False):
            show.done = True
            if isinstance(show, MusicBlock):
                for src in show.srcs:
                    self.cache.release(src)
            else:
                self.cache.mark_played(show.src, show.in_order)

    def snipe(self, show, length=None):
        """The lower third for the end of a show, naming the next one. length: the file it rides on, when
        that isn't the whole show (a music block's last video)."""
        upcoming = self.lineup.upcoming(show.end)
        if not upcoming or upcoming[0].sign_off_before:
            return None, []
        nxt = upcoming[0]
        name = "More " + nxt.name if nxt.marathon and nxt.name == show.name else nxt.name
        end = length or show.duration
        picture = self.picture_of(nxt, show.end)
        art = [(picture, end - SNIPE_SECONDS, end - 2.3, True)] if picture else []
        return events.snipe(name, end - SNIPE_SECONDS, end - 2), art

    def promo(self, show, at):
        """The lower third early in a show: NEXT, then LATER (the next different show) and TONIGHT
        (what's planned for TONIGHT_HOUR) with their times, and the posters that go with them. None, [] when the
        station signs off next."""
        tonight = datetime.datetime.combine(show.start.date(), datetime.time(TONIGHT_HOUR))
        if not (SIGN_ON_HOUR <= show.start.hour and show.end < tonight):
            tonight = None
        if tonight:
            self.lineup.plan(show.end, until=tonight + td(minutes=SLOT_MINUTES))
        upcoming = [s for s in self.lineup.upcoming(show.end)]
        if not upcoming or upcoming[0].sign_off_before:
            return None, []
        nxt = upcoming[0]
        panels = [("Next", "More " + nxt.name if nxt.marathon and nxt.name == show.name else nxt.name)]
        targets = [nxt]
        # LATER skips more episodes of the same series ("X-Play", then "X-play" again)
        later = next((s for s in upcoming[1:] if s.name.lower() != nxt.name.lower() or s.sign_off_before), None)
        if later and later.sign_off_before:
            later = None
        if later:
            panels.append(("Later", f"{later.name} · {cards.clock(later.start)}"))
            targets.append(later)
        if tonight:
            prime = next((s for s in upcoming if s.start and s.start >= tonight), None)
            if prime and prime.start < tonight + td(minutes=SLOT_MINUTES) and not prime.sign_off_before \
                    and prime is not nxt and prime is not later:
                name = f"{prime.name} Marathon" if prime.marathon_intro else prime.name
                panels.append(("Tonight", f"{name} · {cards.clock(prime.start)}"))
                targets.append(prime)
        logger.info("Promo: " + " / ".join(f"{label.upper()} {name}" for label, name in panels))
        art = []
        for i, target in enumerate(targets):
            picture = self.picture_of(target, show.start)
            if picture:
                ends = at + (i + 1) * PROMO_PANEL_SECONDS
                art.append((picture, at + i * PROMO_PANEL_SECONDS, ends - (0.3 if i == len(targets) - 1 else 0.05), i == 0))
        return events.promo(panels, at, PROMO_PANEL_SECONDS), art

    # --- Show art, clips and the marathon ad ---

    @staticmethod
    def picture_of(show, airing):
        """What goes on a lower third for a show: its clip if it has one that standards allow at the time it would air,
        otherwise its poster, or None. Never touches the network, or cuts anything."""
        src = getattr(show, "src", None)
        if src and not isinstance(show, MusicBlock):
            clip = posters.clip_file(src)
            if os.path.exists(clip) and allowed(src, broadcast_hour(airing)[1]):
                return clip
        return posters.thumb(show.name)

    def clip_step(self):
        """One run of the clip job: cuts the next clip that's wanted, of what's planned first, then what the schedule
        wants. Seconds until the next run."""
        wanted = self.clips_wanted()
        wait = CLIP_GAP_SECONDS if self.cut_a_clip(wanted, self.clips_tried) else CLIP_IDLE_SECONDS
        if time.time() - self.clips_pruned > CLIP_PRUNE_SECONDS:
            self.clips_pruned = time.time()
            posters.prune_clips({posters.clip_file(src) for src in wanted})
        return wait

    def clips_wanted(self):
        planned = [s.src for s in self.lineup.snapshot() if not isinstance(s, MusicBlock)]
        return list(dict.fromkeys(planned + sorted(self.cache.wanted)))

    def cut_a_clip(self, wanted, tried):
        """Cuts the first clip that's wanted and missing, if its episode is on the Pi. True if it tried one."""
        for src in wanted:
            e = self.cache.entry(src)
            dest = posters.clip_file(src)
            if not e or not e.get("duration") or os.path.exists(dest) or time.time() - tried.get(src, 0) < CLIP_RETRY_SECONDS:
                continue
            tried[src] = time.time()
            try:
                if posters.make_clip(cache_path(e), e["duration"], dest):
                    health.record("clips", True)
                    logger.info(f"Clip cut: {os.path.basename(src)}")
                else:
                    logger.info(f"No clip to be had from {os.path.basename(src)}")
            except (OSError, subprocess.SubprocessError) as err:
                health.record("clips", False, f"{os.path.basename(src)}: {err}")
                logger.warning(f"Could not cut a clip from {os.path.basename(src)}: {err}")
            return True
        return False

    def art_step(self):
        """One run of the show art job: gets the posters for what's coming up."""
        found = posters.refresh(self.art_wanted())
        if found:
            logger.info(f"Show art: {found} new pictures")

    def art_wanted(self):
        """[(title, category, year, needs the large picture)]: the marathons, what's on the schedule, then what's planned."""
        wanted = {}
        indexer = self.cache.indexer

        def add(src, big=False):
            category = indexer.category_of(src) or "videos"
            title = cards.parse_title(src, category, MEDIA_ROOT)[0]
            if title not in wanted or big:
                series, order = indexer.info(src)
                wanted[title] = (title, category, meta.era_year(src, category, series, order), big)
        for key in MARATHON_SERIES:
            episodes = indexer.episodes(key)
            if episodes:
                add(episodes[0][1], True)
        now = datetime.datetime.now()
        for group in self.cache.schedule["groups"]:
            for item in group["items"]:
                if item.startswith(("shows/", "g4/")):
                    files = self.cache._episodes(item, now)
                    if files:
                        add(files[0])
        for show in self.lineup.snapshot():
            if not isinstance(show, MusicBlock) and show.name not in wanted:
                wanted[show.name] = (show.name, show.category, show.year, False)
        return list(wanted.values())

    def next_marathon(self, now):
        """(when it starts, its title) for the next marathon on the schedule, or None."""
        sched = self.cache.schedule
        today = broadcast_date(now)
        groups = {g["id"] for g in sched["groups"] if "marathon:" in g["items"]}
        for offset in range(8):
            day = today + td(days=offset)
            base = datetime.datetime.combine(day, datetime.time())
            starts = sorted(base + td(hours=s["start"]) for s in sched["slots"] if s["group"] in groups and s["day"] == day.weekday())
            for start in starts:
                key = marathon_series(day, self.cache.indexer)
                episodes = self.cache.indexer.episodes(key) if key and start > now else None
                if episodes:
                    src = episodes[0][1]
                    return start, cards.parse_title(src, self.cache.indexer.category_of(src) or "videos", MEDIA_ROOT)[0]
        return None

    def marathon_ad(self, airs_at, season):
        found = self.next_marathon(airs_at)
        if not found:
            return None
        start, name = found
        image = posters.wide(name)
        if not image:
            return None
        days = (broadcast_date(start) - broadcast_date(airs_at)).days
        when = {0: "Today", 1: "Tomorrow", 7: f"Next {start:%A}"}.get(days, f"This {start:%A}")
        logger.info(f"Marathon ad: {name}, {when} at {cards.clock(start)}")
        return events.marathon_ad(image, name, when, cards.clock(start), season)

    def remote_test(self, ask):
        """Puts something on to see how it looks, from POST /api/test?what=...: scores, promo, launch (with url= and
        seconds=), alert (with kind=crawl or kind=cutin), or special (with kind= one of SPECIALS, at the next break)."""
        what, kind = ask.get("what", "").strip().lower(), ask.get("kind", "").strip().lower()
        if what == "scores":
            return self.remote_scores_test()
        if what == "promo":
            return self.remote_promo_test()
        if what == "alert":
            return self.remote_alert_test(kind)
        if what == "special":
            return self.remote_special_soon(kind)
        if what == "launch":
            try:
                seconds = int(ask.get("seconds") or 60)
            except ValueError:
                return "seconds= has to be a number."
            return self.remote_launch_test(ask.get("url", ""), seconds)
        return "What to test? what=scores, promo, launch, alert or special."

    def remote_promo_test(self):
        """Puts the NEXT / LATER / TONIGHT lower third, pictures and all, over the show on now, to see how it looks."""
        item = self.player.current()
        position = self.player.position()
        if not item or item.kind not in ("act", "detour") or not item.show or isinstance(item.show, MusicBlock) or position is None:
            return "It needs a show on."
        script, art = self.promo(item.show, position + 0.8)
        if not script:
            return "Nothing is coming up before sign-off."
        vf, _ = overlay_filter(0, False, promo=script, art=art)
        if vf:
            self.player.add_filter("vf", "@promotest:" + vf)
        return f"Promo is on, with {len(art)} picture{'s' if len(art) != 1 else ''}."

    def remote_special_soon(self, kind):
        """Makes a special screen due at the next break, to see it without waiting."""
        if kind not in SPECIALS:
            return f"No special called {kind or 'that'}."
        self.special_last[kind] = self.last_special = 0.0
        return f"The {kind} screen is up at the next break that has room for it."

    # --- Local weather ---

    # --- Emergency alerts ---

    def alert_step(self):
        """One run of the alert job: polls the NWS and puts cut-ins and crawls on the air."""
        self.alerts.poll()
        self.check_alerts()

    def check_alerts(self):
        if not self.on_air:
            if off_air(datetime.datetime.now()):
                self.alerts.hold()  # silent overnight
            return
        if self.eas_pending or time.time() < self.eas_until:
            return
        for alert in self.alerts.new_cut_ins():
            try:
                card = alerts.cut_in_card(alert)
            except Exception:
                logger.exception(f"Could not make the {alert['event']} bulletin; it will only crawl")
                card = None
            self.alerts.mark_aired(alert)
            if card:
                self.cut_in(card)
                return
        due = self.alerts.crawl_due(ALERT_CRAWL_MINUTES)
        if due:
            text, label, first, ids = due
            if self.air_crawl(text, label, first):
                self.alerts.crawled(ids)

    def check_launches(self):
        """One run of the launch job: puts a live launch on the air when it's time."""
        if self.launch_live:
            launch, item, began = self.launch_live
            on_for = time.time() - began
            if on_for > item.duration + 30:
                self.launch_live = None
            elif launch.get("id") and on_for < launches.SCRUB_WATCH_SECONDS and not self.launches.still_going(launch):
                logger.info(f"Launch held or scrubbed, back to the show: {launch.get('name')}")
                self.player.end_item(item)
                self.launch_live = None
            return
        if not self.on_air or near_sign_off(datetime.datetime.now()) or self.eas_pending:
            return
        found = self.launches.due()
        if not found:
            return
        launch, _ = found
        urls = self.launches.stream_for(launch)
        lead = launches.liftoff(launch) - time.time()
        # The cut takes a few seconds, the card five
        if urls and lead <= LAUNCH_BEFORE_SECONDS + events.LAUNCH_SECONDS + 3:
            self.launches.started(launch)
            self.air_launch(launch, urls)

    def air_launch(self, launch, urls, seconds=LAUNCH_WATCH_SECONDS):
        rocket, _, mission = launch.get("name", "Live launch").partition(" | ")
        where = ((launch.get("pad") or {}).get("location") or {}).get("name", "").split(",")[0]
        season = holiday_look(datetime.date.today())
        options = {"force-media-title": f"LIVE: {rocket}", "length": seconds, "cache": "yes", "demuxer-max-bytes": "32MiB"}
        if len(urls) > 1:
            options["audio-file"] = urls[1]
        panels = [("LIVE", rocket), ("LIVE", mission or where)]
        vf, _ = overlay_filter(seconds, bug=True, promo=events.promo(panels, 8.0, PROMO_PANEL_SECONDS))
        if vf:
            options["vf-append"] = vf
        ended = threading.Event()
        item = Item(urls[0], float(seconds), "launch", f"LIVE: {launch.get('name')}", options, lambda reason: ended.set())
        item.ended = ended
        self.launch_live = (launch, item, time.time())
        logger.info(f"Live launch cutting in: {launch.get('name')}, liftoff {launch.get('net')}")
        self.cut_in(events.launch_cut_in(rocket, mission, where, season), follow=item)

    def cut_in(self, card, follow=None):
        """Interrupts whatever is on with an alert card. A show picks up a few seconds before where it was cut."""
        item = self.player.current()
        self.eas_resume = None
        self.eas_from_detour = bool(item and item.kind == "detour")
        if item and item.kind == "act" and item.show is self.current_show and item.started:
            position = max(item.offset, item.offset + time.time() - item.started - 3.0)
            if isinstance(item.show, MusicBlock):
                position = (item.video, position)
            self.eas_resume = (item.show, position)
        logger.info(f"Alert cutting in ({card.kind}, {card.duration:.0f}s)")
        self.eas_until = time.time() + card.duration + 10
        self.eas_follow = follow
        self.eas_pending = card
        self.skipping = True
        self.player.flush()

    def air_alert(self):
        """The step after a cut-in: the alert card, then back to the show where it was cut, or to whatever
        the main channel has on by now (if it was cut during a break or on another channel)."""
        card, self.eas_pending = self.eas_pending, None
        follow, self.eas_follow = self.eas_follow, None
        resume, self.eas_resume = self.eas_resume, None
        self.detour, self.rejoining = None, False
        self.prev_kind = "card"  # a burst of static as it cuts in
        if not self.emit(self.card(card)):
            return
        if follow:
            # A live launch eats the time of whatever it cut into: back to wherever the schedule has got to by then
            if not self.emit(follow):
                return
            self._wait_out(follow)
            if self.player.epoch != self.epoch:
                return
            resume = None
        if resume and resume[0] is self.interrupted:
            self.interrupted = None
            show, position = resume
            self.emit(self.card(cards.back_to(show.name, holiday_look(datetime.date.today()))))
            point = self.act_at(show, position)
            if point:
                self.air(show, join=point)
            else:
                self.current_show = show
                self.end_break(show)
                self.current_show = None
        else:
            self.rejoin(flash=self.eas_from_detour)

    @staticmethod
    def act_at(show, position):
        """(act, position) to pick a show back up at a file position, or None when it's all but over.
        For a music block, position is (video, position in that video)."""
        if isinstance(show, MusicBlock):
            video, position = position
            if show.videos[video][2] - position > 15:
                return video, position
            return (video + 1, 0.0) if video < len(show.videos) - 1 else None
        breaks = show.kept_breaks()
        bounds = [0.0] + breaks + [show.duration]
        for i in range(len(bounds) - 1):
            if position < bounds[i + 1]:
                if bounds[i + 1] - position > 15:
                    return i, position
                return (i + 1, bounds[i + 1]) if i < len(bounds) - 2 else None
        return None

    def air_crawl(self, text, label, first):
        """Lays the alert crawl over whatever is playing, if it has time left for all of it. The first
        crawl for a new alert gets the alert beeps."""
        item = self.player.current()
        if not item or item.kind not in CRAWL_KINDS or not item.started:
            return False
        position = self.player.position()
        if position is None:
            return False
        start = position + 0.8
        script, d = alerts.crawl_script(text, label, start)
        if item.duration - (time.time() - item.started) < d + 2:
            return False
        self.player.add_filter("vf", f"@eascrawl:lavfi=[ass=filename={script}:fontsdir={cards.FONTS_DIR}]")
        if first:
            self.player.add_filter("af", f"@eastone:{alerts.crawl_tone(start)}")
            threading.Timer(4.0, self.player.remove_filter, ("af", "@eastone")).start()
        logger.info(f"Alert crawl ({d:.0f}s) over {item.label}")
        return True

    def alert_test_item(self):
        """The weekly test bulletin; it counts as done once it has played to the end."""
        try:
            item = self.card(alerts.test_card())
        except Exception:
            logger.exception("Could not make the weekly alert test")
            return None
        item.on_end = lambda reason: self.alerts.test_aired() if reason == "eof" else None
        return item

    # --- Live scores ---

    def check_scores(self):
        """One run of the score job: puts the score card over a show every SCORES_MINUTES while games are on."""
        # Fetches only once a show is on that the crawl could go over
        if not self.on_air or self.eas_pending or time.time() < self.scores_next or not self._score_window():
            return
        games = self.scores.live()
        if not games:
            self.scores_next = time.time() + SCORES_IDLE_MINUTES * 60
        elif self.air_scores(games):
            self.scores_next = time.time() + SCORES_MINUTES * 60

    def _score_window(self):
        """(item, earliest, latest): the file times a score crawl can run between over what's playing, or
        None. Shows only (not breaks or music videos), clear of the promo early in a show and the NEXT
        lower third at its end."""
        item = self.player.current()
        if not item or item.kind not in ("act", "detour") or not item.started or isinstance(item.show, MusicBlock):
            return None
        overlay = item.overlay or {}
        earliest = item.offset + (PROMO_AT + 3 * PROMO_PANEL_SECONDS if overlay.get("promo") else 0) + 5
        latest = item.offset + item.duration - 2
        if overlay.get("snipe"):
            latest = min(latest, item.show.duration - SNIPE_SECONDS - 2)
        return item, earliest, latest

    def air_scores(self, games):
        """Lays the score card over the show that's playing, if it fits in the window."""
        window = self._score_window()
        position = self.player.position()
        if not window or position is None:
            return False
        item, earliest, latest = window
        start = position + 0.8
        shown, d = scores.fit(games, SCORES_MAX_SECONDS)
        if start < earliest or start + d > latest:
            return False
        logos = scores.logos_for(shown)   # the first time a team comes up, this takes a moment
        position = self.player.position()
        if position is None or self.player.current() is not item:
            return False
        start = position + 0.8
        if start < earliest or start + d > latest:
            return False
        self.player.add_filter("vf", "@scores:" + scores.board_filter(shown, start, logos))
        logger.info(f"Scores ({d:.0f}s, {len(shown)} of {len(games)} games, {len(logos)} logos) over {item.label}")
        return True

    def remote_scores_test(self):
        """Puts the score card on now, with today's games if none are live, to check how it looks."""
        if not self.scores or not self.on_air:
            return "The score card can't run right now."
        games = self.scores.live() or self.scores.today()
        if not games:
            return "There are no games on the scoreboards today."
        return "Score card is on." if self.air_scores(games) else "It needs a show on, clear of its promo and the NEXT card."

    # --- Remote ---

    def _wait_out(self, item):
        """Waits until an item of no fixed length is nearly over, so what follows starts from the real time."""
        latest = time.time() + item.duration + LAUNCH_START_GRACE_SECONDS
        while self.player.epoch == self.epoch and not item.ended.wait(0.5) and time.time() < latest:
            if item.started and time.time() > item.started + item.duration - LAUNCH_QUEUE_NEXT_SECONDS:
                break

    def remote_launch_test(self, url, seconds):
        """Cuts in with any live stream right now, to check live launches."""
        if not self.on_air:
            return "The station is off the air."
        urls = launches.resolve(url)
        if not urls:
            return "That stream isn't live, or can't be read."
        test = {"name": "Live Launch Test | Test Stream", "pad": {"location": {"name": "G|TV"}}}
        self.air_launch(test, urls, max(15, min(seconds, LAUNCH_WATCH_SECONDS)))
        return "Test launch is on."

    def remote_alert_test(self, kind):
        """Airs a test alert right now, to check the alert system: kind "crawl", or a full cut-in."""
        if not self.alerts:
            return "Alerts are off."
        if kind not in ("crawl", "cutin"):
            return "Which test? Use kind=crawl or kind=cutin."
        if not self.on_air:
            return "The station is off the air."
        if kind == "crawl":
            if self.air_crawl(alerts.DEMO_CRAWL, "TEST", True):
                return "Test crawl is on."
            return "Not now: try again during a show."
        self.cut_in(alerts.test_card())
        return "Test alert is on."

    def remote_state(self):
        now = datetime.datetime.now()
        item = self.player.current()
        state = {"time": cards.clock(now), "on_air": self.on_air, "volume": self.player.volume,
                 "muted": self.player.muted, "now": None, "lineup": [], "copying": [],
                 "channel": {"number": MAIN_CHANNEL[0], "name": MAIN_CHANNEL[1], "main": True},
                 "main_channel": {"number": MAIN_CHANNEL[0], "name": MAIN_CHANNEL[1]}}
        if item and item.kind == "detour":
            number, name = item.channel
            state["channel"] = {"number": number, "name": name, "main": False}
        if item:
            elapsed = time.time() - item.started
            now_playing = {"kind": item.kind, "label": item.label, "elapsed": round(elapsed),
                           "duration": round(item.duration)}
            show = item.show or self.current_show
            if show:
                position = item.offset + elapsed if item.show and not isinstance(show, MusicBlock) else None
                now_playing["show"] = {
                    "id": show.src, "name": show.name, "episode": show.episode, "category": show.category,
                    "aired": meta.long_date(show.aired) if show.aired else None,
                    "start": cards.clock(show.start), "end": cards.clock(show.end),
                    "progress": round(position / show.duration, 3) if position is not None else None,
                    "marathon": show.marathon,
                }
            state["now"] = now_playing
        for show in self.lineup.snapshot()[:8]:
            state["lineup"].append({"start": cards.clock(show.start) if show.start else "", "name": show.name,
                                    "episode": show.episode, "requested": show.requested,
                                    "marathon": show.marathon, "off_air_before": bool(show.sign_off_before)})
        with self.cache.lock:
            state["copying"] = [cards.parse_title(src, self.cache.indexer.category_of(src) or "videos", MEDIA_ROOT)[0]
                                for src in self.cache.fetching]
        return state

    def remote_guide(self):
        return self.channels.guide()

    def remote_health(self):
        now = datetime.datetime.now()
        item = self.player.current()
        if item:
            on_air = "ok", f"Playing {item.label}"
        elif self.on_air:
            on_air = "warn", "On the air, but nothing is playing at this moment"
        elif off_air(now):
            on_air = "idle", "Signed off for the night"
        else:
            on_air = "warn", "Not on the air"
        disks = (("sd", "SD card", CACHE_DIR, False), ("usb", "USB drive", USB_DIR, True))
        used = self.player.memory_mb()
        restarts = self.player.memory_restarts
        memory = {"id": "player-memory", "name": "Player memory",
                  "status": "idle" if used is None else "warn" if used > PLAYER_MEMORY_MB else "ok",
                  "message": "Not running" if used is None else f"{used} MB, restarted at {PLAYER_MEMORY_MB} MB"
                  + (f" ({restarts} time{'s' if restarts != 1 else ''} since the station started)" if restarts else "")}
        rows = [memory, health.jobs_row()]
        if self.live:
            rows.insert(1, self._watch_row(now, self.live))
        return health.report(self.on_air, on_air, MEDIA_ROOT, disks, MIN_FREE_GB, self.cache.bad_files, rows)

    @staticmethod
    def _watch_row(now, stream):
        s = stream.status()
        if s["running"]:
            minutes = int((now.timestamp() - s["since"]) // 60)
            message = f"Streaming for {minutes} min, {s['viewers']} watching"
        else:
            message = "Nobody watching"
        if s["error"]:
            message += f". Last complaint: {s['error']}"
        return {"id": "watch-page", "name": "Watch page", "status": "ok" if s["running"] else "idle", "message": message}

    def remote_clear_bad_file(self, record_id):
        src = self.cache.clear_bad_file(record_id)
        return f"{os.path.basename(src)} can air again." if src else "That file isn't on the list."

    def remote_tune(self, number, src):
        """Changes to a make-believe channel's show; the set comes back to the main channel after it."""
        channel = next(((n, name) for n, name, _ in self.channels.listed() if n == number), None)
        if not channel:
            return "No such channel."
        if not self.cache.entry(src):
            return "That show isn't on the Pi anymore."
        self.detour = (channel, src)
        self.skipping = True
        self.player.flush()
        return f"Channel {number}: {cards.parse_title(src, self.cache.entry(src)['category'], MEDIA_ROOT)[0]}"

    def remote_main(self):
        item = self.player.current()
        if not self.detour and not (item and item.kind == "detour"):
            return f"Already on {MAIN_CHANNEL[1]}."
        self.detour = None
        self.rejoining = True
        self.player.flush()
        return f"Back to {MAIN_CHANNEL[1]}."

    def remote_skip(self, whole_show):
        item = self.player.current()
        if item and item.kind == "detour":
            return self.remote_main()
        if not self.on_air:
            return "The station is off the air."
        if whole_show and self.current_show:
            name = self.current_show.name
            self.skipping = True
            self.player.flush()
            return f"Skipping {name}."
        self.player.skip()
        return "Skipped."

    def remote_volume(self, change=None, mute=False):
        self.player.set_volume(self.player.volume + change if change else None, toggle_mute=mute)
        return f"Volume {self.player.volume}" + (" (muted)" if self.player.muted else "")

    def remote_search(self, query):
        out = []
        with self.cache.lock:
            cached = set(self.cache.entries)
        for src, cat, name, episode, _ in self.cache.indexer.search(query):
            out.append({"id": src, "name": name, "episode": episode, "category": cat, "cached": src in cached})
        return out

    def _library_items(self):
        """The schedule page's library, worked out once per NAS index."""
        indexer = self.cache.indexer
        referenced = schedule.referenced_items(schedule.load())
        key = (indexer.last_index_time, frozenset(referenced), self.lengths_stamp)
        if self.library_cache[0] != key:
            with indexer.lock:
                media_map = {k: list(v) for k, v in indexer.media_map.items()}
            self.library_cache = (key, schedule.library(media_map, MEDIA_ROOT, rating, referenced))
        return self.library_cache[1]

    def remote_library(self):
        return {"items": self._library_items(), "rescanning": self.rescanning, "live": True,
                "rules": {"kids_end": KIDS_END_HOUR, "late_start": LATE_NIGHT_HOUR}}

    def remote_save_schedule(self, body):
        version = schedule.save(body, {item["id"] for item in self._library_items()})
        self.cache.load_schedule()
        self.lineup.replan()
        self.cache.wake.set()   # copy what the new week wants
        return version

    def remote_rescan(self):
        """Walks the NAS for new folders in the background, then samples the length of any new ones."""
        if self.rescanning:
            return "Already rescanning."
        self.rescanning = True

        def run():
            try:
                indexer = self.cache.indexer
                indexer.run_indexer()
                with indexer.lock:
                    media_map = {k: list(v) for k, v in indexer.media_map.items()}
                schedule.probe_lengths(media_map, MEDIA_ROOT)
                self.lengths_stamp = time.time()
            except OSError as e:
                logger.error(f"Rescan failed, NAS problem? {e}")
            finally:
                self.rescanning = False
        threading.Thread(target=run, daemon=True).start()
        return "Rescanning the NAS."

    def remote_play_next(self, src):
        if self.cache.indexer.category_of(src) not in MAIN_CATEGORIES:
            return "Not a show."
        if self.cache.entry(src):
            show = self.lineup.request(src)
            return f"{show.name} is up next." if show else "Couldn't queue that."
        self.cache.fetch(src, self.lineup.request)
        return "Copying it from the NAS. It'll be on after that finishes (about a minute)."


def main():
    logger.info("Retro TV Station Starting Up...")
    health.install("station")

    # 1. Initialize Indexer (reads the SD card copy; only touches the NAS on a first run)
    indexer = MediaIndexer()

    # 2. Initialize Cache Manager (refills in the background right away if the cache is low)
    cache_manager = CacheManager(indexer)

    # 3. Plan, then play forever: station ID or bumper, the show in acts with commercial breaks
    #    between them, then a break with the occasional guide or weather and an "Up Next" card
    lineup = Lineup(cache_manager)
    player = Player(cache_manager.set_aside)
    station = Station(cache_manager, lineup, player)
    remote.start(station, REMOTE_PORT)
    # The background jobs, each a heartbeat on the health page
    if LAUNCHES:
        health.repeat("Live launches", station.check_launches, LAUNCH_CHECK_SECONDS)
    health.repeat("Show art", station.art_step, ART_CHECK_MINUTES * 60)
    health.repeat("Clips", station.clip_step, CLIP_IDLE_SECONDS)
    if WEATHER_LAT is not None:
        health.repeat("Weather", wx.Watcher(WEATHER_LAT, WEATHER_LON, WEATHER_TZ).step, wx.RETRY_SECONDS[-1])
    if station.alerts:
        health.repeat("Emergency alerts", station.alert_step, ALERT_CHECK_SECONDS)
    if station.scores:
        health.repeat("Scores", station.check_scores, SCORES_CHECK_SECONDS)
    station.run()

if __name__ == "__main__":
    main()
