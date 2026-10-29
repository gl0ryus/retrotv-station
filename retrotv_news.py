"""
What the text bumps (retrotv_blurbs) get from Claude, every couple of hours (retrotv-news.timer on the Pi):

- Headline quips. The new headlines from UPI's Odd News feed, each with a quip at every level a headline can
  air at (levels_for("news"): cheeky and raunchy), the last NEWS_MAX_AGE_HOURS of them kept in NEWS_FILE. Odd
  news only: the bumps are never political, and general news mostly is. Claude also skips any headline about a
  death, an injury, a victim or a disaster.
- Show riffs. RIFFS_PER_LEVEL riffs per level on each show coming up on the main channel (from the remote's
  guide), kept by show name in SHOW_RIFFS_FILE and reused; a show gets more once one level's have all aired.

This runs in its own venv (~/retrotv_venv, requirements-news.txt) and the station never imports it, so a failed
run only means nothing new to say. The API key is on the Pi's SD card in KEY_FILE, readable only by the
station's user; it is never logged.
"""
import datetime
import email.utils
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import anthropic

from retrotv_blurbs import (LEVELS, MAX_CHARS, MIX, NEWS_FILE, NEWS_MAX_AGE_HOURS, NOT_CLEAN, SHOW_RIFFS_FILE,
                            riff_key, _seen as aired_lately)
from retrotv_meta import artist_key, music_credit, popup_key
from retrotv_screens import DATA_DIR, POPUPS_FILE, SKY_FILE, USER_AGENT

logger = logging.getLogger("RetroTV.news")

FEED = "https://rss.upi.com/news/odd_news.rss"
GUIDE_URL = "http://127.0.0.1/api/guide"   # the phone remote's guide (retro_station's REMOTE_PORT)
KEY_FILE = os.path.expanduser("~/.config/retrotv/anthropic.key")
MODEL = "claude-opus-5-5"
MAX_NEW = 12             # headlines sent per run; any more wait for the next one
MAX_SHOWS = 6            # shows sent per run, likewise
RIFFS_PER_LEVEL = 4      # asked for per show and level each time
RIFFS_KEEP = 12          # kept per show and level; the oldest go first
SKIPPED_KEEP = 500       # headlines and shows Claude passed on, remembered so they aren't sent again
LOCATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "location.json")   # as retro_station's
SKYFIELD_DIR = os.path.join(DATA_DIR, "skyfield")   # the planets' ephemeris (de421.bsp, 17 MB), fetched once
ISS_TLE = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=tle"
SKY_SIGN_OFF_HOUR = 2    # the night ends at retro_station's SIGN_OFF_HOUR, the next day
PLANETS = (("Mercury", "mercury"), ("Venus", "venus"), ("Mars", "mars"), ("Jupiter", "jupiter barycenter"),
           ("Saturn", "saturn barycenter"))
PREFIX = re.compile(r"^(?:look|watch|video|listen|photos?)\s*:\s*", re.I)   # UPI's "Watch: ..." and the like
MANIFEST_FILE = os.path.expanduser("~/retrotv_manifest.json")   # retro_station's cache manifest
WIKI_API = "https://en.wikipedia.org/w/api.php?"
WIKI_CHARS = 6000        # of an article, sent with each video
POPUP_VIDEOS = 15        # music videos looked up per run, sent to Claude 5 at a time
JOB_BUDGET = 200         # seconds: no new batch starts after this (retrotv-news.service stops it at 300)
STARTED = time.time()
POPUP_FACTS = 6          # facts kept per video
FACT_CHARS = 90


def levels_for(source):
    """The levels a source's bumps can air at: the ones whose MIX has it. Something is written for each."""
    return [level for level in LEVELS if any(s == source for _, s, _ in MIX[level])]


NEWS_LEVELS, SHOW_LEVELS = levels_for("news"), levels_for("show")
LEVEL_RULES = {
    "clean": "airs any time, the kids' hours too. No cursing, no innuendo, nothing about sex, drugs or drinking.",
    "cheeky": "airs from 9 PM. Innuendo and mild cursing (damn, hell, ass) are fine.",
    "raunchy": "airs from 11 PM to sign-off. Crude and vulgar are fine, any cursing is fine.",
}


def _rules(levels):
    return "\n".join(f"- {level}: {LEVEL_RULES[level]}" for level in levels)


VOICE = """You write the text bumps for GL0RYUS Network, a retro TV station that plays old shows and \
commercials on a CRT in someone's living room. Bumps are the short white-on-black lines between shows, in the \
tradition of late-night cartoon blocks: lowercase, deadpan, dry, a little absurd, never trying too hard."""

RULES = """At every level: never racist, sexist, homophobic, religious or political, and never punching down. \
Don't mock anyone's looks, body, age or disability."""

STYLE = """Style:
- lowercase; no emojis, hashtags, "lol" or exclamation marks
- one or two beats, with " / " between them (each beat is shown on screen on its own)
- under 15 words in all
- a different joke each time, not one joke with swearing added"""

EXAMPLES = """Lines in the station's voice, for flavour:
somewhere a vcr is still blinking 12:00. / it's been right twice a day for thirty years.
nobody has ever looked cool / carrying a full laundry basket.
a waterbed / is a mattress that wants to get involved.
your ex is watching this too. / probably. / don't text them.
ok but who the fuck asked?"""

NEWS_SYSTEM = f"""{VOICE}

These bumps show a real headline from the odd-news wire, then your quip. For every headline, write one quip \
per level:
{_rules(NEWS_LEVELS)}

{RULES} Don't name a private person from the story. The joke is on the situation.

Skip a headline (skip: true and empty quips) if it involves a death, a serious injury, abuse, a crime with a \
victim, a disaster, an illness or politics. When in doubt, skip it.

{STYLE}
- react to the headline; don't repeat or explain it

{EXAMPLES}"""

RIFF_SYSTEM = f"""{VOICE}

These bumps air in the breaks of a show and riff on it: a kicker names the show ("re: the golden girls"), then \
your riff. A riff has to work whether the show just ended or is about to come back, so don't say which.

For every show, give the title viewers know it by, in lowercase ("attack of the show", "popeye"), and \
{RIFFS_PER_LEVEL} riffs per level:
{_rules(SHOW_LEVELS)}

Riff on what the show is known for: its premise, characters, catchphrases, theme song, look and era. Don't \
invent episode plots, and don't joke about the real cast's lives, deaths or scandals. {RULES}

Skip a show (skip: true, an empty title and no riffs) if you don't know it well enough to riff on it without \
making things up.

{STYLE}
- don't repeat the show's name; the kicker already says it

{EXAMPLES}"""


def _schema(key, fields):
    """A JSON schema for {key: [{n, skip, **fields}]}."""
    return {
        "type": "object",
        "properties": {key: {"type": "array", "items": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "skip": {"type": "boolean"}, **fields},
            "required": ["n", "skip", *fields],
            "additionalProperties": False,
        }}},
        "required": [key],
        "additionalProperties": False,
    }


POPUP_SYSTEM = f"""You write the fact bubbles for Pop-Up Video on GL0RYUS Network, a retro TV station: while a \
music video plays, little bubbles pop up with facts about the song, the band and the video, in the wry, \
trivia-loving voice of VH1's Pop-Up Video in the late 90s.

For each video you get the Wikipedia article on the song or, when there isn't one, on the artist. Use only facts \
the article states. Never add anything from memory and never guess. When the article is about the artist, the \
facts are about the artist, and about the song only where the article mentions it.

Give up to {POPUP_FACTS} facts per video, in the order they should pop up:
- one fact per bubble, under {FACT_CHARS - 10} characters, a plain sentence in sentence case
- a light, wry touch is welcome ("He has since said he hates the song."), but the fact stays exactly as true as \
the article has it
- nothing about deaths, illness, crimes, abuse or tragedies; nothing hateful or political
If the article gives nothing safe and fun to say, skip the video (skip: true, no facts)."""

NEWS_SCHEMA = _schema("quips", {level: {"type": "string"} for level in NEWS_LEVELS})
POPUP_SCHEMA = _schema("videos", {"facts": {"type": "array", "items": {"type": "string"}}})
RIFF_SCHEMA = _schema("shows", {"title": {"type": "string"},
                                **{level: {"type": "array", "items": {"type": "string"}} for level in SHOW_LEVELS}})


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def load(path):
    """The JSON object in path, or {} if it isn't there or can't be read."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({**data, "updated": _now().isoformat()}, f, indent=1)
    os.replace(tmp, path)   # the station may be reading it


def ask(system, content, schema):
    """Claude's answer to content, as the JSON object the schema describes."""
    with open(KEY_FILE) as f:
        client = anthropic.Anthropic(api_key=f.read().strip(), timeout=180.0)
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",   # a request the model declines is re-run on the model Anthropic picks for it
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    usage = response.usage
    logger.info(f"{response.model}: {usage.input_tokens} tokens in, {usage.output_tokens} out "
                f"(request {response._request_id})")
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"nothing written: the response stopped on {response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _numbered(answers, count):
    """Each answer once, in the order given, if its n is one of the 1..count asked about."""
    done = set()
    for a in answers:
        if 1 <= a["n"] <= count and a["n"] not in done:
            done.add(a["n"])
            yield a


def tidy(text, level, headline=""):
    """A quip or riff as beats joined by " / ", or "" when it won't do at this level: too long to read as a
    bump (with the headline before it), or (clean) something in it the daytime bumps keep off the air."""
    beats = [b.strip() for b in re.sub(r"\s+", " ", text).lower().split(" / ") if b.strip()]
    joined = " / ".join(beats)
    if not beats or len(beats) > 3 or len(headline) + 1 + len(" ".join(beats)) > MAX_CHARS:
        return ""
    if level == "clean" and NOT_CLEAN.search(f"{headline} {joined}"):
        return ""
    return joined


# --- Headline quips ---

def headlines():
    """[{key, headline, published}] from the feed, in its order, none older than NEWS_MAX_AGE_HOURS."""
    req = urllib.request.Request(FEED, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    cutoff = _now() - datetime.timedelta(hours=NEWS_MAX_AGE_HOURS)
    out = {}
    for item in root.iter("item"):
        title = re.sub(r"\s+", " ", PREFIX.sub("", html.unescape(item.findtext("title") or ""))).strip()
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            published = _now()
        if published.tzinfo is None:
            published = published.replace(tzinfo=datetime.timezone.utc)
        if title and published > cutoff:
            key = "news-" + hashlib.sha1(title.lower().encode()).hexdigest()[:12]
            out.setdefault(key, {"key": key, "headline": title, "published": published.isoformat()})
    return list(out.values())


def quip_headlines():
    data = load(NEWS_FILE)
    cutoff = _now() - datetime.timedelta(hours=NEWS_MAX_AGE_HOURS)
    items = [i for i in data.get("items", []) if datetime.datetime.fromisoformat(i["published"]) > cutoff]
    skipped = list(data.get("skipped", []))
    known = {i["key"] for i in items} | set(skipped)
    fresh = [h for h in headlines() if h["key"] not in known]
    logger.info(f"{len(fresh)} new headlines, {len(items)} kept")
    new = fresh[:MAX_NEW]
    if new:
        listing = "\n".join(f"{n}. {h['headline']}" for n, h in enumerate(new, 1))
        answers = ask(NEWS_SYSTEM, f"Headlines:\n{listing}\n\nOne entry per headline, by its number.", NEWS_SCHEMA)
        for a in _numbered(answers["quips"], len(new)):
            h = new[a["n"] - 1]
            quips = {} if a["skip"] else {level: tidy(a[level], level, h["headline"]) for level in NEWS_LEVELS}
            if any(quips.values()):
                items.append({**h, "quips": quips})
                logger.info(f"{h['headline']} -> " + " | ".join(f"{k}: {v}" for k, v in quips.items()))
            else:
                skipped.append(h["key"])
                logger.info(f"Skipped: {h['headline']}")
    save(NEWS_FILE, {"items": items, "skipped": skipped[-SKIPPED_KEEP:]})   # also drops what's too old


# --- Show riffs ---

def coming_up():
    """[(name, episode)] for the shows coming up on the main channel, in order, from the remote's guide."""
    req = urllib.request.Request(GUIDE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        guide = json.load(r)
    main = next(c for c in guide["channels"] if c["main"])
    out = {}
    for p in main["programs"]:
        if p["id"]:   # not "Off the air"; a marathon's first show is listed as "<name> Marathon"
            out.setdefault(p["name"].removesuffix(" Marathon"), p["episode"])
    return list(out.items())


def riff_shows():
    data = load(SHOW_RIFFS_FILE)
    shows, unknown = dict(data.get("shows", {})), list(data.get("unknown", []))
    aired = set(aired_lately())
    wanted = []
    for name, episode in coming_up():
        entry = shows.get(name)
        if name in unknown or (entry and all(any(riff_key(name, r) not in aired for r in entry.get(level, []))
                                             for level in SHOW_LEVELS)):
            continue   # passed on before, or still has riffs to air at every level
        wanted.append((name, episode, entry))
    logger.info(f"{len(wanted)} shows coming up need riffs, {len(shows)} have some")
    wanted = wanted[:MAX_SHOWS]
    if not wanted:
        return
    lines = []
    for n, (name, episode, entry) in enumerate(wanted, 1):
        lines.append(f"{n}. {name}" + (f" ({episode})" if episode else ""))
        old = [r for level in SHOW_LEVELS for r in (entry or {}).get(level, [])]
        if old:
            lines.append("   these have aired, so write new ones: " + " | ".join(old))
    answers = ask(RIFF_SYSTEM, "Shows:\n" + "\n".join(lines) + "\n\nOne entry per show, by its number.",
                  RIFF_SCHEMA)
    for a in _numbered(answers["shows"], len(wanted)):
        name, _, entry = wanted[a["n"] - 1]
        if a["skip"]:
            if not entry:
                unknown.append(name)
                logger.info(f"Skipped: {name}")
            continue
        entry = dict(entry or {})
        entry["title"] = re.sub(r"\s+", " ", a["title"]).strip().lower() or entry.get("title") or name.lower()
        for level in SHOW_LEVELS:
            new = [t for t in (tidy(r, level) for r in a[level]) if t and t not in entry.get(level, [])]
            entry[level] = (entry.get(level, []) + new)[-RIFFS_KEEP:]
        shows[name] = entry
        logger.info(f"{name} ({entry['title']}) -> " + " | ".join(
            f"{level}: " + " // ".join(entry[level][-RIFFS_PER_LEVEL:]) for level in SHOW_LEVELS))
    save(SHOW_RIFFS_FILE, {"shows": shows, "unknown": unknown[-SKIPPED_KEEP:]})


# --- Pop-Up Video facts ---

def _wiki(**params):
    """A Wikipedia API call, a second after the last one; a 429 is waited out (Retry-After) and tried once more."""
    url = WIKI_API + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    for attempt in range(2):
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt:
                raise
            time.sleep(min(60, int(e.headers.get("Retry-After") or 10)))


def wiki_article(artist, song):
    """(title, text) of the Wikipedia article on the song (one that names the artist), or failing that on the
    artist; None if neither turns up."""
    for query, want, about_song in ((f'"{song}" {artist} song', song, True), (f"{artist} band", artist, False)):
        hits = _wiki(action="query", list="search", srsearch=query, srlimit=5)["query"]["search"]
        for hit in hits:
            title = hit["title"]
            if artist_key(re.sub(r"\s*\([^)]*\)$", "", title)) != artist_key(want):   # "Song (Band song)"
                continue
            page = _wiki(action="query", prop="extracts", explaintext=1, redirects=1, titles=title)["query"]["pages"][0]
            text = page.get("extract", "")
            if about_song and artist_key(artist) not in artist_key(text[:3000]):
                continue   # a different song with the same name
            if len(text) > 400:
                return title, text[:WIKI_CHARS]
    return None


def _fact(text):
    text = re.sub(r"\s+", " ", text).strip()
    return text if 10 < len(text) <= FACT_CHARS else ""


def popup_facts():
    """Pop-Up Video facts for the cached music videos that don't have them yet: each one's Wikipedia
    article (the song's, else the artist's) goes to Claude, which picks the facts from it, filed in
    POPUPS_FILE by meta.popup_key. Videos with no article or nothing to say are remembered as missing."""
    data = load(POPUPS_FILE)
    videos, missing = dict(data.get("videos", {})), list(data.get("missing", []))
    with open(MANIFEST_FILE) as f:
        manifest = json.load(f)
    todo = {}
    for src, e in manifest.items():
        artist, song, _ = music_credit(src) if e.get("category") == "music" else (None, None, None)
        key = popup_key(artist, song) if artist else None
        if key and key not in videos and key not in missing:
            todo.setdefault(key, (artist, song))
    logger.info(f"{len(todo)} cached music videos need pop-up facts, {len(videos)} have them")
    found = []
    for key, (artist, song) in list(todo.items())[:POPUP_VIDEOS]:
        if time.time() - STARTED > JOB_BUDGET - 70:   # leave time for Claude to write what was found
            break
        try:
            article = wiki_article(artist, song)
        except urllib.error.HTTPError as e:
            logger.warning(f"Wikipedia said {e.code}; the rest wait for the next run")
            break
        if article:
            found.append((key, artist, song, *article))
        else:
            missing.append(key)
            logger.info(f"No article for {artist} - {song}")
    for i in range(0, len(found), 5):
        if time.time() - STARTED > JOB_BUDGET:
            logger.info("Out of time; the rest wait for the next run")
            break
        chunk = found[i:i + 5]
        content = "\n\n".join(f"Video {n}: {artist} - {song}\nArticle: {title}\n{text}"
                              for n, (_, artist, song, title, text) in enumerate(chunk, 1))
        answers = ask(POPUP_SYSTEM, content + "\n\nOne entry per video, by its number.", POPUP_SCHEMA)
        for a in _numbered(answers["videos"], len(chunk)):
            key, artist, song, title, _ = chunk[a["n"] - 1]
            facts = [] if a["skip"] else [f for f in map(_fact, a["facts"]) if f][:POPUP_FACTS]
            if facts:
                videos[key] = {"artist": artist, "song": song, "source": title, "facts": facts}
                logger.info(f"{artist} - {song} ({title}): " + " | ".join(facts))
            else:
                missing.append(key)
                logger.info(f"Nothing to say for {artist} - {song}")
        save(POPUPS_FILE, {"videos": videos, "missing": missing[-2000:]})   # after each batch
    save(POPUPS_FILE, {"videos": videos, "missing": missing[-2000:]})


# --- Tonight's sky ---

def sky_tonight():
    """Works out tonight's sky over the house for screens.tonight_sky, once per broadcast day: sunset, the
    moon, the planets out after dark (where and when they're highest) and the ISS passes you can see in the
    next 3 days (lit by the sun while the sky is dark). Times are Unix timestamps."""
    from skyfield import almanac
    from skyfield.api import EarthSatellite, Loader, wgs84
    from skyfield.magnitudelib import planetary_magnitude

    now = datetime.datetime.now().astimezone()
    day = (now - datetime.timedelta(hours=9)).date()   # the broadcast day: until sign-on, it's still last night
    if load(SKY_FILE).get("day") == day.isoformat():
        return
    with open(LOCATION_FILE) as f:
        place = json.load(f)
    loader = Loader(SKYFIELD_DIR, verbose=False)
    ts, eph = loader.timescale(), loader("de421.bsp")
    earth, sun = eph["earth"], eph["sun"]
    here = wgs84.latlon(place["lat"], place["lon"])
    observer = earth + here
    noon = datetime.datetime.combine(day, datetime.time(12)).astimezone()
    end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(SKY_SIGN_OFF_HOUR))
    end = end.astimezone()
    sets, _ = almanac.find_settings(observer, sun, ts.from_datetime(noon), ts.from_datetime(end))
    sunset = sets[0].utc_datetime()

    def dark(t):
        return observer.at(t).observe(sun).apparent().altaz()[0].degrees < -6

    planets = []
    for name, body in PLANETS:
        best = None
        for minutes in range(45, int((end - sunset).total_seconds() // 60), 15):
            t = ts.from_datetime(sunset + datetime.timedelta(minutes=minutes))
            alt, az, _ = observer.at(t).observe(eph[body]).apparent().altaz()
            if alt.degrees > 10 and dark(t) and (best is None or alt.degrees > best[1]):
                best = (t, alt.degrees, az.degrees)
        if best:
            magnitude = float(planetary_magnitude(earth.at(best[0]).observe(eph[body])))
            planets.append({"name": name, "time": best[0].utc_datetime().timestamp(), "alt": round(best[1]),
                            "az": round(best[2]), "mag": round(magnitude, 1)})
    planets.sort(key=lambda p: p["mag"])   # brightest first

    req = urllib.request.Request(ISS_TLE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        tle = r.read().decode().strip().splitlines()
    iss = EarthSatellite(tle[1], tle[2], tle[0].strip(), ts)
    window = ts.from_datetime(sunset), ts.from_datetime(sunset + datetime.timedelta(days=3))
    times, events = iss.find_events(here, *window, altitude_degrees=10.0)
    passes, current = [], {}
    for t, event in zip(times, events):
        alt, az, _ = (iss - here).at(t).altaz()
        key = ("rise", "peak", "set")[event]
        current[key], current[key + "_alt"], current[key + "_az"] = t, alt.degrees, az.degrees
        if event == 2 and "rise" in current and "peak" in current:
            peak = current["peak"]
            evening = peak.utc_datetime().astimezone().hour not in range(SKY_SIGN_OFF_HOUR, 12)   # while on air
            if evening and iss.at(peak).is_sunlit(eph) and dark(peak):
                passes.append({"rise": current["rise"].utc_datetime().timestamp(),
                               "rise_az": round(current["rise_az"]),
                               "peak": peak.utc_datetime().timestamp(), "peak_alt": round(current["peak_alt"]),
                               "set": t.utc_datetime().timestamp(), "set_az": round(az.degrees)})
            current = {}

    evening = ts.from_datetime(datetime.datetime.combine(day, datetime.time(21)).astimezone())
    moon = {"phase": round(almanac.moon_phase(eph, evening).degrees, 1),
            "lit": round(100 * almanac.fraction_illuminated(eph, "moon", evening))}
    save(SKY_FILE, {"day": day.isoformat(), "from": (sunset - datetime.timedelta(minutes=30)).timestamp(),
                    "until": end.timestamp(), "sunset": sunset.timestamp(), "moon": moon, "planets": planets,
                    "iss": passes[:3]})
    logger.info(f"Sky for {day}: sunset {sunset.astimezone():%H:%M}, moon {moon['phase']}° {moon['lit']}% lit, "
                f"planets {[p['name'] for p in planets]}, {len(passes)} visible ISS passes in 3 days")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    failed = False
    for step in (quip_headlines, riff_shows, sky_tonight, popup_facts):
        try:
            step()
        except Exception:   # one step failing doesn't keep the other from running
            logger.exception(f"{step.__name__} failed")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
