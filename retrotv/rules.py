"""
The broadcast day's rules: the half-hour grid, broadcast standards, holidays and seasons, marathons, and where a
show's commercial breaks go.
"""
import datetime
import functools
import json
import os
import re
import subprocess

from retrotv import screens
from retrotv import events
from retrotv import meta
from retrotv.config import (
    ACT_MINUTES, BLURB_CHEEKY_HOUR, BLURB_RAUNCHY_HOUR, BUMP_FOLDERS, CACHE_DIR, DAYPARTS, DAYTIME_BUMP_KEYWORDS,
    DEFAULT_ACT_MINUTES, DEFAULT_MIX, GRID_MAX_EXTRA_MINUTES, HOLIDAYS, HOLIDAY_IDS, HOLIDAY_LEAD_DAYS,
    INTRO_SECONDS, KIDS_END_HOUR, KIDS_KEYWORDS, LATE_NIGHT_HOUR, LATE_NIGHT_KEYWORDS, LOTTERY_HOUR,
    MARATHON_EPISODES, MARATHON_EPOCH, MARATHON_SERIES, MAX_BREAKS, MEDIA_ROOT, MID_BREAK_SECONDS, MID_SHOW_BREAKS,
    MIN_END_BREAK_SECONDS, MIN_SPLIT_MINUTES, MONTHS, NEUTRAL_KEYWORDS, NEW_YEAR_COUNTDOWN, OWN_COMMERCIALS,
    REMINDERS, REMINDER_DAYS, SHORT_SHOW_MINUTES, SIGN_OFF_HOUR, SIGN_OFF_MARGIN_MINUTES, SIGN_ON_HOUR,
    SLOT_MINUTES, STATIC_SECONDS, TIGHT_END_SECONDS, logger, td)


# --- Schedule ---

def broadcast_hour(t):
    """(weekday, hour) of the broadcast day, where 1 AM Saturday is still Friday at hour 25."""
    if t.hour < SIGN_ON_HOUR:
        t -= td(days=1)
        return t.weekday(), t.hour + 24
    return t.weekday(), t.hour


def broadcast_date(t):
    return (t - td(days=1)).date() if t.hour < SIGN_ON_HOUR else t.date()


def mix_at(day, hour):
    for name, days, start, end, mix in DAYPARTS:
        if day in days and start <= hour < end:
            return mix
    return DEFAULT_MIX


def blurb_level(t):
    """How far a text bump airing at t can go: "clean", "cheeky" or "raunchy" (see BLURB_CHEEKY_HOUR)."""
    day, hour = broadcast_hour(t)   # after midnight counts as hour 24, 25, ...
    if "kids" in mix_at(day, hour) or hour < BLURB_CHEEKY_HOUR:
        return "clean"
    return "raunchy" if hour >= BLURB_RAUNCHY_HOUR else "cheeky"


def base_category(category):
    return "videos" if category == "kids" else category


def is_tape(src):
    return "/.Tapes/" in src


def pool_of(e):
    """The cache pool an entry lives in: archive.org's downloads, commercials and music have their own, the
    rest are shows."""
    if e.get("archive"):
        return "archive"
    return e["category"] if e["category"] in ("commercials", "music") else "shows"


# Parsed in retrotv.meta, which retrotv/news.py shares
music_credit, artist_key = meta.music_credit, meta.artist_key


def popup_facts():
    """{meta.popup_key: {"facts": [...], ...}} for the music videos that have Pop-Up Video facts."""
    try:
        with open(screens.POPUPS_FILE) as f:
            return dict(json.load(f).get("videos", {}))
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def off_air(t):
    if SIGN_OFF_HOUR < SIGN_ON_HOUR:
        return SIGN_OFF_HOUR <= t.hour < SIGN_ON_HOUR
    return t.hour >= SIGN_OFF_HOUR or t.hour < SIGN_ON_HOUR


def near_sign_off(t):
    return off_air(t) or off_air(t + td(minutes=SIGN_OFF_MARGIN_MINUTES))


def sign_on_after(t):
    on = t.replace(hour=SIGN_ON_HOUR, minute=0, second=0, microsecond=0)
    return on if on > t else on + td(days=1)


def round_time(t, minutes=5):
    """Guide-style times: to the nearest 5 minutes."""
    t = t + td(minutes=minutes / 2)
    return t.replace(minute=t.minute - t.minute % minutes, second=0, microsecond=0)


def on_grid(t):
    return t.second == 0 and t.microsecond == 0 and t.minute % SLOT_MINUTES == 0


def next_boundary(t):
    """The first :00 or :30 at or after t."""
    base = t.replace(minute=t.minute - t.minute % SLOT_MINUTES, second=0, microsecond=0)
    return base if base >= t else base + td(minutes=SLOT_MINUTES)


def is_new_year(t):
    return NEW_YEAR_COUNTDOWN and t.month == 1 and t.day == 1 and t.hour == 0 and t.minute == 0 and t.second == 0


def is_lottery(t):
    return LOTTERY_HOUR is not None and t.hour == LOTTERY_HOUR and t.minute == 0 and t.second == 0


# --- Standards, seasons, marathons ---

LATE_NIGHT = re.compile("|".join(LATE_NIGHT_KEYWORDS), re.I)
KIDS = re.compile("|".join(KIDS_KEYWORDS), re.I)
NEUTRAL = re.compile("|".join(NEUTRAL_KEYWORDS), re.I)
DAYTIME_BUMPS = re.compile("|".join(DAYTIME_BUMP_KEYWORDS), re.I)


@functools.lru_cache(maxsize=None)
def rating(src):
    """'late' (after LATE_NIGHT_HOUR only), 'kids' (before KIDS_END_HOUR) or '' (any time)."""
    folders, base = os.path.split(src[len(MEDIA_ROOT):] if src.startswith(MEDIA_ROOT) else src)
    base, folders = (re.sub(r"[_.]", " ", text) for text in (base, folders))  # "Bill.Nye.The.Science.Guy"
    if NEUTRAL.search(base):
        return ""
    for text in (base, folders):
        if LATE_NIGHT.search(text):
            return "late"
        if KIDS.search(text):
            return "kids"
    if any(f in src for f in BUMP_FOLDERS) and not DAYTIME_BUMPS.search(base):
        return "late"
    return ""


def allowed(src, hour):
    """Whether broadcast standards let src air at broadcast hour `hour` (9 .. 26)."""
    r = rating(src)
    if r == "late":
        return hour >= LATE_NIGHT_HOUR
    if r == "kids":
        return hour < KIDS_END_HOUR
    return True


holiday_tag = functools.lru_cache(maxsize=None)(meta.holiday)
commercial_year = functools.lru_cache(maxsize=None)(meta.commercial_year)


def easter(year):
    """Easter Sunday (Gregorian calendar, the anonymous computus)."""
    a, b, c = year % 19, year // 100, year % 100
    h = (19 * a + b - b // 4 - (b - (b + 8) // 25 + 1) // 3 + 15) % 30
    l = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7
    m = (a + 11 * h + 22 * l) // 451
    return datetime.date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def nth_weekday(year, month, weekday, n):
    """The nth weekday (0 = Monday) of a month."""
    first = datetime.date(year, month, 1)
    return first + td(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


FLOATING_DAYS = {
    "easter": easter,
    "thanksgiving": lambda year: nth_weekday(year, 11, 3, 4),  # fourth Thursday in November
    "mothersday": lambda year: nth_weekday(year, 5, 6, 2),     # second Sunday in May
    "fathersday": lambda year: nth_weekday(year, 6, 6, 3),     # third Sunday in June
    "mlkday": lambda year: nth_weekday(year, 1, 0, 3),         # third Monday in January
    "memorialday": lambda year: nth_weekday(year, 6, 0, 1) - td(days=7),  # last Monday in May
    "laborday": lambda year: nth_weekday(year, 9, 0, 1),       # first Monday in September
}


def holiday_date(spec, year):
    """A HOLIDAYS / HOLIDAY_IDS / REMINDERS date in a given year."""
    a, b = spec
    if isinstance(a, str):
        return FLOATING_DAYS[a](year) + td(days=b)
    return datetime.date(year, a, b)


def holiday_season(day, holidays=None):
    for name, (first, last) in (holidays or HOLIDAYS).items():
        if holiday_date(first, day.year) <= day <= holiday_date(last, day.year):
            return name
    return None


def holiday_look(day):
    """How the station ID and the cards dress up today: a holiday day's look, or the season's."""
    return holiday_season(day, HOLIDAY_IDS) or holiday_season(day)


def heritage_month(day):
    """The heritage month (MONTHS) a day falls in, or None."""
    return next((name for name, (first, last) in MONTHS.items() if first <= (day.month, day.day) <= last), None)


def next_reminder(day):
    """(name, date) of a REMINDERS day from today to REMINDER_DAYS ahead, or None."""
    for name, spec in REMINDERS.items():
        for year in (day.year, day.year + 1):
            when = holiday_date(spec, year)
            if 0 <= (when - day).days <= REMINDER_DAYS:
                return name, when
    return None


def holiday_ahead(day, lead=HOLIDAY_LEAD_DAYS):
    """The season that's on now or starts within `lead` days."""
    for i in range(lead + 1):
        name = holiday_season(day + td(days=i))
        if name:
            return name
    return None


def cache_path(e):
    """Where a cached file is: on the USB drive if its entry says so, otherwise on the SD card."""
    return os.path.join(e.get("dir") or CACHE_DIR, e["file"])


def marathon_series(bday, indexer):
    choices = [k for k in MARATHON_SERIES if len(indexer.episodes(k)) >= MARATHON_EPISODES]
    if not choices:
        return None
    return choices[((bday - MARATHON_EPOCH).days // 7) % len(choices)]


# --- Act breaks ---

def _ffmpeg_log(args, timeout=180):
    """Runs an analysis-only ffmpeg at low priority and returns its log."""
    cmd = ["nice", "-n", "15", "ffmpeg", "-hide_banner", "-nostats", "-threads", "1"] + args + ["-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stderr
    except Exception as e:
        logger.warning(f"Break analysis failed: {e}")
        return ""


def _chapters(path):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", path], timeout=30)
        return [float(c["start_time"]) for c in json.loads(out).get("chapters", []) if float(c["start_time"]) > 1]
    except Exception:
        return []


def _black_near(path, start, length):
    """Midpoint of the first black stretch in [start, start + length], or None."""
    log = _ffmpeg_log(["-ss", f"{max(0.0, start):.2f}", "-t", f"{length:.2f}", "-i", path, "-map", "0:v:0", "-an",
                       "-vf", "blackdetect=d=0.08:pix_th=0.12"])
    m = re.search(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", log)
    if m:
        return max(0.0, start) + (float(m.group(1)) + float(m.group(2))) / 2
    return None


def _find_cut(path, target, window):
    """A good place to cut near target: a moment of silence over a black frame, else just silence."""
    start = max(0.0, target - window)
    log = _ffmpeg_log(["-ss", f"{start:.2f}", "-t", f"{2 * window:.2f}", "-i", path, "-map", "0:a:0", "-vn",
                       "-af", "silencedetect=n=-42dB:d=0.3"])
    silences = []
    for m in re.finditer(r"silence_start:\s*([\d.]+)[\s\S]*?silence_end:\s*([\d.]+)", log):
        s0, s1 = start + float(m.group(1)), start + float(m.group(2))
        silences.append((s0, s1))
    silences.sort(key=lambda s: abs((s[0] + s[1]) / 2 - target))
    for s0, s1 in silences[:4]:
        black = _black_near(path, s0 - 1.0, s1 - s0 + 2.0)
        if black is not None:
            return black
    if silences:
        s0, s1 = silences[0]
        return (s0 + s1) / 2
    # No silence at all (music over a fade?): look for black in a narrower window
    return _black_near(path, target - min(window, 60), 2 * min(window, 60))


def act_targets(duration, category, src):
    """Evenly spaced break times for a show, or [] when it plays straight through."""
    if not MID_SHOW_BREAKS or duration < MIN_SPLIT_MINUTES * 60 or OWN_COMMERCIALS.search(src):
        return []
    act = ACT_MINUTES.get(category, DEFAULT_ACT_MINUTES) * 60
    count = min(MAX_BREAKS, max(1, round(duration / act)) - 1)
    return [duration * (i + 1) / (count + 1) for i in range(count)]


def plan_breaks(path, duration, category, src):
    targets = act_targets(duration, category, src)
    if not targets:
        return []
    window = min(120.0, duration / (len(targets) + 1) * 0.25)
    chapters = _chapters(path)
    breaks = []
    for target in targets:
        near = [c for c in chapters if abs(c - target) <= window]
        cut = min(near, key=lambda c: abs(c - target)) if near else _find_cut(path, target, window)
        if cut is None:
            cut = target
        # Keep acts at least a couple of minutes long
        if (not breaks or cut - breaks[-1] > 120) and duration - cut > 120:
            breaks.append(round(cut, 2))
    return breaks


# --- Fitting a show to its slot ---

def slot_end(duration, category, src, start):
    """
    (end, extra seconds, aligned) for a show starting at `start`. Normally the next :00/:30 that leaves
    room for its breaks. Short shows (cartoon segments) and shows that start off the grid don't wait
    for a far-off boundary: they end after ordinary breaks, and a later show lands back on the grid.
    """
    mids = len(act_targets(duration, category, src))
    need = duration + mids * MID_BREAK_SECONDS[0] + MIN_END_BREAK_SECONDS + INTRO_SECONDS
    earliest = start + td(seconds=need)
    end = next_boundary(earliest)
    if is_lottery(end):
        end = next_boundary(earliest + td(seconds=events.LOTTERY_SECONDS + STATIC_SECONDS))
    if is_new_year(end):
        end = next_boundary(earliest + td(seconds=events.NEW_YEAR_MIDNIGHT + STATIC_SECONDS))
    extra = (end - earliest).total_seconds()
    if extra > GRID_MAX_EXTRA_MINUTES * 60 and (not on_grid(start) or duration < SHORT_SHOW_MINUTES * 60):
        return earliest + td(seconds=mids * 45 + 60), extra, False
    return end, extra, True


def tight_plan(duration, mids, start, closes):
    """(mid-show breaks to keep, when it ends) for a show that has to fit by `closes` on short breaks."""
    spare = (closes - start).total_seconds() - duration - INTRO_SECONDS - TIGHT_END_SECONDS
    if spare < 0:
        return 0, start + td(seconds=duration + INTRO_SECONDS + TIGHT_END_SECONDS)
    return min(mids, int(spare // MID_BREAK_SECONDS[0])), closes
