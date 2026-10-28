"""
More break-time screens for the GL0RYUS Network: Pop Quiz, Today in History, Launch Watch,
Who's in Space and Markets. Same approach as retrotv_cards (ASS over an mpv lavfi URL); the data comes
from free public APIs and is cached on the SD card so a flaky connection only means a stale screen.
"""
import datetime
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from retrotv_cards import (
    ARCHIVO, BARLOW_B, BARLOW_S, BARLOW_X, BLACK, CREAM, H, HOME_DIR, MUSTARD, SHADOW_RED, SKY, W, WHITE,
    YELLOW, ASSETS_DIR, Card, Script, circle, clip_text, clock, esc, fit, lavfi_url, music, rect, stripes,
    text_width,
)

logger = logging.getLogger("RetroTV")

DATA_DIR = os.path.join(HOME_DIR, "retrotv_data")
WORLD_MAP = os.path.join(ASSETS_DIR, "world_110m.json")  # Natural Earth 110m land, simplified
USER_AGENT = "GL0RYUS-RetroTV/1.0 (personal hobby TV station)"
SPACE_BACKGROUND = "0x060a1e"
QUIZ_BACKGROUND = "0x1c0a36"
GREEN = "&H0040C050&"
FLORIDA_LOCATIONS = (12, 27)  # Cape Canaveral SFS, Kennedy Space Center


def fetch_json(name, url, max_age_minutes, stale_hours=24):
    """GETs JSON through a cache in DATA_DIR. Falls back to a stale copy when the fetch fails."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{name}.json")
    cached = None
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached["fetched"] < max_age_minutes * 60:
            return cached["data"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"fetched": time.time(), "data": data}, f)
        os.replace(tmp, path)
        return data
    except Exception as e:
        logger.warning(f"Fetch failed for {name}: {e}")
    if cached and time.time() - cached.get("fetched", 0) < stale_hours * 3600:
        return cached["data"]
    return None


# Average glyph width for mixed-case running text, as a fraction of the font size. The cards'
# WIDTH_FACTOR is sized for capitals, which wraps sentences far too early
TEXT_FACTOR = {ARCHIVO: 0.66, BARLOW_X: 0.40, BARLOW_B: 0.38, BARLOW_S: 0.36}


def wrap(text, font, size, max_width, max_lines=3):
    """Word-wraps by estimated width (the scripts use WrapStyle 2, so lines are broken here)."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and len(trial) * size * TEXT_FACTOR[font] > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = clip_text(lines[-1] + " …", len(lines[-1]))
    return lines


def info_header(s, d, kicker, title_pages, now, band="&H003C2818&"):
    """Header band like the weather's: kicker, a title per page [(start, end, text)], ticking clock."""
    fade = (300, 400)
    s.shape(0, d, 0, 0, rect(W, 168), band, fade=fade, layer=1)
    s.shape(0, d, 0, 168, rect(W, 6), "&H002C8AF0&", fade=fade, layer=1)
    s.text(0, d, 80, 82, kicker, BARLOW_X, 30, MUSTARD, align=4, spacing=6, fade=fade)
    for a, b, title in title_pages:
        s.text(a, b, 80, 130, title, BARLOW_X, 56, YELLOW, align=4, shadow=3, shadow_color=BLACK, fade=(300, 300))
    s.text(0, d, 944, 132, now.strftime("%a %b %-d").upper(), BARLOW_S, 32, SKY, align=6, fade=fade)
    for sec in range(int(d) + 1):
        t = now + datetime.timedelta(seconds=sec)
        s.text(sec, min(d, sec + 1), 944, 88, clock(t), BARLOW_X, 44, WHITE, align=6, fade=None)


def starfield(s, d, seed, top=0, count=80):
    rnd = random.Random(seed)
    for _ in range(count):
        x, y = rnd.randrange(16, W - 16), rnd.randrange(top + 8, H - 8)
        r = rnd.choice((1.2, 1.6, 2.2))
        twinkle = ""
        if rnd.random() < 0.25:
            period = rnd.randint(900, 1800)
            twinkle = "".join(f"\\t({t},{t + period // 2},\\1a&HB0&)\\t({t + period // 2},{t + period},\\1a&H00&)"
                              for t in range(0, int(d * 1000), period))
        s.shape(0, d, x - r, y - r, circle(r), rnd.choice((WHITE, SKY, CREAM)), fade=(300, 400), layer=0,
                extra=twinkle)


# --- Pop Quiz ---

# Every Open Trivia DB category, with the short name shown on screen
QUIZ_CATEGORIES = {9: "General Knowledge", 10: "Books", 11: "Movies", 12: "Music", 13: "Musicals", 14: "Television",
                   15: "Video Games", 16: "Board Games", 17: "Science & Nature", 18: "Computers", 19: "Math",
                   20: "Mythology", 21: "Sports", 22: "Geography", 23: "History", 24: "Politics", 25: "Art",
                   26: "Celebrities", 27: "Animals", 28: "Vehicles", 29: "Comics", 30: "Gadgets",
                   31: "Anime & Manga", 32: "Cartoons"}
QUIZ_FILE = os.path.join(DATA_DIR, "quiz.json")       # the shuffled deck still to air
QUIZ_BANK = os.path.join(DATA_DIR, "quiz_bank.json")  # every question we use, from build_quiz_bank()
QUIZ_BANK_SIZE = 3000
QUIZ_BANK_DAYS = 30  # re-download this often to pick up questions Open Trivia DB has added
_bank_thread = None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _opentdb(url):
    """One Open Trivia DB call, paced for its limit of one request per 5 seconds."""
    for attempt in range(5):
        time.sleep(5.5 + 10 * attempt)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:  # it answers too-fast calls with a 429
            if e.code != 429:
                raise
            continue
        if data.get("response_code") != 5:
            return data
    raise RuntimeError("still rate-limited")


def build_quiz_bank():
    """Downloads every easy and medium multiple-choice question in QUIZ_CATEGORIES, up to
    QUIZ_BANK_SIZE. A session token per category means nothing comes back twice. It takes a few
    minutes, so the station runs it on a background thread."""
    bank = {}
    for category in QUIZ_CATEGORIES:
        token = _opentdb("https://opentdb.com/api_token.php?command=request")["token"]
        amount = 50
        while amount:
            data = _opentdb(f"https://opentdb.com/api.php?amount={amount}&category={category}&type=multiple"
                            f"&encode=url3986&token={token}")
            if data["response_code"] != 0:  # fewer than `amount` left, or none: ask for less
                amount //= 2
                continue
            for q in data["results"]:
                q = {k: (urllib.parse.unquote(v) if isinstance(v, str) else [urllib.parse.unquote(x) for x in v])
                     for k, v in q.items()}
                q["category_id"] = category
                q["key"] = hashlib.sha1(q["question"].encode()).hexdigest()[:12]
                if q["difficulty"] != "hard":
                    bank[q["key"]] = q
    keys = random.sample(list(bank), min(len(bank), QUIZ_BANK_SIZE))
    _write_json(QUIZ_BANK, {"built": time.time(), "questions": [bank[k] for k in keys]})
    logger.info(f"Quiz: question bank rebuilt with {len(keys)} questions")


def _refresh_bank():
    try:
        build_quiz_bank()
    except Exception as e:
        logger.warning(f"Quiz bank download failed: {e}")


def _next_question():
    """The next question off a shuffled deck of the whole bank. Every question airs once before
    any repeats; when the deck runs out it's reshuffled and starts over."""
    global _bank_thread
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(QUIZ_BANK) as f:
            saved = json.load(f)
        bank, built = {q["key"]: q for q in saved["questions"]}, saved["built"]
    except (OSError, ValueError, KeyError):
        bank, built = {}, 0
    if time.time() - built > QUIZ_BANK_DAYS * 86400 and not (_bank_thread and _bank_thread.is_alive()):
        _bank_thread = threading.Thread(target=_refresh_bank, daemon=True)
        _bank_thread.start()
    if not bank:
        return None
    try:
        with open(QUIZ_FILE) as f:
            deck = json.load(f).get("deck", [])
    except (OSError, ValueError, AttributeError):
        deck = []
    deck = [k for k in deck if k in bank]  # a rebuilt bank may have dropped some
    if not deck:
        deck = list(bank)
        random.shuffle(deck)
        logger.info(f"Quiz: dealing a fresh deck of {len(deck)} questions")
    q = bank[deck.pop()]
    _write_json(QUIZ_FILE, {"deck": deck})
    return q


def pop_quiz():
    """Game-show trivia: question, four answers, a 20-second countdown, then the reveal at 22 s."""
    q = _next_question()
    if not q:
        return None
    d, start, reveal = 33.0, 2.0, 22.0   # quiz_bed.ogg is cut to these times
    answers = q["incorrect_answers"][:3] + [q["correct_answer"]]
    random.shuffle(answers)
    correct = answers.index(q["correct_answer"])
    category = QUIZ_CATEGORIES.get(q.get("category_id"), q["category"].split(": ")[-1])
    out = (300, 400)
    s = Script()

    s.text(0, d, 80, 88, "POP QUIZ", ARCHIVO, 66, align=4, shadow=6, slant=-0.15, fade=out)
    stripes(s, 0, d, 430, 70, 330)
    s.text(0.2, d, 84, 142, category.upper(), BARLOW_X, 32, MUSTARD, align=4, spacing=4, fade=out)
    # Countdown dial
    s.shape(0, d, 900 - 50, 96 - 50, circle(50), MUSTARD, fade=out, layer=1)
    s.shape(0, d, 900 - 44, 96 - 44, circle(44), "&H00360A1C&", fade=out, layer=2)
    count = int(reveal - start)
    for i in range(count):
        s.text(start + i, start + i + 1, 900, 98, str(count - i), ARCHIVO, 50, YELLOW, fade=None, layer=3)
    s.text(reveal, d, 900, 98, "✓", BARLOW_X, 64, GREEN, fade=(150, 400), layer=3)
    # Timer bar shrinking to the left
    s.shape(0, d, 80, 318, rect(864, 8), "&H00402040&", fade=out, layer=1)
    s.shape(start, reveal, 80, 318, rect(864, 8), MUSTARD, fade=None, layer=2,
            extra=f"\\t(0,{int((reveal - start) * 1000)},\\fscx0)")

    size = 44
    lines = wrap(q["question"], BARLOW_B, size, 860)
    if len(lines) > 2:
        size = 38
        lines = wrap(q["question"], BARLOW_B, size, 860)
    # Multi-line text goes in raw, since esc() would turn the \N line breaks into "/N"
    s.raw(0, d, f"\\an5\\pos(512,245)\\fn{BARLOW_B}\\fs{size}\\c{CREAM}\\bord0\\shad2\\4c{BLACK}\\fad(400,400)",
          "\\N".join(esc(l) for l in lines), layer=2)

    for i, answer in enumerate(answers):
        x = 70 if i % 2 == 0 else 522
        y = 346 if i < 2 else 450
        t_in = 0.8 + i * 0.2
        s.shape(t_in, d, x - 3, y - 3, rect(438, 92), MUSTARD, fade=out, layer=1)
        s.shape(t_in, d, x, y, rect(432, 86), "&H00602040&", fade=out, layer=2)
        if i == correct:
            s.shape(reveal, d, x, y, rect(432, 86), GREEN, fade=(150, 400), layer=3)
            s.shape(reveal, reveal + 0.35, x - 3, y - 3, rect(438, 92), WHITE, fade=(0, 300), layer=6)
        else:
            s.shape(reveal, d, x - 3, y - 3, rect(438, 92), "&H00000000&", fade=(200, 400), layer=6,
                    extra="\\1a&H70&")
        s.shape(t_in, d, x + 18, y + 17, circle(26), MUSTARD, fade=out, layer=4)
        s.text(t_in, d, x + 44, y + 45, "ABCD"[i], ARCHIVO, 34, "&H00200A1C&", fade=out, layer=5)
        answer = clip_text(answer, 44)
        asize = fit(answer, BARLOW_B, 40, 330)
        alines = [answer]
        if asize < 32:
            alines = wrap(answer, BARLOW_B, 32, 330, max_lines=2)
            asize = 32
        s.raw(t_in, d, f"\\an4\\pos({x + 88},{y + 45})\\fn{BARLOW_B}\\fs{asize}\\c{WHITE}\\bord0\\shad1"
                       f"\\4c{BLACK}\\fad(300,400)", "\\N".join(esc(l) for l in alines), layer=5)
    return Card(lavfi_url(s.write("quiz"), d, music("quiz_bed.ogg"), background=QUIZ_BACKGROUND), d, "quiz")


# --- Today in History ---

BIRTH_WORDS = re.compile(r"\b(actor|actress|singer|musician|comedian|director|rapper|songwriter|president|"
                         r"astronaut|player|wrestler|host|author|inventor|businessman|businesswoman|guitarist)\b")


def _history_picks(data, rnd):
    def clean(text):
        return re.sub(r"\s*\((?:pictured|shown|depicted)[^)]*\)", "", text).strip()
    years, events = set(), []
    for e in data.get("selected", []) + data.get("events", []):
        text = clean(e.get("text", ""))
        # One event per year: "selected" and "events" often tell the same story twice
        if e.get("year") and 30 <= len(text) <= 150 and e["year"] not in years:
            years.add(e["year"])
            events.append((int(e["year"]), text))
    # Lean towards the TV years, but keep one from anywhere
    tv_era = [e for e in events if 1950 <= e[0] <= 2012]
    rnd.shuffle(tv_era)
    rest = [e for e in events if e not in tv_era[:2]]
    rnd.shuffle(rest)
    chosen = sorted(tv_era[:2] + rest[:3 - len(tv_era[:2])])

    births = []
    for b in data.get("births", []):
        text = b.get("text", "")
        if b.get("year") and 1900 <= int(b["year"]) <= 1995 and ", " in text and BIRTH_WORDS.search(text):
            name, what = text.split(", ", 1)
            # A page photo is a rough sign of someone people have heard of
            pictured = any(p.get("thumbnail") for p in b.get("pages", [])[:1])
            births.append((int(b["year"]), name, what, ("American" in what) + pictured))
    rnd.shuffle(births)
    births.sort(key=lambda b: -b[3])
    born = sorted(births[:4])
    return chosen, born


def today_in_history(now):
    data = fetch_json(f"history-{now:%m%d}", "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/"
                      f"{now:%m}/{now:%d}", max_age_minutes=24 * 60, stale_hours=20)
    if not data:
        return None
    events, born = _history_picks(data, random.Random())
    if not events:
        return None
    page = 24.0
    d = page * (2 if born else 1)
    s = Script()
    date = now.strftime("%B %-d")
    pages = [(0, page, f"Today in History · {date}")]
    if born:
        pages.append((page, d, f"Born on {date}"))
    info_header(s, d, "GL0RYUS NETWORK", pages, now, band="&H0018283C&")
    fade = (300, 300)
    for i, (year, text) in enumerate(events):
        y = 238 + i * 108
        s.text(0.3 + i * 0.3, page, 80, y, str(year), ARCHIVO, 48, MUSTARD, align=4, shadow=3, fade=fade, layer=3)
        lines = wrap(text, BARLOW_S, 32, 690, max_lines=3)
        s.raw(0.3 + i * 0.3, page, f"\\an4\\pos(250,{y})\\fn{BARLOW_S}\\fs32\\c{CREAM}\\bord0\\shad0\\fad(300,300)",
              "\\N".join(esc(l) for l in lines), layer=3)
    for i, (year, name, what, _) in enumerate(born):
        y = 232 + i * 78
        a = page + 0.3 + i * 0.3
        s.text(a, d, 80, y, str(year), ARCHIVO, 40, MUSTARD, align=4, shadow=3, fade=fade, layer=3)
        name = clip_text(name, 34)
        s.text(a, d, 250, y - 14, name, BARLOW_X, fit(name, BARLOW_X, 40, 690), CREAM, align=4, fade=fade, layer=3)
        s.text(a, d, 252, y + 20, clip_text(what, 60), BARLOW_S, 28, SKY, align=4, fade=fade, layer=3)
    return Card(lavfi_url(s.write("history"), d, music("history_bed.ogg"), loop=True), d, "history")


# --- Launch Watch ---

def _short_place(location):
    """"Jiuquan Satellite Launch Center, People's Republic of China" -> "Jiuquan, China"."""
    parts = [p.strip() for p in location.split(",")]
    site = re.sub(r"\s+(Space Force Station|Space Centre|Space Center|Satellite Launch Center|Cosmodrome|SFB|SFS|"
                  r"Launch Complex \d+)\b.*$", "", parts[0])
    if len(parts) >= 3 and parts[-1] == "USA":
        return f"{site}, {parts[1]}"
    country = parts[-1].replace("People's Republic of China", "China").replace("Republic of Kazakhstan", "Kazakhstan")
    return site if len(parts) == 1 else f"{site}, {country}"


def _launch(raw):
    try:
        rocket, _, mission = raw["name"].partition(" | ")
        precision = raw.get("net_precision") or {}
        return {
            "rocket": rocket.strip(), "mission": mission.strip() or rocket.strip(),
            "provider": (raw.get("launch_service_provider") or {}).get("name", ""),
            "pad": (raw.get("pad") or {}).get("name", ""),
            "place": _short_place(raw["pad"]["location"]["name"]),
            "florida": raw["pad"]["location"]["id"] in FLORIDA_LOCATIONS,
            "net": datetime.datetime.fromisoformat(raw["net"].replace("Z", "+00:00")).astimezone(),
            "precision": precision.get("abbrev", "") if isinstance(precision, dict) else str(precision),
            "status": (raw.get("status") or {}).get("abbrev", "TBD"),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _when(launch, short=False):
    net, p = launch["net"], launch["precision"]
    if p in ("M",):
        return net.strftime("%b %Y").upper()
    if p.startswith("Q"):
        return f"{p} {net:%Y}"
    if p in ("Y",):
        return net.strftime("%Y")
    if p in ("D", "W"):
        return net.strftime("%b %-d").upper()
    return net.strftime("%b %-d").upper() if short else f"{net:%a %b %-d}".upper() + f" · {clock(net)}"


def launch_watch(now):
    base = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?mode=normal"
    world = fetch_json("launches-world", f"{base}&limit=10", max_age_minutes=180)
    florida = fetch_json("launches-florida", f"{base}&limit=4&ordering=net&location__ids=12,27", max_age_minutes=180)
    aware = now.astimezone()
    launches = [l for l in (_launch(r) for r in (world or {}).get("results", [])) if l]
    launches = [l for l in launches if l["net"] > aware - datetime.timedelta(minutes=30)]
    if not launches:
        return None
    fl_next = next((l for l in (_launch(r) for r in (florida or {}).get("results", []))
                    if l and l["net"] > aware - datetime.timedelta(minutes=30)), None)
    featured, rest = launches[0], launches[1:4]
    if fl_next and not featured["florida"] and not any(l["florida"] for l in rest):
        rest = rest[:2] + [fl_next]

    d = 32.0
    s = Script()
    starfield(s, d, seed=int(now.timestamp()) // 3600, top=168)
    info_header(s, d, "GL0RYUS NETWORK", [(0, d, "Launch Watch")], now, band="&H00301408&")
    fade = (300, 400)
    s.text(0.2, d, 80, 204, "NEXT LAUNCH", BARLOW_X, 28, MUSTARD, align=4, spacing=6, fade=fade, layer=3)
    mission = clip_text(featured["mission"], 34)
    s.text(0.3, d, 80, 254, mission, BARLOW_X, fit(mission, BARLOW_X, 56, 560), WHITE, align=4, shadow=3,
           shadow_color=BLACK, fade=fade, layer=3)
    s.text(0.5, d, 80, 306, clip_text(f"{featured['rocket']} · {featured['provider']}", 40), BARLOW_B, 34,
           MUSTARD, align=4, fade=fade, layer=3)
    s.text(0.6, d, 80, 346, clip_text(f"{featured['pad']} · {featured['place']}", 50), BARLOW_S, 30, SKY,
           align=4, fade=fade, layer=3)

    status = featured["status"].upper()
    chip = {"GO": GREEN, "TBC": "&H0030B0E0&", "SUCCESS": GREEN}.get(status, "&H00706868&")
    chip_w = int(text_width(status, BARLOW_X, 30)) + 36
    s.shape(0.4, d, 944 - chip_w, 188, rect(chip_w, 40), chip, fade=fade, layer=3)
    s.text(0.4, d, 944 - chip_w / 2, 209, status, BARLOW_X, 30, WHITE, fade=fade, layer=4)
    precise = featured["precision"] in ("", "SEC", "MIN", "HR", "S", "Second", "Minute", "Hour")
    if precise and featured["net"] > aware:
        s.text(0.5, d, 944, 262, "T-MINUS", BARLOW_X, 28, SKY, align=6, spacing=4, fade=fade, layer=3)
        for sec in range(int(d) + 1):
            left = int((featured["net"] - aware).total_seconds()) - sec
            if left < 0:
                break
            days, rem = divmod(left, 86400)
            text = (f"{days}D " if days else "") + f"{rem // 3600:02d}:{rem // 60 % 60:02d}:{rem % 60:02d}"
            s.text(sec, min(d, sec + 1), 944, 310, text, ARCHIVO, 46, YELLOW, align=6, shadow=3,
                   shadow_color=BLACK, fade=None, layer=3)
        s.text(0.6, d, 944, 352, _when(featured), BARLOW_B, 30, CREAM, align=6, fade=fade, layer=3)
    else:
        s.text(0.5, d, 944, 262, "NO EARLIER THAN", BARLOW_X, 28, SKY, align=6, spacing=4, fade=fade, layer=3)
        s.text(0.5, d, 944, 312, _when(featured), ARCHIVO, 44, YELLOW, align=6, shadow=3, shadow_color=BLACK,
               fade=fade, layer=3)

    s.shape(0.8, d, 80, 384, rect(864, 3), MUSTARD, fade=fade, layer=3)
    s.text(0.9, d, 80, 414, "ALSO COMING UP", BARLOW_X, 28, MUSTARD, align=4, spacing=6, fade=fade, layer=3)
    for i, l in enumerate(rest):
        y = 452 + i * 38
        a = 1.0 + i * 0.2
        color = MUSTARD if l["florida"] else SKY
        s.text(a, d, 80, y, _when(l, short=True), BARLOW_X, 30, YELLOW, align=4, fade=fade, layer=3)
        what = clip_text(f"{l['rocket']} · {l['mission']}", 46)
        s.text(a, d, 230, y, what, BARLOW_B, fit(what, BARLOW_B, 30, 470), WHITE, align=4, fade=fade, layer=3)
        place = ("★ " if l["florida"] else "") + clip_text(l["place"], 24)
        s.text(a, d, 944, y, place, BARLOW_S, fit(place, BARLOW_S, 28, 250), color, align=6, fade=fade, layer=3)
    return Card(lavfi_url(s.write("launch"), d, music("space_bed.ogg"), background=SPACE_BACKGROUND), d, "launch")


# --- Markets ---

MARKET_BACKGROUND = "0x08140c"
UP, DOWN = "&H005AE66E&", "&H006060FF&"  # bright enough to read as text on the dark background
COINGECKO = "https://api.coingecko.com/api/v3/coins/{}/market_chart?vs_currency=usd&days=1"
SPX_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=5m"
CHART_W, CHART_H = 200, 66


def _crypto(coin):
    """(price, change % over 24 h, the 24 h of prices) for a CoinGecko coin id, or None."""
    data = fetch_json(f"markets-{coin}", COINGECKO.format(coin), max_age_minutes=10, stale_hours=6)
    prices = [p for _, p in (data or {}).get("prices", []) if p]
    if len(prices) < 2:
        return None
    return prices[-1], 100 * (prices[-1] / prices[0] - 1), prices, None


def _spx():
    """(level, change % since the previous close, today's levels from that close, quote time) or None.
    Out of market hours this is the last session's, which the footer says."""
    data = fetch_json("markets-spx", SPX_CHART, max_age_minutes=10, stale_hours=96)
    try:
        r = data["chart"]["result"][0]
        price, prev = r["meta"]["regularMarketPrice"], r["meta"]["chartPreviousClose"]
        closes = [c for c in r["indicators"]["quote"][0]["close"] if c]
        when = datetime.datetime.fromtimestamp(r["meta"]["regularMarketTime"])
        return price, 100 * (price / prev - 1), [prev] + closes + [price], when
    except (TypeError, KeyError, IndexError, ZeroDivisionError):
        return None


def _chart_paths(history):
    """ASS drawings for a small area chart of `history`: the filled area and a 3px line on top. Both
    start at 0,0 (the highest point is y=0, the baseline y=CHART_H) so libass places them by that box."""
    step = max(1, len(history) // 60)
    pts = history[::step] + ([history[-1]] if (len(history) - 1) % step else [])
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    xy = [(CHART_W * i / (len(pts) - 1), (CHART_H - 3) * (hi - p) / span) for i, p in enumerate(pts)]
    line = " ".join(f"{x:.0f} {y:.0f}" for x, y in xy)
    area = f"m 0 {CHART_H} l {line} {CHART_W} {CHART_H}"
    ribbon = (f"m {xy[0][0]:.0f} {xy[0][1]:.0f} l {line} "
              + " ".join(f"{x:.0f} {y + 3:.0f}" for x, y in reversed(xy)))
    return area, ribbon


def markets(now):
    """BTC, ETH and the S&P 500: price, change and a chart of the last 24 hours (the S&P's session so far,
    from the previous close). None when no quote is available at all."""
    # Whole dollars and points: cents are noise at TV resolution and only shrink the numbers
    rows = [(symbol, name, quote, fmt) for symbol, name, quote, fmt in (
        ("BTC", "Bitcoin", _crypto("bitcoin"), "${:,.0f}"),
        ("ETH", "Ethereum", _crypto("ethereum"), "${:,.0f}"),
        ("S&P 500", None, _spx(), "{:,.0f}"),
    ) if quote]
    if not rows:
        return None
    d = 24.0
    s = Script()
    info_header(s, d, "GL0RYUS NETWORK", [(0, d, "Markets")], now, band="&H00142410&")
    fade = (300, 400)
    s.text(0.2, d, 320, 206, "LAST 24 HOURS", BARLOW_X, 30, MUSTARD, align=4, spacing=4, fade=fade, layer=3)
    s.text(0.2, d, 944, 206, "CHANGE", BARLOW_X, 30, MUSTARD, align=6, spacing=4, fade=fade, layer=3)
    for i, (symbol, name, (price, change, history, when), fmt) in enumerate(rows):
        y, a = 268 + i * 104, 0.3 + i * 0.25
        color = UP if change >= 0 else DOWN
        s.shape(a, d, 80, y + 50, rect(864, 2), "&H00304030&", fade=fade, layer=1)
        s.text(a, d, 80, y - 16, symbol, ARCHIVO, fit(symbol, ARCHIVO, 48, 220), WHITE, align=4, shadow=3,
               shadow_color=BLACK, fade=fade, layer=3)
        # The S&P's quote time stands in for its name: out of market hours it is the last session's
        if name:
            sub = name.upper()
        else:
            sub = (when.strftime("AS OF %-I:%M %p") if when.date() == now.date()
                   else when.strftime("%a %-I:%M %p").upper())
        s.text(a, d, 80, y + 28, sub, BARLOW_X, fit(sub, BARLOW_X, 30, 230, spacing=2), SKY, align=4, spacing=2,
               fade=fade, layer=3)
        area, ribbon = _chart_paths(history)
        s.shape(a, d, 320, y - CHART_H / 2, area, color, fade=fade, layer=2, extra="\\1a&HA0&")
        s.shape(a, d, 320, y - CHART_H / 2, ribbon, color, fade=fade, layer=3)
        text = fmt.format(price)
        s.text(a, d, 760, y, text, BARLOW_X, fit(text, BARLOW_X, 64, 220), YELLOW, align=6, shadow=3,
               shadow_color=BLACK, fade=fade, layer=3)
        pct = f"{abs(change):.2f}%"
        size = fit(pct, BARLOW_X, 50, 140)
        arrow = "m 11 0 l 22 18 l 0 18" if change >= 0 else "m 0 0 l 22 0 l 11 18"
        s.shape(a, d, 806, y - 9, arrow, color, fade=fade, layer=3)
        s.text(a, d, 944, y, pct, BARLOW_X, size, color, align=6, shadow=2, shadow_color=BLACK, fade=fade, layer=3)
    sources = "CoinGecko"
    if any(sym == "S&P 500" for sym, *_ in rows):
        sources = "S&P 500 change vs. prior close · CoinGecko · Yahoo Finance"
    s.text(0.8, d, 512, 566, sources.upper(), BARLOW_B, 28, "&H00B0B8B0&", spacing=1, fade=fade, layer=3)
    return Card(lavfi_url(s.write("markets"), d, music("weather_bed.ogg"), background=MARKET_BACKGROUND, loop=True),
                d, "markets")


# --- Who's in Space ---

MAP_X, MAP_Y, MAP_SCALE = 152, 182, 2.0  # 360 x 180 degrees -> 720 x 360 px


def _map_xy(lon, lat):
    return MAP_X + (lon + 180) * MAP_SCALE, MAP_Y + (90 - lat) * MAP_SCALE


def _iss_positions(at):
    """Current ISS position plus its ground track from 30 minutes ago to an hour ahead."""
    stamps = [int(at.timestamp()) + k * 300 for k in range(-6, 13)]  # 19 points, 5 minutes apart
    stamps = [int(at.timestamp())] + [t for t in stamps if t != int(at.timestamp())]
    points = []
    for chunk in (stamps[:10], stamps[10:]):
        url = ("https://api.wheretheiss.at/v1/satellites/25544/positions?units=miles&timestamps="
               + ",".join(str(t) for t in chunk))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                points += json.load(r)
        except Exception as e:
            logger.warning(f"ISS position fetch failed: {e}")
            return None, []
        time.sleep(1.1)  # the API allows about one request a second
    current = points[0]
    track = sorted(points[1:] + [current], key=lambda p: p["timestamp"])
    return current, track


def _night_path(solar_lat, solar_lon):
    """The night side of the earth, as a polygon in map pixels."""
    import math
    dec = math.radians(solar_lat if abs(solar_lat) > 0.5 else 0.5)
    sun_lon = (solar_lon + 180) % 360 - 180
    pts = []
    for lon in range(-180, 181, 4):
        lat = math.degrees(math.atan(-math.cos(math.radians(lon - sun_lon)) / math.tan(dec)))
        pts.append(_map_xy(lon, lat))
    pole = -90 if solar_lat > 0 else 90
    pts += [_map_xy(180, pole), _map_xy(-180, pole)]
    return "m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in pts)


def whos_in_space(now):
    crew = fetch_json("astronauts", "https://ll.thespacedevs.com/2.3.0/astronauts/?in_space=true&limit=40"
                      "&mode=normal", max_age_minutes=360, stale_hours=72)
    people = [a for a in (crew or {}).get("results", []) if (a.get("type") or {}).get("name") != "Non-Human"]
    starman = any(a.get("name") == "Starman" for a in (crew or {}).get("results", []))
    if not people:
        return None
    current, track = _iss_positions(now)
    page = 24.0
    d = page * (2 if current else 1)
    s = Script()
    starfield(s, d, seed=int(now.timestamp()) // 3600 + 7, top=168)
    pages = [(0, page, "Who's in Space")] + ([(page, d, "Where's the ISS?")] if current else [])
    info_header(s, d, "GL0RYUS NETWORK", pages, now, band="&H00301408&")
    fade = (300, 300)

    # Page 1: the crews. Chinese astronauts are on Tiangong; everyone else is on the ISS
    stations = {"INTERNATIONAL SPACE STATION": [], "TIANGONG": []}
    for a in people:
        agency = (a.get("agency") or {}).get("abbrev", "")
        stations["TIANGONG" if agency == "CNSA" else "INTERNATIONAL SPACE STATION"].append((a["name"], agency))
    count = len(people)
    headline = f"{count} {'PERSON IS' if count == 1 else 'PEOPLE ARE'} IN SPACE RIGHT NOW"
    s.text(0.2, page, 512, 210, headline, ARCHIVO, fit(headline, ARCHIVO, 42, 880), CREAM, shadow=4, fade=fade, layer=3)
    if starman:
        s.text(1.5, page, 512, 246, "(plus Starman, still driving his Tesla Roadster around the sun since 2018)",
               BARLOW_S, 24, SKY, fade=fade, layer=3)
    # One column per station: name on the left, agency right-aligned at the column's edge
    columns = [(80, 520, "INTERNATIONAL SPACE STATION", stations["INTERNATIONAL SPACE STATION"]),
               (600, 944, "TIANGONG", stations["TIANGONG"])]
    for left, right, title, names in columns:
        if not names:
            continue
        s.text(0.4, page, left, 288, title, BARLOW_X, 28, MUSTARD, align=4, spacing=3, fade=fade, layer=3)
        s.shape(0.4, page, left, 306, rect(right - left, 2), MUSTARD, fade=fade, layer=3)
        for i, (name, agency) in enumerate(names[:7]):
            y = 334 + i * 32
            name = clip_text(name, 26)
            s.text(0.5 + i * 0.1, page, left, y, name, BARLOW_B, fit(name, BARLOW_B, 30, right - left - 80), WHITE,
                   align=4, fade=fade, layer=3)
            s.text(0.5 + i * 0.1, page, right, y + 1, agency, BARLOW_S, 24, SKY, align=6, fade=fade, layer=3)

    # Page 2: the ISS over a world map, with the night side shaded and its ground track
    if current:
        a, b = page, d
        s.shape(a, b, MAP_X, MAP_Y, rect(720, 360), "&H00502810&", fade=fade, layer=2)
        for lon in range(-150, 180, 30):
            x, _ = _map_xy(lon, 0)
            s.shape(a, b, x, MAP_Y, rect(1, 360), "&H00805838&", fade=fade, layer=3)
        for lat in (-60, -30, 0, 30, 60):
            _, y = _map_xy(0, lat)
            s.shape(a, b, MAP_X, y, rect(720, 1), "&H00805838&", fade=fade, layer=3)
        try:
            with open(WORLD_MAP) as f:
                rings = json.load(f)
            land = []
            for ring in rings:
                pts = [_map_xy(ring[i], ring[i + 1]) for i in range(0, len(ring), 2)]
                land.append("m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in pts))
            s.shape(a, b, 0, 0, " ".join(land), "&H0050A048&", fade=fade, layer=4)
        except (OSError, ValueError) as e:
            logger.warning(f"World map unavailable: {e}")
        s.shape(a, b, 0, 0, _night_path(current["solar_lat"], current["solar_lon"]), "&H00000000&", fade=fade,
                layer=5, extra="\\1a&H70&")
        for p, q in zip(track, track[1:]):
            if abs(p["longitude"] - q["longitude"]) > 180:
                continue  # crosses the date line
            x1, y1 = _map_xy(p["longitude"], p["latitude"])
            x2, y2 = _map_xy(q["longitude"], q["latitude"])
            future = p["timestamp"] >= current["timestamp"]
            dx, dy = x2 - x1, y2 - y1
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ox, oy = -dy / length * 1.5, dx / length * 1.5
            path = (f"m {x1 + ox:.1f} {y1 + oy:.1f} l {x2 + ox:.1f} {y2 + oy:.1f} "
                    f"l {x2 - ox:.1f} {y2 - oy:.1f} l {x1 - ox:.1f} {y1 - oy:.1f}")
            s.shape(a, b, 0, 0, path, YELLOW if future else SKY, fade=fade, layer=6,
                    extra="" if future else "\\1a&H60&")
        x, y = _map_xy(current["longitude"], current["latitude"])
        pulse = "".join(f"\\t({t},{t + 600},\\fscx180\\fscy180\\1a&HFF&)\\t({t + 600},{t + 601},\\fscx100\\fscy100\\1a&H40&)"
                        for t in range(0, int(page * 1000), 1200))
        s.shape(a, b, x, y, circle(9), WHITE, fade=fade, layer=7, align=5, extra="\\1a&H40&" + pulse)
        s.shape(a, b, x - 6, y - 6, circle(6), WHITE, fade=fade, layer=8)
        lx = x + 14 if x < MAP_X + 640 else x - 14
        s.text(a, b, lx, y - 16, "ISS", BARLOW_X, 28, WHITE, align=4 if x < MAP_X + 640 else 6, shadow=2,
               shadow_color=BLACK, fade=fade, layer=8)
        lat, lon = current["latitude"], current["longitude"]
        caption = (f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}  {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"
                   f"   ·   {current['velocity']:,.0f} MPH   ·   {current['altitude']:,.0f} MILES UP")
        s.shape(a, b, MAP_X, MAP_Y + 360 - 40, rect(720, 40), "&H00000000&", fade=fade, layer=8, extra="\\1a&H50&")
        s.text(a, b, 512, MAP_Y + 360 - 20, caption, BARLOW_B, 28, WHITE, fade=fade, layer=9)
    return Card(lavfi_url(s.write("space"), d, music("space_bed.ogg"), background=SPACE_BACKGROUND, loop=True), d,
                "space")
