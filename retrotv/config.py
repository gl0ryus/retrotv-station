"""
The station's settings, where its files live, and the log. Everything else imports from here.
"""
import datetime
import json
import logging
import os
import re

from retrotv import cards
from retrotv.paths import ASSETS_DIR, HOME_DIR, INDEX_FILE, LOCATION_FILE, MANIFEST_FILE, MEDIA_ROOT, NAS_ROOT


PATHS = {
    "commercials": os.path.join(MEDIA_ROOT, "commercials"),
    "movies": os.path.join(MEDIA_ROOT, "movies"),
    "videos": os.path.join(MEDIA_ROOT, "shows"),
    "g4": os.path.join(MEDIA_ROOT, "g4"),
    "music": os.path.join(MEDIA_ROOT, "music_videos"),
}
MAIN_CATEGORIES = ["videos", "movies", "g4"]
VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.wmv')
CACHE_DIR = os.path.join(HOME_DIR, "retrotv_cache")
# Shows and movies go on the USB drive when it's mounted (see CacheManager._usb_ready); commercials, music videos and
# archive.org's downloads stay on the SD card
USB_DIR = "/mnt/retrotv_usb"
USB_SHOWS_DIR = os.path.join(USB_DIR, "shows")
# Video files the player can't load wait here for a person to look at them, on the drive they were on
REVIEW_DIRS = {USB_DIR: os.path.join(USB_DIR, "_for_review"), HOME_DIR: os.path.join(HOME_DIR, "_for_review")}
# What mpv says when the file is at fault. Anything else (no sound device, the player restarting) isn't the file's doing
BAD_FILE_ERRORS = ("unrecognized file format", "loading failed", "no audio or video data played", "nothing to play")
MAX_SET_ASIDE_AN_HOUR = 3   # more than this and it's the drive, not the files: they stay where they are
USB_SHOWS_GB = 105         # of the drive's 114
USB_WAIT_SECONDS = 30      # at boot the drive can mount a little after the station starts
TURNS_FILE = os.path.join(HOME_DIR, "retrotv_turns.json")      # group id -> which of its shows takes the next turn
SCHEDULE_FILL_DAYS = 14    # the refill copies what the weekly schedule airs this far ahead, soonest first
FULL_AFTER_MISSES = 10     # this many copies in a row without room: the pool is full of what's wanted sooner
POOL_CHOICES = 8           # a shuffle group picks whichever of the longest waiting this many best fills its slot
SIGN_OFF_MARGIN_MINUTES = 10   # a show that would start this close to sign-off waits for the morning
# The index (INDEX_FILE) lives on the SD card too, so the NAS share can stay read-only and asleep
NAS_INDEX_FILE = os.path.join(NAS_ROOT, "station", "legacy", "media_index.json")  # old shared index, only read to seed a first run

# Everything plays from the cache. The NAS is only touched during a refill, which fills
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

# Public domain video from archive.org (retrotv/archive.py): 50s and 60s TV, old movies, cartoons, short films and
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
# The CRT crunch, under everything a file lays over itself (retrotv.overlays). One scale straight to cover 342x200 in
# the picture's display shape, then a centre crop: the panel's shape at 200 lines, shown with SAR 512/513 at exactly
# 1024x600, so each scanline is 3 panel pixels. Then composite-style colour fringing, saturation, grain and a vignette,
# all at low res so they stay cheap. Video faster than about 33 fps first drops every other frame: nobody sees it at this
# size, and the CRT shader can't keep up at 60 anyway (it dropped 143 frames in 30 s of a 60 fps show; this, 1).
CRUNCH_VF = ("lavfi=[select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.03)',"
             "scale=w='if(gt(dar,342/200),trunc(200*dar/2)*2,342)':h='if(gt(dar,342/200),200,trunc(342/dar/2)*2)':flags=area,"
             "setsar=1,crop=342:200,setsar=sar=512/513:max=1000,eq=saturation=1.3:contrast=1.08,chromashift=cbh=-1:crh=1,"
             "noise=alls=12:allf=t+u,vignette=angle=PI/5]")
# Tuned on 2026-11-03 by playing the same files each way on the Pi: a quarter less CPU and memory for mpv.
# - read ahead 16 MiB, keep nothing behind: every act loads afresh, so nothing ever seeks back into it (was 64 MiB
#   ahead and 50 behind). Refill copies are also paced while on the air (COPY_MB_PER_SEC_ON_AIR)
# - bilinear scaling on the GPU: the CRT shader makes the look, lanczos was wasted work
# - skip H.264/HEVC deblocking: invisible at 342x200, 15-25% less decoding
# - not the hardware decoder: copying its frames back for the crunch costs more CPU than it saves
PLAYER_CMD = ["/usr/bin/mpv", "--profile=retrotv", "--really-quiet", "--idle=yes", "--force-window=yes",
              "--cache=yes", "--demuxer-max-bytes=16MiB", "--demuxer-max-back-bytes=0",
              "--scale=bilinear", "--cscale=bilinear", "--dscale=bilinear", "--vd-lavc-skiploopfilter=all",
              f"--vf={CRUNCH_VF}", f"--input-ipc-server={MPV_SOCKET}"]
# glibc gives each of mpv's threads its own memory pool and keeps what they free: two pools at most cut how fast mpv's
# memory grows from 20 to 4.5 MB a minute, with short items back to back
PLAYER_ENV = {"MALLOC_ARENA_MAX": "2"}
# mpv's memory creeps up while it runs (2.8 GB after 13 hours on 2026-11-03, which hung the Pi): past this it's
# restarted at the next break, never during a show. It costs a second of black
PLAYER_MEMORY_MB = 1200
PLAYER_MEMORY_CHECK_SECONDS = 60
SHOW_TITLE_SECONDS = 0     # show a show's name on screen as it starts (0 = off; the promo lower third and the
                           # Up Next card already name it); commercials never get one
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
# A show that would miss its schedule slot with full breaks gives up commercials to fit: fewer mid-show breaks, then
# none, then only the Up Next card before the next show. If it still doesn't fit, its group decides: with "overflow" on
# it runs past the slot and the next one starts late; off, it's cut off at the end of the slot. A group that gets cut
# off starts nothing with less than MIN_CUT_MINUTES left in the slot.
TIGHT_END_SECONDS = UP_NEXT_SECONDS[0] + STATIC_SECONDS + INTRO_SECONDS
MIN_CUT_MINUTES = 10
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
    "blurb": 30,     # text bumps: facts, food for thought, dad jokes, raunchier late at night (retrotv.blurbs)
    "sky": 120,      # Tonight's Sky: the moon, planets and ISS passes over the house, sunset to sign-off
    "teletext": 150, # G|TV Text: teletext pages of tonight's listings, the weather, markets and odd news
    "art": 240,      # Art Break: the day's picture, drawn by Claude, building up shape by shape
    "marathon": 120, # an ad for the next marathon, its picture scrolling behind the day and start time
}
# How far the text bumps go: clean until BLURB_CHEEKY_HOUR, cheeky until BLURB_RAUNCHY_HOUR, raunchy to sign-off
# (crude, never hateful). Always clean in the kids' dayparts
BLURB_CHEEKY_HOUR = 21
BLURB_RAUNCHY_HOUR = 23
SPECIAL_GAP_MINUTES = 10
FRESH_SPECIALS = ("weather",)   # made again with the latest data just before they're queued
# Where the weather and the emergency alerts are for, kept out of the repo in LOCATION_FILE at the top of the station
# folder: {"place": "Town, ST", "lat": 28.0, "lon": -82.5}. Without it there's no weather card and no alerts
WEATHER_TZ = "America/New_York"
# Emergency alerts from the National Weather Service for WEATHER_LAT/LON (retrotv/alerts.py). The worst
# warnings cut into the show with the alert tones and a voice, then it picks up where it left off; other
# warnings and watches crawl across the top. Silent while off the air: what's still active crawls at sign-on
# Live launches cut in: Florida launches, crewed flights and the first flight of a new rocket (see retrotv.launches)
LAUNCHES = True
LAUNCH_CHECK_SECONDS = 15
LAUNCH_BEFORE_SECONDS = 60     # the stream starts this long before liftoff, after the card
LAUNCH_WATCH_SECONDS = 600     # and stays on this long: to about when the booster lands and the payload reaches orbit
LAUNCH_START_GRACE_SECONDS = 60     # how long a stream gets to start before it's given up on
LAUNCH_QUEUE_NEXT_SECONDS = 6       # what follows it is queued this long before it ends
ALERTS = True
ALERT_CHECK_SECONDS = 15
ALERT_CRAWL_MINUTES = 15     # active alerts crawl again this often
ALERT_TEST_HOURS = (10, 19)  # the weekly test airs at a break between shows within these hours
# Items a crawl can go over. They all carry per-file vf options, so mpv drops the crawl filter when they end
CRAWL_KINDS = ("act", "detour", "commercial")
LOTTERY_HOUR = 23          # the nightly lottery drawing, right before the 11 PM show (None = off)
# Live scores from ESPN's scoreboard (retrotv/scores.py): while games are on, a SCORES lower third shows them
# across a show (not breaks or music videos) every SCORES_MINUTES. The leagues in crawl order, each with the
# home team whose game leads it in mustard: Bucs, Magic, Lightning, Rays
SCORES = True
SCORES_TEAMS = {"nfl": "TB", "nba": "ORL", "nhl": "TB", "mlb": "TB"}
SCORES_MINUTES = 20
SCORES_IDLE_MINUTES = 10   # with no games on, look again this often
SCORES_MAX_SECONDS = 60    # a longer card leaves games off the end
SCORES_CHECK_SECONDS = 15

# Time-of-day programming: the first daypart that matches sets the mix, as shares of airtime (a pick's
# odds are divided by how long it would run, so a movie isn't three times its share). Days are 0 = Monday
# ... 5 = Saturday; hours run past 24 for after midnight (26 = 2 AM), still counted as the evening before
EVERY_DAY = tuple(range(7))
DAYPARTS = [
    ("Saturday morning cartoons", (5,), 9, 12, {"kids": 80, "videos": 20}),
    ("Kids' mornings", (0, 1, 2, 3, 4, 6), 9, 12, {"kids": 30, "videos": 70}),
    ("Late night", EVERY_DAY, 22, 26, {"movies": 45, "videos": 55}),
]
DEFAULT_MIX = {"videos": 90, "movies": 10}
# Mix categories that take in more than one library folder: G4 airs as ordinary shows, each of its
# series one among the rest
MIX_FOLDERS = {"videos": ("videos", "g4")}

# Broadcast standards, matched (case-insensitive regexes) against the file name and then its
# folders. Late-night fare airs only from LATE_NIGHT_HOUR until sign-off, kids' shows only before
# KIDS_END_HOUR. Bumps in the Bumps folder are Adult Swim's unless they say Toonami or Cartoon Network
LATE_NIGHT_HOUR = 22
KIDS_END_HOUR = 12
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
# Heritage months: (first, last) as (month, day). Kept subtle on screen (see retrotv.cards.MONTH_LOOKS), and
# Today in History and Pop Quiz lean toward them (retrotv.screens.TOPICS)
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
HOLIDAY_COMMERCIALS_CACHED = 60
HOLIDAY_LEAD_DAYS = 3
NEW_YEAR_COUNTDOWN = True  # a countdown into midnight on New Year's Eve

# Series play in episode order across days (season/episode numbers or air dates), picking up where
# they left off. SERIES_CACHED_AHEAD is the most unplayed episodes of one series kept on the SD card
SERIES_FILE = os.path.join(HOME_DIR, "retrotv_series.json")
RERUN_HOURS = 12           # nothing airs again within this long (holiday episodes included)
# When each show and music video last aired, kept even after its copy leaves the SD card (the manifest forgets
# it then), so the refill doesn't copy back what just aired: nothing aired within REFETCH_DAYS is fetched again,
# unless everything a pick could use has been (then what aired longest ago goes first), and never anything aired
# within REFETCH_MIN_DAYS
AIRED_FILE = os.path.join(HOME_DIR, "retrotv_aired.json")
REFILL_NOW_FILE = os.path.join(HOME_DIR, "retrotv_refill_now")   # touch it and restart: a refill right away
AIRED_KEEP_DAYS = 3650     # long enough for a show to run its course on the schedule
SEASON_RERUN_DAYS = 180    # a holiday episode that aired longer ago than this is ready for its season again
REFETCH_DAYS = 21
REFETCH_MIN_DAYS = 7

# Tapes (whole recorded broadcasts in videos/shows/.Tapes, often hours long) share one pick between
# them: together they weigh TAPE_WEIGHT, about as much as one series, and at most TAPES_CACHED
# unplayed tapes are kept on the SD card
TAPE_WEIGHT = 1.0
TAPES_CACHED = 3

# Marathons: one series back to back for the whole block, where the schedule has one; the
# series rotates weekly through MARATHON_SERIES (MARATHON_EPOCH is a Monday in the week of the first)
MARATHON_SERIES = ("simpsons", "seinfeld", "futurama", "friends", "scrubs", "that70sshow", "freshprinceofbelair",
                   "malcolminthemiddle", "officeus", "drewcarey", "inlivingcolor", "howimetyourmother",
                   "kingofqueens", "twoandahalfmen")
MARATHON_EPOCH = datetime.date(2026, 10, 26)
MARATHON_EPISODES = 10     # cached ahead of the block

# Music video blocks: videos back to back filling a slot on the grid, each with its artist and song
# in a lower third, and a commercial break about every 20 minutes. They air where the schedule says.
MUSIC_VIDEO_SECONDS = (90, 480)   # videos outside this length stay out of the blocks
MUSIC_TITLE_AT = 4.0              # seconds into each video for its lower third
MUSIC_TITLE_SECONDS = 4.5         # per panel: NOW · ARTIST, then YEAR · SONG
MUSIC_MIN_FILL = 0.8              # a block airs only if its cached videos fill this much of it
# These blocks open with the Weekly Countdown (Last.fm, screens.countdown), then the video of the best placed
# artist in it that's cached
COUNTDOWN_BLOCKS = ("After School Videos", "Saturday Night Videos", "Music Videos")
POPUP_VIDEO = True   # VH1-style fact bubbles over the music videos that have facts (retrotv/news.py finds them)

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
PROMO_PANEL_SECONDS = 8.0   # long enough to take in the clip that goes with each
TONIGHT_HOUR = 20          # TONIGHT names the show planned for this hour, until shows near it
# The day's anniversary episode (picked by retrotv/news.py: first aired on this date years ago) goes next in
# line at the first show from this hour on that its rating allows, after a From the Vault card
ANNIVERSARY_HOUR = 20

# Phone remote and live guide on the home Wi-Fi: http://retrotv.local/
REMOTE_PORT = 80
REMOTE_STATE_FILE = os.path.join(HOME_DIR, "retrotv_remote.json")

# The watch page, http://retrotv.local/watch: the station as its own screen shows it, CRT look and all, with its
# sound, in a browser on the home network (retrotv/live.py). It's only made while someone watches.
LIVE_STREAM = True
LIVE_CAPTURE_FFMPEG = "/usr/local/lib/retrotv/ffmpeg"   # the copy of ffmpeg that may read the screen (README)
LIVE_DRM_DEVICE = "/dev/dri/by-path/platform-gpu-card"   # the panel's display (mpv.conf's drm-device)
# The player's sound goes to the panel through ALSA's tee, which pipes a copy to retrotv/audio_tap.py (run from
# AUDIO_TAP_FILE, on the SD card), which sends it to this UDP port on the Pi. Always 16-bit stereo at LIVE_AUDIO_RATE
LIVE_AUDIO_DEVICE = "hdmi:CARD=vc4hdmi1,DEV=0"           # the panel's speakers (mpv.conf's audio-device)
LIVE_AUDIO_PORT = 5004
LIVE_AUDIO_RATE = 48000
AUDIO_TAP_FILE = os.path.join(HOME_DIR, ".cache", "retrotv", "audio_tap.py")
LIVE_AUDIO_SHIFT_MS = -60  # moves the stream's sound against its picture: a flash-and-beep clip had it 60 ms late
                           # (2026-11-03), so it's brought forward
LIVE_VIDEO_KBPS = 4000
LIVE_IDLE_SECONDS = 20     # keep streaming this long after the last viewer leaves, so a reload rejoins at once
LIVE_MAX_VIEWERS = 4

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
ART_CHECK_MINUTES = 30
CLIP_GAP_SECONDS = 20          # between cutting one clip and the next, so they stay in the background
CLIP_IDLE_SECONDS = 120
CLIP_RETRY_SECONDS = 86400
CLIP_PRUNE_SECONDS = 3600
