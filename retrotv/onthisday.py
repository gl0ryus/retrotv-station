"""
This Day in Gaming, Born Today and #1 This Week: the data for their cards (retrotv.screens), gathered a little at a
time by the writing job (retrotv/news.py) and kept in DATA_DIR. The station only reads ONTHISDAY_FILE.

- Games: the library's game commercials name their game and year. Each game is looked up once on Wikidata for its
  release date, kept only when that year agrees with the ad's (a remake has the same name), so every game on a card
  has an ad of its own to follow it. Wikidata's query service was down when this was written, so it's the plain API.
- Born Today: the main cast of each show in the library, from TVmaze, looked up again after CAST_REFRESH_DAYS.
- #1 This Week: Billboard's Hot 100 number ones from Wikipedia's yearly lists, 1977 to 2012 (before 1977 the tables
  are laid out differently, and there are few music videos), read once and matched to the library's music videos.

Run by hand to fill everything at once (politely paced): python3 -m retrotv.onthisday
"""
import datetime
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse

from retrotv import art
from retrotv.jsonio import get_json, write_json
from retrotv.meta import music_credit
from retrotv.paths import DATA_DIR, INDEX_FILE

logger = logging.getLogger("RetroTV")

ONTHISDAY_FILE = os.path.join(DATA_DIR, "onthisday.json")      # each broadcast day's picks: what the station reads
GAMES_FILE = os.path.join(DATA_DIR, "onthisday_games.json")    # game -> its Wikidata release, or that it has none
CAST_FILE = os.path.join(DATA_DIR, "onthisday_cast.json")      # show folder -> its TVmaze cast with birthdays
HOT100_FILE = os.path.join(DATA_DIR, "onthisday_hot100.json")  # year -> the Hot 100's number ones, week by week
WIKIDATA_API = "https://www.wikidata.org/w/api.php?"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php?"
TVMAZE = "https://api.tvmaze.com"
HOT100_YEARS = range(1977, 2013)
# Lookups per writing-job run (every two hours), and seconds between requests: Wikimedia turned away a request every
# 3 s, and one every 8 s after 14 (429), in November 2026, so the games fill in over a week or two; the cards use
# what there is. The Hot 100 years were seeded from a one-off read
PER_RUN = {"games": 20, "cast": 60, "hot100": 4}
GAP_SECONDS = {"games": 12.0, "cast": 0.6, "hot100": 15.0}
CAST_REFRESH_DAYS = 30
GAME_RETRY_DAYS = 60       # a game with no dated release on Wikidata is looked up again after this long
DAYS_KEPT = 4
PLACES_FIRST = ("Q49", "Q30")   # North America, United States: the release date an American station would know
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

# What's left of a YouTube title once its game is found: words about the ad, not the game
AD_WORDS = re.compile(r"\b(?:retro video game|video games?|tv|television|commercials?|ads?|advert(?:isement)?|spots?|"
                      r"trailers?|promo|pub|usa|japanese|japan|french|german|european|hd|official|version|rare|short|"
                      r"\d{1,2}s|\d{3,4}p|(?:19|20)\d\d)\b", re.I)
COUNTRY_TAGS = re.compile(r"\b(?:US|UK|FR|EU|JP)\b")   # capitals only: "Us" is a word in titles (The Last of Us)
FOR_A_MAKER = re.compile(r"\bfor\s+(?:nintendo|sega|sony|atari|microsoft)\b", re.I)
PLATFORM_WORDS = re.compile(
    r"\b(?:for\s+)?(?:nintendo\s+(?:64|ds|entertainment\s+system|gamecube|wii|switch)|super\s+nintendo|snes|nes|n64|"
    r"famicom|game\s*boy(?:\s+(?:advance|color))?|gba|gbc|gamecube|gcn|wii|playstation(?:\s*[1-4]|\s+portable)?|"
    r"ps[1-4p]|psx|xbox(?:\s*360)?|sega(?:\s+(?:genesis|cd|saturn|master\s+system))?|genesis|mega\s*drive|saturn|"
    r"dreamcast|game\s+gear|master\s+system|32x|atari(?:\s+(?:2600|5200|7800|jaguar|lynx))?|jaguar|lynx|"
    r"intellivision|colecovision|turbografx(?:[\s-]*16)?|neo\s*geo|3do|pc|mac)\b", re.I)
NOT_A_GAME = {"", "nintendo", "sega", "atari", "sony", "microsoft", "coleco", "mattel", "nec", "snk", "console",
              "system", "game", "games", "the", "new", "and"}


def _plain(text):
    return art._plain(text)


# --- Fetching, paced per service, stopping a service for this run when it says slow down ---

_last = {}


def _get(service, url):
    wait = GAP_SECONDS[service] - (time.time() - _last.get(service, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last[service] = time.time()
    return get_json(url, timeout=20)


def load(path, empty):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


# --- Games ---

def clean_title(raw, console="", split=True):
    """The game in a YouTube video's title, or None: "F-ZERO GP Legend GBA Commercial" -> "F-ZERO GP Legend".
    Of the pieces between dashes and commas, the longest that's still a name once ad and platform words are out.
    split=False for the older names, where " - " stands for a colon: "The Legend of Zelda - Twilight Princess"."""
    raw = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", raw).replace("™", " ").replace("®", " ")
    if console:
        raw = re.sub(re.escape(console), " ", raw, flags=re.I)
    raw = re.sub(r"\b(?:commercials?|ads?|spots?|adverts?)\s+\d{1,2}\b", " ", raw, flags=re.I)   # the ad's number
    pieces = re.split(r"\s+-\s+|,\s*|\s+–\s+|\s+\|\s+", raw) if split else [re.sub(r"\s+-\s+", ": ", raw)]
    best = ""
    for piece in pieces:
        piece = PLATFORM_WORDS.sub(" ", AD_WORDS.sub(" ", FOR_A_MAKER.sub(" ", COUNTRY_TAGS.sub(" ", piece))))
        piece = re.sub(r"\s*&\s*&.*$", "", re.sub(r"\s+", " ", piece)).strip(" -:_.!&")   # "Shrek the Third & & Nintendo"
        piece = re.sub(r"^(?:of|for|from|with|by)\s+", "", piece, flags=re.I)
        if _plain(piece) not in NOT_A_GAME and len(piece) > len(best):
            best = piece
    return best if len(best) >= 3 else None


def ad_game(src):
    """(year, title, platform) for a game ad named after its game, else None. Two kinds of name: the YouTube downloads,
    "2003 - Game Boy Advance - F-ZERO GP Legend GBA Commercial [IDkuUkG3XdU].mp4", and the older ones,
    "2002 - Tekken 4 (Playstation 2) - Retro Video Game Commercial 4-240p.mkv". No year, no game: the year is what
    tells a game from its remake."""
    name = os.path.splitext(os.path.basename(src))[0]
    m = re.match(r"^((?:19|20)\d\d) - ([^-\[]+?) - (.+?) \[[\w-]{11}\]$", name)
    if m:
        title = clean_title(m.group(3), m.group(2))
        return (int(m.group(1)), title, m.group(2).strip()) if title else None
    m = re.match(r"^((?:19|20)\d\d) - (.+?) \(([^)]+)\)(?:\s|-|$)", name)
    if m:
        title = clean_title(m.group(2), split=False)
        return (int(m.group(1)), title, m.group(3).replace(" _ ", " / ").strip()) if title else None
    return None


def game_ads(index):
    """{plain title: {"title", "year", "platform", "ads": [src]}} for the game commercials in the NAS listing."""
    found = {}
    for src in index.get("commercials", []):
        if "/commercials/games/" not in src and " - Retro Video Game Commercial" not in src:
            continue
        parsed = ad_game(src)
        if not parsed:
            continue
        year, title, platform = parsed
        key = f"{_plain(title)}|{year}"
        game = found.setdefault(key, {"title": title, "year": year, "platform": platform, "ads": []})
        game["ads"].append(src)
    return found


def release_date(statements):
    """The day a game came out, "YYYY-MM-DD", from its Wikidata publication dates (P577): North America's or the
    United States' first, then one with no place given, then the earliest. Only dates given to the day count."""
    dated = []
    for st in statements:
        if st.get("rank") == "deprecated":
            continue
        value = ((st.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        when = str(value.get("time", ""))
        if value.get("precision") != 11 or not re.match(r"^\+\d{4}-\d\d-\d\d", when):
            continue
        places = {((q.get("datavalue") or {}).get("value") or {}).get("id") for q in (st.get("qualifiers") or {}).get("P291", [])}
        dated.append((0 if places & set(PLACES_FIRST) else 1 if not places else 2, when[1:11]))
    return min(dated)[1] if dated else None


def search_games(title):
    """Wikidata items that are video games, closest to title first (full-text search, so "Warioworld" finds Wario
    World)."""
    hits = _get("games", WIKIDATA_API + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": f"{title} haswbstatement:P31=Q7889", "srlimit": 5,
         "format": "json"}))
    return [h["title"] for h in (hits.get("query") or {}).get("search", []) if re.fullmatch(r"Q\d+", h.get("title", ""))]


def pick_game(candidates, entities, year, title):
    """{"q", "label", "date", "links"} for the first candidate released within a year of the ad's year, or None.
    links (Wikipedias with an article on it) is how well known it is."""
    for q in candidates:
        e = entities.get(q) or {}
        date = release_date((e.get("claims") or {}).get("P577", []))
        if date and abs(int(date[:4]) - year) <= 1:
            labels = e.get("labels") or {}
            label = (labels.get("en") or labels.get("mul") or {}).get("value") or title
            return {"q": q, "label": label, "date": date, "links": len(e.get("sitelinks") or {})}
    return None


def gather_games(found, budget):
    """Looks up games not looked up yet (or not found, a while ago), those with the most ads first: a search each,
    then one request for all their candidates' dates. Returns {key: lookup}."""
    data = load(GAMES_FILE, {"games": {}})
    games = data.setdefault("games", {})
    now = time.time()
    todo = sorted((k for k in found if k not in games
                   or (not games[k].get("q") and now - games[k].get("checked", 0) > GAME_RETRY_DAYS * 86400)),
                  key=lambda k: -len(found[k]["ads"]))[:budget]
    searched = {}
    try:
        for key in todo:
            searched[key] = search_games(found[key]["title"])
        ids = sorted({q for qs in searched.values() for q in qs})
        entities = {}
        for i in range(0, len(ids), 50):
            entities.update(_get("games", WIKIDATA_API + urllib.parse.urlencode(
                {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": "labels|claims|sitelinks",
                 "languages": "en|mul", "format": "json"})).get("entities", {}))
    except (OSError, ValueError) as e:   # Wikidata having trouble: what wasn't finished waits for the next run
        logger.warning(f"Game lookups stopped after {len(searched)} searches: {e}")
        return games
    for key, candidates in searched.items():
        game = found[key]
        games[key] = {**(pick_game(candidates, entities, game["year"], game["title"]) or {"q": None}), "checked": time.time()}
    if searched:
        write_json(GAMES_FILE, data)
        logger.info(f"Looked up {len(searched)} games on Wikidata; {sum(1 for g in games.values() if g.get('q'))} dated, "
                    f"{sum(1 for k in found if k not in games)} still to look up")
    return games


def games_on(day, found, games):
    """The games released on day's date in any year, best known first, each with its ads."""
    md = day.strftime("-%m-%d")
    by_q = {}
    for key, game in found.items():
        hit = games.get(key) or {}
        if not str(hit.get("date") or "").endswith(md):
            continue
        entry = by_q.setdefault(hit["q"], {"title": hit["label"], "year": int(hit["date"][:4]), "platform": game["platform"],
                                           "links": hit.get("links", 0), "ads": []})
        entry["ads"] += game["ads"]
    return sorted(by_q.values(), key=lambda g: (-g["links"], g["year"]))[:6]


# --- Born today ---

def library_shows(index):
    """The library's show folders (videos/shows/<Show>, videos/g4/<Show>), dot-folders aside."""
    names = set()
    for category in ("videos", "g4"):
        for src in index.get(category, []):
            m = re.search(r"/videos/(?:shows|g4)/([^/]+)/", src)
            if m and not m.group(1).startswith("."):
                names.add(m.group(1))
    return sorted(names)


def gather_cast(shows, budget):
    """Looks up the shows' casts that are missing or older than CAST_REFRESH_DAYS. Returns {folder: entry}."""
    data = load(CAST_FILE, {"shows": {}})
    casts = data.setdefault("shows", {})
    now = time.time()
    todo = [s for s in shows if now - casts.get(s, {}).get("checked", 0) > CAST_REFRESH_DAYS * 86400]
    done = 0
    try:
        for folder in todo:
            if done >= budget:
                break
            show = art.pick_show(_get("cast", f"{TVMAZE}/search/shows?q=" + urllib.parse.quote(art._search_name(folder))), folder)
            cast = []
            if show:
                for c in _get("cast", f"{TVMAZE}/shows/{int(show['id'])}/cast"):
                    person, character = c.get("person") or {}, c.get("character") or {}
                    if person.get("name") and person.get("birthday"):
                        cast.append([person["name"], person["birthday"], person.get("deathday"), character.get("name")])
            casts[folder] = {"name": show["name"] if show else None, "cast": cast, "checked": time.time()}
            done += 1
    except (OSError, ValueError) as e:
        logger.warning(f"Cast lookups stopped after {done}: {e}")
    if done:
        write_json(CAST_FILE, data)
        logger.info(f"Looked up {done} casts on TVmaze; {len(todo) - done} to go")
    return casts


def born_on(day, casts):
    """The cast members born on day's date, in the most library shows first, each with their roles."""
    md = day.strftime("-%m-%d")
    people = {}
    for folder, show in casts.items():
        for name, born, died, character in show.get("cast", []):
            if born.endswith(md):
                person = people.setdefault(name, {"name": name, "born": born, "died": died, "roles": []})
                if not any(r[1] == (show.get("name") or folder) for r in person["roles"]):
                    person["roles"].append([character, show.get("name") or folder])
    return sorted(people.values(), key=lambda p: (-len(p["roles"]), p["born"]))[:6]


# --- #1 this week ---

def _cell(text):
    """A wikitext table cell as plain text: links to their labels, refs and templates out, quotes off."""
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{sortname\|([^|}]+)\|([^|}]+)[^}]*\}\}", r"\1 \2", text)
    text = re.sub(r"\{\{sort\|[^|{}]*\|([^{}]*)\}\}", r"\1", text)              # {{sort|Hall|[[Hall & Oates]]}}
    text = re.sub(r"\{\{(?:nowrap|nobr|small)\|([^{}]*)\}\}", r"\1", text)
    text = re.sub(r"\{\{dts\|(?:format=\w+\|)?([^{}|]*)\}\}", r"\1", text)     # {{dts|January 16}}
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    head, sep, rest = text.partition("|")
    if sep and "=" in head and "[" not in head:   # cell attributes: rowspan="3"|value
        text = rest
    return re.sub(r"\s+", " ", text).strip().strip('"').strip()


def hot100_weeks(year, text):
    """[[issue date, song, artist]] from a "List of Billboard Hot 100 number ones of YEAR" page's wikitext. A song's
    cells span its weeks (rowspan), so each week's row may hold only its date: the song carries on until the next."""
    weeks, song, artist = [], None, None
    table = text[text.find("Issue date"):] if "Issue date" in text else text
    for row in table.split("\n|-")[1:]:
        cells = [c for c in re.split(r"\n[|!]|\|\||!!", "\n" + row.strip()) if c.strip()]
        date = None
        m = re.search(r"hot-100/(\d{4}-\d\d-\d\d)", row)
        if m:
            date = m.group(1)
        else:
            for c in cells:
                m = re.fullmatch(rf"({'|'.join(MONTHS)}) (\d{{1,2}})", _cell(c))
                if m:
                    date = f"{year}-{MONTHS.index(m.group(1)) + 1:02d}-{int(m.group(2)):02d}"
        # The song's cell: its text (after any align="..."| attributes) starts with a quote mark
        quoted = [i for i, c in enumerate(cells) if re.search(r'^(?:[^|]*\|)?\s*"', c.strip())]
        if quoted:
            i = quoted[0]
            song = _cell(cells[i])
            # A new song by the act that had the last one has no artist cell: the last one's spans both
            following = _cell(cells[i + 1]) if i + 1 < len(cells) else ""
            if following and not re.fullmatch(rf"({'|'.join(MONTHS)}) \d{{1,2}}|\d+", following):
                artist = following
        if date and date.startswith(str(year)) and song and artist:
            weeks.append([date, song, artist])
        if "\n|}" in "\n" + row and weeks:
            break
    return weeks


def gather_hot100(budget):
    data = load(HOT100_FILE, {"years": {}})
    years = data.setdefault("years", {})
    done = 0
    try:
        for year in [y for y in HOT100_YEARS if str(y) not in years][:budget]:
            page = _get("hot100", WIKIPEDIA_API + urllib.parse.urlencode(
                {"action": "parse", "format": "json", "prop": "wikitext", "redirects": 1,
                 "page": f"List of Billboard Hot 100 number ones of {year}"}))
            weeks = hot100_weeks(year, page["parse"]["wikitext"]["*"])
            if len(weeks) >= 50:
                years[str(year)] = weeks
            else:
                logger.warning(f"Hot 100 {year}: only {len(weeks)} weeks read, not kept")
            done += 1
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"Hot 100 lists stopped after {done}: {e}")
    if done:
        write_json(HOT100_FILE, data)
    return years


def _song_key(text):
    text = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", text or "")
    text = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with|&|and|x)\s+", text, flags=re.I)[0]
    return _plain(text)


def music_videos(index):
    """[(src, artist key, song key)] for the library's music videos, both ways round where the name might be
    "Song - Artist"."""
    out = []
    for src in index.get("music", []):
        artist, song, _ = music_credit(src)
        if artist and song:
            out.append((src, _song_key(artist), _song_key(song)))
            out.append((src, _song_key(song), _song_key(artist)))
    return out


def find_video(song, artist, videos):
    """The library's video for this song, or None: the same song, and the same lead artist."""
    want_song, want_artist = _song_key(song), _song_key(artist)
    for src, a, s in videos:
        if s == want_song and a and (a == want_artist or a in want_artist or want_artist in a):
            return src
    return None


def number_ones_on(day, years, videos):
    """For each year, the Hot 100's number one in the week of day's date: the chart in force that day (the latest issue
    on or before it), its weeks at number one that run, and the library's video for it if there is one."""
    out = []
    for year, weeks in sorted(years.items()):
        target = f"{year}-{day:%m-%d}"
        current = max((i for i, w in enumerate(weeks) if w[0] <= target), default=None)
        if current is None:
            continue
        date, song, artist = weeks[current]
        run = sum(1 for w in weeks if w[1] == song and w[2] == artist)
        out.append({"year": int(year), "song": song, "artist": artist, "issue": date, "weeks": run,
                    "video": find_video(song, artist, videos)})
    return out


# --- The day's picks ---

def pick(day, found, games, casts, years, videos):
    numbers = number_ones_on(day, years, videos)
    with_video = [n for n in numbers if n["video"]]
    # The one the refill copies ahead (Station plays it if it's on the SD card in time): the same all day
    featured = random.Random(day.toordinal()).choice(with_video)["year"] if with_video else None
    return {"games": games_on(day, found, games), "born": born_on(day, casts), "number_ones": numbers,
            "featured_year": featured}


def gather(today, budgets=None):
    """One run's worth: some lookups for each kind, then today's and tomorrow's picks into ONTHISDAY_FILE."""
    budgets = budgets or PER_RUN
    with open(INDEX_FILE) as f:
        index = json.load(f)["media_map"]
    found = game_ads(index)
    games = gather_games(found, budgets["games"])
    casts = gather_cast(library_shows(index), budgets["cast"])
    years = gather_hot100(budgets["hot100"])
    videos = music_videos(index)
    data = load(ONTHISDAY_FILE, {"days": {}})
    days = data.setdefault("days", {})
    for day in (today, today + datetime.timedelta(days=1)):
        days[day.isoformat()] = pick(day, found, games, casts, years, videos)
    data["days"] = {d: days[d] for d in sorted(days)[-DAYS_KEPT:]}
    write_json(ONTHISDAY_FILE, data)
    picks = days[today.isoformat()]
    logger.info(f"On this day {today}: {len(picks['games'])} games, {len(picks['born'])} birthdays, "
                f"{sum(1 for n in picks['number_ones'] if n['video'])} number ones with a video")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    day = (datetime.datetime.now() - datetime.timedelta(hours=9)).date()
    for _ in range(40):   # until nothing's left to look up
        before = (len(load(GAMES_FILE, {"games": {}})["games"]), len(load(CAST_FILE, {"shows": {}})["shows"]),
                  len(load(HOT100_FILE, {"years": {}})["years"]))
        gather(day)
        after = (len(load(GAMES_FILE, {"games": {}})["games"]), len(load(CAST_FILE, {"shows": {}})["shows"]),
                 len(load(HOT100_FILE, {"years": {}})["years"]))
        if after == before:
            break
