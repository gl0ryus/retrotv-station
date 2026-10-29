import os
import re
import time
import json
import math
import random
import socket
import hashlib
import threading
import collections
import functools
import shutil
import subprocess
import datetime
import logging

import retrotv_cards as cards
import retrotv_screens as screens
import retrotv_events as events
import retrotv_meta as meta
import retrotv_remote as remote
import retrotv_alerts as alerts
import retrotv_blurbs as blurbs
import retrotv_archive as archive
import retrotv_scores as scores

# --- Configuration ---
MEDIA_ROOT = "/mnt/tower/.retrotv"
PATHS = {
    "commercials": os.path.join(MEDIA_ROOT, "commercials"),
    "movies": os.path.join(MEDIA_ROOT, "movies"),
    "videos": os.path.join(MEDIA_ROOT, "videos"),
    "g4": os.path.join(MEDIA_ROOT, "g4"),
    "music": os.path.join(MEDIA_ROOT, "music_videos"),
}
MAIN_CATEGORIES = ["videos", "movies", "g4"]
VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.wmv')
HOME_DIR = "/home/gl0ryus"
CACHE_DIR = os.path.join(HOME_DIR, "retrotv_cache")
MANIFEST_FILE = os.path.join(HOME_DIR, "retrotv_manifest.json")
# The index lives on the SD card too, so the NAS share can stay read-only and asleep
INDEX_FILE = os.path.join(HOME_DIR, "retrotv_index.json")
NAS_INDEX_FILE = os.path.join(MEDIA_ROOT, "station", "legacy", "media_index.json")  # old shared index, only read to seed a first run

# Everything plays from the SD card cache. The NAS is only touched during a refill, which fills
# the cache back up. That happens at REFILL_HOUR once the cache won't last another day, or right
# away if it's about to run dry (e.g. the NAS was offline at REFILL_HOUR).
CACHE_LIMIT_GB = 80        # the SD card has ~100 GB free; this is ~4 days of programming
COMMERCIAL_POOL_GB = 8     # part of CACHE_LIMIT_GB, roughly 650 commercials
MUSIC_POOL_GB = 4          # part of CACHE_LIMIT_GB, roughly 250 music videos
MUSIC_MIN_CACHED = 30      # fewer unplayed music videos than this cached: an early refill (once a day)
MIN_FREE_GB = 5            # stop a refill early rather than fill the SD card
COPY_MB_PER_SEC_ON_AIR = 3 # copies while the station is on the air are paced so the SD card can keep feeding
                           # playback (at full speed it was 100% busy and shows stalled); off the air they run flat out
REFILL_HOUR = 4            # local time of the NAS check (the station is off the air then)
REFILL_BELOW_HOURS = 30    # refill at REFILL_HOUR only when fewer unplayed show hours are left (a day airs under 24h)
LOW_WATER_HOURS = 2        # refill immediately below this
REFILL_RETRY_MINUTES = 60  # minimum gap between early refills, e.g. while the NAS is offline
INDEX_MAX_AGE_DAYS = 7     # re-walk the NAS during a refill once the index is this old
GB = 1024 ** 3

# Public domain video from archive.org (retrotv_archive.py): 50s and 60s TV, old movies, cartoons, short films and
# commercials, picked automatically and never kept on the NAS. A few of each kind are downloaded just ahead
# (ARCHIVE_READY), air from the SD card like everything else, and are deleted once they've aired. Between them
# they get about ARCHIVE_SHARE of the picks in their category (shows, movies, kids' shows, commercials)
ARCHIVE = True
ARCHIVE_SHARE = 0.1
ARCHIVE_READY = {"commercials": 40, "tv": 4, "cartoons": 6, "shorts": 3, "movies": 1}
ARCHIVE_POOL_GB = 3        # part of CACHE_LIMIT_GB
ARCHIVE_CHECK_MINUTES = 10
# Made-up paths for archive.org's files, so the name parsers and broadcast standards read them like NAS files.
# Nothing is ever stored there
ARCHIVE_ROOT = os.path.join(MEDIA_ROOT, "archive.org")

# One mpv runs for the life of the station and is fed over its IPC socket, so nothing (not even the
# console) shows between items. mpv "retrotv" profile (~/.config/mpv/mpv.conf): full screen on the
# HDMI panel with a CRT shader
MPV_SOCKET = "/tmp/retrotv-mpv.sock"
# --cache=yes reads well ahead even for local files, so a busy SD card can't stall playback (refill
# copies are also paced while on the air, see COPY_MB_PER_SEC_ON_AIR)
PLAYER_CMD = ["/usr/bin/mpv", "--profile=retrotv", "--really-quiet", "--idle=yes", "--force-window=yes",
              "--cache=yes", "--demuxer-max-bytes=64MiB", f"--input-ipc-server={MPV_SOCKET}"]
SHOW_TITLE_SECONDS = 0     # show a show's name on screen as it starts (0 = off; the promo lower third and the
                           # Up Next card already name it); commercials never get one
ASSETS_DIR = os.path.join(HOME_DIR, "retrotv_assets")
BUMPER_FILE = os.path.join(HOME_DIR, "retrotv_bumper.mp4")    # plays before every show (None = off)
STATIC_FILE = os.path.join(ASSETS_DIR, "static.mp4")          # a burst of snow between items (None = off)
STATIC_SECONDS = 0.4
LOGO_BUG_FILE = os.path.join(ASSETS_DIR, "logo_bug.png")      # corner logo during shows (None = off)
PROGRESS_BAR = True        # a faint line along the bottom showing how far into the show or commercial we are
PROGRESS_COLOR = "white@0.35"
OFF_AIR_FILE = os.path.join(ASSETS_DIR, "off_air_loop.mp4")   # loops between sign-off and sign-on
OFF_AIR_SECONDS = 60.0

# Broadcast day: the station signs off after the show that's on at SIGN_OFF_HOUR and comes back
# at SIGN_ON_HOUR: the set warms up, a minute of colour bars, a "good morning" ID, then the first show
SIGN_OFF_HOUR = 2
SIGN_ON_HOUR = 9
LINEUP_HOURS = 3           # shows are scheduled this far ahead, for the guide and the "Up Next" cards
ANTHEM = True              # the national anthem before "This concludes our broadcast day"
OFF_AIR_LOOP_MINUTES = 30  # the off-air slate runs this long, then the set "turns off" until morning (None = all night)

# Shows start on the hour and half hour. Breaks are packed with commercials, topped up with bumps,
# and the Up Next card stretches a few seconds, so the next show lands right on time. A show that
# starts off the grid (after a restart or a skip) gets back on it at its end if that takes no more
# than GRID_MAX_EXTRA_MINUTES of extra commercials; otherwise the next show tries
SLOT_MINUTES = 30
GRID_MAX_EXTRA_MINUTES = 12
MID_BREAK_SECONDS = (75, 240)     # each mid-show break runs at least / at most this long
MIN_END_BREAK_SECONDS = 70        # the break between shows, at least
INTRO_SECONDS = 4.5               # the station ID or bumper right before a show
UP_NEXT_SECONDS = (5.5, 12.0)     # the Up Next card stretches within this to absorb the last seconds
SHORT_SHOW_MINUTES = 15           # shorter shows (cartoon segments) share a half hour instead of padding one out
FILLER_MAX_SECONDS = 35           # bumps and commercials this short top up the end of a break
COMMERCIAL_MAX_SECONDS = 240      # longer "commercials" (music videos, specials) stay out of breaks

# Shows longer than MIN_SPLIT_MINUTES get commercial breaks every ACT_MINUTES or so, cut where the
# picture goes black and the sound drops out (or at a chapter mark) near each target time
MID_SHOW_BREAKS = True
MIN_SPLIT_MINUTES = 12
ACT_MINUTES = {"movies": 20}
DEFAULT_ACT_MINUTES = 8
MAX_BREAKS = 6             # so a 6-hour marathon recording isn't chopped into 45 pieces
OWN_COMMERCIALS = re.compile(r"\bwoc\b|with (original )?commercials", re.I)  # recordings that already have breaks

# Commercials from the show's era: one whose name has a year within ERA_YEARS of the show's year
# is ERA_WEIGHT times as likely as an undated one (and a far-off year a quarter as likely)
ERA_YEARS = 3
ERA_WEIGHT = 6.0

# Special screens that air inside commercial breaks: minutes between airings (0 = off). The most
# overdue one airs, one per break at most, and never two within SPECIAL_GAP_MINUTES
SPECIALS = {
    "guide": 90,     # the scrolling program guide (between shows only)
    "weather": 60,   # local weather from Open-Meteo
    "quiz": 45,      # Pop Quiz trivia from Open Trivia DB
    "history": 180,  # Today in History from Wikipedia
    "launch": 150,   # Launch Watch from The Space Devs
    "space": 180,    # Who's in Space, with the ISS on a world map
    "markets": 90,   # BTC, ETH and the S&P 500 with their 24-hour change (CoinGecko, Yahoo Finance)
    "reminder": 120, # a heads-up before Valentine's, Mother's and Father's Day (see REMINDERS)
    "blurb": 30,     # text bumps: facts, food for thought, dad jokes, raunchier late at night (retrotv_blurbs)
    "sky": 120,      # Tonight's Sky: the moon, planets and ISS passes over the house, sunset to sign-off
    "teletext": 150, # G|TV Text: teletext pages of tonight's listings, the weather, markets and odd news
    "art": 240,      # Art Break: the day's picture, drawn by Claude, building up shape by shape
}
# How far the text bumps go: clean until BLURB_CHEEKY_HOUR, cheeky until BLURB_RAUNCHY_HOUR, raunchy to sign-off
# (crude, never hateful). Always clean in the kids' dayparts
BLURB_CHEEKY_HOUR = 21
BLURB_RAUNCHY_HOUR = 23
SPECIAL_GAP_MINUTES = 10
FRESH_SPECIALS = ("weather",)   # made again with the latest data just before they're queued
# Where the weather and the emergency alerts are for, kept out of the repo in this file next to the script:
# {"place": "Town, ST", "lat": 28.0, "lon": -82.5}. Without it there's no weather card and no alerts
LOCATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "location.json")
WEATHER_TZ = "America/New_York"
# Emergency alerts from the National Weather Service for WEATHER_LAT/LON (retrotv_alerts.py). The worst
# warnings cut into the show with the alert tones and a voice, then it picks up where it left off; other
# warnings and watches crawl across the top. Silent while off the air: what's still active crawls at sign-on
ALERTS = True
ALERT_CHECK_SECONDS = 15
ALERT_CRAWL_MINUTES = 15     # active alerts crawl again this often
ALERT_TEST_HOURS = (10, 19)  # the weekly test airs at a break between shows within these hours
# Items a crawl can go over. They all carry per-file vf options, so mpv drops the crawl filter when they end
CRAWL_KINDS = ("act", "detour", "commercial")
LOTTERY_HOUR = 23          # the nightly lottery drawing, right before the 11 PM show (None = off)
# Live scores from ESPN's scoreboard (retrotv_scores.py): while games are on, a SCORES lower third crawls them
# across a show (not breaks or music videos) every SCORES_MINUTES. The leagues in crawl order, each with the
# home team whose game leads it in mustard: Bucs, Magic, Lightning, Rays
SCORES = True
SCORES_TEAMS = {"nfl": "TB", "nba": "ORL", "nhl": "TB", "mlb": "TB"}
SCORES_MINUTES = 20
SCORES_IDLE_MINUTES = 10   # with no games on, look again this often
SCORES_MAX_SECONDS = 60    # a longer crawl leaves games off the end
SCORES_CHECK_SECONDS = 15

# Time-of-day programming: the first daypart that matches sets the mix. Days are 0 = Monday ...
# 5 = Saturday; hours run past 24 for after midnight (26 = 2 AM), still counted as the evening before
EVERY_DAY = tuple(range(7))
DAYPARTS = [
    ("Saturday morning cartoons", (5,), 9, 12, {"kids": 85, "videos": 15}),
    ("Kids' mornings", (0, 1, 2, 3, 4, 6), 9, 12, {"kids": 40, "videos": 45, "g4": 15}),
    ("G4 afternoon", EVERY_DAY, 12, 18, {"g4": 55, "videos": 40, "movies": 5}),
    ("Late night", EVERY_DAY, 22, 26, {"movies": 30, "videos": 50, "g4": 20}),
]
DEFAULT_MIX = {"videos": 70, "g4": 24, "movies": 6}
MIN_CACHED_KIDS = 8        # keep this many unplayed kids' shows cached for the mornings

# Broadcast standards, matched (case-insensitive regexes) against the file name and then its
# folders. Late-night fare airs only from LATE_NIGHT_HOUR until sign-off, kids' shows only before
# KIDS_END_HOUR. Bumps in the Bumps folder are Adult Swim's unless they say Toonami or Cartoon Network
LATE_NIGHT_HOUR = 22
KIDS_END_HOUR = 21
LATE_NIGHT_KEYWORDS = (
    r"adult swim", r"\[as\]", r"\bas\d{3}\b", r"springer", r"south park", r"chappelle", r"reno ?911",
    r"always sunny", r"\biasip\b", r"totally busted", r"flavor of love", r"\broast\b", r"uncensored",
    r"wildboyz", r"jackass", r"tom green", r"spawn", r"dexter[ .]s(eason)?[ .]?\d", r"curb your enthusiasm",
    r"nathan for you", r"beavis", r"family guy", r"aqua teen", r"\bathf\b", r"robot chicken", r"squidbillies",
    r"venture bro", r"metalocalypse", r"harvey birdman", r"sealab", r"space ghost coast", r"boondocks",
    r"playboy", r"\bppv\b", r"girls gone wild", r"howard stern",
)
KIDS_KEYWORDS = (
    r"cartoon", r"toon\b", r"nickelodeon", r"\bnick\b", r"snick", r"kids'? ?wb", r"fox kids", r"disney",
    r"spongebob", r"dexter'?s ?lab", r"dexterslab", r"\beene\b", r"ed,? edd", r"powerpuff", r"hidden temple",
    r"bill nye", r"salute your shorts", r"pokemon", r"hamtaro", r"rugrats", r"hey arnold", r"rocko",
    r"animaniacs", r"tiny toon", r"boomerang", r"ocwoc", r"saturday morning",
)
NEUTRAL_KEYWORDS = (r"toonami",)   # Toonami recordings are fine any time of day
DAYTIME_BUMP_KEYWORDS = (r"toonami", r"cartoon network", r"\bcn\b", r"cn city")
BUMP_FOLDERS = ("/commercials/Bumps/",)

# Holidays: (first, last), each a (month, day) or a (floating holiday, days after it) from FLOATING_DAYS.
# Shows and commercials tagged for the season get HOLIDAY_WEIGHT times the chance, the hourly station ID
# gets the season's look, the remote gets a seasonal channel, and refills top up the season's shows from
# HOLIDAY_LEAD_DAYS before it starts. The first match wins, so Christmas takes Dec 1 from a late Thanksgiving
HOLIDAYS = {
    "valentines": ((2, 7), (2, 14)),
    "halloween": ((10, 19), (10, 31)),
    "christmas": ((12, 1), (12, 25)),
    "thanksgiving": (("thanksgiving", -6), ("thanksgiving", 3)),  # the Friday before to the Sunday after
}
# Days the station ID and the cards dress up for without a season of programming (there isn't enough of it).
# On its day one of these wins over a season (Mardi Gras in Valentine's week), and the first match wins
# among them (Easter over an April Fools' Day Easter, Juneteenth over a Father's Day on June 19)
HOLIDAY_IDS = {
    "newyearsday": ((1, 1), (1, 1)),
    "mlkday": (("mlkday", 0), ("mlkday", 0)),
    "groundhog": ((2, 2), (2, 2)),
    "mardigras": (("easter", -47), ("easter", -47)),
    "stpatricks": ((3, 17), (3, 17)),
    "easter": (("easter", 0), ("easter", 0)),
    "aprilfools": ((4, 1), (4, 1)),
    "cincodemayo": ((5, 5), (5, 5)),
    "mothersday": (("mothersday", 0), ("mothersday", 0)),
    "memorialday": (("memorialday", 0), ("memorialday", 0)),
    "juneteenth": ((6, 19), (6, 19)),
    "fathersday": (("fathersday", 0), ("fathersday", 0)),
    "july4": ((7, 4), (7, 4)),
    "laborday": (("laborday", 0), ("laborday", 0)),
    "veteransday": ((11, 11), (11, 11)),
}
# Heritage months: (first, last) as (month, day). Kept subtle on screen (see retrotv_cards.MONTH_LOOKS), and
# Today in History and Pop Quiz lean toward them (retrotv_screens.TOPICS)
MONTHS = {
    "blackhistory": ((2, 1), (2, 29)),
    "womenshistory": ((3, 1), (3, 31)),
    "aapi": ((5, 1), (5, 31)),
    "pride": ((6, 1), (6, 30)),
    "hispanic": ((9, 15), (10, 15)),
    "nativeamerican": ((11, 1), (11, 30)),
}
# Heads-ups in the breaks before days you're expected to get someone something or spend time with them:
# the "reminder" special, from REMINDER_DAYS ahead through the day itself
REMINDERS = {"Valentine's Day": (2, 14), "Mother's Day": ("mothersday", 0), "Father's Day": ("fathersday", 0)}
REMINDER_DAYS = 7
HOLIDAY_WEIGHT = 5.0
HOLIDAY_SHOWS_CACHED = 14
HOLIDAY_PER_SERIES = 2     # of those, at most this many from one series (late-October Attack of the Show is a lot)
HOLIDAY_COMMERCIALS_CACHED = 60
HOLIDAY_LEAD_DAYS = 3
NEW_YEAR_COUNTDOWN = True  # a countdown into midnight on New Year's Eve

# Series play in episode order across days (season/episode numbers or air dates), picking up where
# they left off. SERIES_CACHED_AHEAD is the most unplayed episodes of one series kept on the SD card
SERIES_FILE = os.path.join(HOME_DIR, "retrotv_series.json")
SERIES_CACHED_AHEAD = 6
RERUN_HOURS = 12           # nothing airs again within this long (holiday episodes included)
# When each show and music video last aired, kept even after its copy leaves the SD card (the manifest forgets
# it then), so the refill doesn't copy back what just aired: nothing aired within REFETCH_DAYS is fetched again,
# unless everything a pick could use has been (then what aired longest ago goes first), and never anything aired
# within REFETCH_MIN_DAYS
AIRED_FILE = os.path.join(HOME_DIR, "retrotv_aired.json")
AIRED_KEEP_DAYS = 60
REFETCH_DAYS = 21
REFETCH_MIN_DAYS = 7

# Tapes (whole recorded broadcasts in videos/shows/.Tapes, often hours long) share one pick between
# them: together they weigh TAPE_WEIGHT, about as much as one series, and at most TAPES_CACHED
# unplayed tapes are kept on the SD card
TAPE_WEIGHT = 1.0
TAPES_CACHED = 3

# Marathons: (name, days, start hour, end hour). One series back to back for the whole block; the
# series rotates weekly through MARATHON_SERIES (MARATHON_EPOCH is a Monday in the week of the first)
MARATHONS = [("Friday Night Marathon", (4,), 20, 24)]
MARATHON_SERIES = ("simpsons", "seinfeld", "futurama", "friends", "scrubs", "that70sshow", "freshprinceofbelair",
                   "malcolminthemiddle", "officeus", "drewcarey", "inlivingcolor", "howimetyourmother",
                   "kingofqueens", "twoandahalfmen")
MARATHON_EPOCH = datetime.date(2026, 10, 26)
MARATHON_EPISODES = 10     # cached ahead of the block
MARATHON_PREP_DAYS = 3

# Music video blocks: videos back to back filling a slot on the grid, each with its artist and song
# in a lower third, and a commercial break about every 20 minutes. MUSIC_BLOCKS air at set times:
# (name, days, start hour, minutes). Otherwise a half-hour MUSIC_BLOCK_NAME turns up now and then in
# the regular lineup: MUSIC_CHANCE per slot, never two in a row, not in the kids' dayparts.
MUSIC_BLOCKS = [("After School Videos", (0, 1, 2, 3, 4), 15.5, 60),
                ("Saturday Night Videos", (5,), 22, 60)]
MUSIC_BLOCK_NAME = "Music Videos"
MUSIC_CHANCE = 0.06
MUSIC_VIDEO_SECONDS = (90, 480)   # videos outside this length stay out of the blocks
MUSIC_TITLE_AT = 4.0              # seconds into each video for its lower third
MUSIC_TITLE_SECONDS = 4.5         # per panel: NOW · ARTIST, then YEAR · SONG
MUSIC_MIN_FILL = 0.8              # a block airs only if its cached videos fill this much of it
# These blocks open with the Weekly Countdown (Last.fm, screens.countdown), then the video of the best placed
# artist in it that's cached
COUNTDOWN_BLOCKS = ("After School Videos", "Saturday Night Videos")
POPUP_VIDEO = True   # VH1-style fact bubbles over the music videos that have facts (retrotv_news.py finds them)

# Effects
GLITCH_MINUTES = (40, 80)  # a 1-second tracking roll or interference burst about this often (None = off)

# Panorama stretch: a picture narrower than the screen (4:3 shows, most old ads) fills it without
# cropping. The middle keeps its true shape and the stretch grows toward the edges, in STRETCH_STRIPS
# vertical strips: each strip's widening follows 1 + a·x^STRETCH_POWER (x = 0 at the centre, 1 at an
# edge). A picture is stretched by at most STRETCH_MAX; anything narrower keeps black side bars.
# Pictures as wide as the screen or wider are left to mpv.conf's crop, as before.
STRETCH = True
STRETCH_STRIPS = 16
STRETCH_POWER = 2
STRETCH_MAX = 1.45
STAGE_W, STAGE_H = 512, 300   # mpv.conf's square-pixel 300-line stage (the cards render at this size too)
SNIPE_SECONDS = 30         # a "NEXT: ..." lower third for the last seconds of a show (0 = off)
PROMO_AT = 8.0             # seconds into a show for the NEXT / LATER / TONIGHT lower third (0 = off)
PROMO_PANEL_SECONDS = 4.5
TONIGHT_HOUR = 20          # TONIGHT names the show planned for this hour, until shows near it
# The day's anniversary episode (picked by retrotv_news.py: first aired on this date years ago) goes next in
# line at the first show from this hour on that its rating allows, after a From the Vault card
ANNIVERSARY_HOUR = 20

# Phone remote and live guide on the home Wi-Fi: http://retrotv.local/
REMOTE_PORT = 80
REMOTE_STATE_FILE = os.path.join(HOME_DIR, "retrotv_remote.json")

# The remote's TV guide: the main channel on top, then make-believe channels filled from the cached shows.
# Picking a show on one plays it right away ("changing the channel"); when it ends the set goes back to the
# main channel and joins whatever it has on by then. (number, name, kind); "season" only shows in season
MAIN_CHANNEL = (3, cards.CHANNEL)
CHANNELS = [
    (4, "TOON TOWN", "kids"),
    (5, "G4", "g4"),
    (6, "SITCOM CITY", "sitcoms"),
    (7, "LATE SHOW", "talk"),
    (8, "MOVIE PALACE", "movies"),
    (9, "TAPE VAULT", "tapes"),
    (13, "AFTER DARK", "late"),
    (31, "SEASONAL", "season"),
]
SEASON_CHANNEL_NAMES = {"halloween": "FRIGHT TV", "christmas": "HOLIDAY TV", "thanksgiving": "TURKEY TV",
                        "valentines": "LOVE TV"}
TALK_SERIES = ("conan", "latenightwithconanobrien", "colbertreport", "dailyshow", "soup")
GUIDE_HOURS = 4

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(HOME_DIR, "retro_station.log"))
    ]
)
logger = logging.getLogger("RetroTV")

td = datetime.timedelta


def load_location():
    """(place, lat, lon) from LOCATION_FILE, or Nones without it."""
    try:
        with open(LOCATION_FILE) as f:
            here = json.load(f)
        return str(here["place"]), float(here["lat"]), float(here["lon"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"No location ({e}): no weather card or emergency alerts")
        return None, None, None


WEATHER_PLACE, WEATHER_LAT, WEATHER_LON = load_location()


def write_json(path, data):
    """Writes through a temp file so a crash never leaves half a JSON file behind."""
    tmp = path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


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


def roll_category(when=None):
    """Main content mix for the daypart at `when` (default: now)."""
    mix = mix_at(*broadcast_hour(when or datetime.datetime.now()))
    return random.choices(list(mix), weights=list(mix.values()))[0]


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


# Parsed in retrotv_meta, which retrotv_news.py shares
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


def marathon_at(t):
    """(name, broadcast date) when t falls in a marathon block, else None."""
    day, hour = broadcast_hour(t)
    for name, days, start, end in MARATHONS:
        if day in days and start <= hour < end:
            return name, broadcast_date(t)
    return None


def marathon_series(bday, indexer):
    choices = [k for k in MARATHON_SERIES if len(indexer.episodes(k)) >= MARATHON_EPISODES]
    if not choices:
        return None
    return choices[((bday - MARATHON_EPOCH).days // 7) % len(choices)]


def upcoming_marathons(now, indexer, days=MARATHON_PREP_DAYS):
    """[(name, broadcast date, series key)] for marathons from today through `days` ahead."""
    out = []
    today = broadcast_date(now)
    for i in range(days + 1):
        d = today + td(days=i)
        for name, wdays, start, end in MARATHONS:
            if d.weekday() in wdays:
                block_end = datetime.datetime.combine(d, datetime.time()) + td(hours=end)
                key = marathon_series(d, indexer)
                if key and block_end > now:
                    out.append((name, d, key))
    return out


class MediaIndexer:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_index_time = 0
        self.media_map = {k: [] for k in PATHS}
        self.kids = []
        self.shows = {}     # NAS path -> (series key, order key) for episodes we can put in order
        self.series = {}    # series key -> [(order key, NAS path)], in order
        self.titles = []    # (NAS path, category, name, episode, lowercase text) for the remote's search
        self.holidays = {}  # (holiday, is commercial) -> [NAS paths]
        self.load_index()

    def run_indexer(self):
        """Walks the NAS directories and updates the index. Only run while the NAS is awake anyway."""
        logger.info("Starting media indexing...")
        new_map = {k: [] for k in PATHS}

        for key, path in PATHS.items():
            if not os.path.exists(path):
                logger.warning(f"Path not found: {path}")
                continue

            for root, _, files in os.walk(path):
                for file in files:
                    if file.lower().endswith(VIDEO_EXTENSIONS):
                        new_map[key].append(os.path.join(root, file))

        with self.lock:
            for key, files in new_map.items():
                # An empty walk usually means the NAS dropped out, so keep what we had
                if files:
                    self.media_map[key] = files
            self.last_index_time = time.time()
        self._derive()
        self.save_index()
        counts = ", ".join(f"{k}: {len(v)}" for k, v in self.media_map.items())
        logger.info(f"Indexing complete. {counts}, kids: {len(self.kids)}")

    def _derive(self):
        """Series and episode order, titles and holiday tags for everything in the index."""
        t0 = time.time()
        shows, series, titles, holidays = {}, {}, [], {}
        with self.lock:
            media_map = {k: list(v) for k, v in self.media_map.items()}
        for cat in MAIN_CATEGORIES:
            for src in media_map.get(cat, []):
                name, episode, key, order = meta.describe(src, cat, MEDIA_ROOT)
                if cat != "movies" and order:
                    shows[src] = (key, order)
                    series.setdefault(key, []).append((order, src))
                titles.append((src, cat, name, episode, f"{name} {episode}".lower()))
        for eps in series.values():
            eps.sort()
        for cat in MAIN_CATEGORIES + ["commercials"]:   # music videos keep to their own pool
            for src in media_map.get(cat, []):
                tag = holiday_tag(src)
                if tag:
                    holidays.setdefault((tag, cat == "commercials"), []).append(src)
        kids = [f for f in media_map.get("videos", []) if rating(f) == "kids"]
        with self.lock:
            self.shows, self.series, self.titles, self.holidays, self.kids = shows, series, titles, holidays, kids
        logger.info(f"Index sorted into {len(series)} series in {time.time() - t0:.1f}s")

    def save_index(self):
        try:
            with self.lock:
                write_json(INDEX_FILE, {
                    "timestamp": self.last_index_time,
                    "media_map": self.media_map
                })
        except Exception as e:
            logger.error(f"Failed to save index: {e}")

    def load_index(self):
        # The NAS copy is only read when the SD card doesn't have an index yet
        for path in (INDEX_FILE, NAS_INDEX_FILE):
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self.media_map.update(data.get("media_map", {}))
                self.last_index_time = data.get("timestamp", 0)
                logger.info(f"Loaded index from {path}")
                missing = [k for k in PATHS if k not in data.get("media_map", {})]
                if missing:
                    logger.info(f"Index has no {', '.join(missing)} yet, re-walking the NAS at the next refill.")
                    self.last_index_time = 0
                self._derive()
                if path != INDEX_FILE:
                    self.save_index()
                return
            except Exception as e:
                logger.error(f"Failed to load index {path}: {e}")
        self.run_indexer()

    def is_stale(self):
        return time.time() - self.last_index_time > INDEX_MAX_AGE_DAYS * 86400

    def category_of(self, src):
        with self.lock:
            for cat, files in self.media_map.items():
                if src in files:
                    return cat
        return None

    def get_random(self, category, exclude=()):
        with self.lock:
            files = self.kids if category == "kids" else self.media_map.get(category, [])
            for _ in range(50):
                if not files:
                    break
                path = random.choice(files)
                if path not in exclude:
                    return path
        return None

    def info(self, src):
        with self.lock:
            return self.shows.get(src, (None, None))

    def episodes(self, key):
        with self.lock:
            return list(self.series.get(key, ()))

    def has_after(self, key, position):
        with self.lock:
            return any(position is None or order > position for order, _ in self.series.get(key, ()))

    def holiday_files(self, name, commercials):
        with self.lock:
            return list(self.holidays.get((name, commercials), ()))

    def search(self, query, limit=40):
        words = query.lower().split()
        if not words:
            return []
        with self.lock:
            hits = [t for t in self.titles if all(w in t[4] for w in words)]
        hits.sort(key=lambda t: (not t[2].lower().startswith(words[0]), t[2].lower(), t[3].lower()))
        return hits[:limit]

    def remove(self, path):
        """Forgets a file that has disappeared from the NAS."""
        with self.lock:
            for files in list(self.media_map.values()) + [self.kids]:
                if path in files:
                    files.remove(path)


# Set while shows are playing from the SD card (the Station keeps it current)
ON_AIR = threading.Event()
if not off_air(datetime.datetime.now()):
    ON_AIR.set()


COPY_LOCK = threading.Lock()  # one copy at a time, so a refill's and an archive.org download don't add up


def paced_copy(src, dest, chunk=4 * 1024 * 1024):
    """Copies src (a path, or a URL to download) to dest, one copy at a time. While on the air it's paced to
    COPY_MB_PER_SEC_ON_AIR and flushed chunk by chunk, so the SD card is never too busy to feed playback; off the
    air it runs flat out."""
    with COPY_LOCK, (archive.open_url(src) if "://" in src else open(src, "rb")) as fin, open(dest, "wb") as fout:
        while True:
            t0 = time.time()
            buf = fin.read(chunk)
            if not buf:
                break
            fout.write(buf)
            if ON_AIR.is_set():
                fout.flush()
                os.fdatasync(fout.fileno())
                time.sleep(max(0.0, len(buf) / (COPY_MB_PER_SEC_ON_AIR * 1e6) - (time.time() - t0)))


def get_video_duration(file_path):
    """Returns the duration of a video file in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        output = subprocess.check_output(cmd).decode().strip()
        return float(output)
    except Exception:
        return 0.0


def probe_aspect(path):
    """A video file's display aspect ratio (width × sample aspect / height, rotation applied), or None."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,sample_aspect_ratio:stream_side_data=rotation",
                              "-of", "json", path], capture_output=True, text=True, timeout=15).stdout
        s = json.loads(out)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        num, _, den = (s.get("sample_aspect_ratio") or "1:1").partition(":")
        sar = int(num) / int(den) if num.isdigit() and den.isdigit() and int(num) and int(den) else 1.0
        rotated = any(abs(int(d.get("rotation", 0))) % 180 == 90 for d in s.get("side_data_list", []))
        return (h / (w * sar)) if rotated else (w * sar / h)
    except Exception:
        return None


@functools.lru_cache(maxsize=16)
def display_aspect(path):
    """probe_aspect, remembered: for the station's own files outside the cache (bumper, static, off air)."""
    return probe_aspect(path)


def stretch_filter(dar):
    """An mpv vf-pre that panorama-stretches a picture of display aspect `dar` onto the 512x300 stage, or
    None when it needs none (as wide as the screen or wider, or unknown)."""
    if not dar or dar >= STAGE_W / STAGE_H - 0.01:
        return None
    width = max(2, round(STAGE_H * dar / 2) * 2)          # its true width at 300 lines
    s = min(STRETCH_MAX, STAGE_W / width)
    target = min(STAGE_W, round(width * s / 2) * 2)
    n, p = STRETCH_STRIPS, STRETCH_POWER
    a = (p + 1) * (target / width - 1)                    # so the strips add up to `target`

    def out_x(sx):
        """Output position of source column sx, centred mapping x + a·x^(p+1)/(p+1)."""
        x = 2 * sx / width - 1
        q = x + a / (p + 1) * math.copysign(abs(x) ** (p + 1), x)
        return round(target / 4 * (1 + q / (1 + a / (p + 1)))) * 2
    # Strip edges and widths stay even: on 4:2:0 frames crop rounds odd ones down, dropping columns
    src = [round(width * j / n / 2) * 2 for j in range(n + 1)]
    dst = [out_x(sx) for sx in src]
    parts = [f"scale=w={width}:h={STAGE_H}:flags=bilinear,setsar=1,split={n}" + "".join(f"[s{j}]" for j in range(n))]
    for j in range(n):
        parts.append(f"[s{j}]crop={src[j + 1] - src[j]}:{STAGE_H}:{src[j]}:0,"
                     f"scale={max(2, dst[j + 1] - dst[j])}:{STAGE_H}:flags=bilinear[t{j}]")
    tail = "".join(f"[t{j}]" for j in range(n)) + f"hstack=inputs={n},setsar=1"
    if target < STAGE_W:
        tail += f",pad={STAGE_W}:{STAGE_H}:{(STAGE_W - target) // 2}:0:black"
    parts.append(tail)
    return "lavfi=[" + ";".join(parts) + "]"


class CacheManager:
    """
    Keeps a pool of shows and a pool of commercials on the SD card and tops both up from
    the NAS in one burst per refill, so the NAS disks can spin down the rest of the day.
    Shows are only evicted after they've played; unplayed ones are never thrown away, and
    neither is anything reserved for the lineup or a commercial break. Series are cached a few
    episodes at a time in order, so they can air in order.
    """
    def __init__(self, indexer):
        self.indexer = indexer
        self.lock = threading.RLock()
        self.entries = {}  # NAS path -> {"file", "category", "size", "duration", "plays", "last_played", "breaks",
                           #              "series", "order", "cached"}
        self.series_pos = {}  # series key -> order key of the last episode aired in order
        self.aired = {}       # NAS path -> when it last aired (shows and music videos; see AIRED_FILE)
        self.reserved = set()
        self.fetching = {}    # NAS path -> callback, for shows asked for from the remote
        self.refilling = False
        self.last_refill = 0
        self.holiday_refill_day = None
        self.music_refill_day = None
        self.wake = threading.Event()
        self.archive_wake = threading.Event()
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._load_manifest()
        try:
            with open(SERIES_FILE) as f:
                self.series_pos = json.load(f)
        except (OSError, ValueError):
            pass
        try:
            with open(AIRED_FILE) as f:
                self.aired = dict(json.load(f))
        except (OSError, ValueError, TypeError):
            pass
        for src, e in self.entries.items():   # what's cached and has aired counts too (before AIRED_FILE existed)
            if e.get("last_played") and e["category"] != "commercials":
                self.aired[src] = max(self.aired.get(src, 0), e["last_played"])

        # Start background thread
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        if ARCHIVE:
            threading.Thread(target=self._archive_worker, daemon=True).start()

    def _load_manifest(self):
        try:
            with open(MANIFEST_FILE, 'r') as f:
                self.entries = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"Failed to load cache manifest, starting empty: {e}")

        # Drop entries whose file is gone, and files nothing refers to (partial copies, old cache layout)
        on_disk = set(os.listdir(CACHE_DIR))
        self.entries = {src: e for src, e in self.entries.items() if e["file"] in on_disk}
        known = {e["file"] for e in self.entries.values()}
        for name in on_disk - known:
            try:
                os.remove(os.path.join(CACHE_DIR, name))
            except OSError as e:
                logger.error(f"Could not remove stray cache file {name}: {e}")
        for src, e in self.entries.items():
            self._describe(src, e)
        self._save_manifest()
        logger.info(f"Cache loaded. {self.stats_line()}")

    @staticmethod
    def _describe(src, e):
        """Series and episode order for a cached show, worked out once."""
        if "series" not in e:
            key = order = None
            if e["category"] in ("videos", "g4"):
                _, _, key, order = meta.describe(src, e["category"], MEDIA_ROOT)
            e["series"], e["order"] = (key, order) if order else (None, None)
        e.setdefault("cached", 0)

    def _save_manifest(self):
        try:
            with self.lock:
                write_json(MANIFEST_FILE, self.entries)
        except Exception as e:
            logger.error(f"Failed to save cache manifest: {e}")

    @staticmethod
    def _matches(src, e, category):
        if category is None:
            return e["category"] in MAIN_CATEGORIES
        if category == "kids":
            return e["category"] == "videos" and rating(src) == "kids"
        return e["category"] == category

    def show_candidates(self, category, when, exclude=(), series=None):
        """
        Cached shows that may air at `when`: for each series the next episode in order, plus
        anything that isn't part of a series, plus the season's holiday shows (out of order).
        Tapes split one TAPE_WEIGHT between them, archive.org's downloads ARCHIVE_SHARE of the odds.
        Returns [(src, entry, weight, in_order)].
        """
        hour = broadcast_hour(when)[1]
        season = holiday_season(when.date())
        out, by_series, tapes, archived = [], {}, [], []
        with self.lock:
            positions = dict(self.series_pos)
            # Episodes already in the lineup count as aired, so the next pick follows them (and a
            # second copy of the same episode from another folder is passed over)
            for src in exclude:
                e = self.entries.get(src)
                if e and e.get("series") and e.get("order"):
                    if positions.get(e["series"]) is None or e["order"] > positions[e["series"]]:
                        positions[e["series"]] = e["order"]
            recently = time.time() - RERUN_HOURS * 3600
            for src, e in self.entries.items():
                if (src in exclude or src in self.reserved or not self._matches(src, e, category)
                        or (series and e.get("series") != series) or not allowed(src, hour)
                        or e["last_played"] > recently):
                    continue
                tag = holiday_tag(src)
                if tag and tag != season:
                    continue  # a Christmas episode waits for Christmas
                if e.get("archive"):
                    archived.append((src, e, HOLIDAY_WEIGHT if tag else 1.0))
                elif is_tape(src):
                    # Odds within the tapes' shared slot: in season, the holiday tape is the likely one
                    tapes.append((src, e, (HOLIDAY_WEIGHT if tag else 1.0) / (1 + 2 * e["plays"])))
                elif tag:
                    out.append((src, e, HOLIDAY_WEIGHT / (1 + 3 * e["plays"]), False))
                elif e.get("series"):
                    by_series.setdefault(e["series"], []).append((src, e))
                else:
                    out.append((src, e, 1.0 / (1 + 2 * e["plays"]), False))
        for key, eps in by_series.items():
            pos = positions.get(key)
            eps.sort(key=lambda x: x[1]["order"])
            ahead = [x for x in eps if pos is None or x[1]["order"] > pos]
            if not ahead and not self.indexer.has_after(key, pos):
                ahead = eps  # the series has run its course: start it over
            if ahead:
                src, e = ahead[0]
                waiting = sum(1 for _, x in ahead if x["plays"] == 0)
                out.append((src, e, max(1, waiting) / (1 + 2 * e["plays"]), True))
        total = sum(w for _, _, w in tapes)
        out += [(src, e, TAPE_WEIGHT * w / total, False) for src, e, w in tapes]
        if archived:
            # All of the odds when nothing else is cached
            others = sum(w for _, _, w, _ in out)
            share = others * ARCHIVE_SHARE / (1 - ARCHIVE_SHARE) if others else 1.0
            total = sum(w for _, _, w in archived)
            out += [(src, e, share * w / total, False) for src, e, w in archived]
        return out

    def music_pool(self, when):
        """Cached music videos that may air at `when`, not already booked for a block. A holiday's
        videos wait for its season, like its shows."""
        hour = broadcast_hour(when)[1]
        season = holiday_season(when.date())
        with self.lock:
            return [(src, e) for src, e in self.entries.items()
                    if e["category"] == "music" and src not in self.reserved and allowed(src, hour)
                    and MUSIC_VIDEO_SECONDS[0] <= e["duration"] <= MUSIC_VIDEO_SECONDS[1]
                    and holiday_tag(src) in (None, season)]

    def commercial_pool(self, hour):
        """Cached commercials allowed at broadcast hour `hour`, not already booked for a break."""
        with self.lock:
            return [(src, e) for src, e in self.entries.items()
                    if e["category"] == "commercials" and src not in self.reserved and allowed(src, hour)
                    and 0 < e["duration"] <= COMMERCIAL_MAX_SECONDS]

    def reserve(self, src):
        with self.lock:
            self.reserved.add(src)

    def entry(self, src):
        with self.lock:
            e = self.entries.get(src)
            return dict(e) if e else None

    def release(self, src):
        with self.lock:
            self.reserved.discard(src)

    def aspect(self, path):
        """The display aspect of a cached file, saved when it was copied. Older copies (and a probe that
        failed) are probed now; a good answer is kept and saved with the manifest next time."""
        name = os.path.basename(path)
        with self.lock:
            e = next((e for e in self.entries.values() if e["file"] == name), None)
            if e is None:
                return None
            if e.get("aspect"):
                return e["aspect"]
        dar = probe_aspect(path)
        if dar:
            with self.lock:
                e["aspect"] = dar
        return dar

    def set_breaks(self, src, breaks):
        with self.lock:
            if src in self.entries:
                self.entries[src]["breaks"] = breaks
        self._save_manifest()

    def mark_played(self, src, in_order=False):
        with self.lock:
            self.reserved.discard(src)
            e = self.entries.get(src)
            if e:
                e["plays"] += 1
                e["last_played"] = time.time()
                if e["category"] != "commercials":
                    self.aired[src] = e["last_played"]
                    keep = time.time() - AIRED_KEEP_DAYS * 86400
                    self.aired = {s: t for s, t in self.aired.items() if t > keep}
                    try:
                        write_json(AIRED_FILE, self.aired)
                    except OSError as err:
                        logger.error(f"Could not save what aired: {err}")
                if in_order and e.get("series") and e.get("order"):
                    self.series_pos[e["series"]] = e["order"]
                    try:
                        write_json(SERIES_FILE, self.series_pos)
                    except OSError as err:
                        logger.error(f"Could not save series positions: {err}")
                if e.get("archive"):
                    # archive.org's downloads go once they've aired, and the archive worker fetches another
                    del self.entries[src]
                    self._remove_file(e)
                    self.archive_wake.set()
        self._save_manifest()

        if self.refilling or self.wake.is_set() or time.time() - self.last_refill < REFILL_RETRY_MINUTES * 60:
            return
        if self.unplayed_hours() < LOW_WATER_HOURS:
            logger.info(f"Less than {LOW_WATER_HOURS}h of unplayed shows cached, refilling early.")
            self.wake.set()
        elif self._holiday_short():
            logger.info("Holiday shows are running low, refilling early.")
            self.wake.set()
        elif self._music_short():
            logger.info("Music videos are running low, refilling early.")
            self.wake.set()

    # The NAS's shows only: archive.org's come and go by themselves, so they don't hold off a refill

    def unplayed_hours(self):
        with self.lock:
            return sum(e["duration"] for e in self.entries.values()
                       if e["category"] in MAIN_CATEGORIES and e["plays"] == 0 and not e.get("archive")) / 3600

    def unplayed_count(self, category):
        with self.lock:
            return sum(1 for src, e in self.entries.items()
                       if self._matches(src, e, category) and e["plays"] == 0 and not e.get("archive"))

    def stats_line(self):
        with self.lock:
            size = sum(e["size"] for e in self.entries.values())
            archived = sum(1 for e in self.entries.values() if e.get("archive"))
            commercials = sum(1 for e in self.entries.values() if e["category"] == "commercials" and not e.get("archive"))
            music = sum(1 for e in self.entries.values() if e["category"] == "music")
            shows = len(self.entries) - commercials - music - archived
        return (f"Cache: {shows} shows, {commercials} commercials, {music} music videos, {archived} from archive.org, "
                f"{size / GB:.1f}GB, {self.unplayed_hours():.1f}h of unplayed shows")

    # --- What to fetch first ---

    def _ahead(self, key):
        """Unplayed cached episodes of a series that come after its position."""
        pos = self.series_pos.get(key)
        return [e for e in self.entries.values()
                if e.get("series") == key and e["plays"] == 0 and (pos is None or e["order"] > pos)]

    def _recently_aired(self, days=REFETCH_DAYS):
        """What aired within `days`: not to be copied back from the NAS yet."""
        cutoff = time.time() - days * 86400
        with self.lock:
            return {src for src, t in self.aired.items() if t > cutoff}

    def _tapes_waiting(self):
        """Unplayed tapes on the SD card."""
        with self.lock:
            return sum(1 for src, e in self.entries.items() if is_tape(src) and e["plays"] == 0)

    def _next_episodes(self, key, count, holiday=None):
        """Up to `count` episodes of a series to copy next, so the cache holds a run in order. With
        `holiday`, that season's episodes of the series come first. Episodes aired within REFETCH_DAYS wait,
        unless there's nothing else (then the ones aired longest ago)."""
        with self.lock:
            ahead = self._ahead(key)
            have = {tuple(e["order"]) for e in self.entries.values() if e.get("series") == key and e.get("order")}
            pos = self.series_pos.get(key)
            frontier = max([o for o in [pos] + [e["order"] for e in ahead] if o is not None], default=None)
            cached = set(self.entries)
            aired = dict(self.aired)
        recent, too_soon = self._recently_aired(), self._recently_aired(REFETCH_MIN_DAYS)
        episodes = self.indexer.episodes(key)
        out = []

        def take(order, src, again=False):
            if (len(out) < count and src not in cached and tuple(order) not in have
                    and src not in (too_soon if again else recent)):
                out.append(src)
                have.add(tuple(order))
        if holiday:
            for order, src in episodes:
                if holiday_tag(src) == holiday:
                    take(order, src)
        for order, src in episodes:
            if frontier is None or order > frontier:
                take(order, src)
        for order, src in episodes:  # past the last episode: start again from the top
            take(order, src)
        for order, src in sorted(episodes, key=lambda ep: aired.get(ep[1], 0)):
            take(order, src, again=True)
        return out

    def _next_episode(self, key):
        """The next episode of a series to copy, or None when enough of it is cached already."""
        with self.lock:
            if len(self._ahead(key)) >= SERIES_CACHED_AHEAD:
                return None
        nxt = self._next_episodes(key, 1)
        return nxt[0] if nxt else None

    def _wanted_shows(self, urgent=False):
        """Shows to copy before anything else: the next marathon's episodes, then the season's shows.
        urgent: only what's needed today (a marathon within a day, a season that's already on)."""
        now = datetime.datetime.now()
        with self.lock:
            cached = set(self.entries)
        today = broadcast_date(now)
        held = [a["src"] for a in screens.anniversaries(today, today + td(days=1)).values()]
        for src in held:
            self.reserve(src)   # an anniversary episode waits for its slot (Station._queue_anniversary)
        wanted = [src for src in held if src not in cached]
        for name, day, key in upcoming_marathons(now, self.indexer, 1 if urgent else MARATHON_PREP_DAYS):
            season = holiday_season(day)
            with self.lock:
                have = len(self._ahead(key)) + sum(1 for s, e in self.entries.items()
                                                   if e.get("series") == key and e["plays"] == 0
                                                   and season and holiday_tag(s) == season)
            wanted += [s for s in self._next_episodes(key, MARATHON_EPISODES - have, season) if s not in wanted]
        season = holiday_season(now.date()) if urgent else holiday_ahead(now.date())
        if season:
            with self.lock:
                waiting = [(s, e) for s, e in self.entries.items() if e["category"] in MAIN_CATEGORIES
                           and e["plays"] == 0 and holiday_tag(s) == season and not e.get("archive")]
                cached = set(self.entries)
                aired = dict(self.aired)
            have = len(waiting)
            per_series = collections.Counter(e.get("series") for _, e in waiting if e.get("series"))
            too_soon = self._recently_aired(REFETCH_MIN_DAYS)
            pool = [s for s in self.indexer.holiday_files(season, False)
                    if s not in cached and s not in wanted and s not in too_soon]
            random.shuffle(pool)
            # Not aired lately first, in random order; once the season has run through those, the ones aired
            # longest ago (a stable sort keeps the shuffle among the fresh ones)
            cutoff = time.time() - REFETCH_DAYS * 86400
            pool.sort(key=lambda s: (aired.get(s, 0) > cutoff, aired.get(s, 0) if aired.get(s, 0) > cutoff else 0))
            tape_room = TAPES_CACHED - self._tapes_waiting()
            for src in pool:
                if have >= HOLIDAY_SHOWS_CACHED:
                    break
                key = self.indexer.info(src)[0]
                if key and per_series[key] >= HOLIDAY_PER_SERIES:
                    continue
                if is_tape(src):
                    if tape_room <= 0:
                        continue
                    tape_room -= 1
                wanted.append(src)
                have += 1
                if key:
                    per_series[key] += 1
        return wanted

    def _wanted_commercials(self):
        season = holiday_ahead(datetime.date.today())
        if not season:
            return []
        with self.lock:
            have = sum(1 for s, e in self.entries.items()
                       if e["category"] == "commercials" and holiday_tag(s) == season and not e.get("archive"))
            cached = set(self.entries)
        pool = [s for s in self.indexer.holiday_files(season, True) if s not in cached]
        return random.sample(pool, min(len(pool), max(0, HOLIDAY_COMMERCIALS_CACHED - have)))

    def _holiday_short(self):
        """True (once a day) when today's marathon or the current season is short of cached shows."""
        today = datetime.date.today()
        if self.holiday_refill_day == today or not self._wanted_shows(urgent=True):
            return False
        self.holiday_refill_day = today
        return True

    def _music_short(self):
        """True (once a day) when fewer than MUSIC_MIN_CACHED unplayed music videos are cached and the
        NAS has more to give."""
        today = datetime.date.today()
        if self.music_refill_day == today or self.unplayed_count("music") >= MUSIC_MIN_CACHED:
            return False
        with self.lock:
            cached = set(self.entries)
        if not self.indexer.get_random("music", exclude=cached):
            return False
        self.music_refill_day = today
        return True

    def _worker(self):
        if self.unplayed_hours() < LOW_WATER_HOURS or self._holiday_short() or self._music_short():
            self._safe_refill()
        while True:
            now = datetime.datetime.now()
            next_run = now.replace(hour=REFILL_HOUR, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += td(days=1)
            # Short waits, so the schedule follows the clock if NTP corrects it after boot
            while not self.wake.is_set() and datetime.datetime.now() < next_run:
                self.wake.wait(timeout=600)
            hours = self.unplayed_hours()
            if (self.wake.is_set() or hours < REFILL_BELOW_HOURS or self.indexer.is_stale()
                    or self._wanted_shows() or self._wanted_commercials()):
                self._safe_refill()
            else:
                logger.info(f"Refill check: {hours:.1f}h of unplayed shows cached, leaving the NAS asleep.")
            self.wake.clear()

    def _safe_refill(self):
        try:
            self.refill()
        except Exception as e:
            logger.error(f"Refill failed: {e}")

    def refill(self):
        """Wakes the NAS once, tops up the pools, and refreshes the index if it's old."""
        self.refilling = True
        start = time.time()
        copied = 0
        try:
            if not os.path.isdir(MEDIA_ROOT):
                logger.error(f"Refill skipped: NAS not reachable at {MEDIA_ROOT}")
                return
            logger.info(f"Refill starting. {self.stats_line()}")
            # An old index first, so this refill can already copy what's new on the NAS
            if self.indexer.is_stale():
                self.indexer.run_indexer()
            # Marathon episodes and holiday shows first, making room even if that means unplayed shows go
            for src in self._wanted_shows():
                if self._fetch_into_pool(src, "shows", force=True):
                    copied += 1
            for src in self._wanted_commercials():
                if self._fetch_into_pool(src, "commercials", force=True):
                    copied += 1
            full = {"shows": False, "commercials": False, "music": False}
            # Interleave so a cold cache gets a playable block (a show and its commercials) early
            while not all(full.values()):
                for pool, turns in (("shows", 1), ("commercials", 6), ("music", 2)):
                    for _ in range(turns):
                        if full[pool]:
                            break
                        if self._fill_one(pool):
                            copied += 1
                        else:
                            full[pool] = True
        except OSError as e:
            logger.error(f"Refill aborted, NAS problem? {e}")
        finally:
            self.refilling = False
            self.last_refill = time.time()
            elapsed = td(seconds=int(time.time() - start))
            logger.info(f"Refill done: {copied} new files in {elapsed}. {self.stats_line()}")

    def _fill_category(self, attempt):
        """What kind of show to copy next, so the cache matches what the dayparts will ask for."""
        if attempt == 0 and self.unplayed_count("kids") < MIN_CACHED_KIDS:
            return "kids"
        day = random.randrange(7)
        hour = random.randrange(SIGN_ON_HOUR, 24 + SIGN_OFF_HOUR)
        mix = mix_at(day, hour)
        return random.choices(list(mix), weights=list(mix.values()))[0]

    def _fill_one(self, pool):
        """Copies one new file into a pool ("shows", "commercials" or "music"). False once that pool is full."""
        for attempt in range(5):  # a few tries, so one movie that doesn't fit doesn't end the refill
            category = pool if pool != "shows" else self._fill_category(attempt)
            with self.lock:
                cached = set(self.entries)
            recent = self._recently_aired() if pool != "commercials" else set()
            src = self.indexer.get_random(category, exclude=cached | recent)
            if not src:
                continue
            if pool == "shows":
                # A random pick decides the series; copy that series' next episode so it can air in order
                key, order = self.indexer.info(src)
                if key:
                    src = self._next_episode(key)
                    if not src:
                        continue
                if is_tape(src) and self._tapes_waiting() >= TAPES_CACHED:
                    continue
            result = self._fetch_into_pool(src, pool)
            if result is None:
                return False
            if result:
                return True
        return False

    def _fetch_into_pool(self, src, pool, force=False):
        """Copies src if it fits the pool. True when copied, False when skipped, None when the SD card is full."""
        category = "commercials" if pool == "commercials" else self.indexer.category_of(src)
        if not category:
            return False
        budget = {"commercials": COMMERCIAL_POOL_GB, "music": MUSIC_POOL_GB,
                  "shows": CACHE_LIMIT_GB - COMMERCIAL_POOL_GB - MUSIC_POOL_GB - ARCHIVE_POOL_GB}[pool] * GB
        try:
            size = os.path.getsize(src)
        except FileNotFoundError:
            if not os.path.isdir(PATHS[category]):
                raise
            self.indexer.remove(src)
            return False
        if not self._make_room(pool, size, budget, force):
            return False
        if shutil.disk_usage(CACHE_DIR).free - size < MIN_FREE_GB * GB:
            logger.warning("SD card is nearly full, stopping the refill early.")
            return None
        return self._copy(src, category)

    def fetch(self, src, done):
        """Copies one show now (asked for from the remote), then calls done(src)."""
        with self.lock:
            if src in self.entries:
                threading.Thread(target=done, args=(src,), daemon=True).start()
                return True
            if src in self.fetching:
                return True
            self.fetching[src] = done

        def run():
            ok = False
            try:
                ok = bool(self._fetch_into_pool(src, "shows", force=True))
            except Exception as e:
                logger.error(f"Could not fetch {os.path.basename(src)}: {e}")
            with self.lock:
                self.fetching.pop(src, None)
            if ok:
                done(src)
        threading.Thread(target=run, daemon=True).start()
        return True

    def _make_room(self, which, size, budget, force=False):
        """
        Evicts from a pool until size fits: played items first (most plays, longest ago first), then
        unplayed episodes their series has already passed. With force (marathon, holiday and requested
        shows), unplayed shows go too, oldest copies first, except the season's own.
        """
        with self.lock:
            pool = {src: e for src, e in self.entries.items() if pool_of(e) == which}
            used = sum(e["size"] for e in pool.values())
            free = [src for src in pool if src not in self.reserved]
            played = sorted((src for src in free if pool[src]["plays"] > 0),
                            key=lambda src: (-pool[src]["plays"], pool[src]["last_played"]))

            def passed(src):
                e = pool[src]
                pos = self.series_pos.get(e.get("series"))
                return e["plays"] == 0 and e.get("order") and pos is not None and e["order"] <= pos
            candidates = played + [src for src in free if passed(src)]
            if force:
                season = holiday_ahead(datetime.date.today())
                candidates += sorted((src for src in free if src not in candidates
                                      and not (season and holiday_tag(src) == season)),
                                     key=lambda src: pool[src].get("cached", 0))
            evict = []
            while used + size > budget and candidates:
                src = candidates.pop(0)
                evict.append(src)
                used -= pool[src]["size"]
            if used + size > budget:
                return False

            for src in evict:
                e = self.entries.pop(src)
                self._remove_file(e)
                logger.info(f"Evicted: ({e['category']}) {os.path.basename(src)}")
        if evict:
            self._save_manifest()
        return True

    @staticmethod
    def _remove_file(e):
        try:
            os.remove(os.path.join(CACHE_DIR, e["file"]))
        except OSError as err:
            logger.error(f"Error deleting {e['file']}: {err}")

    @staticmethod
    def _file_name(src):
        """The cache's name for src: a hash of the full path keeps same-named episodes from different folders apart."""
        stem, ext = os.path.splitext(os.path.basename(src))
        stem = stem.encode()[:120].decode(errors="ignore")
        return f"{hashlib.sha1(src.encode()).hexdigest()[:10]}-{stem}{ext}"

    def _copy(self, src, category):
        """Copies via a .part file so an interrupted copy never looks complete."""
        name = self._file_name(src)
        dest = os.path.join(CACHE_DIR, name)
        part = dest + ".part"
        try:
            paced_copy(src, part)
        except FileNotFoundError:
            if not os.path.isdir(PATHS[category]):
                raise
            self.indexer.remove(src)
            return False
        except BaseException:
            if os.path.exists(part):
                os.remove(part)
            raise
        os.replace(part, dest)
        self._register(src, name, category)
        return True

    def _register(self, src, name, category, **extra):
        """Adds a file just copied into the cache to the manifest."""
        dest = os.path.join(CACHE_DIR, name)
        entry = {
            "file": name,
            "category": category,
            "size": os.path.getsize(dest),
            "duration": get_video_duration(dest),
            "aspect": probe_aspect(dest),
            "plays": 0,
            "last_played": 0,
            "cached": time.time(),
        }
        entry.update(extra)
        self._describe(src, entry)
        with self.lock:
            self.entries[src] = entry
        self._save_manifest()
        logger.info(f"Cached: ({category}{', archive.org' if entry.get('archive') else ''}) {os.path.basename(src)}")

    # --- archive.org, just ahead ---

    def _archive_ready(self, kind):
        """archive.org downloads of a kind waiting to air."""
        with self.lock:
            return sum(1 for e in self.entries.values() if e.get("archive") == kind and e["plays"] == 0)

    def _archive_worker(self):
        """Keeps ARCHIVE_READY files of each kind downloaded, topping up as soon as one has aired (and every
        ARCHIVE_CHECK_MINUTES, in case archive.org was down)."""
        while True:
            self.archive_wake.clear()
            self._archive_prune()
            for kind, want in ARCHIVE_READY.items():
                try:
                    while self._archive_ready(kind) < want and self._archive_one(kind):
                        pass
                except Exception as e:
                    logger.warning(f"archive.org: no {kind} right now ({e})")
            self.archive_wake.wait(timeout=ARCHIVE_CHECK_MINUTES * 60)

    def _archive_prune(self):
        """Deletes downloads whose holiday has gone by without them airing, so they don't hold a place for a year."""
        season = holiday_ahead(datetime.date.today())
        with self.lock:
            stale = [src for src, e in self.entries.items() if e.get("archive") and src not in self.reserved
                     and holiday_tag(src) not in (None, season)]
            for src in stale:
                self._remove_file(self.entries.pop(src))
                logger.info(f"Out of season, deleted: {os.path.basename(src)}")
        if stale:
            self._save_manifest()

    def _archive_one(self, kind):
        """Downloads one file of a kind from archive.org into the cache. False when none could be had, or it
        wouldn't fit. A holiday's files are only fetched in (or just ahead of) its season."""
        got = archive.pick(kind)
        if not got:
            return False
        src = os.path.join(ARCHIVE_ROOT, *got["path"].split("/"))
        tag = holiday_tag(src)
        if tag and tag != holiday_ahead(datetime.date.today()):
            return True   # passed over: try another
        with self.lock:
            used = sum(e["size"] for e in self.entries.values() if e.get("archive"))
        if (used + got["size"] > ARCHIVE_POOL_GB * GB
                or shutil.disk_usage(CACHE_DIR).free - got["size"] < MIN_FREE_GB * GB):
            return False
        name = self._file_name(src)
        part = os.path.join(CACHE_DIR, name + ".part")
        try:
            paced_copy(got["url"], part)
        except BaseException:
            if os.path.exists(part):
                os.remove(part)
            raise
        os.replace(part, os.path.join(CACHE_DIR, name))
        # Not part of a series: they air as they come
        self._register(src, name, got["category"], archive=kind, item=got["item"], series=None, order=None)
        return True


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


# --- Lineup ---

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

    def plan_slot(self, start):
        self.start = start
        self.end, _, _ = slot_end(self.duration, self.category, self.src, start)
        mids = len(act_targets(self.duration, self.category, self.src))
        fill = (self.end - start).total_seconds() - self.duration - INTRO_SECONDS
        if mids:
            self.mid_target = min(MID_BREAK_SECONDS[1], max(MID_BREAK_SECONDS[0], fill / (mids + 1.5)))
        self.end_target = fill - mids * self.mid_target + INTRO_SECONDS


class MusicBlock:
    """A run of cached music videos that airs like a show: back to back, a break after each video in
    break_after, the next show on the grid after its closing break. Has what the lineup, cards, guide and
    breaks read from a Show; Station.air_music plays it."""
    category = "music"

    def __init__(self, name, minutes, videos, break_after, countdown=(), lead=None):
        self.src = f"music:{name}:{id(self)}"
        self.srcs = [src for src, _ in videos]
        self.videos = [(src, os.path.join(CACHE_DIR, e["file"]), e["duration"]) for src, e in videos]
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


class Lineup:
    """The next few hours of shows, chosen ahead of time so cards and the guide can say what's coming."""
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
                if off_air(t):
                    show.sign_off_before = t
                    t = sign_on_after(t)
                show.plan_slot(t)
                if show.marathon:
                    show.marathon_intro = not (prev and prev.marathon == show.marathon and not show.sign_off_before)
                t = show.end
                prev = show
        return t

    def plan(self, first_start, until=None):
        """Schedules shows until the lineup covers LINEUP_HOURS from first_start (or up to `until`)."""
        end = self.retime(first_start)
        horizon = first_start + td(hours=LINEUP_HOURS)
        if until and until > horizon:
            horizon = until
        while end < horizon or not self.shows:
            start = sign_on_after(end) if off_air(end) else end
            with self.lock:
                prev = self.shows[-1] if self.shows else None
            show = self._pick(start, prev)
            if not show:
                break
            with self.lock:
                self.shows.append(show)
            end = self.retime(first_start)
        self.wake.set()

    def _pick(self, start, prev=None):
        with self.lock:
            planned = {s.src for s in self.shows}
        m = marathon_at(start)
        if m:
            name, bday = m
            key = marathon_series(bday, self.cache.indexer)
            cands = self.cache.show_candidates(None, start, planned, series=key) if key else []
            if cands:
                # The season's episodes of the series first, then on in order
                cands.sort(key=lambda c: (c[3], c[1]["order"] or []))
                show = self._take(cands[0])
                show.marathon = name
                return show
        block = self._music_block(start, prev)
        if block:
            return block
        category = roll_category(start)
        order = [category] + [c for c in MAIN_CATEGORIES if c != base_category(category)]
        if category == "kids":
            order.insert(1, "videos")
        points = self._fixed_points(start)
        fallback = None
        for c in order:
            cands = self.cache.show_candidates(c, start, planned)
            fitting = [x for x in cands if not self._crosses(x, start, points)]
            if fitting:
                return self._choose(fitting, start, prev)
            fallback = fallback or cands
        return self._choose(fallback, start, prev) if fallback else None

    def _fixed_points(self, start):
        """Times a show starting at `start` shouldn't run across: midnight on New Year's Eve (for the
        countdown) and the start of a marathon block or a set-time music block (when enough videos are
        cached to fill it)."""
        points = []
        if NEW_YEAR_COUNTDOWN and start.month == 12 and start.day == 31:
            points.append(datetime.datetime.combine(start.date() + td(days=1), datetime.time()))
        bday = broadcast_date(start)
        for day in (bday, bday + td(days=1)):
            for name, days, first, last in MARATHONS:
                if day.weekday() in days:
                    points.append(datetime.datetime.combine(day, datetime.time()) + td(hours=first))
            for name, days, first, minutes in MUSIC_BLOCKS:
                at = datetime.datetime.combine(day, datetime.time()) + td(hours=first)
                if day.weekday() in days and at > start:
                    _, target = self._music_target(minutes, at + td(minutes=minutes))
                    if sum(e["duration"] for _, e in self.cache.music_pool(at)) >= target * MUSIC_MIN_FILL:
                        points.append(at)
        return [p for p in points if p > start]

    @staticmethod
    def _crosses(cand, start, points):
        if not points:
            return False
        src, e, _, _ = cand
        end, _, _ = slot_end(e["duration"] or 1800.0, e["category"], src, start)
        return any(start < p < end for p in points)

    def _choose(self, cands, start, prev=None):
        """Weighted pick that favours shows whose length fits the half-hour grid, and after a short
        segment, the next segment of the same series."""
        grid = on_grid(start)
        weights = []
        for src, e, w, in_order in cands:
            _, extra, aligned = slot_end(e["duration"] or 1800.0, e["category"], src, start)
            if not grid:
                w *= 8.0 if aligned else 1.0  # off the grid: prefer a show that gets us back on it
            elif aligned and e["category"] != "movies":
                w /= 1.0 + extra / 300.0
            if prev and prev.duration < SHORT_SHOW_MINUTES * 60 and in_order and e.get("series") == prev.series:
                w *= 4.0
            weights.append(w)
        return self._take(random.choices(cands, weights=weights)[0])

    def _take(self, cand):
        src, e, _, in_order = cand
        self.cache.reserve(src)
        return Show(src, os.path.join(CACHE_DIR, e["file"]), e, in_order)

    def _music_block(self, start, prev):
        """A music block for `start`: one of MUSIC_BLOCKS when it's that time, otherwise now and then a
        half-hour MUSIC_BLOCK_NAME. None when there isn't one, or not enough videos are cached for it."""
        if not on_grid(start):
            return None
        day, hour = broadcast_hour(start)
        at = start.hour + start.minute / 60
        name, minutes = next(((n, m) for n, days, first, m in MUSIC_BLOCKS
                              if start.weekday() in days and abs(at - first) < 0.01), (None, None))
        if not name:
            if isinstance(prev, MusicBlock) or "kids" in mix_at(day, hour) or random.random() >= MUSIC_CHANCE:
                return None
            name, minutes = MUSIC_BLOCK_NAME, SLOT_MINUTES
            after = start + td(minutes=minutes)
            if any(after.weekday() in days and abs(after.hour + after.minute / 60 - first) < 0.01
                   for _, days, first, _ in MUSIC_BLOCKS):
                return None   # a set-time block follows: no two blocks back to back
        end = start + td(minutes=minutes)
        if any(start < p < end for p in self._fixed_points(start)):
            return None
        return self._build_music(name, minutes, start, end)

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
                show = Show(src, os.path.join(CACHE_DIR, e["file"]), e)
            show.requested, show.marathon, show.in_order = True, None, False
            at = next((i for i, s in enumerate(self.shows) if not s.requested), len(self.shows))
            self.shows.insert(at, show)
            if first_start:
                self.retime(first_start)
        logger.info(f"Requested from the remote: {show.title()}")
        self.wake.set()
        return show

    def next_show(self, start):
        self.plan(start)
        with self.lock:
            if not self.shows:
                return None
            self.last = self.shows.pop(0)
            return self.last

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

    def drop(self, show):
        """Forgets a planned show whose slot went by while the set was on another channel."""
        with self.lock:
            if show in self.shows:
                self.shows.remove(show)
        for src in getattr(show, "srcs", [show.src]):
            self.cache.release(src)

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


# --- Player ---

class Item:
    """One thing for mpv to play. on_end(reason) runs when it finishes, fails or is dropped."""
    def __init__(self, url, duration, kind, label, options=None, on_end=None):
        self.url, self.duration, self.kind, self.label = url, duration, kind, label
        self.options = options or {}
        self.on_end = on_end
        self.started = None
        self.show = None        # the Show an act belongs to
        self.offset = 0.0       # where in the file the act starts
        self.overlay = None     # overlay_filter() arguments, so a glitch can be added at the last moment
        self.channel = None     # (number, name) for a make-believe channel's show
        self.refresh = None     # remakes a card with the latest data just before it's queued (Station._refresh)


class Player:
    """Feeds a long-running mpv over its JSON IPC socket, one item ahead so playback is gapless."""
    AHEAD = 2  # the playing item plus one queued

    def __init__(self):
        self.cv = threading.Condition()
        self.send_lock = threading.Lock()
        self.pending = {}      # playlist entry id -> Item, in queue order
        self.ended = {}        # end-file events that beat their loadfile reply
        self.started = {}      # start-file events that beat their loadfile reply
        self.replies = {}
        self.request_id = 0
        self.epoch = 0         # bumped by flush(), so an item waiting to be queued is dropped
        self.proc = None
        self.sock = None
        self.alive = False
        self.volume, self.muted = 100, False
        try:
            with open(REMOTE_STATE_FILE) as f:
                state = json.load(f)
            self.volume, self.muted = int(state.get("volume", 100)), bool(state.get("muted", False))
        except (OSError, ValueError):
            pass

    def _start(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        try:
            os.remove(MPV_SOCKET)
        except FileNotFoundError:
            pass
        self.proc = subprocess.Popen(PLAYER_CMD + [f"--volume={self.volume}", f"--mute={'yes' if self.muted else 'no'}"])
        for _ in range(100):
            try:
                sock = socket.socket(socket.AF_UNIX)
                sock.connect(MPV_SOCKET)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mpv did not open its IPC socket")
        self.sock = sock
        self.alive = True
        threading.Thread(target=self._reader, args=(sock,), daemon=True).start()
        logger.info("Player started.")

    def _reader(self, sock):
        finished = []
        for line in sock.makefile("rb"):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                if "request_id" in msg:
                    self.replies[msg["request_id"]] = msg
                event = msg.get("event")
                if event == "start-file":
                    item = self.pending.get(msg.get("playlist_entry_id"))
                    if item:
                        item.started = time.time()
                        if item.kind != "static":
                            logger.info(f"Playing: {item.label}")
                    else:
                        self.started[msg.get("playlist_entry_id")] = time.time()  # beat its loadfile reply
                elif event == "end-file":
                    eid = msg.get("playlist_entry_id")
                    item = self.pending.pop(eid, None)
                    if item:
                        finished.append((item, msg))
                    else:
                        self.ended[eid] = msg
                self.cv.notify_all()
            self._finish(finished)
        with self.cv:
            if sock is self.sock:
                self.alive = False
            self.cv.notify_all()

    @staticmethod
    def _finish(finished):
        while finished:
            item, msg = finished.pop(0)
            reason = msg.get("reason", "error")
            if reason == "error":
                logger.error(f"Player failed on {item.label}: {msg.get('file_error', 'unknown error')}")
            if item.on_end:
                try:
                    item.on_end(reason)
                except Exception as e:
                    logger.error(f"Error after {item.label}: {e}")

    def _command(self, command):
        with self.cv:
            self.request_id += 1
            rid = self.request_id
        with self.send_lock:
            self.sock.sendall((json.dumps({"command": command, "request_id": rid}) + "\n").encode())
        with self.cv:
            if not self.cv.wait_for(lambda: rid in self.replies or not self.alive, timeout=10):
                raise RuntimeError(f"mpv did not answer {command}")
            if rid not in self.replies:
                raise RuntimeError("mpv went away")
            return self.replies.pop(rid)

    def _restart(self):
        logger.error("Player stopped, restarting it.")
        with self.cv:
            dropped = [(item, {"reason": "error", "file_error": "player restarted"}) for item in self.pending.values()]
            self.pending.clear()
            self.ended.clear()
            self.started.clear()
        self._finish(dropped)
        time.sleep(2)
        self._start()

    def play(self, item, epoch=None):
        """Queues an item; blocks while mpv already has one playing and one waiting. Returns False, dropping
        the item, if a flush() has happened since `epoch` (default: since the call)."""
        if not self.alive:
            self._start()
        with self.cv:
            if epoch is None:
                epoch = self.epoch
            self.cv.wait_for(lambda: len(self.pending) < self.AHEAD or not self.alive or self.epoch != epoch)
            dropped = self.epoch != epoch
        if dropped:
            self._finish([(item, {"reason": "stop"})])
            return False
        if not self.alive or (self.proc and self.proc.poll() is not None):
            self._restart()
        options = {k: str(v) for k, v in item.options.items()}
        try:
            reply = self._command({"name": "loadfile", "url": item.url, "flags": "append-play", "index": -1,
                                   "options": options})
        except (OSError, RuntimeError) as e:
            logger.error(f"Could not queue {item.label}: {e}")
            self._restart()
            self._finish([(item, {"reason": "error", "file_error": str(e)})])
            return True
        if reply.get("error") != "success":
            self._finish([(item, {"reason": "error", "file_error": reply.get("error")})])
            return True
        eid = reply["data"]["playlist_entry_id"]
        with self.cv:
            early = self.ended.pop(eid, None)
            started = self.started.pop(eid, None)
            if started and not early:
                item.started = started
                if item.kind != "static":
                    logger.info(f"Playing: {item.label}")
            if not early:
                self.pending[eid] = item
        if early:
            self._finish([(item, early)])
        return True

    def busy_until(self):
        """When everything queued so far should have finished playing."""
        now = time.time()
        with self.cv:
            left = 0.0
            for item in self.pending.values():
                if item.started:
                    left += max(0.0, item.duration - (now - item.started))
                else:
                    left += item.duration
        return datetime.datetime.fromtimestamp(now + left)

    def current(self):
        with self.cv:
            return next((item for item in self.pending.values() if item.started), None)

    # --- Remote control ---

    def skip(self):
        """Ends whatever is playing; the queued item takes over."""
        if self.alive:
            self._command(["playlist-next", "force"])

    def flush(self):
        """Drops everything queued and ends the current item (skipping a whole show)."""
        if not self.alive:
            return
        with self.cv:
            self.epoch += 1
            queued = [(eid, item) for eid, item in self.pending.items() if not item.started]
            for eid, _ in queued:
                del self.pending[eid]
            now = time.time()
            for item in self.pending.values():  # being cut short: done as far as the schedule is concerned
                if item.started:
                    item.duration = min(item.duration, now - item.started)
            self.cv.notify_all()
        self._command(["playlist-clear"])
        self._finish([(item, {"reason": "stop"}) for _, item in queued])
        self._command(["playlist-next", "force"])

    def set_volume(self, volume=None, toggle_mute=False):
        if volume is not None:
            self.volume = max(0, min(130, int(volume)))
        if toggle_mute:
            self.muted = not self.muted
        if self.alive:
            self._command(["set_property", "volume", self.volume])
            self._command(["set_property", "mute", self.muted])
        try:
            write_json(REMOTE_STATE_FILE, {"volume": self.volume, "muted": self.muted})
        except OSError:
            pass

    def position(self):
        """Where the current file is (mpv's time-pos), or None."""
        if not self.alive:
            return None
        reply = self._command(["get_property", "time-pos"])
        return reply.get("data") if reply.get("error") == "success" else None

    def add_filter(self, chain, spec):
        """Adds a labelled filter ("@label:...") to the vf or af chain while a file plays. mpv restores
        per-file options when the file ends, so on an item that set vf-append the filter ends with it."""
        self._command([chain, "remove", spec.split(":", 1)[0]])
        reply = self._command([chain, "add", spec])
        if reply.get("error") != "success":
            raise RuntimeError(f"mpv refused the {chain} filter: {reply.get('error')}")

    def remove_filter(self, chain, label):
        if self.alive:
            self._command([chain, "remove", label])


# --- Station ---

def glitch_filters(t0, kind):
    """About a second of bad reception at file time t0: a tracking roll, an interference burst or tearing."""
    t1 = t0 + 1.0
    on = f"enable='between(t,{t0:.2f},{t1:.2f})'"
    if kind == "roll":
        # The picture rolls with the blanking bar going past, then the tracking catches
        video = [f"scroll=v=0.045:{on}", f"drawbox=y='mod((t-{t0:.2f})*300,ih)':w=iw:h=14:color=black:t=fill:{on}",
                 f"noise=alls=25:allf=t:{on}"]
        audio = [f"volume=volume=0.45:{on}", f"highpass=f=500:{on}"]
    elif kind == "tear":
        video = []
        for i in range(6):
            a = t0 + i / 6
            shift = random.choice((-9, -6, 6, 9))
            video.append(f"rgbashift=rh={shift}:gh={shift}:bh={shift}:enable='between(t,{a:.2f},{a + 1 / 6:.2f})'")
        video += [f"noise=alls=35:allf=t:{on}", f"eq=saturation=0.5:{on}"]
        audio = [f"acrusher=bits=7:mix=0.5:{on}"]
    else:  # interference
        video = [f"noise=alls=70:allf=t:{on}", f"rgbashift=rh=4:bh=-4:{on}", f"eq=brightness=0.06:saturation=0.4:{on}",
                 f"drawbox=y='mod((t-{t0:.2f})*520,ih)':w=iw:h=6:color=white@0.35:t=fill:{on}"]
        audio = [f"volume=volume=0.4:{on}", f"acrusher=bits=6:mix=0.6:{on}"]
    return ",".join(video), ",".join(audio)


def overlay_filter(duration, bug, snipe=None, glitch=None, promo=None, popups=None):
    """
    Per-file filters added after the CRT crunch (342x200): the logo bug, the progress line, the
    NEXT / LATER / TONIGHT promo, the "NEXT" lower third, Pop-Up Video bubbles and a reception glitch.
    Returns (vf-append, af-append); either may be None.
    """
    graph, sources, cur, n = [], [], "in", 0

    def stage(text, extra=""):
        nonlocal cur, n
        n += 1
        graph.append(f"[{cur}]{extra}{text}[v{n}]")
        cur = f"v{n}"

    if bug and LOGO_BUG_FILE and os.path.exists(LOGO_BUG_FILE):
        # Bottom right, inside the curved corner
        sources.append(f"movie={LOGO_BUG_FILE}[bug]")
        stage("overlay=x=W-w-16:y=H-h-13", "[bug]")
    if PROGRESS_BAR and duration > 0:
        # One scanline high, sliding in from the left. The frame timestamp is the position in the whole
        # file, so the line carries on across act breaks instead of restarting each act
        sources.append(f"color=c={PROGRESS_COLOR}:s=342x1:d=1,format=rgba[bar]")
        stage(f"overlay=x='-w+w*min(1,t/{duration:.2f})':y=H-11:eval=frame", "[bar]")
    for script in (popups, promo, snipe):
        if script:
            stage(f"ass=filename={script}:fontsdir={cards.FONTS_DIR}")
    audio = None
    if glitch:
        video, audio = glitch_filters(*glitch)
        stage(video)
    if not graph:
        return None, audio and f"lavfi=[{audio}]"
    graph[-1] = graph[-1][:-len(f"[v{n}]")]  # the last stage feeds mpv
    return "lavfi=[" + ";".join(sources + graph) + "]", audio and f"lavfi=[{audio}]"


def osd_msg(text):
    """mpv reads C-style escapes in osd-playing-msg (a lone backslash before N is a "broken escape
    sequence"), so the ASS tags' backslashes have to be doubled there. show-text over IPC doesn't do this."""
    return text.replace("\\", "\\\\")


def channel_osd(number, name, show=None):
    """On-screen text after a channel change: the channel number in VCR green, then the show's title."""
    text = "${osd-ass-cc/0}{\\c&H66FF66&}CH " + f"{number:02d}  " + cards.esc(name).replace("$", "$$")
    if show:
        title = show.osd_title()
        if title.startswith("${osd-ass-cc/0}"):
            title = title[len("${osd-ass-cc/0}"):]
        else:
            title = cards.esc(show.title()).replace("$", "$$")
        text += "\\N{\\c&HFFFFFF&}" + title
    return text


def half_hour_floor(t):
    return t.replace(minute=t.minute - t.minute % 30, second=0, microsecond=0)


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


class Station:
    """Decides what airs next and hands it to the player, slightly ahead of time."""
    def __init__(self, cache, lineup, player):
        self.cache, self.lineup, self.player = cache, lineup, player
        self.prev_kind = None
        self.last_id_hour = None
        self.good_morning = False
        self.on_air = False
        self.next_start = None      # when the next show should start (a :00/:30 boundary, or sign-on)
        self.current_show = None
        self.anniversary = None     # today's anniversary episode once it's queued, until its card airs
        self.anniversary_day = None
        self.skipping = False
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
        if os.path.dirname(item.url) == CACHE_DIR:
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
        if off_air(start):
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
        item = Item(os.path.join(CACHE_DIR, e["file"]), e["duration"], "commercial", os.path.basename(src),
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
                    item.refresh = lambda kind=kind: self.build_special(kind, self.eta(), first_start, fresh=True)
                return item
            self.special_last[kind] = now - SPECIALS[kind] * 60 + 900  # no data right now, try again in 15 min
        return None

    def build_special(self, kind, airs_at, first_start, fresh=False):
        """A special screen for airs_at, or None without data. fresh: made just before it's queued, so the
        data is fetched again unless it's very recent, with less time to wait for it (see _refresh)."""
        season, month = holiday_look(airs_at.date()), heritage_month(airs_at.date())
        if kind == "guide":
            return cards.guide(self.guide_rows(first_start), airs_at, season)
        if kind == "weather":
            if WEATHER_LAT is None:
                return None
            data = (cards.fetch_weather(WEATHER_LAT, WEATHER_LON, WEATHER_TZ, max_age_minutes=2, timeout=4) if fresh
                    else cards.fetch_weather(WEATHER_LAT, WEATHER_LON, WEATHER_TZ))
            return cards.weather(data, WEATHER_PLACE, airs_at, season) if data else None
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
            data = (cards.fetch_weather(WEATHER_LAT, WEATHER_LON, WEATHER_TZ) if WEATHER_LAT is not None
                    else None)
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

        breaks = show.breaks if show.breaks is not None else show.even_breaks()
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
            if not last:
                options["end"] = bounds[i + 1]
            if i == first and SHOW_TITLE_SECONDS:
                options["osd-playing-msg"] = osd_msg(show.osd_title())
                options["osd-playing-msg-duration"] = SHOW_TITLE_SECONDS * 1000
            overlay = dict(duration=show.duration, bug=True)
            if i == first and not join and PROMO_AT and bounds[i + 1] - begin > PROMO_AT + 3 * PROMO_PANEL_SECONDS + 15:
                overlay["promo"] = self.promo(show, begin + PROMO_AT)
            if last and SNIPE_SECONDS and show.duration - begin > SNIPE_SECONDS + 15:
                overlay["snipe"] = self.snipe(show)
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
            overlay = dict(duration=duration, bug=True)
            if duration - begin > MUSIC_TITLE_AT + 2 * MUSIC_TITLE_SECONDS + 5:
                panels = ([("Now", artist)] if artist else []) + [(str(year) if year else "Video", song)]
                overlay["promo"] = events.promo(panels, begin + MUSIC_TITLE_AT, MUSIC_TITLE_SECONDS)
            if i == last and SNIPE_SECONDS and duration - begin > SNIPE_SECONDS + 15:
                overlay["snipe"] = self.snipe(block, duration)
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
        show = Show(src, os.path.join(CACHE_DIR, e["file"]), e)
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
        breaks = show.breaks if show.breaks is not None else show.even_breaks()
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
            return None
        nxt = upcoming[0]
        name = "More " + nxt.name if nxt.marathon and nxt.name == show.name else nxt.name
        end = length or show.duration
        return events.snipe(name, end - SNIPE_SECONDS, end - 2)

    def promo(self, show, at):
        """The lower third early in a show: NEXT, then LATER (the next different show) and TONIGHT
        (what's planned for TONIGHT_HOUR) with their times. None when the station signs off next."""
        tonight = datetime.datetime.combine(show.start.date(), datetime.time(TONIGHT_HOUR))
        if not (SIGN_ON_HOUR <= show.start.hour and show.end < tonight):
            tonight = None
        if tonight:
            self.lineup.plan(show.end, until=tonight + td(minutes=SLOT_MINUTES))
        upcoming = [s for s in self.lineup.upcoming(show.end)]
        if not upcoming or upcoming[0].sign_off_before:
            return None
        nxt = upcoming[0]
        panels = [("Next", "More " + nxt.name if nxt.marathon and nxt.name == show.name else nxt.name)]
        # LATER skips more episodes of the same series ("X-Play", then "X-play" again)
        later = next((s for s in upcoming[1:] if s.name.lower() != nxt.name.lower() or s.sign_off_before), None)
        if later and later.sign_off_before:
            later = None
        if later:
            panels.append(("Later", f"{later.name} · {cards.clock(later.start)}"))
        if tonight:
            prime = next((s for s in upcoming if s.start and s.start >= tonight), None)
            if prime and prime.start < tonight + td(minutes=SLOT_MINUTES) and not prime.sign_off_before \
                    and prime is not nxt and prime is not later:
                name = f"{prime.name} Marathon" if prime.marathon_intro else prime.name
                panels.append(("Tonight", f"{name} · {cards.clock(prime.start)}"))
        logger.info("Promo: " + " / ".join(f"{label.upper()} {name}" for label, name in panels))
        return events.promo(panels, at, PROMO_PANEL_SECONDS)

    # --- Emergency alerts ---

    def watch_alerts(self):
        """Background thread: polls the NWS and puts cut-ins and crawls on the air."""
        while True:
            try:
                self.alerts.poll()
                self.check_alerts()
            except Exception:
                logger.exception("Alert check failed")
            time.sleep(ALERT_CHECK_SECONDS)

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

    def cut_in(self, card):
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
        self.eas_pending = card
        self.skipping = True
        self.player.flush()

    def air_alert(self):
        """The step after a cut-in: the alert card, then back to the show where it was cut, or to whatever
        the main channel has on by now (if it was cut during a break or on another channel)."""
        card, self.eas_pending = self.eas_pending, None
        resume, self.eas_resume = self.eas_resume, None
        self.detour, self.rejoining = None, False
        self.prev_kind = "card"  # a burst of static as it cuts in
        if not self.emit(self.card(card)):
            return
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
        breaks = show.breaks if show.breaks is not None else show.even_breaks()
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

    def watch_scores(self):
        """Background thread: crawls the live scores across a show every SCORES_MINUTES while games are on."""
        while True:
            try:
                self.check_scores()
            except Exception:
                logger.exception("Score check failed")
            time.sleep(SCORES_CHECK_SECONDS)

    def check_scores(self):
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
        """Lays the score crawl over the show that's playing, if it fits in the window."""
        window = self._score_window()
        position = self.player.position()
        if not window or position is None:
            return False
        item, earliest, latest = window
        start = position + 0.8
        shown, d = scores.fit(games, SCORES_MAX_SECONDS)
        if start < earliest or start + d > latest:
            return False
        script = scores.ticker_script(shown, start)
        self.player.add_filter("vf", f"@scores:lavfi=[ass=filename={script}:fontsdir={cards.FONTS_DIR}]")
        logger.info(f"Scores ({d:.0f}s, {len(shown)} of {len(games)} live games) over {item.label}")
        return True

    # --- Remote ---

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

    # 1. Initialize Indexer (reads the SD card copy; only touches the NAS on a first run)
    indexer = MediaIndexer()

    # 2. Initialize Cache Manager (refills in the background right away if the cache is low)
    cache_manager = CacheManager(indexer)

    # 3. Plan, then play forever: station ID or bumper, the show in acts with commercial breaks
    #    between them, then a break with the occasional guide or weather and an "Up Next" card
    lineup = Lineup(cache_manager)
    player = Player()
    station = Station(cache_manager, lineup, player)
    remote.start(station, REMOTE_PORT)
    if station.alerts:
        threading.Thread(target=station.watch_alerts, daemon=True).start()
    if station.scores:
        threading.Thread(target=station.watch_scores, daemon=True).start()
    station.run()

if __name__ == "__main__":
    main()
