"""
The media library: the NAS index (MediaIndexer) and the cache of it on the SD card and the USB drive (CacheManager),
with archive.org downloads.
"""
import collections
import datetime
import functools
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import threading
import time

from retrotv import screens
from retrotv import meta
from retrotv import schedule
from retrotv import health
from retrotv.jsonio import write_json
from retrotv import archive
from retrotv.config import (
    AIRED_FILE, AIRED_KEEP_DAYS, ARCHIVE, ARCHIVE_CHECK_MINUTES, ARCHIVE_POOL_GB, ARCHIVE_READY, ARCHIVE_ROOT,
    ARCHIVE_SHARE, BAD_FILE_ERRORS, CACHE_DIR, CACHE_LIMIT_GB, COMMERCIAL_MAX_SECONDS, COMMERCIAL_POOL_GB,
    COPY_MB_PER_SEC_ON_AIR, FULL_AFTER_MISSES, GB, HOLIDAY_COMMERCIALS_CACHED, HOLIDAY_WEIGHT, HOME_DIR, INDEX_FILE,
    INDEX_MAX_AGE_DAYS, LOW_WATER_HOURS, MAIN_CATEGORIES, MANIFEST_FILE, MAX_SET_ASIDE_AN_HOUR, MEDIA_ROOT,
    MIN_FREE_GB, MIX_FOLDERS, MUSIC_MIN_CACHED, MUSIC_POOL_GB, MUSIC_VIDEO_SECONDS, NAS_INDEX_FILE, PATHS,
    POOL_CHOICES, REFETCH_DAYS, REFILL_BELOW_HOURS, REFILL_HOUR, REFILL_NOW_FILE, REFILL_RETRY_MINUTES, REPEAT_DAYS,
    RERUN_HOURS, REVIEW_DIRS, SCHEDULE_FILL_DAYS, SEASON_RERUN_DAYS, SERIES_FILE, SHORT_SHOW_MINUTES, SIGN_ON_HOUR,
    SLOT_MINUTES, STAGE_H, STAGE_W, STRETCH_MAX, STRETCH_POWER, STRETCH_STRIPS, TAPE_WEIGHT, TURNS_FILE, USB_DIR,
    USB_SHOWS_DIR, USB_SHOWS_GB, USB_WAIT_SECONDS, VIDEO_EXTENSIONS, logger, td)
from retrotv.rules import (
    allowed, broadcast_date, broadcast_hour, cache_path, holiday_ahead, holiday_season, holiday_tag, is_tape,
    marathon_series, off_air, pool_of, rating, slot_end)


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
            files = self.kids if category == "kids" else [
                f for folder in MIX_FOLDERS.get(category, (category,)) for f in self.media_map.get(folder, [])]
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
        self.music_refill_day = None
        self.wanted = set()   # what the schedule airs soon: never evicted to make room
        self.bad_files = health.BadFiles()
        self.blocked = self.bad_files.blocked()   # files set aside for review: not copied back, not aired
        self.item_files, self.item_stamp = {}, None   # schedule item -> its files in airing order, per NAS index
        self.schedule = schedule.load()
        try:
            with open(TURNS_FILE) as f:
                self.turns = json.load(f)
        except (OSError, ValueError):
            self.turns = {}
        self.wake = threading.Event()
        self.archive_wake = threading.Event()
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.usb = self._usb_ready()
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

    @staticmethod
    def _usb_ready():
        for _ in range(USB_WAIT_SECONDS):
            if os.path.ismount(USB_DIR):
                os.makedirs(USB_SHOWS_DIR, exist_ok=True)
                return True
            time.sleep(1)
        logger.error(f"USB drive isn't mounted at {USB_DIR}: shows stay on the SD card.")
        return False

    def _load_manifest(self):
        try:
            with open(MANIFEST_FILE, 'r') as f:
                self.entries = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"Failed to load cache manifest, starting empty: {e}")

        # Drop entries whose file is gone, and files nothing refers to (partial copies, old cache layout)
        on_disk = {CACHE_DIR: set(os.listdir(CACHE_DIR))}
        if self.usb:
            on_disk[USB_SHOWS_DIR] = set(os.listdir(USB_SHOWS_DIR))
        self.entries = {src: e for src, e in self.entries.items() if e["file"] in on_disk.get(e.get("dir") or CACHE_DIR, ())}
        for folder, names in on_disk.items():
            known = {e["file"] for e in self.entries.values() if (e.get("dir") or CACHE_DIR) == folder}
            for name in names - known:
                try:
                    os.remove(os.path.join(folder, name))
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
        return e["category"] in MIX_FOLDERS.get(category, (category,))

    def show_candidates(self, category, when, exclude=(), series=None, rerun=False):
        """
        Cached shows that may air at `when`: for each series the next episode in order, plus
        anything that isn't part of a series, plus the season's holiday shows (out of order).
        Each series counts once however many of its episodes are cached, as much as a show outside a
        series, and its holiday episodes share one HOLIDAY_WEIGHT. Tapes split one TAPE_WEIGHT between
        them, archive.org's downloads ARCHIVE_SHARE of the odds. Nothing that aired in the last REPEAT_DAYS
        (a series offers its next episode that hasn't), or with rerun, the last RERUN_HOURS.
        Returns [(src, entry, weight, in_order)].
        """
        hour = broadcast_hour(when)[1]
        season = holiday_season(when.date())
        out, by_series, holiday_eps, tapes, archived = [], {}, {}, [], []
        with self.lock:
            positions = dict(self.series_pos)
            # Episodes already in the lineup count as aired, so the next pick follows them (and a
            # second copy of the same episode from another folder is passed over)
            for src in exclude:
                e = self.entries.get(src)
                if e and e.get("series") and e.get("order"):
                    if positions.get(e["series"]) is None or e["order"] > positions[e["series"]]:
                        positions[e["series"]] = e["order"]
            recently = time.time() - (RERUN_HOURS * 3600 if rerun else REPEAT_DAYS * 86400)
            for src, e in self.entries.items():
                if (src in exclude or src in self.reserved or not self._matches(src, e, category)
                        or (series and e.get("series") != series) or not allowed(src, hour)
                        or self._last_aired(src, e) > recently):
                    continue
                tag = holiday_tag(src)
                if tag and tag != season:
                    continue  # a Christmas episode waits for Christmas
                if e.get("archive"):
                    archived.append((src, e, HOLIDAY_WEIGHT if tag else 1.0))
                elif is_tape(src):
                    # Odds within the tapes' shared slot: in season, the holiday tape is the likely one
                    tapes.append((src, e, (HOLIDAY_WEIGHT if tag else 1.0) / (1 + 2 * e["plays"])))
                elif tag and e.get("series"):
                    holiday_eps.setdefault(e["series"], []).append((src, e))
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
                out.append((src, e, 1.0 / (1 + 2 * e["plays"]), True))
        for eps in holiday_eps.values():
            out += [(src, e, HOLIDAY_WEIGHT / (1 + 3 * e["plays"]) / len(eps), False) for src, e in eps]
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

    def _last_aired(self, src, e):
        """When a show last went on the air: by AIRED_FILE (which outlives its copy) or its copy's own last play."""
        return max(self.aired.get(src, 0), e["last_played"])

    def _save_aired(self, src, when):
        """Caller holds the lock. Notes when src aired, in AIRED_FILE."""
        self.aired[src] = when
        keep = time.time() - AIRED_KEEP_DAYS * 86400
        self.aired = {s: t for s, t in self.aired.items() if t > keep}
        try:
            write_json(AIRED_FILE, self.aired)
        except OSError as err:
            logger.error(f"Could not save what aired: {err}")

    def note_aired(self, src):
        """A show has gone on the air: it counts as aired from now, not only once it's over (mark_played), so a
        restart partway through can't put it straight back on. Its plays and its series' place wait for the end."""
        with self.lock:
            self._save_aired(src, time.time())

    def mark_played(self, src, in_order=False):
        with self.lock:
            self.reserved.discard(src)
            e = self.entries.get(src)
            if e:
                e["plays"] += 1
                e["last_played"] = time.time()
                if e["category"] != "commercials":
                    self._save_aired(src, e["last_played"])
                if in_order and e.get("series") and e.get("order"):
                    self.series_pos[e["series"]] = e["order"]
                    try:
                        write_json(SERIES_FILE, self.series_pos)
                    except OSError as err:
                        logger.error(f"Could not save series positions: {err}")
                if e.get("archive") and not e.get("held"):
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

    # --- The weekly schedule ---

    def load_schedule(self):
        self.schedule = schedule.load()

    def schedule_slot(self, t):
        """(group, when its slot opens, when it closes) for the slot on the air at t, or None."""
        day, hour = broadcast_hour(t)
        slot = schedule.slot_at(self.schedule, day, hour + t.minute / 60 + t.second / 3600)
        if not slot:
            return None
        group = schedule.group_of(self.schedule, slot["group"])
        if not group:
            return None
        base = datetime.datetime.combine(broadcast_date(t), datetime.time())
        return group, base + td(hours=slot["start"]), base + td(hours=slot["end"])

    def set_turn(self, group_id, turn):
        self.turns[group_id] = turn
        try:
            write_json(TURNS_FILE, self.turns)
        except OSError as e:
            logger.error(f"Could not save whose turn it is: {e}")

    def _episodes(self, item, when):
        """What a schedule item can air, in airing order: episodes by their order, the rest by name."""
        if item == "marathon:":
            key = marathon_series(broadcast_date(when), self.indexer)
            return [src for _, src in self.indexer.episodes(key)] if key else []
        if item.startswith("archive:"):
            kind = archive.KINDS.get(item[8:])
            folder = os.path.join(ARCHIVE_ROOT, kind[2]) + "/" if kind else None
            with self.lock:
                return sorted(s for s, e in self.entries.items() if e.get("archive") and folder and s.startswith(folder))
        if self.item_stamp != self.indexer.last_index_time:
            with self.indexer.lock:
                media_map = {k: list(v) for k, v in self.indexer.media_map.items()}

            def airing_order(src):
                _, order = self.indexer.info(src)
                return (0, order, src) if order else (1, [], os.path.basename(src).lower())
            self.item_files = {i: sorted(files, key=airing_order)
                               for i, (_, _, files) in schedule.item_files(media_map, MEDIA_ROOT).items()}
            self.item_stamp = self.indexer.last_index_time
        return self.item_files.get(item, [])

    def _is_new(self, src):
        """Hasn't aired since the show last started over, by what aired or by its series' place."""
        if src in self.aired:
            return False
        key, order = self.indexer.info(src)
        place = self.series_pos.get(key) if key else None
        return not (place and order and order <= place)

    def _item_queue(self, item, when):
        """What an item airs next, in order: the season's episodes, then whatever is new. Others' holidays wait."""
        season = holiday_season(when.date())
        ready_again = time.time() - SEASON_RERUN_DAYS * 86400
        queue, seasonal = [], []
        files = self._episodes(item, when)
        with self.lock:
            for src in files:
                if src in self.blocked:
                    continue   # waiting for review: the show carries on with its next episode
                tag = holiday_tag(src)
                if not tag:
                    if self._is_new(src):
                        queue.append(src)
                elif tag == season and self.aired.get(src, 0) < ready_again:
                    seasonal.append(src)
        return seasonal + queue

    def _start_over(self, item, when):
        """An item has aired everything: it starts again from the top."""
        files = self._episodes(item, when)
        with self.lock:
            for src in files:
                self.aired.pop(src, None)
                self.series_pos.pop(self.indexer.info(src)[0], None)
        logger.info(f"Schedule: {item} has aired everything, starting over.")

    def next_in_item(self, item, start, exclude, fits, must_air=False):
        """(src, entry, in order) for an item's next airing at `start`, or None. A show keeps to its order: when its
        next episode can't air, the show sits this turn out. Anything else airs whichever cached file works.
        fits: (src, entry) -> bool, or None when it may run over, which then takes the shortest. must_air: a show's next
        episode airs whether or not it fits."""
        queue = self._item_queue(item, start)
        if not queue and self._episodes(item, start):
            self._start_over(item, start)
            queue = self._item_queue(item, start)
        hour = broadcast_hour(start)[1]
        in_order = item.startswith(("shows/", "g4/", "marathon:"))
        loose = []
        for src in queue:
            if src in exclude or src in self.reserved or not allowed(src, hour):
                continue
            ordered = in_order and bool(self.indexer.info(src)[1]) and not holiday_tag(src)
            e = self.entry(src)
            if not e:
                if ordered:
                    return None
                continue
            if fits is None and not ordered:
                loose.append((src, e))
            elif fits is None or fits(src, e) or (ordered and must_air):
                return src, e, ordered
            elif ordered:
                return None
        if loose:
            src, e = min(loose, key=lambda x: x[1]["duration"] or 1800.0)
            return src, e, False
        return None

    def _pool_files(self, items, when):
        """A shuffle group's files, whatever has waited longest first, the same way every time."""
        season = holiday_season(when.date())
        files = {src for item in items for src in self._episodes(item, when)
                 if holiday_tag(src) in (None, season) and src not in self.blocked}
        with self.lock:
            aired = dict(self.aired)
        return sorted(files, key=lambda s: (aired.get(s, 0), hashlib.sha1(s.encode()).hexdigest()))

    def next_in_pool(self, items, start, exclude, fits):
        """(src, entry, False) for a shuffle group's next airing at `start`, or None. See next_in_item for fits. Nothing
        that aired in the last REPEAT_DAYS while anything else cached could air (when none of that fits, the slot fills
        some other way): a rerun, RERUN_HOURS apart at least, only when everything has aired that recently."""
        hour = broadcast_hour(start)[1]
        files = []
        for src in self._pool_files(items, start):
            e = self.entry(src)
            if e and src not in exclude and src not in self.reserved and allowed(src, hour):
                files.append((src, e))
        now = time.time()
        fresh = [(s, e) for s, e in files if self._last_aired(s, e) <= now - REPEAT_DAYS * 86400]
        loose, choices = [], []
        for src, e in fresh or [(s, e) for s, e in files if self._last_aired(s, e) <= now - RERUN_HOURS * 3600]:
            if fits is None:
                loose.append((src, e))
            elif fits(src, e):
                choices.append((src, e))
                if len(choices) >= POOL_CHOICES:
                    break
        if choices:
            src, e = max(choices, key=lambda x: slot_end(x[1]["duration"] or 1800.0, x[1]["category"], x[0], start)[0])
            return src, e, False
        if loose:
            src, e = min(loose, key=lambda x: x[1]["duration"] or 1800.0)
            return src, e, False
        return None

    def _schedule_wants(self):
        """What the schedule airs over the next SCHEDULE_FILL_DAYS, soonest first: a dry run of the week using each
        show's usual length, since a file's real length is only known once it's copied."""
        now = datetime.datetime.now()
        today = broadcast_date(now)
        usual = schedule.lengths()
        turns = dict(self.turns)
        queues, places = {}, collections.Counter()
        wants = []

        def slot_minutes(minutes):
            return minutes * 1.4 if minutes < SHORT_SHOW_MINUTES else math.ceil(minutes / 0.8 / SLOT_MINUTES) * SLOT_MINUTES

        def next_of(key, files, hour):
            """The next not yet wanted file of a queue that may air at this hour."""
            queue = queues.setdefault(key, files)
            while places[key] < len(queue):
                src = queue[places[key]]
                places[key] += 1
                if allowed(src, hour) and src not in seen:
                    return src
            return None

        def cached_first(files):
            return sorted(files, key=lambda s: self.entry(s) is None)

        def usual_minutes(item, when):
            """An item's usual length. The marathon has none of its own: it takes that week's show's, by its folder."""
            if item == "marathon:":
                files = self._episodes(item, when)
                if files and files[0].startswith(MEDIA_ROOT + "/"):
                    item = "/".join(files[0][len(MEDIA_ROOT) + 1:].split("/")[:2])   # as schedule.item_files names it
            return usual.get(item, 25)

        seen = set()
        for offset in range(SCHEDULE_FILL_DAYS):
            day = today + td(days=offset)
            base = datetime.datetime.combine(day, datetime.time())
            when = base + td(hours=SIGN_ON_HOUR)
            slots = sorted((s for s in self.schedule["slots"] if s["day"] == day.weekday()), key=lambda s: s["start"])
            for slot in slots:
                closes = base + td(hours=slot["end"])
                group = schedule.group_of(self.schedule, slot["group"])
                if closes <= now or not group or not group["items"]:
                    continue
                window = (closes - max(base + td(hours=slot["start"]), now)).total_seconds() / 60
                left, picked = window, []
                items = group["items"]
                if group["mode"] == "shuffle":
                    typical = max([usual.get(i, 90) for i in items])
                    # One more than fits, so a slot has a choice of lengths
                    for _ in range(math.ceil(left / max(typical, SLOT_MINUTES)) + 1):
                        files = queues.get(group["id"]) or cached_first(self._pool_files(items, when))
                        picked.append(next_of(group["id"], files, slot["start"]))
                else:
                    turn, misses = turns.get(group["id"], 0), 0
                    while left >= SHORT_SHOW_MINUTES and misses < len(items):
                        item = items[turn % len(items)]
                        turn += 1
                        if item.startswith("music:"):
                            left -= min(left, 60)
                            misses = 0
                            continue
                        minutes = slot_minutes(usual_minutes(item, when))
                        files = queues.get(item)
                        if files is None:
                            files = self._item_queue(item, when)
                            if not item.startswith(("shows/", "g4/", "marathon:")):
                                files = cached_first(files)
                        room = minutes <= left or left == window or not group.get("overflow", True)
                        src = next_of(item, files, slot["start"]) if room else None
                        if not src:
                            misses += 1
                            continue
                        picked.append(src)
                        left -= minutes
                        misses = 0
                        if not item.startswith(("shows/", "g4/", "marathon:")):
                            # Recordings vary a lot in length: a couple more to choose from
                            picked += [next_of(item, files, slot["start"]) for _ in range(2)]
                    turns[group["id"]] = turn % len(items)
                for src in picked:
                    if src and src not in seen:
                        seen.add(src)
                        wants.append(src)
        return wants

    def _sweep_sd_shows(self):
        """Shows still on the SD card from before the USB drive stay only until they air or aren't wanted."""
        with self.lock:
            gone = [src for src, e in self.entries.items() if pool_of(e) == "shows" and not e.get("dir")
                    and src not in self.reserved and (e["plays"] > 0 or src not in self.wanted)]
            for src in gone:
                self._remove_file(self.entries.pop(src))
        if gone:
            logger.info(f"Cleared {len(gone)} shows the schedule doesn't need from the SD card.")
            self._save_manifest()

    # --- What to fetch first ---

    def _recently_aired(self, days=REFETCH_DAYS):
        """What aired within `days`: not to be copied back from the NAS yet."""
        cutoff = time.time() - days * 86400
        with self.lock:
            return {src for src, t in self.aired.items() if t > cutoff}

    def _wanted_shows(self):
        """Shows to copy: any anniversary episode, then what the schedule airs, soonest first."""
        now = datetime.datetime.now()
        today = broadcast_date(now)
        held = [a["src"] for a in screens.anniversaries(today, today + td(days=1)).values()]
        for src in held:
            self.reserve(src)   # an anniversary episode waits for its slot (Station._queue_anniversary)
        wanted = list(dict.fromkeys(held + self._schedule_wants()))
        self.wanted = set(wanted)
        with self.lock:
            return [src for src in wanted if src not in self.entries]

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

    def _wanted_features(self):
        """[(src, pool)]: the ad and the music video the On This Day cards follow themselves with, today and tomorrow
        (retrotv/onthisday.py's picks), that aren't on the SD card yet."""
        today = broadcast_date(datetime.datetime.now())
        want = []
        for day in (today, today + td(days=1)):
            picks = screens.on_this_day(day)
            games = picks.get("games") or []
            if games and games[0].get("ads"):
                want.append((games[0]["ads"][0], "commercials"))
            video = next((n["video"] for n in picks.get("number_ones") or []
                          if n.get("video") and n["year"] == picks.get("featured_year")), None)
            if video:
                want.append((video, "music"))
        with self.lock:
            return [(src, pool) for src, pool in dict.fromkeys(want) if src not in self.entries]

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
        asked = os.path.exists(REFILL_NOW_FILE)
        if asked:
            os.remove(REFILL_NOW_FILE)
            logger.info("Refill asked for (REFILL_NOW_FILE)")
        if asked or self.unplayed_hours() < LOW_WATER_HOURS or self._music_short():
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
                    or self._wanted_shows() or self._wanted_commercials() or self._wanted_features()):
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
            for src in self._wanted_commercials():
                if self._fetch_into_pool(src, "commercials", force=True):
                    copied += 1
            for src, pool in self._wanted_features():
                if self._fetch_into_pool(src, pool, force=True):
                    copied += 1
            # What the schedule airs, soonest first, making room if that means shows it doesn't want go
            wanted = self._wanted_shows()
            if self.usb:
                self._sweep_sd_shows()
            misses = 0
            for src in wanted:
                if self._fetch_into_pool(src, "shows", force=True):
                    copied += 1
                    misses = 0
                else:
                    misses += 1
                    if misses >= FULL_AFTER_MISSES:
                        break
            full = {"commercials": False, "music": False}
            while not all(full.values()):
                for pool, turns in (("commercials", 6), ("music", 2)):
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

    def _fill_one(self, pool):
        """Copies one new file into the "commercials" or "music" pool. False once that pool is full."""
        for _ in range(5):  # a few tries, so one that doesn't fit doesn't end the refill
            with self.lock:
                cached = set(self.entries)
            recent = self._recently_aired() if pool != "commercials" else set()
            src = self.indexer.get_random(pool, exclude=cached | recent)
            if not src:
                continue
            result = self._fetch_into_pool(src, pool)
            if result is None:
                return False
            if result:
                return True
        return False

    def _fetch_into_pool(self, src, pool, force=False):
        """Copies src if it fits the pool. True when copied, False when skipped, None when the SD card is full."""
        if src in self.blocked:
            return False
        category = "commercials" if pool == "commercials" else self.indexer.category_of(src)
        if not category:
            return False
        budget = {"commercials": COMMERCIAL_POOL_GB, "music": MUSIC_POOL_GB,
                  "shows": USB_SHOWS_GB if self.usb else CACHE_LIMIT_GB - COMMERCIAL_POOL_GB - MUSIC_POOL_GB - ARCHIVE_POOL_GB
                  }[pool] * GB
        try:
            size = os.path.getsize(src)
        except FileNotFoundError:
            if not os.path.isdir(PATHS[category]):
                raise
            self.indexer.remove(src)
            return False
        if not self._make_room(pool, size, budget, force):
            return False
        if shutil.disk_usage(self._folder_for(category)).free - size < MIN_FREE_GB * GB:
            logger.warning("The cache's disk is nearly full, stopping the refill early.")
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
        unplayed episodes their series has already passed. With force (what the schedule wants, and requested
        shows), unplayed shows go too, oldest copies first, except the season's own. Nothing the schedule
        wants soon ever goes. With the USB drive, the shows pool is what's on the drive.
        """
        with self.lock:
            pool = {src: e for src, e in self.entries.items() if pool_of(e) == which
                    and (which != "shows" or not self.usb or e.get("dir"))}
            used = sum(e["size"] for e in pool.values())
            free = [src for src in pool if src not in self.reserved and src not in self.wanted]
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
            os.remove(cache_path(e))
        except OSError as err:
            logger.error(f"Error deleting {e['file']}: {err}")

    def set_aside(self, path, label, error):
        """The player couldn't load a file. It's logged and moved to _for_review, never deleted, and stays off the air
        until it's cleared on the health page. A file that's simply gone is only logged. The copy on the NAS isn't touched:
        the Pi can't write there, and it may be seeding."""
        if error not in BAD_FILE_ERRORS or not path.startswith("/"):
            return
        moved_id = dest = None
        with self.lock:
            src = next((s for s, e in self.entries.items() if cache_path(e) == path), None)
            if src is None:
                # A later act of a file already dealt with, or one that isn't in the cache at all (a bumper, say)
                if not path.startswith((CACHE_DIR + "/", USB_SHOWS_DIR + "/")):
                    self.bad_files.add(path, label, error, "left", original=path)
                return
            e = self.entries[src]
            original = f"https://archive.org/details/{e['item']}" if e.get("item") else src
            if not os.path.exists(path):
                del self.entries[src]
                self.bad_files.add(src, label, error, "missing", original=original)
            elif self.bad_files.moved_since(time.time() - 3600) >= MAX_SET_ASIDE_AN_HOUR:
                e["held"] = True
                self.bad_files.add(src, label, error, "held", original=original)
                logger.error(f"Too many bad files this hour, is the drive all right? Left in place: {path}")
            else:
                folder = REVIEW_DIRS[USB_DIR if path.startswith(USB_DIR + "/") else HOME_DIR]
                dest = os.path.join(folder, os.path.basename(path))
                try:
                    os.makedirs(folder, exist_ok=True)
                    os.rename(path, dest)
                except OSError as err:
                    e["held"] = True
                    dest = None
                    self.bad_files.add(src, label, f"{error}; could not move it: {err}", "held", original=original)
                    logger.error(f"Could not move a bad file to _for_review: {path}: {err}")
                else:
                    del self.entries[src]
                    self.blocked.add(src)
                    moved_id = self.bad_files.add(src, label, error, "moved", original=original, moved_to=dest)
                    logger.error(f"Bad file moved to {dest}: {error}")
            if e.get("archive"):
                self.archive_wake.set()
        self._save_manifest()
        if moved_id:
            threading.Thread(target=self._probe_bad_file, args=(moved_id, dest), daemon=True).start()

    def _probe_bad_file(self, record_id, path):
        """A second opinion on a file the player refused, for whoever reviews it."""
        try:
            done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                  capture_output=True, text=True, timeout=60)
            problem = done.stderr.strip()[-160:]
            self.bad_files.note_probe(record_id, f"ffprobe can't read it either: {problem}" if done.returncode or problem
                                      else f"ffprobe reads it fine ({float(done.stdout) / 60:.0f} min), so it may have been a one-off")
        except (OSError, ValueError, subprocess.SubprocessError) as err:
            self.bad_files.note_probe(record_id, f"Couldn't check it: {err}")

    def clear_bad_file(self, record_id):
        src = self.bad_files.clear(record_id)
        with self.lock:
            self.blocked = self.bad_files.blocked()
        return src

    def _folder_for(self, category):
        return USB_SHOWS_DIR if self.usb and category in MAIN_CATEGORIES else CACHE_DIR

    @staticmethod
    def _file_name(src):
        """The cache's name for src: a hash of the full path keeps same-named episodes from different folders apart."""
        stem, ext = os.path.splitext(os.path.basename(src))
        stem = stem.encode()[:120].decode(errors="ignore")
        return f"{hashlib.sha1(src.encode()).hexdigest()[:10]}-{stem}{ext}"

    def _copy(self, src, category):
        """Copies via a .part file so an interrupted copy never looks complete."""
        name = self._file_name(src)
        folder = self._folder_for(category)
        dest = os.path.join(folder, name)
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
        self._register(src, name, category, **({} if folder == CACHE_DIR else {"dir": folder}))
        return True

    def _register(self, src, name, category, **extra):
        """Adds a file just copied into the cache to the manifest."""
        dest = os.path.join(extra.get("dir") or CACHE_DIR, name)
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
        if src in self.blocked or (tag and tag != holiday_ahead(datetime.date.today())):
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
