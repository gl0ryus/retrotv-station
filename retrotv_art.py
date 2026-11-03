"""
Show artwork, kept on the SD card ahead of time so nothing on the air waits for the network: a 16:9 poster for the
NEXT / LATER / TONIGHT lower thirds, and a large picture for the scrolling marathon ad. Shows come from TVmaze and movies
from Wikipedia, neither of which needs a key. Only an exact title match counts, because no picture beats the wrong one.
Standard library, plus ffmpeg to size the pictures.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from retrotv_screens import DATA_DIR, USER_AGENT

logger = logging.getLogger("RetroTV")

ART_DIR = os.path.join(DATA_DIR, "show_art")
LOOKUPS_FILE = os.path.join(ART_DIR, "lookups.json")
TVMAZE_SEARCH = "https://api.tvmaze.com/search/shows?q="
TVMAZE_IMAGES = "https://api.tvmaze.com/shows/{}/images"
WIKI_API = "https://en.wikipedia.org/w/api.php?"
ART_HOSTS = ("tvmaze.com", "upload.wikimedia.org")
RETRY_DAYS = 14             # a title with no art is looked for again after this long
REQUEST_GAP_SECONDS = 1.0   # kinder than either service asks for
MAX_BYTES = 12 * 1024 * 1024
THUMB_W, THUMB_H = 114, 64  # 16:9, a third of the 342x200 picture across and down, before the border
BORDER = 2                  # even: in a subsampled format pad rounds an odd offset down, which put a 1 px frame all on two sides
# Wide art fills it; a tall poster sits whole in the middle, over a blurred copy of itself
THUMB_FILTER = (f"split=2[a][b];[a]scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={THUMB_W}:{THUMB_H},"
                f"gblur=sigma=5,eq=brightness=-0.12[back];[b]scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease:flags=lanczos[front];"
                f"[back][front]overlay=(W-w)/2:(H-h)/2,pad={THUMB_W + 2 * BORDER}:{THUMB_H + 2 * BORDER}:{BORDER}:{BORDER}:color=0xEFE6D2,format=rgba")
# Bigger than the 512x300 card in the direction it scrolls: wide pictures pan across, tall ones down
WIDE_FILTER = "scale='if(gt(iw/ih,1.2),-2,512)':'if(gt(iw/ih,1.2),400,-2)':flags=lanczos"
MOVIE_CATEGORIES = ("movies",)
CLIP_DIR = os.path.join(DATA_DIR, "show_clips")
CLIP_SECONDS = 30           # long enough for the end-of-show NEXT bar without going round again
CLIP_FPS = 15
CLIP_SPOTS = (0.3, 0.5, 0.7)
CLIP_MIN_VIDEO_SECONDS = 120
CLIP_CUT_TIMEOUT = 180
CLIP_KEEP_DAYS = 14
CLIP_FILTER = (f"fps={CLIP_FPS},scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase:flags=bilinear,crop={THUMB_W}:{THUMB_H},"
               f"blackdetect=d=1:pix_th=0.12,pad={THUMB_W + 2 * BORDER}:{THUMB_H + 2 * BORDER}:{BORDER}:{BORDER}:color=0xEFE6D2,format=yuv420p")
# What TVmaze calls a show, where it isn't what the files do
ALIASES = {"daily show": "The Daily Show with Jon Stewart", "comedy central roast": "Comedy Central Roasts"}
NO_ART = ("big-thinkers",)  # a different show has the same name, and nothing in the files says which year this one is
LOOKALIKES = str.maketrans("Χχ", "Xx")   # the Greek letter in some of the X-Play file names


def download_image(url, hosts, dest, picture_filter, max_bytes=MAX_BYTES):
    """Downloads a PNG or JPEG from an allowed host and writes it to dest through a filter. Raises if it can't: OSError,
    ValueError or SubprocessError. The format is checked first, so ffmpeg is only ever handed a picture."""
    parts = urllib.parse.urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in hosts):
        raise ValueError(f"not an allowed host: {url[:80]}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    original, sized = dest + ".download", dest + ".sized" + os.path.splitext(dest)[1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("too big")
            if data.startswith(b"\x89PNG"):
                demuxer = "png_pipe"
            elif data.startswith(b"\xff\xd8\xff"):
                demuxer = "jpeg_pipe"
            else:
                raise ValueError("not a PNG or JPEG")
        with open(original, "wb") as f:
            f.write(data)
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", demuxer, "-i", original,
                        "-vf", picture_filter, "-frames:v", "1", "-q:v", "4", sized], check=True, capture_output=True, timeout=60)
        os.replace(sized, dest)
    finally:
        for leftover in (original, sized):
            if os.path.exists(leftover):
                os.remove(leftover)


def key_of(name):
    """The file name a title's art goes by. A dated recording ("The Screen Savers 06 04 2004") shares its show's."""
    name = re.sub(r"\s+\d{1,2}[ .-]\d{1,2}[ .-]\d{2,4}$", "", (name or "").translate(LOOKALIKES))
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def thumb(name):
    """The 16:9 poster for a title's lower third, if there is one. Only looks at the SD card."""
    path = os.path.join(ART_DIR, "promos", key_of(name) + ".png")
    return path if key_of(name) and os.path.exists(path) else None


def wide(name):
    """The large picture for a title, if there is one. Only looks at the SD card."""
    path = os.path.join(ART_DIR, "wide", key_of(name) + ".jpg")
    return path if key_of(name) and os.path.exists(path) else None


def _plain(title):
    """A title for comparing: no leading "The", brackets, punctuation or case."""
    title = re.sub(r"\([^)]*\)", " ", (title or "").translate(LOOKALIKES)).replace("&", " and ")
    title = re.sub(r"[^a-z0-9 ]+", "", title.lower())
    return re.sub(r"\s+", " ", re.sub(r"^the\s+", "", title.strip())).strip()


def _search_name(name):
    name = name.translate(LOOKALIKES)
    name = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", re.sub(r"\s+\d{1,2}[ .-]\d{1,2}[ .-]\d{2,4}$", "", name))).strip()
    return ALIASES.get(_plain(name), name)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def pick_show(results, name, year=None):
    """The search result that's really this show, or None. "(US)" or "(1990)" in a name narrows it down, and so does
    the year of the episode: a show can't have aired before it premiered (give or take a year for a dating slip)."""
    want = re.search(r"\((\w+)\)", name)
    hint = want.group(1).upper() if want else ""
    exact = [r["show"] for r in results if _plain(r["show"].get("name")) == _plain(_search_name(name)) and r["show"].get("image")]
    if hint.isdigit():
        exact = [s for s in exact if (s.get("premiered") or "").startswith(hint)]
    elif hint:
        exact = [s for s in exact if ((s.get("network") or s.get("webChannel") or {}).get("country") or {}).get("code") == hint]
    if year:
        exact = [s for s in exact if not s.get("premiered") or int(s["premiered"][:4]) <= int(year) + 1]
    # The same name twice is usually a remake, and these are old shows: the 2001 Scrubs, not the 2026 one
    exact.sort(key=lambda s: s.get("premiered") or "9999")
    return exact[0] if exact else None


def _look_up_show(name, year):
    show = pick_show(_get(TVMAZE_SEARCH + urllib.parse.quote(_search_name(name))), name, year)
    if not show:
        return None
    poster = (show.get("image") or {}).get("medium") or (show.get("image") or {}).get("original")
    time.sleep(REQUEST_GAP_SECONDS)
    pictures = _get(TVMAZE_IMAGES.format(int(show["id"])))
    backgrounds = [p for p in pictures if p.get("type") == "background"]
    backgrounds.sort(key=lambda p: not p.get("main"))
    large = ((backgrounds[0].get("resolutions") or {}).get("original") or {}).get("url") if backgrounds else None
    return {"poster": poster, "wide": large or (show.get("image") or {}).get("original") or poster}


def _look_up_movie(name, year):
    title = _search_name(re.sub(r"^\d+\s*-\s*", "", name))
    for candidate in ([f"{title} ({year} film)"] if year else []) + [f"{title} (film)", title]:
        query = urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": 2, "redirects": 1, "titles": candidate,
                                        "prop": "pageimages|description", "piprop": "thumbnail", "pithumbsize": 800})
        pages = _get(WIKI_API + query).get("query", {}).get("pages", [])
        time.sleep(REQUEST_GAP_SECONDS)
        page = pages[0] if pages else {}
        source = (page.get("thumbnail") or {}).get("source")
        if source and "film" in (page.get("description") or "").lower():
            return {"poster": source, "wide": source}
    return None


# --- Clips ---

def clip_file(src):
    """Where the clip of an episode goes. Whether it's there yet is for the caller to check."""
    return os.path.join(CLIP_DIR, hashlib.sha1(src.encode()).hexdigest()[:20] + ".mp4")


def _cut(video, start, dest):
    """Cuts a candidate at low priority. True if it has no stretch of black in it."""
    done = subprocess.run(["nice", "-n", "15", "ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-y", "-threads", "1", "-ss", f"{start:.1f}",
                           "-i", video, "-t", str(CLIP_SECONDS), "-an", "-sn", "-dn", "-vf", CLIP_FILTER, "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "26", "-threads", "1", "-movflags", "+faststart", dest], capture_output=True, text=True, timeout=CLIP_CUT_TIMEOUT, check=True)
    return "black_start" not in done.stderr


def make_clip(video, duration, dest):
    """Cuts a silent clip from the middle of a video to dest, from whichever of a few spots has the most going on (it
    compresses the largest) and no black. False if none will do, or the video is too short for it. Raises if ffmpeg can't."""
    spots = [duration * f for f in CLIP_SPOTS if duration * f + CLIP_SECONDS < duration]
    if duration < CLIP_MIN_VIDEO_SECONDS or not spots:
        return False
    os.makedirs(CLIP_DIR, exist_ok=True)
    best, best_size = None, 0
    candidates = [f"{dest}.{i}.mp4" for i in range(len(spots))]
    try:
        for start, candidate in zip(spots, candidates):
            if _cut(video, start, candidate) and os.path.getsize(candidate) > best_size:
                best, best_size = candidate, os.path.getsize(candidate)
        if best:
            os.replace(best, dest)
        return bool(best)
    finally:
        for candidate in candidates:
            if os.path.exists(candidate):
                os.remove(candidate)


def prune_clips(keep):
    """Removes clips of episodes no longer wanted, once they've sat for a while. keep: the clip files still wanted."""
    try:
        names = os.listdir(CLIP_DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(CLIP_DIR, name)
        if path not in keep and time.time() - os.path.getmtime(path) > CLIP_KEEP_DAYS * 86400:
            os.remove(path)


def _load_lookups():
    try:
        with open(LOOKUPS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_lookups(lookups):
    os.makedirs(ART_DIR, exist_ok=True)
    tmp = LOOKUPS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lookups, f, indent=0)
    os.replace(tmp, LOOKUPS_FILE)


def refresh(wanted):
    """Gets what's missing for [(name, category, year, needs the large picture)], one at a time. A title with no art isn't
    asked about again for RETRY_DAYS; if the network is the problem, this stops and tries again next time."""
    lookups = _load_lookups()
    found = 0
    for name, category, year, needs_wide in wanted:
        key = key_of(name)
        if not key or key in NO_ART:
            continue
        entry = lookups.get(key)
        if not entry or (not entry.get("poster") and time.time() - entry.get("tried", 0) > RETRY_DAYS * 86400):
            try:
                art = (_look_up_movie if category in MOVIE_CATEGORIES else _look_up_show)(name, year)
            except (OSError, ValueError, KeyError, TypeError) as e:
                logger.info(f"Show art: stopping for now ({e})")
                break
            entry = lookups[key] = {"name": name, "tried": time.time(), **(art or {})}
            _save_lookups(lookups)
            logger.info(f"Show art for {name}: {'found' if art else 'none'}")
            time.sleep(REQUEST_GAP_SECONDS)
        for url, have, dest, picture_filter in (
                (entry.get("wide") or entry.get("poster"), thumb(name), os.path.join(ART_DIR, "promos", key + ".png"), THUMB_FILTER),
                (entry.get("wide") if needs_wide else None, wide(name), os.path.join(ART_DIR, "wide", key + ".jpg"), WIDE_FILTER)):
            if url and not have:
                try:
                    download_image(url, ART_HOSTS, dest, picture_filter)
                    found += 1
                except (OSError, ValueError, subprocess.SubprocessError) as e:
                    logger.info(f"Show art: couldn't get a picture for {name} ({e})")
                time.sleep(REQUEST_GAP_SECONDS)
    return found
