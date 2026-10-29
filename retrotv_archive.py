"""
Public domain video from the Internet Archive (archive.org): old TV, movies, cartoons, short films and
commercials, aired without being kept on the NAS. The station's archive worker (retro_station's CacheManager)
asks pick() for one file at a time, downloads it to the SD card just ahead of airing, and deletes it once it
has aired.

Picks come from public-domain-marked items in a few collections (KINDS), the most downloaded first, in
English. "Marked" is the uploader's word for it: archive.org doesn't check. Anything with a whiff of SKIP
in its title, subject tags, description or file name never airs: sex and nudity, drugs and drink, gore,
hate, war and the old scare films. So do files too short or long for their kind (compilations, mostly).
The catalog of items is searched again every CATALOG_DAYS. A file isn't picked again until half as many
others of its kind as the catalog has items have been.
"""
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request

from retrotv_screens import DATA_DIR, USER_AGENT

logger = logging.getLogger("RetroTV")

STATE_FILE = os.path.join(DATA_DIR, "archive.json")   # the catalog, what's been picked, items with nothing to air
CATALOG_DAYS = 7
DUDS_KEEP = 5000   # items with nothing that can air, not looked at again
SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"
DOWNLOAD_URL = "https://archive.org/download/"
# kind: (collections, the station's category, made-up folder, (shortest, longest) seconds, most-downloaded
# items searched). The folders are what the station's name parsers and broadcast standards see: "Cartoons"
# rates as a kids' show, so those air before KIDS_END_HOUR only
KINDS = {
    "tv": (("classic_tv",), "videos", "Classic TV", (18 * 60, 65 * 60), 2500),
    "movies": (("feature_films",), "movies", "Movies", (60 * 60, 150 * 60), 1500),
    "cartoons": (("animationandcartoons", "classic_cartoons"), "videos", "Cartoons", (3 * 60, 20 * 60), 1000),
    "shorts": (("prelinger",), "videos", "Short Films", (4 * 60, 25 * 60), 1000),
    "commercials": (("classic_tv_commercials",), "commercials", "Commercials", (10, 120), 1000),
}
SERIES_KINDS = ("tv", "cartoons")   # titles like "Betty Boop: Snow White" name a series and an episode
# (first, last) year a kind can be from. Movies only from Hollywood's Production Code years, when its own
# censors kept them clean; no cartoons from before it (racy early Betty Boop). Both need a known year (the
# undated ones are mostly knockoffs and fan edits). Nothing after 1979: those are new uploads or someone
# else's, whatever they're marked
YEARS = {"movies": (1934, 1960), "cartoons": (1934, 1979), "tv": (None, 1979), "shorts": (None, 1979),
         "commercials": (None, 1979)}
YEAR_REQUIRED = ("movies", "cartoons")
WAR_YEARS = (1941, 1945)   # wartime cartoons, shorts and films are full of propaganda and slurs: none of those years


def _year_ok(kind, year):
    first, last = YEARS.get(kind, (None, None))
    if not year:
        return kind not in YEAR_REQUIRED
    return not ((first and year < first) or (last and year > last) or WAR_YEARS[0] <= year <= WAR_YEARS[1])
# archive.org's MP4s, best first. Its own h.264 copies are about 512x384, as much as the 512x300 stage shows
FORMATS = ("h.264", "h.264 IA", "MPEG4", "h.264 HD", "512Kb MPEG4")
MAX_BYTES_PER_SECOND = 600_000   # heavier files (HD originals) aren't worth the download
SKIP_WORDS = (
    # sex and nudity
    r"\bsex", r"\bnud(?:e|es|ist|ists|ism|ity)\b", r"naked", r"topless", r"erotic", r"porn", r"obscen", r"burlesque",
    r"strip-?tease", r"stripper", r"\bstag\b", r"pin-?ups?\b", r"cheesecake", r"girlie", r"lingerie", r"bikini",
    r"bathing beaut", r"skinny.?dip", r"seduc", r"\blust", r"sensual", r"steamy", r"risqu", r"\bracy\b", r"titillat",
    r"lurid", r"\badults?\b", r"x-?rated", r"exploitation", r"sexploit", r"white slave", r"prostitut", r"brothel",
    r"call girl", r"\bvice\b", r"harlot", r"wanton", r"\bsin\b", r"sinful", r"forbidden", r"taboo", r"scandal",
    r"shocking", r"pervers", r"pervert", r"molest", r"predator", r"boys beware", r"girls beware", r"child bride",
    r"incest", r"\brap(?:e|ed|ist)\b", r"bondage", r"fetish", r"venereal", r"syphilis", r"gonorrh", r"hygiene",
    r"menstruat", r"puberty", r"pregnan", r"childbirth", r"reproducti", r"birth control", r"showgirl", r"follies",
    r"can-?can", r"fresh from paris", r"she gods",
    # drugs, drink and smoking (the ads too)
    r"\bdrugs?\b", r"reefer", r"marihuana", r"marijuana", r"narcotic", r"cocaine", r"\bheroin\b", r"opium", r"\bdope\b",
    r"addict", r"\bdrunk", r"booze", r"alcohol", r"liquor", r"cocktail", r"\bbeers?\b", r"whiske?y", r"vodka",
    r"bourbon", r"\bwines?\b", r"budweiser", r"schlitz", r"pabst", r"hamm'?s", r"ballantine", r"falstaff",
    r"miller high life", r"rheingold", r"cigar", r"tobacco", r"smoking", r"marlboro", r"winston", r"lucky strike",
    r"chesterfield", r"pall mall", r"\bkool\b", r"\bsalem\b", r"viceroy",
    # gore, horror and cruelty
    r"\bgore\b", r"\bgory\b", r"blood", r"tortur", r"sadis", r"gruesome", r"grisly", r"dismember", r"mutilat",
    r"decapitat", r"severed", r"vivisect", r"corpse", r"cannibal", r"flesh", r"zombie", r"ghoul", r"living dead",
    r"undead", r"vampire", r"satan", r"occult", r"witchcraft", r"sacrific", r"revival of organisms", r"maniac",
    r"massacre", r"slasher", r"atrocit", r"execution", r"suicid", r"autopsy", r"surg(?:ery|ical)",
    r"disaster", r"hindenburg", r"explodes", r"\bwreck", r"\bcrash",
    # politics, war, hate and stereotypes
    r"election", r"presidential", r"politic", r"\bcampaign",
    r"propaganda", r"\bnazi", r"hitler", r"f(?:ue|ü)hrer", r"holocaust", r"concentration camp", r"newsreel", r"\bwar\b",
    r"wartime", r"world war", r"racis", r"stereotyp", r"blackface", r"minstrel", r"caricatur", r"offensive",
    r"politically incorrect", r"censored", r"banned", r"controversial", r"ku klux", r"jim crow", r"\bjaps?\b",
    r"\bnips\b", r"japoteur", r"tokio jokio", r"darkie", r"pickaninn", r"sambo", r"\binjuns?\b", r"redskin",
    r"\bcoons?\b", r"savages", r"mammy", r"\bbosko\b", r"\binki\b", r"snafu", r"coal black", r"jungle jitters",
    r"tin pan alley cats", r"clean pastures", r"uncle tom", r"meetin'? time", r"rabbit stew", r"angel puss",
    r"jivin'? bears", r"pingo pongo", r"scrub me mama", r"you rascal you", r"hallelujah land", r"rise to fame",
    r"bamboo isle", r"boop-oop-a-doop", r"chess-nuts", r"red hot mamma", r"yellowface", r"charlie chan",
    r"mr\.? wong", r"mr\.? moto", r"fu manchu", r"chinatown", r"tarzan", r"\bnatives?\b", r"tokyo", r"tokio",
    r"\baxis\b", r"mr\.? hook",
)
SKIP = re.compile("|".join(SKIP_WORDS), re.I)
ENGLISH = re.compile(r"^(?:en|eng|english)\b", re.I)
SERIES_SPLIT = re.compile(r"^(.{3,40}?)(?:\s+[:\-–]\s+|:\s+)(.+)$")                        # "Betty Boop: Snow White"
EPISODE_NUMBER = re.compile(r"^(.{3,40}?)\s+(?:ep|episode|e)\.?\s*\d+\s*[:\-–]?\s*(.+)$", re.I)  # "Show Ep01 Title"
SERIES_JUNK = re.compile(r"\b(?:public domain|pd|episodes?|complete(?: series)?|collection|tv show|tv series|"
                         r"the series|cartoons?|season \d+|vol(?:ume)?\.? ?\d+|ep(?:isode)?\.? ?\d+)\b|\([^)]*\)|[!#]|"
                         r"\b\d+\s*$", re.I)

_lock = threading.Lock()
_state = None


# --- archive.org ---

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def open_url(url):
    """A download, for retro_station's paced_copy to read from."""
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30)


def _text(value):
    """A metadata field (a string, a list of them, or missing) as one string, without HTML."""
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _year(value):
    m = re.search(r"\b(1[89]\d\d|20[0-2]\d)\b", _text(value))
    return int(m.group(1)) if m else None


def _search(kind):
    """[[identifier, title, year]] for a kind: its collections' public domain video, most downloaded first,
    with anything SKIP turns up (or not in English) left out."""
    collections, _, _, _, rows = KINDS[kind]
    q = (f"collection:({' OR '.join(collections)}) AND mediatype:movies "
         f"AND (licenseurl:*publicdomain* OR licenseurl:*zero*)")
    params = [("q", q), ("rows", str(rows)), ("page", "1"), ("output", "json"), ("sort[]", "downloads desc")]
    params += [("fl[]", f) for f in ("identifier", "title", "year", "subject", "description", "language")]
    out = []
    for d in _get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")["response"]["docs"]:
        language = _text(d.get("language")).strip()
        words = " ".join(_text(d.get(f)) for f in ("title", "subject", "description")) + " " + d["identifier"].replace("_", " ")
        year = _year(d.get("year")) or _year(d.get("title"))
        if (language and not ENGLISH.match(language)) or SKIP.search(words) or not d.get("title") or not _year_ok(kind, year):
            continue
        title = _text(d["title"])
        title = title if " " in title.strip() else title.replace("_", " ")   # "The_Lone_Wolf_-_The_Blue_Lantern"
        out.append([d["identifier"], re.sub(r"\s+", " ", title).strip(), year])
    return out


def _seconds(length):
    """A file's length: "1492.37", "24:52" or "0:24:52"."""
    try:
        parts = [float(p) for p in str(length).split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _playable(meta, kind):
    """The files of an item that can air as `kind`: one MP4 per video, the best FORMATS copy, long enough and
    not too long, with nothing SKIP turns up in its name."""
    if meta.get("is_dark") or (meta.get("metadata") or {}).get("access-restricted-item"):
        return []
    shortest, longest = KINDS[kind][3]
    best = {}
    for f in meta.get("files", []):
        name, fmt = f.get("name", ""), f.get("format", "")
        if not name.lower().endswith(".mp4") or fmt not in FORMATS:
            continue
        length, size = _seconds(f.get("length")), int(f.get("size") or 0)
        if not shortest <= length <= longest or not size or size / length > MAX_BYTES_PER_SECOND:
            continue
        words = f"{name.replace('_', ' ')} {_text(f.get('title'))}"
        year = _year(words)   # a year in the file's own name, for items that don't give one
        if SKIP.search(words) or (year and not _year_ok(kind, year)):
            continue
        group = f.get("original") or name   # copies of the same video share their original
        group = group[0] if isinstance(group, list) else group
        rank = FORMATS.index(fmt)
        if group not in best or rank < best[group][0]:
            best[group] = (rank, {"name": name, "title": _text(f.get("title")).strip(), "length": length, "size": size})
    return [f for _, f in best.values()]


# --- Names the station can read ---

def _series_name(text):
    name = re.sub(r"^\s*more\s+", "", SERIES_JUNK.sub(" ", text), flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" -:–,.'\"") or None


def _split(title, identifier):
    """(series, episode) from a one-video item: "Betty Boop: Snow White", "Beverly Hillbillies Ep01 The
    Clampetts Strike Oil", or "A Wife For Andy" filed as "Andy_Griffith_A_Wife_For_Andy". (None, title) when
    there's no telling."""
    for pattern in (SERIES_SPLIT, EPISODE_NUMBER):
        m = pattern.match(title)
        if m:
            series = _series_name(m.group(1))
            if series:
                return series, m.group(2).strip()
    words = re.sub(r"[_\-.]+", " ", identifier).split()
    title_words = re.sub(r"[^\w\s]", "", title).lower().split()
    if title_words and len(words) > len(title_words) and [w.lower() for w in words[-len(title_words):]] == title_words:
        series = _series_name(" ".join(words[:-len(title_words)]))
        if series and len(series) > 3:
            return series, title
    return None, title


def _safe(text, limit=150):
    """A path part a file name can have: no slashes, colons or the like, not too long."""
    text = re.sub(r"[\\/\x00-\x1f]+", "-", text.replace(":", " -"))
    text = re.sub(r"[*?\"<>|]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" .")[:limit].strip() or "untitled"


def _path(kind, identifier, title, year, f, multi):
    """A made-up "Folder/Series/Series - Episode (year) [tag].mp4" for a file: what the station's name parsers
    read for its title, era and holiday. The tag keeps two files with the same title apart."""
    folder = KINDS[kind][2]
    series, name = None, title
    if multi:
        # One of many videos in an item ("Bonanza public domain episodes"): the file has the episode's name
        name = f["title"] or os.path.splitext(f["name"])[0].replace("_", " ")
        name = re.sub(r"[ _-]*\b512kb\b|\.ia$", "", name, flags=re.I).strip() or name   # archive.org's own suffixes
        if kind in SERIES_KINDS:
            series = _series_name(title)
            if series and name.lower().startswith(series.lower()):
                name = name[len(series):].strip(" -:–") or name
    elif kind in SERIES_KINDS:
        series, name = _split(title, identifier)
    label = f"{series} - {name}" if series else name
    if year and not re.search(r"\b(?:18|19|20)\d\d\b", label):   # its own date or year beats the item's
        label += f" ({year})"
    tag = hashlib.sha1(f"{identifier}/{f['name']}".encode()).hexdigest()[:8]
    return "/".join([folder] + ([_safe(series)] if series else []) + [f"{_safe(label)} [{tag}].mp4"])


# --- Picking ---

def _load():
    global _state
    if _state is None:
        try:
            with open(STATE_FILE) as f:
                _state = json.load(f)
        except (OSError, ValueError):
            _state = {}
        for key, empty in (("built", 0), ("kinds", {}), ("seen", {}), ("duds", [])):
            _state.setdefault(key, empty)
    return _state


def _save(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _catalog(state):
    """The items to pick from, searched again once the catalog is CATALOG_DAYS old (the old one stays if
    archive.org can't be reached)."""
    if time.time() - state["built"] < CATALOG_DAYS * 86400 and all(k in state["kinds"] for k in KINDS):
        return state["kinds"]
    try:
        kinds = {kind: _search(kind) for kind in KINDS}
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"archive.org search failed ({e}), {'keeping the old catalog' if state['kinds'] else 'nothing to pick from yet'}")
        return state["kinds"]
    state["kinds"], state["built"] = kinds, time.time()
    _save(state)
    logger.info("archive.org catalog: " + ", ".join(f"{len(v)} {k}" for k, v in kinds.items()))
    return kinds


def pick(kind, tries=8):
    """The next file to air as `kind`, as {"url", "path", "category", "size", "length", "item"}, with path the
    made-up one from _path(). None when nothing turns up in a few tries (or archive.org can't be reached)."""
    with _lock:
        state = _load()
        duds = set(state["duds"])
        items = [x for x in _catalog(state).get(kind, []) if x[0] not in duds]
        seen = set(state["seen"].get(kind, []))
        for identifier, title, year in random.sample(items, min(tries, len(items))):
            try:
                files = _playable(_get(METADATA_URL + urllib.parse.quote(identifier)), kind)
            except (OSError, ValueError) as e:
                logger.warning(f"archive.org: no details for {identifier} ({e})")
                continue
            if not files:
                state["duds"] = (state["duds"] + [identifier])[-DUDS_KEEP:]   # nothing in it can air: don't ask again
                continue
            fresh = [f for f in files if f"{identifier}/{f['name']}" not in seen]
            if not fresh:
                continue
            f = random.choice(fresh)
            keep = max(20, len(items) // 2)
            state["seen"][kind] = (state["seen"].get(kind, []) + [f"{identifier}/{f['name']}"])[-keep:]
            _save(state)
            return {"url": DOWNLOAD_URL + urllib.parse.quote(f"{identifier}/{f['name']}"),
                    "path": _path(kind, identifier, title, year, f, len(files) > 1),
                    "category": KINDS[kind][1], "size": f["size"], "length": f["length"], "item": identifier}
        _save(state)
        return None
