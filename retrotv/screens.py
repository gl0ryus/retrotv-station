"""
More break-time screens for the GL0RYUS Network: Pop Quiz, Today in History, Launch Watch,
Who's in Space and Markets. Same approach as retrotv.cards (ASS over an mpv lavfi URL); the data comes
from free public APIs and is cached on the SD card so a flaky connection only means a stale screen.
"""
import datetime
import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse

from retrotv.jsonio import USER_AGENT, get_json, write_json  # USER_AGENT: other modules still import it from here
from retrotv import brand, cards
from retrotv.brand import Part
from retrotv.cards import (
    CREAM, H, SKY, W, WHITE, CHANNEL, WMO,
    Script, circle, clip_text, clock, info_header, look, music, rect, weather_as_of,
)
from retrotv.onthisday import ONTHISDAY_FILE
from retrotv.paths import ASSETS_DIR, DATA_DIR, HOME_DIR

logger = logging.getLogger("RetroTV")

WORLD_MAP = os.path.join(ASSETS_DIR, "world_110m.json")  # Natural Earth 110m land, simplified
FLORIDA_LOCATIONS = (12, 27)  # Cape Canaveral SFS, Kennedy Space Center
SKY_FILE = os.path.join(DATA_DIR, "sky.json")   # tonight's sky over the house, worked out by retrotv/news.py
POPUPS_FILE = os.path.join(DATA_DIR, "popups.json")   # Pop-Up Video facts by meta.popup_key, from retrotv/news.py
ANNIVERSARY_FILE = os.path.join(DATA_DIR, "anniversary.json")   # each day's anniversary episode, from retrotv/news.py
ART_FILE = os.path.join(DATA_DIR, "art.json")   # the day's Art Break picture, drawn by Claude (retrotv/news.py)
ART_W, ART_H = 800, 356                         # its canvas, between the info header and the caption


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
        data = get_json(url, timeout=10)
        write_json(path, {"fetched": time.time(), "data": data})
        return data
    except Exception as e:
        logger.warning(f"Fetch failed for {name}: {e}")
    if cached and time.time() - cached.get("fetched", 0) < stale_hours * 3600:
        return cached["data"]
    return None


def starfield(s, d, seed, top=0, count=80, right=W):
    rnd = random.Random(seed)
    for _ in range(count):
        x, y = rnd.randrange(16, right - 16), rnd.randrange(top + 8, H - 8)
        r = rnd.choice((1.2, 1.6, 2.2))
        twinkle = ""
        if rnd.random() < 0.25:
            period = rnd.randint(900, 1800)
            twinkle = "".join(f"\\t({t},{t + period // 2},\\1a&HB0&)\\t({t + period // 2},{t + period},\\1a&H00&)"
                              for t in range(0, int(d * 1000), period))
        s.shape(0, d, x - r, y - r, circle(r), rnd.choice((WHITE, SKY, CREAM)), fade=(300, 400), layer=1,
                extra=twinkle)


def _ground(s, d, season):
    """The info screens' ground: the ink and dotted purple panels, and the season's props."""
    brand.split(s, d)
    look(season).dress(s, 0, d, layer=2)


def _rows(s, d, rows, tint, top=128, height=86, gap=16, x=62, width=900, start=0.3, step=0.3, lines=1):
    """Rows on leaning ink slabs with a hard shadow in the accent, like the guide's title bars: [(left, title, sub,
    right)]. left goes on a yellow box at the start (a year), title in Anton with sub under it (lines: how many
    lines sub may wrap to), right in yellow at the end. Returns where the last row ends."""
    y = top
    for i, (left, title, sub, right) in enumerate(rows):
        a = start + i * step
        brand.slab(s, a, d, x, y, width, height, brand.INK, shadow=(6, 6, tint), layer=4, slide=None)
        tx = x + 24
        if left:
            box = 118
            brand.slab(s, a, d, x, y, box, height, brand.YELLOW, layer=5)
            brand.words(s, a, d, x + box / 2, y + height / 2, left, brand.ANTON, 40, brand.INK, align="center",
                        layer=6)
            tx = x + box + 22
        room = x + width - tx - 24 - (brand.measure(right, brand.ANTON, 30) + 24 if right else 0)
        if title and sub:
            sub_lines = brand.lines(sub, brand.ARCHIVO, 20, room)
            if len(sub_lines) > lines:
                sub_lines = sub_lines[:lines - 1] + [brand.clip(" ".join(sub_lines[lines - 1:]), brand.ARCHIVO, 20, room)]
            block = 34 + 26 * len(sub_lines)
            ty = y + (height - block) / 2 + 15
            brand.words(s, a, d, tx, ty, brand.clip(title.upper(), brand.ANTON, 32, room), brand.ANTON, 32, brand.WHITE,
                        layer=6)
            for k, line in enumerate(sub_lines):
                brand.words(s, a, d, tx + 1, ty + 34 + k * 26, line.upper(), brand.ARCHIVO, 20, brand.SILVER, bold=True,
                            layer=6)
        elif sub:   # running text alone, up to `lines` lines
            text = brand.lines(sub, brand.ARCHIVO, 23, room)
            if len(text) > lines:
                text = text[:lines - 1] + [brand.clip(" ".join(text[lines - 1:]), brand.ARCHIVO, 23, room)]
            ty = y + height / 2 - (len(text) - 1) * 30 / 2
            for k, line in enumerate(text):
                brand.words(s, a, d, tx, ty + k * 30, line, brand.ARCHIVO, 23, brand.WHITE, bold=True, layer=6)
        elif title:
            brand.words(s, a, d, tx, y + height / 2, brand.clip(title.upper(), brand.ANTON, 32, room), brand.ANTON, 32,
                        brand.WHITE, layer=6)
        if right:
            brand.words(s, a, d, x + width - 24, y + height / 2, right, brand.ANTON, 30, brand.YELLOW, align="right",
                        layer=6)
        y += height + gap
    return y


def _next_strip(s, d, text, tint=brand.ORANGE, at=1.5):
    """What follows the card, along the bottom: NEXT on yellow, then what it is."""
    brand.strip(s, at, d, 62, 532, 42, [Part("NEXT", brand.ANTON, 28, brand.INK, brand.YELLOW, pad=16),
                                        Part(brand.clip(text.upper(), brand.ARCHIVO_BLACK, 20, 760), brand.ARCHIVO_BLACK,
                                             20, brand.WHITE, brand.INK, pad=18)],
                shadow=(5, 5, tint), slide=(int(at * 1000), int(at * 1000) + 400, 90), layer=8)


CHECK = "m 0 22 l 10 12 l 24 26 l 52 0 l 62 10 l 24 46"



# --- Holidays and heritage months ---

# What fits a holiday or a heritage month (retrotv.station's HOLIDAYS, HOLIDAY_IDS and MONTHS): The Trivia API
# tags to fetch questions from, the words a quiz question or a Wikipedia "on this day" entry has to mention
# (tagged questions too, since a tag can be broader), and for births, what to look for in the person's page
# summary when the words alone wouldn't find them. Few questions fit (a dozen or two a year per subject), so
# Pop Quiz takes at most QUIZ_TOPIC_PER_DAY of them; Wikipedia has new entries for every date.
BLACK_HISTORY = (r"african[- ]american|civil rights|segregat|slavery|enslaved|abolition|underground railroad|naacp|"
                 r"emancipation|juneteenth|jim crow|harlem renaissance|tuskegee|negro leagues?|freedom riders?|"
                 r"apartheid|mandela|martin luther king|rosa parks|harriet tubman|frederick douglass|malcolm x|"
                 r"jackie robinson|thurgood marshall|sojourner truth|booker t\.|du bois|"
                 r"black (?:american|panther|history|power)")
SERVICE = (r"veterans?\b|memorial day|armistice|world war|medal of honor|arlington national cemetery|"
             r"purple heart|\bu\.?s\.? (?:army|navy|marines?|air force)")
TOPICS = {
    "blackhistory": dict(tags=("civil_rights",), words=BLACK_HISTORY),
    "mlkday": dict(tags=("civil_rights",), words=BLACK_HISTORY),
    "juneteenth": dict(tags=("civil_rights",), words=BLACK_HISTORY),
    "womenshistory": dict(tags=("women", "feminism"),
                          words=r"women'?s (?:rights|suffrage|liberation|march|history)|suffrag|feminis|"
                                r"first (?:woman|female)|\bfemale\b|empress|nineteenth amendment|19th amendment|"
                                r"equal rights amendment|title ix",
                          people=r"(?:^|\. )She\b"),
    "hispanic": dict(tags=("mexico",),
                     words=r"hispanic|latin[oa]s?\b|latin america|mexic|puerto ric|\bcuba\b|cuban[- ]american|"
                           r"dominican republic|"
                           r"colombia|venezuel|argentin|\bchile|\bperu\b|chican|el salvador|guatemal|honduras|"
                           r"nicaragua|costa rica|panama|ecuador|bolivia|paraguay|uruguay|cesar chavez"),
    "cincodemayo": dict(tags=("mexico",), words=r"mexic|puebla"),
    "nativeamerican": dict(tags=("native_americans", "indigenous_people"),
                           words=r"native american|first nations|indigenous|american indian|\btribes?\b|\btribal\b|"
                                 r"cherokee|navajo|lakota|sioux|apache|comanche|iroquois|ojibwe|chippewa|\bhopi\b|"
                                 r"pueblo|shoshone|seminole|mohawk|cheyenne|crazy horse|sitting bull|geronimo|"
                                 r"sacagawea|pocahontas|wounded knee|little bighorn|trail of tears"),
    "pride": dict(tags=("lgbtq",), words=r"\bgay\b|lesbian|\blgbt|transgender|bisexual|same-sex|stonewall|"
                                         r"harvey milk|pride (?:march|parade)"),
    "aapi": dict(tags=(), words=r"asian[- ]american|japanese[- ]american|chinese[- ]american|korean[- ]american|"
                                r"filipino|vietnamese[- ]american|indian[- ]american|pacific islander|hawai|samoa|"
                                r"\bguam\b|chinese exclusion|internment"),
    "christmas": dict(tags=("christmas",), words=r"christmas|santa claus|reindeer|\bxmas\b"),
    "halloween": dict(tags=("halloween",), words=r"hallowe'?en|vampire|dracula|frankenstein|haunted|witch(?:es)?\b|"
                                                 r"zombie|ghost|horror"),
    "thanksgiving": dict(tags=(), words=r"thanksgiving|pilgrims?\b|mayflower|plymouth colony"),
    "valentines": dict(tags=(), words=r"valentine"),
    "stpatricks": dict(tags=(), words=r"st\.? patrick|\bireland\b|\birish\b"),
    "easter": dict(tags=(), words=r"\beaster\b"),
    "july4": dict(tags=(), words=r"declaration of independence|independence day|founding fathers?|"
                                 r"american revolution|continental congress|\b1776\b"),
    "newyearsday": dict(tags=(), words=r"new year"),
    "mardigras": dict(tags=("mardi_gras",), words=r"mardi gras|new orleans|carnival"),
    "memorialday": dict(tags=(), words=SERVICE),
    "veteransday": dict(tags=(), words=SERVICE),
    "laborday": dict(tags=(), words=r"labor (?:union|day|movement)|trade union|\bstrikes?\b|workers"),
    "groundhog": dict(tags=(), words=r"groundhog|punxsutawney"),
    "aprilfools": dict(tags=(), words=r"april fool|\bhoax|prank"),
}
TRIVIA_API = "https://the-trivia-api.com/v2/questions"   # CC BY-NC 4.0: free for a hobby station, with a credit


def topic_of(season, month):
    """The subject Pop Quiz and Today in History lean toward: the holiday's, or else the month's."""
    return season if season in TOPICS else month if month in TOPICS else None


def _fits(topic, *texts):
    return bool(re.search(TOPICS[topic]["words"], " ".join(t for t in texts if t), re.I))


def _trivia_api(tag):
    """Questions with a tag from The Trivia API, in the Open Trivia DB's shape. Cached for a week."""
    data = fetch_json(f"trivia-api-{tag}", f"{TRIVIA_API}?limit=50&tags={tag}", max_age_minutes=7 * 24 * 60,
                      stale_hours=60 * 24)
    out = []
    for q in data if isinstance(data, list) else []:
        try:
            out.append({"question": q["question"]["text"], "correct_answer": q["correctAnswer"],
                        "incorrect_answers": list(q["incorrectAnswers"][:3]), "category": q.get("category", ""),
                        "key": f"tta-{q['id']}", "source": "The Trivia API"})
        except (KeyError, TypeError):
            continue
    return out


# --- Pop Quiz ---

QUIZ_TOPIC_PER_DAY = 1   # questions a day on the holiday's or heritage month's subject, while unused ones last

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


def _opentdb(url):
    """One Open Trivia DB call, paced for its limit of one request per 5 seconds."""
    for attempt in range(5):
        time.sleep(5.5 + 10 * attempt)
        try:
            data = get_json(url, timeout=20)
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
    write_json(QUIZ_BANK, {"built": time.time(), "questions": [bank[k] for k in keys]})
    logger.info(f"Quiz: question bank rebuilt with {len(keys)} questions")


def _refresh_bank():
    try:
        build_quiz_bank()
    except Exception as e:
        logger.warning(f"Quiz bank download failed: {e}")


def _topic_question(topic, bank, state):
    """A question on the topic not yet asked this year (from its Trivia API tags and the bank), recorded in
    state, or None when today's QUIZ_TOPIC_PER_DAY are done or none are left."""
    today = datetime.date.today()
    day, count = state.get("topic_day", {}).get(topic, ("", 0))
    if day == today.isoformat() and count >= QUIZ_TOPIC_PER_DAY:
        return None
    asked = state.get("topics_asked", {})
    asked = {k: v for k, v in asked.items() if k.endswith(f"-{today.year}")}   # the topics come round once a year
    used = set(asked.get(f"{topic}-{today.year}", []))
    pool = {q["key"]: q for tag in TOPICS[topic]["tags"] for q in _trivia_api(tag)}
    pool.update(bank)
    fits = [q for k, q in pool.items() if k not in used and _fits(topic, q["question"], q["correct_answer"])]
    if not fits:
        return None
    q = random.choice(fits)
    asked[f"{topic}-{today.year}"] = sorted(used | {q["key"]})
    state["topics_asked"] = asked
    state.setdefault("topic_day", {})[topic] = (today.isoformat(), count + 1 if day == today.isoformat() else 1)
    logger.info(f"Quiz: a {topic} question ({len(fits) - 1} more left this year)")
    return dict(q, topic=topic)


def _next_question(topic=None):
    """The next question off a shuffled deck of the whole bank, or now and then one on the topic (see
    TOPICS). Every question airs once before any repeats; when the deck runs out it's reshuffled and
    starts over."""
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
    try:
        with open(QUIZ_FILE) as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    q = _topic_question(topic, bank, state) if topic else None
    if not q:
        if not bank:
            return None
        deck = [k for k in state.get("deck", []) if k in bank]  # a rebuilt bank may have dropped some
        if not deck:
            deck = list(bank)
            random.shuffle(deck)
            logger.info(f"Quiz: dealing a fresh deck of {len(deck)} questions")
        q = bank[deck.pop()]
        state["deck"] = deck
    write_json(QUIZ_FILE, state)
    return q


def pop_quiz(season=None, month=None):
    """Game-show trivia: question, four answers, a 20-second countdown, then the reveal at 22 s. Now and then
    the question is on the holiday's or heritage month's subject, and the title says so."""
    q = _next_question(topic_of(season, month))
    if not q:
        return None
    d, start, reveal = 33.0, 2.0, 22.0   # quiz_bed.ogg is cut to these times
    answers = q["incorrect_answers"][:3] + [q["correct_answer"]]
    random.shuffle(answers)
    correct = answers.index(q["correct_answer"])
    if q.get("topic"):
        category = look(q["topic"]).title
    else:
        category = QUIZ_CATEGORIES.get(q.get("category_id"), q["category"].split(": ")[-1])
    tint = look(season).tint
    out = (300, 400)
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, f"Pop Quiz · {category}")], None, season=season, ticking=False)
    # The countdown dial, where the other screens have their date
    cx, cy = 900, 62
    s.shape(0, d, cx - 46, cy - 46, circle(46), brand.YELLOW, fade=out, layer=4)
    s.shape(0, d, cx - 38, cy - 38, circle(38), brand.INK, fade=out, layer=5)
    count = int(reveal - start)
    for i in range(count):
        brand.words(s, start + i, start + i + 1, cx, cy, str(count - i), brand.ANTON, 46, brand.WHITE, align="center",
                    fade=None, layer=6)
    brand.shape(s, reveal, d, cx - 21, cy - 16, CHECK, brand.TEAL, fade=(150, 400), layer=6,
                extra="\\fscx68\\fscy68")
    # The question, and the timer bar shrinking to the left under it
    lines, em = cards.wrapped(q["question"], brand.ARCHIVO, 30, 880, lines=3, least=0.8)
    for i, line in enumerate(lines):
        brand.words(s, 0, d, W / 2, 196 + (i - (len(lines) - 1) / 2) * em * 1.25, line, brand.ARCHIVO, em,
                    brand.WHITE, align="center", bold=True, fade=(400, 400), layer=6)
    s.shape(0, d, 72, 262, rect(880, 8), brand.PANEL, fade=out, layer=4)
    s.shape(start, reveal, 72, 262, rect(880, 8), brand.YELLOW, fade=None, layer=5,
            extra=f"\\t(0,{int((reveal - start) * 1000)},\\fscx0)")

    for i, answer in enumerate(answers):
        x = 66 if i % 2 == 0 else 536
        y = 296 if i < 2 else 404
        w, h = 424, 88
        t_in = 0.8 + i * 0.2
        brand.slab(s, t_in, d, x, y, w, h, brand.INK, shadow=(6, 6, tint), fade=out, layer=4)
        brand.slab(s, t_in, d, x, y, 64, h, brand.YELLOW, fade=out, layer=5)
        brand.words(s, t_in, d, x + 32, y + h / 2, "ABCD"[i], brand.ANTON, 40, brand.INK, align="center", fade=out,
                    layer=7)
        if i == correct:
            brand.slab(s, reveal, d, x + 64, y, w - 64, h, brand.TEAL, fade=(150, 400), layer=5)
        else:
            brand.slab(s, reveal, d, x, y, w, h, brand.INK, fade=(200, 400), layer=8)
        alines, aem = cards.wrapped(answer, brand.ARCHIVO_BLACK, 22, w - 64 - 40, lines=2, least=0.8)
        ink = brand.WHITE
        for k, line in enumerate(alines):
            ty = y + h / 2 + (k - (len(alines) - 1) / 2) * aem * 1.25
            brand.words(s, t_in, reveal, x + 84, ty, line, brand.ARCHIVO_BLACK, aem, ink, fade=(300, 0), layer=7)
            brand.words(s, reveal, d, x + 84, ty, line, brand.ARCHIVO_BLACK, aem,
                        brand.INK if i == correct else brand.GREY, fade=(0, 400), layer=9)
    if q.get("source"):   # the credit its licence asks for
        brand.footer(s, d, f"Question from {q['source']}", y=560)
    return s.card("quiz", d, music("quiz_bed.ogg"), background=brand.INK_BG, card_kind="quiz")


# --- Today in History ---

BIRTH_WORDS = re.compile(r"\b(actor|actress|singer|musician|comedian|director|rapper|songwriter|president|"
                         r"astronaut|player|wrestler|host|author|inventor|businessman|businesswoman|guitarist)\b")


def _summary(entry):
    """The first page's description and summary for an "on this day" entry: more to match a topic against."""
    page = (entry.get("pages") or [{}])[0]
    return f"{page.get('description', '')} {page.get('extract', '')}"


def _history_picks(data, rnd, topic=None):
    """(events, births, on topic): three events and up to four births, at most two of each that fit the topic
    (see TOPICS) when there are any, the rest picked as on any other day."""
    def clean(text):
        return re.sub(r"\s*\((?:pictured|shown|depicted)[^)]*\)", "", text).strip()
    years, events, fitting = set(), [], []
    for e in data.get("selected", []) + data.get("events", []):
        text = clean(e.get("text", ""))
        # One event per year: "selected" and "events" often tell the same story twice
        if e.get("year") and 30 <= len(text) <= 150 and e["year"] not in years:
            years.add(e["year"])
            events.append((int(e["year"]), text))
            if topic and _fits(topic, text, _summary(e)):
                fitting.append(events[-1])
    rnd.shuffle(fitting)
    picked = fitting[:2]
    # Otherwise lean towards the TV years, but keep one from anywhere
    tv_era = [e for e in events if 1950 <= e[0] <= 2012 and e not in picked]
    rnd.shuffle(tv_era)
    tv_era = tv_era[:max(0, 2 - len(picked))]
    rest = [e for e in events if e not in picked and e not in tv_era]
    rnd.shuffle(rest)
    chosen = sorted(picked + tv_era + rest[:3 - len(picked) - len(tv_era)])

    people = TOPICS.get(topic, {}).get("people")
    births, matches = [], []
    for b in data.get("births", []):
        text = b.get("text", "")
        if not (b.get("year") and ", " in text):
            continue
        year, (name, what) = int(b["year"]), text.split(", ", 1)
        # A page photo is a rough sign of someone people have heard of
        pictured = any(p.get("thumbnail") for p in b.get("pages", [])[:1])
        if topic and 1800 <= year <= 2000 and (re.search(people, _summary(b)) if people else _fits(topic, text, _summary(b))):
            matches.append((year, name, what, 2 + pictured))
        elif 1900 <= year <= 1995 and BIRTH_WORDS.search(text):
            births.append((year, name, what, ("American" in what) + pictured))
    for group in (matches, births):
        rnd.shuffle(group)
        group.sort(key=lambda b: -b[3])
    born = sorted(matches[:2] + births[:4 - len(matches[:2])])
    return chosen, born, bool(picked or matches)


def today_in_history(now, season=None, month=None):
    """Three events and four births from Wikipedia's "on this day", leaning toward the holiday's or heritage
    month's subject when the day has something on it, and saying so in the header."""
    data = fetch_json(f"history-{now:%m%d}", "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/"
                      f"{now:%m}/{now:%d}", max_age_minutes=24 * 60, stale_hours=20)
    if not data:
        return None
    topic = topic_of(season, month)
    events, born, on_topic = _history_picks(data, random.Random(), topic)
    if not events:
        return None
    page = 24.0
    d = page * (2 if born else 1)
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    pages = [(0.0, page, "Today in History")]
    if born:
        pages.append((page, d, "Born Today"))
    kicker = f"{CHANNEL} · {look(topic).title.upper()}" if on_topic else CHANNEL
    info_header(s, d, pages, now, kicker=kicker, season=season)
    events_page = Script()
    _rows(events_page, page, [(str(year), None, text, None) for year, text in events], tint, top=124, height=118,
          gap=16, lines=3)
    births_page = Script()
    _rows(births_page, d - page, [(str(year), name, what, None) for year, name, what, _ in born], tint, top=124,
          height=86, gap=16)
    s.events += events_page.events
    s.events += [(layer, a + page, b + page, body) for layer, a, b, body in births_page.events]
    return s.card("history", d, music("history_bed.ogg"), loop=True, card_kind="history")


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


def launch_watch(now, season=None):
    """The guide's Launch Watch: the next launch on the left (its status, the mission, the rocket and pad, and a
    T-minus clock when the time is firm), three more on the purple side."""
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
    lk = look(season)
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "Launch Watch")], now, season=season)
    fade = (300, 400)
    status = featured["status"].upper()
    brand.strip(s, 0.2, d, 60, 126, 38, [Part("NEXT LAUNCH", brand.ANTON, 26, brand.INK, brand.YELLOW, pad=16),
                                         Part(status, brand.ARCHIVO_BLACK, 18, brand.INK,
                                              brand.TEAL if status in ("GO", "SUCCESS") else lk.tint, pad=14)],
                fade=fade, slide=(200, 600, 90))
    lines, em = cards.wrapped(featured["mission"].upper(), brand.ANTON, 58, 520)
    y = 204
    for i, line in enumerate(lines):
        brand.words(s, 0.3, d, 56, y + i * em, line, brand.ANTON, em, brand.WHITE, fade=fade, slide=(300, 700, 90))
    y += (len(lines) - 1) * em + em / 2 + 26
    brand.words(s, 0.5, d, 58, y, brand.clip(f"{featured['rocket']} · {featured['provider']}".upper(),
                                             brand.ARCHIVO_BLACK, 21, 520), brand.ARCHIVO_BLACK, 21, brand.WHITE,
                fade=fade)
    brand.words(s, 0.6, d, 58, y + 32, brand.clip(f"{featured['pad']}, {featured['place']}".upper(), brand.ARCHIVO,
                                                  19, 520), brand.ARCHIVO, 19, brand.SILVER, bold=True, fade=fade)
    top = y + 62
    precise = featured["precision"] in ("", "SEC", "MIN", "HR", "S", "Second", "Minute", "Hour")
    brand.slab(s, 0.7, d, 62, top, 500, 86, brand.PURPLE, shadow=(7, 7, brand.YELLOW), fade=fade, layer=4)
    if precise and featured["net"] > aware:
        brand.words(s, 0.7, d, 86, top + 43, "T-MINUS", brand.ANTON, 28, brand.YELLOW, fade=fade, layer=6)
        for sec in range(int(d) + 1):
            left = int((featured["net"] - aware).total_seconds()) - sec
            if left < 0:
                break
            days, rem = divmod(left, 86400)
            text = (f"{days}D " if days else "") + f"{rem // 3600:02d}:{rem // 60 % 60:02d}:{rem % 60:02d}"
            brand.words(s, sec, min(d, sec + 1), 538, top + 43, text, brand.ANTON, 60, brand.WHITE, align="right",
                        fade=None, layer=6)
        brand.strip(s, 0.8, d, 66, top + 104, 36, [Part(_when(featured), brand.ARCHIVO_BLACK, 19, brand.WHITE, pad=16,
                                                        border=(3, brand.WHITE))], fade=fade)
    else:
        brand.words(s, 0.7, d, 86, top + 43, "NO EARLIER THAN", brand.ANTON, 28, brand.YELLOW, fade=fade, layer=6)
        brand.words(s, 0.7, d, 538, top + 43, _when(featured), brand.ANTON, 46, brand.WHITE, align="right", fade=fade,
                    layer=6)

    brand.strip(s, 0.9, d, 700, 134, 36, [Part("ALSO COMING UP", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14)],
                shadow=(5, 5, brand.INK), fade=fade, layer=6)
    for i, l in enumerate(rest):
        y, a = 184 + i * 92, 1.0 + i * 0.2
        brand.slab(s, a, d, 694, y, 272, 78, brand.INK, shadow=(6, 6, brand.INK), fade=fade, layer=6)
        brand.slab(s, a, d, 694, y, 74, 78, brand.YELLOW, fade=fade, layer=7)
        month, _, day = _when(l, short=True).partition(" ")
        brand.words(s, a, d, 731, y + (25 if day else 39), month, brand.ANTON, 24, brand.INK, align="center", fade=fade,
                    layer=8)
        if day:
            brand.words(s, a, d, 731, y + 54, day, brand.ANTON, 24, brand.INK, align="center", fade=fade, layer=8)
        brand.words(s, a, d, 782, y + 27, brand.clip(l["mission"].upper(), brand.ANTON, 25, 172), brand.ANTON, 25,
                    brand.WHITE, fade=fade, layer=8)
        brand.words(s, a, d, 783, y + 56, brand.clip(f"{l['rocket']} · {l['place']}".upper(), brand.ARCHIVO, 16, 172),
                    brand.ARCHIVO, 16, brand.YELLOW if l["florida"] else brand.SILVER, bold=True, fade=fade, layer=8)
    brand.footer(s, d, "Source: The Space Devs · Florida in yellow")
    return s.card("launch", d, music("space_bed.ogg"), background=brand.INK_BG, card_kind="launch")


# --- Tonight's Sky ---

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
MOON_PHASES = ((22.5, "New moon"), (67.5, "Waxing crescent"), (112.5, "First quarter"), (157.5, "Waxing gibbous"),
               (202.5, "Full moon"), (247.5, "Waning gibbous"), (292.5, "Last quarter"), (337.5, "Waning crescent"),
               (360.1, "New moon"))
MOON_LIT, MOON_DARK = "&H00D8F0F8&", "&H004A3F3A&"


def _compass(azimuth):
    return COMPASS[int((azimuth % 360 + 22.5) // 45) % 8]


def _height(altitude):
    return "low" if altitude < 20 else "mid-sky" if altitude < 50 else "high" if altitude < 75 else "overhead"


def _moon(phase, r):
    """ASS drawings of the moon at a phase angle (0 new, 180 full; waxing lit on the right), as
    [(path, dx, dy, colour)]: each path starts at 0,0 and goes at the disc's top-left plus dx, dy."""
    disc = [(r * (1 + math.cos(math.radians(a))), r * (1 + math.sin(math.radians(a)))) for a in range(0, 360, 6)]
    k = math.cos(math.radians(phase))
    rows = [r * (2 * i / 40 - 1) for i in range(41)]
    side = 1 if phase < 180 else -1
    limb = [(side * (r * r - y * y) ** 0.5, y) for y in rows]
    terminator = [(side * k * (r * r - y * y) ** 0.5, y) for y in reversed(rows)]
    lit = [(r + x, r + y) for x, y in limb + terminator]
    out = []
    for points, colour in ((disc, MOON_DARK), (lit, MOON_LIT)):
        x0, y0 = min(x for x, _ in points), min(y for _, y in points)
        path = "m " + " l ".join(f"{x - x0:.1f} {y - y0:.1f}" for x, y in points)
        out.append((path, x0, y0, colour))
    return out


def tonight_sky(now, season=None):
    """The guide's Tonight's Sky: the moon and its phase, sunset and the next ISS pass you can see on the left, the
    planets out tonight on the purple side; from SKY_FILE, None outside the evening it was worked out for."""
    try:
        with open(SKY_FILE) as f:
            sky = json.load(f)
    except (OSError, ValueError):
        return None
    if not sky.get("from", 0) <= now.timestamp() <= sky.get("until", 0):
        return None
    at = datetime.datetime.fromtimestamp
    d = 24.0
    s = Script()
    _ground(s, d, season)
    starfield(s, d, seed=int(now.timestamp()) // 3600, top=100, count=50, right=560)
    info_header(s, d, [(0, d, "Tonight's Sky")], now, season=season)
    fade = (300, 400)

    moon = sky["moon"]
    r, cx, cy = 66, 136, 222
    for path, dx, dy, colour in _moon(moon["phase"], r):
        s.shape(0.3, d, cx - r + dx, cy - r + dy, path, colour, fade=fade, layer=4)
    name = next(n for limit, n in MOON_PHASES if moon["phase"] < limit)
    brand.strip(s, 0.4, d, 236, 150, 34, [Part("MOON", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14)], fade=fade)
    lines, em = cards.wrapped(name.upper(), brand.ANTON, 50, 320)
    for i, line in enumerate(lines):
        brand.words(s, 0.5, d, 232, 214 + i * em, line, brand.ANTON, em, brand.WHITE, fade=fade)
    brand.words(s, 0.6, d, 234, 214 + (len(lines) - 1) * em + em / 2 + 22, f"{moon['lit']}% LIT", brand.ARCHIVO_BLACK,
                21, brand.WHITE, fade=fade)
    brand.strip(s, 0.7, d, 62, 330, 44, [Part("SUNSET", brand.ANTON, 28, brand.INK, brand.YELLOW, pad=16),
                                        Part(clock(at(sky["sunset"])), brand.ARCHIVO_BLACK, 21, brand.INK, brand.WHITE,
                                             pad=18)], shadow=(6, 6, brand.PURPLE), fade=fade)
    passes = [p for p in sky["iss"] if p["peak"] > now.timestamp()]
    if passes:
        p = passes[0]
        when = at(p["rise"])
        day = "TONIGHT" if when < at(sky["until"]) else when.strftime("%a").upper()
        detail = (f"{day} {clock(when)} · {max(1, round((p['set'] - p['rise']) / 60))} MIN · "
                  f"{_compass(p['rise_az'])} TO {_compass(p['set_az'])}")
    else:
        detail = "NONE YOU CAN SEE IN THE NEXT 3 DAYS"
    brand.strip(s, 0.9, d, 62, 404, 52, [Part("ISS FLYOVER", brand.ANTON, 30, brand.YELLOW, brand.PURPLE, pad=18),
                                        Part(brand.clip(detail, brand.ARCHIVO_BLACK, 19, 330), brand.ARCHIVO_BLACK, 19,
                                             brand.WHITE, brand.PURPLE, pad=14)],
                shadow=(6, 6, brand.YELLOW), fade=fade)
    if passes:
        brand.words(s, 1.0, d, 66, 486, f"LOOK {_compass(passes[0]['rise_az'])} FIRST · {round(passes[0]['peak_alt'])}° UP AT ITS HIGHEST",
                    brand.ARCHIVO, 19, brand.SILVER, bold=True, fade=fade)

    brand.strip(s, 0.5, d, 700, 134, 36, [Part("LOOK UP", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14)],
                shadow=(5, 5, brand.INK), fade=fade, layer=6)
    planets = sky["planets"][:4]
    for i, p in enumerate(planets):
        y, a = 184 + i * 86, 0.7 + i * 0.2
        brand.slab(s, a, d, 694, y, 272, 72, brand.INK, shadow=(6, 6, brand.INK), fade=fade, layer=6)
        brand.words(s, a, d, 716, y + 26, p["name"].upper(), brand.ANTON, 30, brand.WHITE, fade=fade, layer=8)
        where = f"{_height(p['alt'])} in the {_compass(p['az'])} · best {clock(at(p['time']))}".upper()
        brand.words(s, a, d, 717, y + 54, brand.clip(where, brand.ARCHIVO, 16, 228), brand.ARCHIVO, 16, brand.SILVER,
                    bold=True, fade=fade, layer=8)
    if not planets:
        brand.slab(s, 0.7, d, 694, 184, 272, 72, brand.INK, shadow=(6, 6, brand.INK), fade=fade, layer=6)
        brand.words(s, 0.7, d, 716, 220, "NONE UP AFTER DARK", brand.ANTON, 28, brand.WHITE, fade=fade, layer=8)
    brand.footer(s, d, "Times are local")
    return s.card("sky", d, music("space_bed.ogg"), background=brand.INK_BG, loop=True, card_kind="sky")


# --- Markets ---

COINGECKO = "https://api.coingecko.com/api/v3/coins/{}/market_chart?vs_currency=usd&days=1"
SPX_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=5m"
CHART_W, CHART_H = 232, 92


def _crypto(coin):
    """(price, change % over 24 h, the 24 h of prices) for a CoinGecko coin id, or None."""
    data = fetch_json(f"markets-{coin}", COINGECKO.format(coin), max_age_minutes=10, stale_hours=6)
    prices = [p for _, p in (data or {}).get("prices", []) if p]
    if len(prices) < 2:
        return None
    return prices[-1], 100 * (prices[-1] / prices[0] - 1), prices, None


def _spx():
    """(level, change % since the previous close, today's levels from that close, quote time) or None.
    Out of market hours this is the last session's, which the card says."""
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


def markets(now, season=None):
    """The guide's Markets: a card each for BTC, ETH and the S&P 500 with the price, the change (on yellow when
    it's up, white when it's down) and a chart of the last 24 hours (the S&P's session so far, from the previous
    close). None when no quote is available at all."""
    # Whole dollars and points: cents are noise at TV resolution and only shrink the numbers
    rows = [(symbol, name, quote, fmt) for symbol, name, quote, fmt in (
        ("BTC", "Bitcoin", _crypto("bitcoin"), "${:,.0f}"),
        ("ETH", "Ethereum", _crypto("ethereum"), "${:,.0f}"),
        ("S&P 500", "Index", _spx(), "{:,.0f}"),
    ) if quote]
    if not rows:
        return None
    d = 24.0
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "Markets")], now, season=season)
    fade = (300, 400)
    w, gap = 288, 24
    left = W / 2 - (len(rows) * w + (len(rows) - 1) * gap) / 2
    for i, (symbol, name, (price, change, history, when), fmt) in enumerate(rows):
        x, a = left + i * (w + gap), 0.3 + i * 0.25
        top, h = 124, 392
        brand.shape(s, a, d, x + 10, top + 10, rect(w, h), tint, fade=fade, layer=3)
        brand.shape(s, a, d, x, top, rect(w, h), brand.PANEL, fade=fade, layer=4)
        brand.shape(s, a, d, x, top, rect(w, 74), brand.YELLOW, fade=fade, layer=5)
        em = brand.shrink(symbol, brand.ANTON, 50, 150)
        brand.words(s, a, d, x + 18, top + 37, symbol, brand.ANTON, em, brand.INK, fade=fade, layer=6)
        # The S&P's quote time stands in for its name out of market hours: it is the last session's
        sub = name.upper()
        if when:
            sub = (when.strftime("AS OF %-I:%M %p") if when.date() == now.date()
                   else when.strftime("%a %-I:%M %p").upper())
        brand.words(s, a, d, x + w - 16, top + 37, sub, brand.ARCHIVO_BLACK, 15, brand.INK, align="right", fade=fade,
                    layer=6)
        text = fmt.format(price)
        brand.words(s, a, d, x + 18, top + 124, text, brand.ANTON, brand.shrink(text, brand.ANTON, 54, w - 36),
                    brand.WHITE, fade=fade, layer=6)
        up = change >= 0
        pct = f"{abs(change):.2f}%"
        chip = brand.measure(pct, brand.ARCHIVO_BLACK, 21) + 54
        brand.shape(s, a, d, x + 18, top + 168, rect(chip, 38), brand.YELLOW if up else brand.WHITE, fade=fade, layer=6)
        arrow = "m 8 0 l 16 13 l 0 13" if up else "m 0 0 l 16 0 l 8 13"
        brand.shape(s, a, d, x + 30, top + 180, arrow, brand.INK, fade=fade, layer=7)
        brand.words(s, a, d, x + 54, top + 187, pct, brand.ARCHIVO_BLACK, 21, brand.INK, fade=fade, layer=7)
        brand.words(s, a, d, x + 18 + chip + 12, top + 187, "TODAY" if symbol == "S&P 500" else "24H",
                    brand.ARCHIVO_BLACK, 17, brand.SILVER, fade=fade, layer=6)
        area, ribbon = _chart_paths(history)
        color = brand.TEAL if up else brand.PINK
        brand.shape(s, a, d, x + 28, top + 236, area, color, fade=fade, layer=6, extra="\\1a&HA0&")
        brand.shape(s, a, d, x + 28, top + 236, ribbon, color, fade=fade, layer=7)
        lo, hi = min(history), max(history)
        brand.words(s, a, d, x + 18, top + h - 26, f"HIGH {fmt.format(hi)} · LOW {fmt.format(lo)}", brand.ARCHIVO, 16,
                    brand.SILVER, bold=True, fade=fade, layer=6)
    sources = "CoinGecko"
    if any(sym == "S&P 500" for sym, *_ in rows):
        sources = "S&P vs. prior close · CoinGecko · Yahoo Finance"
    brand.footer(s, d, sources)
    return s.card("markets", d, music("weather_bed.ogg"), background=brand.INK_BG, loop=True, card_kind="markets")


# --- Art Break ---

ART_X, ART_Y = (W - ART_W) // 2, 128
ART_PATH = re.compile(r"^[mnlbsc0-9.\s-]+$")


def placed(path):
    """(path moved to start at 0,0, its left, its top): libass places a drawing by its bounding box, so a
    shape drawn in canvas coordinates goes at the box's corner."""
    tokens = path.split()
    numbers = [i for i, t in enumerate(tokens) if not t.isalpha()]
    xs = [float(tokens[i]) for k, i in enumerate(numbers) if k % 2 == 0]
    ys = [float(tokens[i]) for k, i in enumerate(numbers) if k % 2 == 1]
    x0, y0 = min(xs), min(ys)
    for k, i in enumerate(numbers):
        tokens[i] = f"{float(tokens[i]) - (x0 if k % 2 == 0 else y0):.0f}"
    return " ".join(tokens), x0, y0


def art_break(now, season=None):
    """Art Break: the day's picture from ART_FILE drawn shape by shape, back to front, like an old paint
    program, on a white canvas with a hard shadow, then held over its caption. None without a picture for today."""
    try:
        with open(ART_FILE) as f:
            art = json.load(f)
    except (OSError, ValueError):
        return None
    if art.get("day") != (now - datetime.timedelta(hours=9)).date().isoformat() or not art.get("shapes"):
        return None
    shapes = art["shapes"]
    d = 24.0
    draw = min(14.0, 0.35 * len(shapes))   # the drawing, then the finished picture held
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "Art Break")], now, season=season)
    s.shape(0, d, ART_X - 8 + 12, ART_Y - 8 + 12, rect(ART_W + 16, ART_H + 16), tint, fade=(300, 400), layer=3)
    s.shape(0, d, ART_X - 8, ART_Y - 8, rect(ART_W + 16, ART_H + 16), brand.WHITE, fade=(300, 400), layer=4)
    for i, shape in enumerate(shapes):
        a = 0.8 + draw * i / len(shapes)
        path, x0, y0 = placed(shape["path"])
        r, g, b = (int(shape["color"][k:k + 2], 16) for k in (1, 3, 5))
        s.shape(a, d, ART_X + x0, ART_Y + y0, path, f"&H00{b:02X}{g:02X}{r:02X}&", fade=(180, 400), layer=5,
                extra="\\clip(" + f"{ART_X},{ART_Y},{ART_X + ART_W},{ART_Y + ART_H})")
    caption = art.get("title", "").upper()
    if caption:
        em = brand.shrink(caption, brand.ANTON, 30, 760)
        cw = brand.measure(caption, brand.ANTON, em) + 40
        brand.strip(s, draw + 1.2, d, W / 2 - cw / 2, ART_Y + ART_H + 30, 46, [Part(caption, brand.ANTON, em, brand.INK,
                                                                                  brand.YELLOW, pad=20)],
                    shadow=(6, 6, brand.INK), fade=(400, 400), layer=8)
    return s.card("art", d, music("blurb_vapor1.ogg"), background=brand.INK_BG, loop=True, card_kind="art")


# --- Anniversary Airing ---

def anniversaries(*days):
    """{day: {"src", "aired", "years", "week"}} from ANNIVERSARY_FILE for the given dates that have one."""
    try:
        with open(ANNIVERSARY_FILE) as f:
            picks = json.load(f).get("days", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return {d: picks[d.isoformat()] for d in days if picks.get(d.isoformat(), {}).get("src")}


def anniversary(info, name, episode, now, season=None):
    """The card before an anniversary airing: how many years ago tonight it first aired, the show and
    episode, the date, and on the purple side a line or two on what else was going on that week."""
    d = 14.0
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "From the Vault")], now, season=season)
    fade = (300, 400)
    aired = datetime.date.fromisoformat(info["aired"])
    episode = (episode or "").replace(f"{aired:%b} {aired.day}, {aired.year}", "").strip(" ·-")   # the date's below
    brand.words(s, 0.2, d, 56, 150, f"{info['years']} YEARS AGO TONIGHT", brand.ANTON, 44, brand.YELLOW,
                shadow=(5, 5, brand.PURPLE), fade=fade, slide=(200, 600, 90))
    lines, em = cards.wrapped(name.upper(), brand.ANTON, 66, 520)
    y = 234
    for i, line in enumerate(lines):
        brand.words(s, 0.5, d, 54, y + i * em, line, brand.ANTON, em, brand.WHITE, fade=fade, slide=(500, 900, 90))
    y += (len(lines) - 1) * em + em / 2 + 30
    if episode:
        brand.words(s, 0.8, d, 58, y, brand.clip(episode.upper(), brand.ARCHIVO_BLACK, 22, 520), brand.ARCHIVO_BLACK, 22,
                    brand.WHITE, fade=fade)
        y += 40
    first = f"FIRST AIRED {aired:%A, %B} {aired.day}, {aired.year}".upper()
    brand.strip(s, 1.0, d, 62, y, 38, [Part(first, brand.ARCHIVO_BLACK, 18, brand.INK, tint, pad=16)], fade=fade)
    week = info.get("week") or []
    if week:
        brand.strip(s, 1.4, d, 700, 134, 36, [Part("THAT WEEK", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14)],
                    shadow=(5, 5, brand.INK), fade=fade, layer=6)
        y = 184
        for i, line in enumerate(week[:2]):
            text = brand.lines(line.upper(), brand.ARCHIVO_BLACK, 18, 236)[:5]
            h = 26 + len(text) * 26
            brand.slab(s, 1.6 + i * 0.4, d, 694, y, 272, h, brand.INK, shadow=(6, 6, brand.INK), fade=fade, layer=6)
            for k, t in enumerate(text):
                brand.words(s, 1.6 + i * 0.4, d, 716, y + 26 + k * 26, t, brand.ARCHIVO_BLACK, 18, brand.WHITE,
                            fade=fade, layer=8)
            y += h + 18
    return s.card("anniversary", d, music("history_bed.ogg"), background=brand.INK_BG, loop=True,
                  card_kind="anniversary")


# --- Weekly Countdown ---

LASTFM_KEY_FILE = os.path.join(HOME_DIR, ".config", "retrotv", "lastfm.key")   # on the SD card, never logged
LASTFM_USER = "LOLDAN"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/?method={}&user={}&period=7day&limit={}&format=json&api_key={}"
COUNTDOWN_SECONDS = 28.0   # retrotv.station plans music blocks that open with it around this


def weekly_chart():
    """[(rank, artist, plays, song)]: the top 10 artists of the last 7 days on Last.fm, each with its most
    played song that week (or None); [] without the key or the data. Cached for 6 hours."""
    try:
        with open(LASTFM_KEY_FILE) as f:
            key = f.read().strip()
    except OSError:
        return []
    artists = fetch_json("lastfm-artists", LASTFM_API.format("user.gettopartists", LASTFM_USER, 10, key), 360)
    tracks = fetch_json("lastfm-tracks", LASTFM_API.format("user.gettoptracks", LASTFM_USER, 200, key), 360)
    try:
        top = {}
        for t in (tracks or {}).get("toptracks", {}).get("track", []):
            top.setdefault(t["artist"]["name"].lower(), t["name"])   # most played first
        return [(int(a["@attr"]["rank"]), a["name"], int(a["playcount"]), top.get(a["name"].lower()))
                for a in artists["topartists"]["artist"][:10]]
    except (TypeError, KeyError, ValueError):
        return []


def countdown(chart, up_first, now, season=None):
    """The guide's Weekly Countdown: the week's top artists counted down the purple side from 10 to 2, then
    number one big on the left with its song. up_first (rank, artist, song): the video that opens the block
    after it, named along the bottom at the end."""
    d = COUNTDOWN_SECONDS
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "Weekly Countdown")], now, season=season, ticking=False)
    monday = now.date() - datetime.timedelta(days=now.weekday())
    brand.date_chip(s, d, now, text=f"WEEK OF {monday:%b %-d}".upper())
    fade = (250, 300)
    brand.words(s, 0.3, d, 60, 140, "YOUR MOST PLAYED ARTISTS THIS WEEK", brand.ARCHIVO_BLACK, 18, brand.SILVER,
                fade=fade)
    step = 1.5
    rest = sorted((r for r in chart if r[0] > 1), reverse=True)
    for i, (rank, artist, plays, _) in enumerate(rest):
        a = 1.0 + i * step
        y = 120 + (rank - 2) * 50
        brand.slab(s, a, d, 690, y, 278, 42, brand.INK, shadow=(5, 5, brand.INK), fade=fade, layer=6)
        brand.slab(s, a, d, 690, y, 48, 42, brand.YELLOW, fade=fade, layer=7)
        brand.words(s, a, d, 714, y + 21, str(rank), brand.ANTON, 28, brand.INK, align="center", fade=fade, layer=8)
        brand.words(s, a, d, 752, y + 21, brand.clip(artist.upper(), brand.ANTON, 24, 150), brand.ANTON, 24, brand.WHITE,
                    fade=fade, layer=8)
        brand.words(s, a, d, 954, y + 21, f"{plays}", brand.ARCHIVO_BLACK, 16, brand.SILVER, align="right", fade=fade,
                    layer=8)
    top = next((r for r in chart if r[0] == 1), None)
    reveal = 1.0 + len(rest) * step + 0.8
    until = d - 6.5 if up_first else d
    if top:
        _, artist, plays, song = top
        brand.strip(s, reveal, d, 62, 176, 36, [Part("THIS WEEK'S NO. 1", brand.ANTON, 24, brand.INK, brand.YELLOW,
                                                     pad=14)], fade=fade)
        brand.words(s, reveal, d, 52, 300, "1", brand.ANTON, 200, brand.YELLOW, shadow=(9, 9, brand.PURPLE), fade=fade,
                    extra="\\t(0,250,\\fscx115\\fscy115)\\t(250,450,\\fscx100\\fscy100)")
        lines, em = cards.wrapped(artist.upper(), brand.ANTON, 54, 400)
        for i, line in enumerate(lines):
            brand.words(s, reveal + 0.3, d, 166, 252 + i * em, line, brand.ANTON, em, brand.WHITE, fade=fade)
        y = 252 + (len(lines) - 1) * em + em / 2 + 26
        if song:
            brand.words(s, reveal + 0.5, d, 168, y, brand.clip(song.upper(), brand.ARCHIVO_BLACK, 22, 400),
                        brand.ARCHIVO_BLACK, 22, brand.YELLOW, fade=fade)
            y += 40
        brand.strip(s, reveal + 0.7, d, 172, y, 36, [Part(f"{plays} PLAYS", brand.ARCHIVO_BLACK, 19, brand.WHITE, pad=16,
                                                          border=(3, brand.WHITE))], fade=fade)
    if up_first:
        rank, artist, song = up_first
        a = until if top else reveal
        brand.strip(s, a, d, 62, 520, 46, [Part(f"UP FIRST · NO. {rank}", brand.ANTON, 26, brand.INK, brand.YELLOW,
                                               pad=16),
                                          Part(brand.clip(f"{artist} · {song}".upper(), brand.ARCHIVO_BLACK, 19, 540),
                                               brand.ARCHIVO_BLACK, 19, brand.WHITE, brand.INK, pad=16)],
                    shadow=(5, 5, tint), fade=fade, layer=8)
    brand.footer(s, d, "Chart data: Last.fm", y=580)
    return s.card("countdown", d, music("lotto_bed.ogg"), background=brand.INK_BG, loop=True, card_kind="countdown")


# --- Teletext ---

# A 40x25 teletext page in Bedstead (the SAA5050 character set, CC0): its glyphs are 0.602 em wide and 1 em
# tall, so 22 px cells take a 166% horizontal stretch. The grid sits inside the tube's curve
TT_FONT, TT_COLS, TT_ROWS, TT_CELL = "Bedstead", 40, 25, 22
TT_X, TT_Y = (W - TT_COLS * TT_CELL) // 2, (H - TT_ROWS * TT_CELL) // 2
TT_STRETCH = round(100 * TT_CELL / (0.602 * TT_CELL))
TT_PAGE_SECONDS = 6.5
TT_BAR = "│"   # Bedstead's "|" looks like an I; the box-drawing bar keeps G|TV reading right
# The teletext palette, as ASS colours; red lifted a little, as pure red goes dark through the CRT chain
TT = {"red": "&H004848FF&", "green": "&H0000FF00&", "yellow": "&H0000FFFF&", "blue": "&H00FF0000&",
      "magenta": "&H00FF00FF&", "cyan": "&H00FFFF00&", "white": "&H00FFFFFF&", "black": "&H00000000&"}


class TeletextPage:
    """One page: text at grid cells, each run in a foreground and an optional background colour. Double
    height runs take their row and the one under it."""
    def __init__(self, number, title, band):
        self.number, self.runs = number, []
        self.band(1, band, double=True)
        self.put(1, 1, title, "white" if band in ("blue", "red", "magenta") else "black", double=True)
        self.put(1, 39 - len(f"P{number}"), f"P{number}", "white" if band in ("blue", "red", "magenta")
                 else "black", double=True)

    def put(self, row, col, text, fg="white", bg=None, double=False):
        text = text[:TT_COLS - col]
        if text:
            self.runs.append((row, col, text, fg, bg, double))

    def band(self, row, colour, double=False):
        self.put(row, 0, " " * TT_COLS, colour, colour, double)

    def draw(self, s, start, end):
        for row, col, text, fg, bg, double in self.runs:
            x, y = TT_X + col * TT_CELL, TT_Y + row * TT_CELL
            if bg:
                s.shape(start, end, x, y, rect(len(text) * TT_CELL, TT_CELL * (2 if double else 1)), TT[bg],
                        fade=None, layer=1)
            if text.strip():
                _tt_text(s, start, end, row, col, text, fg, double)


def _tt_text(s, start, end, row, col, text, fg, double=False):
    extra = f"\\fscx{TT_STRETCH}" + ("\\fscy200" if double else "")
    s.text(start, end, TT_X + col * TT_CELL, TT_Y + row * TT_CELL, text.replace("|", TT_BAR), TT_FONT, TT_CELL,
           TT[fg], align=7, fade=None, layer=2, extra=extra)


def _tt_wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def _tt_index(place, weather=True):
    page = TeletextPage(100, "G|TV TEXT", "blue")
    page.put(4, 1, f"{place or 'Your'}'s own teletext service"[:38], "cyan")
    listed = (("TONIGHT ON G|TV", 401, "red"), ("WEATHER", 302, "blue"), ("MARKETS", 220, "green"), ("ODD NEWS", 555, "magenta"))
    for i, (name, number, colour) in enumerate(entry for entry in listed if weather or entry[1] != 302):
        row = 7 + i * 2
        page.put(row, 2, " ", colour, colour)
        page.put(row, 4, name.ljust(31, "."), "white")
        page.put(row, 35, str(number), "yellow")
    for i, colour in enumerate(("yellow", "cyan", "green", "magenta", "red", "blue")):
        page.put(17, 2 + i * 6, " " * 6, colour, colour, double=True)
    return page


def _tt_tonight(listings):
    page = TeletextPage(401, "TONIGHT ON G|TV", "red")
    row = 4
    for when, name, episode in listings:
        if row > 21:
            break
        lines = _tt_wrap(name, 30)
        page.put(row, 1, clock(when).rjust(8), "yellow")
        for line in lines[:2]:
            page.put(row, 10, line, "white")
            row += 1
        if episode and row <= 22:
            page.put(row, 10, clip_text(episode, 29), "cyan")
            row += 1
        row += 1
    if row == 4:
        page.put(4, 1, "Listings are not available", "white")
    return page


def _tt_weather(data, place, now):
    page = TeletextPage(302, "WEATHER", "blue")
    try:
        cur, daily = data["current"], data["daily"]
        today = daily["time"].index(now.date().isoformat())
    except (TypeError, KeyError, ValueError):
        page.put(5, 1, "Weather is not available", "white")
        return page
    page.put(4, 1, (place or "").upper(), "cyan")
    as_of = weather_as_of(data)
    if as_of:
        page.put(5, 1, f"AS OF {as_of}".upper(), "yellow")
    page.put(6, 1, "NOW" if not as_of else "THEN", "yellow")
    page.put(6, 6, f"{round(cur['temperature_2m'])}F", "white", double=True)
    page.put(6, 12, WMO.get(cur["weather_code"], ("",))[0].upper(), "white", double=True)
    page.put(9, 1, f"HUMIDITY {round(cur['relative_humidity_2m'])}%   WIND {round(cur['wind_speed_10m'])} MPH",
             "cyan")
    page.put(11, 1, "DAY   HIGH  LOW  RAIN", "yellow")
    for i in range(today, min(today + 5, len(daily["time"]))):
        row = 13 + (i - today) * 2
        day = datetime.date.fromisoformat(daily["time"][i])
        page.put(row, 1, "TODAY" if i == today else day.strftime("%a").upper(), "white")
        page.put(row, 7, f"{round(daily['temperature_2m_max'][i]):>3}", "red")
        page.put(row, 12, f"{round(daily['temperature_2m_min'][i]):>3}", "cyan")
        page.put(row, 17, f"{daily['precipitation_probability_max'][i] or 0:>3}%", "green")
        page.put(row, 23, WMO.get(daily["weather_code"][i], ("",))[0].upper()[:17], "white")
    return page


def _tt_markets():
    page = TeletextPage(220, "MARKETS", "green")
    rows = (("BTC", "BITCOIN", _crypto("bitcoin"), "${:,.0f}"), ("ETH", "ETHEREUM", _crypto("ethereum"), "${:,.0f}"),
            ("S&P", "S&P 500", _spx(), "{:,.0f}"))
    page.put(4, 1, "24 HOUR CHANGE (S&P SINCE LAST CLOSE)", "cyan")
    row = 7
    for symbol, name, quote, fmt in rows:
        if not quote:
            continue
        price, change = quote[0], quote[1]
        page.put(row, 1, symbol, "yellow", double=True)
        page.put(row, 8, fmt.format(price).rjust(10), "white", double=True)
        page.put(row, 24, f"{'+' if change >= 0 else '-'}{abs(change):.2f}%", "green" if change >= 0 else "red",
                 double=True)
        page.put(row + 2, 1, name, "cyan")
        row += 5
    if row == 7:
        page.put(7, 1, "Prices are not available", "white")
    return page


def _tt_news(headlines):
    page = TeletextPage(555, "ODD NEWS", "magenta")
    row = 4
    for i, headline in enumerate(headlines):
        lines = _tt_wrap(headline, 38)
        if row + len(lines) > 22:
            break
        for line in lines:
            page.put(row, 1, line, "white" if i % 2 == 0 else "cyan")
            row += 1
        row += 1
    if row == 4:
        page.put(4, 1, "No odd news right now", "white")
    return page


def teletext(now, weather, place, listings, headlines, season=None):
    """G|TV Text: a teletext service flipping through its pages (index, tonight's listings, weather, markets
    and the odd news headlines), each with the rolling page header and a ticking clock. Without weather to show,
    its page is left out rather than saying so on the air."""
    pages = [_tt_index(place, bool(weather)), _tt_tonight(listings)] + ([_tt_weather(weather, place, now)] if weather else [])
    pages += [_tt_markets(), _tt_news(headlines)]
    d = round(len(pages) * TT_PAGE_SECONDS, 2)
    s = Script()
    for i, page in enumerate(pages):
        start, end = i * TT_PAGE_SECONDS, (i + 1) * TT_PAGE_SECONDS
        page.draw(s, start, end)
        # The header: while the set looks for the page its number rolls, then it holds; the clock ticks
        rolling = [page.number - 30 + 7 * k for k in range(4)] + [page.number]
        for k, number in enumerate(rolling):
            a = start + k * 0.15
            b = end if number == page.number else start + (k + 1) * 0.15
            _tt_text(s, a, b, 0, 1, f"P{page.number}  G|TV TEXT {number}", "white")
        for sec in range(int(start), int(end) + 1):
            t = now + datetime.timedelta(seconds=sec)
            a, b = max(start, sec), min(end, sec + 1)
            if b > a:
                stamp = f"{t:%a %d %b} {t:%H:%M/%S}"
                _tt_text(s, a, b, 0, TT_COLS - len(stamp), stamp, "yellow")
        col = 1
        for label, colour in (("TV", "red"), ("Weather", "green"), ("Markets", "yellow"), ("News", "cyan")):
            _tt_text(s, start, end, 24, col, label, colour)
            col += len(label) + 3
    return s.card("teletext", d, music("weather_bed.ogg"), background="0x000000", loop=True, card_kind="teletext")


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
            points += get_json(url, timeout=10)
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


def whos_in_space(now, season=None):
    """The guide's Who's in Space: how many people are off the planet, by station, with the crews on the purple
    side; then the ISS over a world map with its ground track."""
    crew = fetch_json("astronauts", "https://ll.thespacedevs.com/2.3.0/astronauts/?in_space=true&limit=40"
                      "&mode=normal", max_age_minutes=360, stale_hours=72)
    people = [a for a in (crew or {}).get("results", []) if (a.get("type") or {}).get("name") != "Non-Human"]
    starman = any(a.get("name") == "Starman" for a in (crew or {}).get("results", []))
    if not people:
        return None
    current, track = _iss_positions(now)
    page = 24.0
    d = page * (2 if current else 1)
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    pages = [(0, page, "Who's in Space")] + ([(page, d, "Where's the ISS?")] if current else [])
    info_header(s, d, pages, now, season=season)
    fade = (300, 300)

    # Page 1: the crews. Chinese astronauts are on Tiangong; everyone else is on the ISS
    stations = {"ISS": [], "TIANGONG": []}
    for a in people:
        agency = (a.get("agency") or {}).get("abbrev", "")
        stations["TIANGONG" if agency == "CNSA" else "ISS"].append((a["name"], agency))
    count = len(people)
    brand.words(s, 0.2, page, 48, 214, str(count), brand.ANTON, 170, brand.YELLOW, shadow=(9, 9, brand.PURPLE),
                fade=fade, slide=(200, 600, 90))
    off = "PERSON OFF THE PLANET" if count == 1 else "PEOPLE OFF THE PLANET"
    nx = 60 + brand.measure(str(count), brand.ANTON, 170) + 24
    brand.words(s, 0.4, page, nx, 186, off, brand.ANTON, 40, brand.WHITE, fade=fade)
    brand.words(s, 0.5, page, nx, 236, "RIGHT NOW", brand.ANTON, 40, brand.WHITE, fade=fade)
    cx = 66
    for name, fill, border in (("ISS", tint, None), ("TIANGONG", None, (3, brand.WHITE))):
        if stations[name]:
            label = f"{name}: {len(stations[name])}"
            cx += brand.strip(s, 0.6, page, cx, 300, 38, [Part(label, brand.ARCHIVO_BLACK, 20,
                                                               brand.INK if fill else brand.WHITE, fill, pad=16,
                                                               border=border)], fade=fade) + 12
    if starman:
        brand.words(s, 1.5, page, 62, 352, "PLUS STARMAN, IN HIS TESLA ROADSTER SINCE 2018",
                    brand.ARCHIVO, 16, brand.SILVER, bold=True, fade=fade)
    # The crews: the ISS's down the purple side, Tiangong's under the count
    for name, x, y, room in (("ISS", 694, 134, 9), ("TIANGONG", 62, 376, 3)):
        names = stations[name]
        if not names:
            continue
        brand.strip(s, 0.5, page, x + 6, y, 34, [Part(name, brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14),
                                                 Part(f"{len(names)} ABOARD", brand.ARCHIVO_BLACK, 16, brand.WHITE,
                                                      brand.INK, pad=12)], shadow=(5, 5, brand.INK), fade=fade, layer=6)
        for i, (person, agency) in enumerate(names[:room]):
            ry, a = y + 46 + i * 44, 0.6 + i * 0.1
            brand.slab(s, a, page, x, ry, 272 if x > 600 else 470, 36, brand.INK, shadow=(5, 5, brand.INK), fade=fade,
                       layer=6)
            brand.words(s, a, page, x + 18, ry + 18, brand.clip(person.upper(), brand.ARCHIVO_BLACK, 18,
                                                                (272 if x > 600 else 470) - 110),
                        brand.ARCHIVO_BLACK, 18, brand.WHITE, fade=fade, layer=8)
            if agency:
                aw = brand.measure(agency, brand.ARCHIVO_BLACK, 14) + 14
                right = x + (272 if x > 600 else 470) - 14
                brand.shape(s, a, page, right - aw, ry + 7, rect(aw, 22), tint, fade=fade, layer=8)
                brand.words(s, a, page, right - aw / 2, ry + 18, agency, brand.ARCHIVO_BLACK, 14, brand.INK,
                            align="center", fade=fade, layer=9)
        if len(names) > room:
            brand.words(s, 0.6, page, x + 18, y + 46 + room * 44 + 6, f"AND {len(names) - room} MORE", brand.ARCHIVO_BLACK,
                        16, brand.SILVER, fade=fade, layer=8)
    brand.footer(s, page, "Source: The Space Devs")

    # Page 2: the ISS over a world map, with the night side shaded and its ground track
    if current:
        a, b = page, d
        s.shape(a, b, MAP_X + 10, MAP_Y + 10, rect(720, 360), tint, fade=fade, layer=3)
        s.shape(a, b, MAP_X, MAP_Y, rect(720, 360), brand.PANEL, fade=fade, layer=4)
        for lon in range(-150, 180, 30):
            x, _ = _map_xy(lon, 0)
            s.shape(a, b, x, MAP_Y, rect(1, 360), brand.DEEP_PURPLE, fade=fade, layer=5)
        for lat in (-60, -30, 0, 30, 60):
            _, y = _map_xy(0, lat)
            s.shape(a, b, MAP_X, y, rect(720, 1), brand.DEEP_PURPLE, fade=fade, layer=5)
        try:
            with open(WORLD_MAP) as f:
                rings = json.load(f)
            land = []
            for ring in rings:
                pts = [_map_xy(ring[i], ring[i + 1]) for i in range(0, len(ring), 2)]
                land.append("m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in pts))
            s.shape(a, b, 0, 0, " ".join(land), brand.PURPLE, fade=fade, layer=6)
        except (OSError, ValueError) as e:
            logger.warning(f"World map unavailable: {e}")
        s.shape(a, b, 0, 0, _night_path(current["solar_lat"], current["solar_lon"]), "&H00000000&", fade=fade,
                layer=7, extra="\\1a&H70&")
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
            s.shape(a, b, 0, 0, path, brand.YELLOW if future else WHITE, fade=fade, layer=8,
                    extra="" if future else "\\1a&H60&")
        x, y = _map_xy(current["longitude"], current["latitude"])
        pulse = "".join(f"\\t({t},{t + 600},\\fscx180\\fscy180\\1a&HFF&)\\t({t + 600},{t + 601},\\fscx100\\fscy100\\1a&H40&)"
                        for t in range(0, int(page * 1000), 1200))
        s.shape(a, b, x, y, circle(9), WHITE, fade=fade, layer=9, align=5, extra="\\1a&H40&" + pulse)
        s.shape(a, b, x - 6, y - 6, circle(6), WHITE, fade=fade, layer=10)
        east = x < MAP_X + 640
        brand.strip(s, a, b, x + 16 if east else x - 16 - 60, y - 34, 28, [Part("ISS", brand.ANTON, 20, brand.INK,
                                                                                brand.YELLOW, pad=12)],
                    fade=fade, layer=10)
        lat, lon = current["latitude"], current["longitude"]
        caption = (f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}  {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"
                   f"  ·  {current['velocity']:,.0f} MPH  ·  {current['altitude']:,.0f} MILES UP")
        cw = brand.measure(caption, brand.ARCHIVO_BLACK, 19) + 36
        brand.strip(s, a, b, W / 2 - cw / 2, MAP_Y + 360 - 22, 38, [Part(caption, brand.ARCHIVO_BLACK, 19, brand.INK,
                                                                       brand.WHITE, pad=18)],
                    shadow=(5, 5, brand.INK), fade=fade, layer=11)
    return s.card("space", d, music("space_bed.ogg"), background=brand.INK_BG, loop=True, card_kind="space")


# --- This Day in Gaming, Born Today, #1 This Week (their data: retrotv/onthisday.py, gathered by the writing job) ---

def on_this_day(day):
    """The writing job's picks for a broadcast day: {"games", "born", "number_ones", "featured_year"}, or {}."""
    try:
        with open(ONTHISDAY_FILE) as f:
            return json.load(f).get("days", {}).get(day.isoformat(), {}) or {}
    except (OSError, ValueError, AttributeError):
        return {}


def games_today(now, day, season, cached):
    """This Day in Gaming: games released on this date, best known first, every one of them with a TV ad in the
    library. The first whose ad is on the SD card (cached(src)) leads, and its ad follows the card (Card.follow)."""
    games = on_this_day(day).get("games") or []
    if not games:
        return None
    lead = ad = None
    for game in games:
        ad = next((src for src in game.get("ads", []) if cached(src)), None)
        if ad:
            lead = game
            break
    shown = ([lead] if lead else []) + [g for g in games if g is not lead]
    d = 16.0
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "This Day in Gaming")], now, season=season)
    _rows(s, d, [(str(g["year"]), g["title"], g.get("platform"), None) for g in shown[:4]], tint)
    if lead:
        _next_strip(s, d, f"The TV ad for {lead['title']}", tint)
    card = s.card("games", d, music("quiz_bed.ogg"), loop=True, card_kind="games", background=brand.INK_BG)
    card.follow = ad
    return card


def born_today(now, day, season, tonight=()):
    """Born Today: people from the library's shows born on this date: who they played, and how old they turn (or
    their years). tonight: [(start, show name)] still to air today, so a birthday's show on tonight says when."""
    people = (on_this_day(day).get("born") or [])[:4]
    if not people:
        return None
    rows = []
    for p in people:
        born = int(p["born"][:4])
        # The role whose show is on later tonight, if one is, else their first
        on = next(((t, role) for t, name in tonight for role in p["roles"] if _same_show(name, role[1])), None)
        character, show = on[1] if on else p["roles"][0]
        sub = f"{character} on {show}" if character else show
        if on:
            sub += f" · tonight at {clock(on[0])}"
        right = f"{born}–{p['died'][:4]}" if p.get("died") else f"TURNS {now.year - born}"
        rows.append((None, p["name"], sub, right))
    d = 16.0
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "Born Today")], now, season=season)
    _rows(s, d, rows, tint)
    return s.card("born", d, music("history_bed.ogg"), loop=True, card_kind="born", background=brand.INK_BG)


def _same_show(a, b):
    plain = lambda t: re.sub(r"^the\s+", "", re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\([^)]*\)", " ", (t or "").lower()))).strip()
    return plain(a) == plain(b)


def number_one(now, day, season, year, cached):
    """#1 This Week: the Hot 100's number one in this week of a past year. The writing job's featured year when its
    video is on the SD card, else any year whose video is (the video follows the card); else the year nearest `year`
    (the show's era) without one. Laid out like the guide's Weekly Countdown number one, the year big on the
    purple side."""
    picks = on_this_day(day)
    numbers = picks.get("number_ones") or []
    if not numbers:
        return None
    ready = [n for n in numbers if n.get("video") and cached(n["video"])]
    lead = next((n for n in ready if n["year"] == picks.get("featured_year")), ready[0] if ready else None)
    chosen = lead or min(numbers, key=lambda n: (abs(n["year"] - (year or 1990)), n["year"]))
    d = 12.0
    tint = look(season).tint
    s = Script()
    _ground(s, d, season)
    info_header(s, d, [(0, d, "#1 This Week")], now, season=season)
    fade = (300, 300)
    issue = datetime.date.fromisoformat(chosen["issue"])
    brand.strip(s, 0.2, d, 62, 134, 36, [Part("BILLBOARD HOT 100", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14),
                                        Part(f"WEEK OF {issue:%b %-d, %Y}".upper(), brand.ARCHIVO_BLACK, 17, brand.WHITE,
                                             brand.INK, pad=14)], fade=fade)
    brand.words(s, 0.3, d, 48, 300, "1", brand.ANTON, 200, brand.YELLOW, shadow=(9, 9, brand.PURPLE), fade=fade,
                extra="\\t(0,250,\\fscx115\\fscy115)\\t(250,450,\\fscx100\\fscy100)")
    lines, em = cards.wrapped(f"“{chosen['song']}”".upper(), brand.ANTON, 52, 400)
    for i, line in enumerate(lines):
        brand.words(s, 0.5, d, 166, 252 + i * em, line, brand.ANTON, em, brand.WHITE, fade=fade)
    y = 252 + (len(lines) - 1) * em + em / 2 + 28
    brand.words(s, 0.7, d, 168, y, brand.clip(chosen["artist"].upper(), brand.ARCHIVO_BLACK, 24, 400),
                brand.ARCHIVO_BLACK, 24, brand.YELLOW, fade=fade)
    if chosen.get("weeks", 1) > 1:
        brand.strip(s, 1.0, d, 172, y + 34, 36, [Part(f"{chosen['weeks']} WEEKS AT NO. 1", brand.ARCHIVO_BLACK, 19,
                                                      brand.WHITE, pad=16, border=(3, brand.WHITE))], fade=fade)
    brand.words(s, 0.4, d, 830, 330, str(chosen["year"]), brand.ANTON, 120, brand.WHITE, align="center",
                shadow=(8, 8, brand.INK), fade=fade, layer=6)
    if lead:
        _next_strip(s, d, "The video", tint)
    card = s.card("number_one", d, music("guide_bed.ogg"), loop=True, card_kind="number_one", background=brand.INK_BG)
    card.follow = lead["video"] if lead else None
    return card
