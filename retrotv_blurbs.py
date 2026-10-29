"""
Text bumps in the late-night cartoon block tradition: lowercase white type on black, a few beats long, over a
lo-fi or vaporwave bed (music/blurb_*.ogg, made by music_src/blurb_beds.py), ending on a small "g|tv".

What they say follows the level the station passes for the hour (retro_station's BLURB_CHEEKY_HOUR and
BLURB_RAUNCHY_HOUR): by day clean facts ("did you know?", "ain't that interesting?"), food for thought and dad
jokes; cheekier from 9 PM; raunchy from 11 PM to sign-off, crude but never racist, sexist, religious or
political; cursing and vulgar are fine from 9 PM. Facts come from uselessfacts.jsph.pl, dad jokes from
icanhazdadjoke.com, the late-night jokes from JokeAPI (its own flags keep the hateful ones out), and our lines
from retrotv_blurbs.txt. From 9 PM, real odd-news headlines air with a quip at the hour's level (NEWS_FILE),
and bumps in a show's breaks riff on that show (SHOW_RIFFS_FILE); retrotv_news.py writes both on its own timer. Now and then a bump ends on a reaction ("damn, that's
crazy.", "ok but who asked?"), and facts and headlines open with a kicker; both come from
retrotv_blurbs_extras.txt. Nothing airs again until SEEN_KEEP others have.
"""
import datetime
import hashlib
import json
import logging
import os
import random
import re
import urllib.request

from retrotv_cards import BARLOW_S, CHANNEL, WHITE, Card, Script, esc, lavfi_url, music
from retrotv_screens import DATA_DIR, USER_AGENT, wrap

logger = logging.getLogger("RetroTV")

HERE = os.path.dirname(os.path.abspath(__file__))
LINES_FILE = os.path.join(HERE, "retrotv_blurbs.txt")           # the bumps we wrote
EXTRAS_FILE = os.path.join(HERE, "retrotv_blurbs_extras.txt")   # reactions, and what facts open with
SEEN_FILE = os.path.join(DATA_DIR, "blurbs.json")   # what has aired lately, so nothing repeats soon
SEEN_KEEP = 1500
NEWS_FILE = os.path.join(DATA_DIR, "news_quips.json")   # headlines and their quips, written by retrotv_news.py
NEWS_MAX_AGE_HOURS = 48  # older headlines aren't news any more: they don't air, and don't get quips written
SHOW_RIFFS_FILE = os.path.join(DATA_DIR, "show_riffs.json")   # riffs on the shows, by name, from retrotv_news.py
LEVELS = ("clean", "cheeky", "raunchy")
BEDS = ("blurb_lofi1.ogg", "blurb_lofi2.ogg", "blurb_vapor1.ogg", "blurb_vapor2.ogg")
MAX_CHARS = 220          # anything longer doesn't read as a bump
TEXT_SIZE, KICKER_SIZE, SIGN_SIZE = 55, 44, 37   # the words, the kicker above them, the g|tv at the end
GREY = "&H00A8A8A8&"     # the kicker, a step behind the words
# What can air at each level: (kicker, source, weight). "ours" is retrotv_blurbs.txt with its own kickers; the
# kicker of a fact or a headline (None) comes from EXTRAS_FILE, see KICKERS
MIX = {
    "clean": ((None, "fact", 4), ("dad joke", "dad", 3), (None, "ours", 4)),
    "cheeky": ((None, "fact", 2), ("dad joke", "dad", 2), ("after hours", "joke", 3), (None, "news", 3),
               (None, "show", 4), (None, "ours", 5)),
    "raunchy": ((None, "fact", 1), ("after hours", "joke", 5), (None, "news", 3), (None, "show", 4),
                (None, "ours", 6)),
}
# The EXTRAS_FILE section a source's kicker comes from, and what it says if that section has none for the level.
# A show's kicker names it where it says {show}
KICKERS = {"fact": ("fact kickers", "did you know?"), "news": ("news kickers", "actual headline"),
           "show": ("show kickers", "a word about {show}")}
# How often a bump from each source gets a reaction from EXTRAS_FILE as its last beat ("ok but who asked?").
# Never after a headline or a riff: the quip is the reaction
REACTION_CHANCE = {"fact": 0.6, "dad": 0.4, "joke": 0.4, "news": 0.0, "show": 0.0, "ours": 0.2}
NEVER = "racist,sexist,religious,political"   # JokeAPI flags kept off the air at any hour
# Words that keep a fact or joke from the APIs off the air at the clean level (the day, and the kids' hours)
NOT_CLEAN = re.compile(r"\b(?:sex\w*|naked|nude|penis\w*|vagina\w*|breasts?|boobs?|condoms?|orgasm\w*|porn\w*|"
                       r"drunk\w*|beers?|vodka|whiske?y|kill\w*|murder\w*|suicid\w*|drugs?|hell|damn\w*|ass|"
                       r"shit\w*|fuck\w*|bitch\w*)\b", re.I)


# --- Where the words come from ---

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.load(r)


def _tidy(text):
    """One line of plain text; "this/that" gets spaces so long runs of them can wrap."""
    text = re.sub(r"(?<=\w)/(?=\w)", " / ", text.replace("`", "'"))
    return re.sub(r"\s+", " ", text).strip()


def _fact(level, seen):
    d = _get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en")
    return f"fact-{d['id']}", [_tidy(d["text"])]


def _dad_joke(level, seen):
    d = _get("https://icanhazdadjoke.com/")
    joke = _tidy(d["joke"])
    setup, mark, punchline = joke.partition("? ")   # "Why ...? Because ..." plays as two beats
    return f"dad-{d['id']}", ([setup + "?", punchline] if mark and punchline else [joke])


def _late_joke(level, seen):
    """A JokeAPI joke, from ten at a time so the night's level can be found: innuendo (flagged nsfw) when
    cheeky, nsfw or explicit when raunchy; any joke not aired lately if none of those turn up."""
    flags = NEVER if level == "raunchy" else NEVER + ",explicit"
    d = _get(f"https://v2.jokeapi.dev/joke/Misc,Pun,Programming?blacklistFlags={flags}&amount=10&lang=en")
    if d.get("error"):
        raise ValueError(d.get("message", "JokeAPI error"))
    jokes = [j for j in d.get("jokes", []) if f"joke-{j['id']}" not in seen]
    if not jokes:
        raise ValueError("nothing new")
    spicy = [j for j in jokes if j["flags"].get("nsfw") or (level == "raunchy" and j["flags"].get("explicit"))]
    j = random.choice(spicy or jokes)
    return f"joke-{j['id']}", ([_tidy(j["joke"])] if j["type"] == "single" else [_tidy(j["setup"]), _tidy(j["delivery"])])


def _news_items():
    """The headlines in NEWS_FILE from the last NEWS_MAX_AGE_HOURS, newest first."""
    with open(NEWS_FILE) as f:
        items = json.load(f)["items"]
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=NEWS_MAX_AGE_HOURS)
    items = [i for i in items if datetime.datetime.fromisoformat(i["published"]) > cutoff]
    return sorted(items, key=lambda i: i["published"], reverse=True)


def headlines():
    """The recent odd-news headlines, newest first, for other screens (G|TV Text); [] when there are none."""
    try:
        return [i["headline"] for i in _news_items()]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _news(level, seen):
    """A headline from NEWS_FILE that hasn't aired, with its quip for this level as the following beats."""
    fresh = [i for i in _news_items() if i["key"] not in seen and i["quips"].get(level)]
    if not fresh:
        raise ValueError("nothing new")
    item = random.choice(fresh)
    return item["key"], [item["headline"]] + item["quips"][level].split(" / ")


SOURCES = {"fact": _fact, "dad": _dad_joke, "joke": _late_joke, "news": _news}


def _sections(path):
    """[(section, level, beats, key)] from one of our files: under a "[section | level]" header, an entry a
    line, " / " between its beats."""
    out, section, level = [], None, None
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^\[(.+?)\s*\|\s*(clean|cheeky|raunchy)\s*\]$", line, re.I)
                if m:
                    section, level = m.group(1).strip(), m.group(2).lower()
                elif section:
                    beats = [b.strip() for b in line.split(" / ") if b.strip()]
                    out.append((section, level, beats, "ours-" + hashlib.sha1(line.encode()).hexdigest()[:12]))
    except OSError as e:
        logger.warning(f"Blurb lines unavailable: {e}")
    return out


def our_lines():
    """[(kicker, level, beats, key)]: the bumps in LINES_FILE."""
    return _sections(LINES_FILE)


def _choose(entries, level, seen=()):
    """One of the entries that may air at this level, the level's own ones three times as likely; seen ones
    only if every one has been seen. None if there are none."""
    allowed = LEVELS[:LEVELS.index(level) + 1]
    ok = [e for e in entries if e[1] in allowed]
    fresh = [e for e in ok if e[3] not in seen] or ok
    return random.choices(fresh, weights=[3 if e[1] == level else 1 for e in fresh])[0] if fresh else None


def _extra(name, level):
    """The beats of a "reactions" or "fact kickers" entry from EXTRAS_FILE for this level, or None."""
    got = _choose([e for e in _sections(EXTRAS_FILE) if e[0] == name], level)
    return got[2] if got else None


def _from_ours(level, seen):
    got = _choose(our_lines(), level, seen)
    return (got[0], got[2], got[3]) if got else None


def riff_key(show, riff):
    """What a riff on a show is remembered by once it has aired (retrotv_news.py checks these too)."""
    return "show-" + hashlib.sha1(f"{show}|{riff}".encode()).hexdigest()[:12]


def _from_show(level, seen, show):
    """(kicker, beats, key) for a riff on the show (by name) from SHOW_RIFFS_FILE, the kicker naming it; None
    with no show on, or no riff at this level that hasn't aired lately."""
    if not show:
        return None
    try:
        with open(SHOW_RIFFS_FILE) as f:
            entry = json.load(f)["shows"].get(show)
    except (OSError, ValueError, KeyError, AttributeError):
        return None
    fresh = [r for r in (entry or {}).get(level, []) if riff_key(show, r) not in seen]
    if not fresh:
        return None
    riff = random.choice(fresh)
    section, default = KICKERS["show"]
    kicker = " ".join(_extra(section, level) or [default]).replace("{show}", entry.get("title") or show)
    return kicker, riff.split(" / "), riff_key(show, riff)


def _pick(level, seen, show=None):
    """(kicker, beats, key, source) for a bump at this level, nothing in `seen` if it can be helped; None if
    nothing turns up. show: the name of the show this break is in, for a riff on it. A source that's down or
    out of material isn't asked again this time; our lines are the last resort."""
    options = list(MIX[level])
    for _ in range(6):
        kicker, source, _ = random.choices(options, weights=[w for _, _, w in options])[0]
        if source in ("ours", "show"):
            got = _from_show(level, seen, show) if source == "show" else _from_ours(level, seen)
            if got:
                return (*got, source)
            options = [o for o in options if o[1] != source] or options
            continue
        try:
            key, beats = SOURCES[source](level, seen)
        except Exception as e:
            logger.warning(f"Blurb: no {source} right now ({e})")
            options = [o for o in options if o[1] != source] or options
            continue
        text = " ".join(beats)
        if key not in seen and len(text) <= MAX_CHARS and not (level == "clean" and NOT_CLEAN.search(text)):
            if source in KICKERS:
                section, default = KICKERS[source]
                kicker = " ".join(_extra(section, level) or [default])
            return kicker, beats, key, source
    got = _from_ours(level, seen)
    return (*got, "ours") if got else None


def _seen():
    try:
        with open(SEEN_FILE) as f:
            return list(json.load(f).get("seen", []))
    except (OSError, ValueError, AttributeError):
        return []


def _remember(seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"seen": seen[-SEEN_KEEP:]}, f)
    os.replace(tmp, SEEN_FILE)


# --- On screen ---

def _beat(s, start, text, color, size):
    """One beat: the words centred, lowercase, held long enough to read. Returns when the next one starts."""
    lines = wrap(text.lower(), BARLOW_S, size, 780, max_lines=4)
    seconds = min(6.0, max(2.2, 1.4 + 0.33 * len(text.split())))
    s.raw(start, start + seconds, f"\\an5\\pos(512,300)\\fn{BARLOW_S}\\fs{size}\\c{color}\\bord0\\shad0"
                                  f"\\fsp1\\fad(120,150)", "\\N".join(esc(l) for l in lines))
    return start + seconds + 0.35


def blurb(level="clean", show=None):
    """A text bump for the level ("clean", "cheeky" or "raunchy"), or None when nothing could be found. show:
    the name of the show on, which the bump may riff on."""
    seen = _seen()
    picked = _pick(level if level in MIX else "clean", set(seen), show)
    if not picked:
        return None
    kicker, beats, key, source = picked
    if random.random() < REACTION_CHANCE.get(source, 0):
        beats = beats + (_extra("reactions", level) or [])
    _remember(seen + [key])
    logger.info(f"Blurb ({level}): {kicker or ''} {' / '.join(beats)}")
    return card(kicker, beats)


def card(kicker, beats):
    """The bump itself: the kicker (if any), each beat in turn, then a small g|tv, over a random bed."""
    s = Script()
    t = 0.6
    if kicker:
        t = _beat(s, t, kicker, GREY, KICKER_SIZE)
    for text in beats:
        t = _beat(s, t, text, WHITE, TEXT_SIZE)
    s.text(t, t + 1.8, 512, 300, CHANNEL.lower(), BARLOW_S, SIGN_SIZE, WHITE, spacing=2, fade=(120, 300))
    d = round(t + 2.1, 2)
    beds = [b for b in BEDS if os.path.exists(music(b))]
    bed = music(random.choice(beds)) if beds else None
    return Card(lavfi_url(s.write("blurb"), d, bed, background="0x000000", loop=True), d, "blurb")
