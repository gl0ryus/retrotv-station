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
import urllib.request
import xml.etree.ElementTree as ET

import anthropic

from retrotv_blurbs import (LEVELS, MAX_CHARS, MIX, NEWS_FILE, NEWS_MAX_AGE_HOURS, NOT_CLEAN, SHOW_RIFFS_FILE,
                            riff_key, _seen as aired_lately)
from retrotv_screens import USER_AGENT

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
PREFIX = re.compile(r"^(?:look|watch|video|listen|photos?)\s*:\s*", re.I)   # UPI's "Watch: ..." and the like


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


NEWS_SCHEMA = _schema("quips", {level: {"type": "string"} for level in NEWS_LEVELS})
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


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    failed = False
    for step in (quip_headlines, riff_shows):
        try:
            step()
        except Exception:   # one step failing doesn't keep the other from running
            logger.exception(f"{step.__name__} failed")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
