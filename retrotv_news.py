"""
Headline quips for the text bumps (retrotv_blurbs): every couple of hours (retrotv-news.timer on the Pi) this
takes the new headlines from UPI's Odd News feed, has Claude write a quip for each at every level a headline
can air at (NEWS_LEVELS, from the bumps' MIX: cheeky and raunchy), and keeps the last NEWS_MAX_AGE_HOURS of
them in NEWS_FILE for the bumps to air.

Odd news only: the bumps are never political, and general news mostly is. Claude also skips any headline about
a death, an injury, a victim or a disaster. This runs in its own venv (~/retrotv_venv, requirements-news.txt)
and the station never imports it, so a failed run only means no new headlines. The API key is on the Pi's SD
card in KEY_FILE, readable only by the station's user; it is never logged.
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

from retrotv_blurbs import LEVELS, MAX_CHARS, MIX, NEWS_FILE, NEWS_MAX_AGE_HOURS, NOT_CLEAN
from retrotv_screens import USER_AGENT

logger = logging.getLogger("RetroTV.news")

FEED = "https://rss.upi.com/news/odd_news.rss"
KEY_FILE = os.path.expanduser("~/.config/retrotv/anthropic.key")
MODEL = "claude-opus-5-5"
MAX_NEW = 12             # headlines sent per run; any more wait for the next one
SKIPPED_KEEP = 500       # headlines Claude passed on, remembered so they aren't sent again
PREFIX = re.compile(r"^(?:look|watch|video|listen|photos?)\s*:\s*", re.I)   # UPI's "Watch: ..." and the like
# The levels a headline can air at: the ones whose MIX has news. A quip is written for each
NEWS_LEVELS = [level for level in LEVELS if any(source == "news" for _, source, _ in MIX[level])]
LEVEL_RULES = {
    "clean": "airs any time, the kids' hours too. No cursing, no innuendo, nothing about sex, drugs or drinking.",
    "cheeky": "airs from 9 PM. Innuendo and mild cursing (damn, hell, ass) are fine.",
    "raunchy": "airs from 11 PM to sign-off. Crude and vulgar are fine, any cursing is fine.",
}

SYSTEM = """You write the text bumps for GL0RYUS Network, a retro TV station that plays old shows and \
commercials on a CRT in someone's living room. Bumps are the short white-on-black lines between shows, in the \
tradition of late-night cartoon blocks: lowercase, deadpan, dry, a little absurd, never trying too hard.

These bumps show a real headline from the odd-news wire, then your quip. For every headline, write one quip \
per level:
""" + "\n".join(f"- {level}: {LEVEL_RULES[level]}" for level in NEWS_LEVELS) + """

At every level: never racist, sexist, homophobic, religious or political, and never punching down. Don't mock \
anyone's looks, body, age or disability, and don't name a private person from the story. The joke is on the \
situation.

Skip a headline (skip: true and empty quips) if it involves a death, a serious injury, abuse, a crime with a \
victim, a disaster, an illness or politics. When in doubt, skip it.

Style:
- lowercase; no emojis, hashtags, "lol" or exclamation marks
- one or two beats, with " / " between them (each beat is shown on screen on its own)
- under 15 words in all
- react to the headline; don't repeat or explain it
- a different joke for each level, not one joke with swearing added

Lines in the station's voice, for flavour (these aren't about news):
somewhere a vcr is still blinking 12:00. / it's been right twice a day for thirty years.
nobody has ever looked cool / carrying a full laundry basket.
a waterbed / is a mattress that wants to get involved.
your ex is watching this too. / probably. / don't text them.
ok but who the fuck asked?"""

SCHEMA = {
    "type": "object",
    "properties": {"quips": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "skip": {"type": "boolean"},
                       **{level: {"type": "string"} for level in NEWS_LEVELS}},
        "required": ["n", "skip", *NEWS_LEVELS],
        "additionalProperties": False,
    }}},
    "required": ["quips"],
    "additionalProperties": False,
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def headlines():
    """[{key, headline, published}] from the feed, newest first as the feed has them, none older than
    NEWS_MAX_AGE_HOURS."""
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


def load():
    """(items, skipped) from NEWS_FILE; nothing yet if it isn't there or can't be read."""
    try:
        with open(NEWS_FILE) as f:
            data = json.load(f)
        return list(data.get("items", [])), list(data.get("skipped", []))
    except (OSError, ValueError, AttributeError):
        return [], []


def save(items, skipped):
    os.makedirs(os.path.dirname(NEWS_FILE), exist_ok=True)
    tmp = NEWS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"updated": _now().isoformat(), "items": items, "skipped": skipped[-SKIPPED_KEEP:]}, f, indent=1)
    os.replace(tmp, NEWS_FILE)   # the station may be reading it


def write_quips(new):
    """Claude's [{n, skip, and a quip per level in NEWS_LEVELS}] for the numbered headlines."""
    with open(KEY_FILE) as f:
        client = anthropic.Anthropic(api_key=f.read().strip(), timeout=180.0)
    listing = "\n".join(f"{n}. {h['headline']}" for n, h in enumerate(new, 1))
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",   # a batch the model declines is re-run on the model Anthropic picks for it
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Headlines:\n{listing}\n\nOne entry per headline, by its number."}],
    )
    usage = response.usage
    logger.info(f"{response.model}: {usage.input_tokens} tokens in, {usage.output_tokens} out "
                f"(request {response._request_id})")
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"no quips: the response stopped on {response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))["quips"]


def tidy(quip, headline, level):
    """The quip as beats joined by " / ", or "" when it won't do at this level: too long to read as a bump,
    or (clean) something in it or the headline the daytime bumps keep off the air."""
    beats = [b.strip() for b in re.sub(r"\s+", " ", quip).lower().split(" / ") if b.strip()]
    text = " / ".join(beats)
    if not beats or len(beats) > 3 or len(headline) + 1 + len(" ".join(beats)) > MAX_CHARS:
        return ""
    if level == "clean" and NOT_CLEAN.search(f"{headline} {text}"):
        return ""
    return text


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    items, skipped = load()
    cutoff = _now() - datetime.timedelta(hours=NEWS_MAX_AGE_HOURS)
    items = [i for i in items if datetime.datetime.fromisoformat(i["published"]) > cutoff]
    known = {i["key"] for i in items} | set(skipped)
    fresh = [h for h in headlines() if h["key"] not in known]
    logger.info(f"{len(fresh)} new headlines, {len(items)} kept")
    new = fresh[:MAX_NEW]
    if new:
        try:
            answers = write_quips(new)
        except anthropic.APIError as e:
            save(items, skipped)
            logger.error(f"Claude API: {e}")
            sys.exit(1)
        done = set()
        for a in answers:
            if not 1 <= a["n"] <= len(new) or a["n"] in done:
                continue
            done.add(a["n"])
            h = new[a["n"] - 1]
            quips = {} if a["skip"] else {level: tidy(a[level], h["headline"], level) for level in NEWS_LEVELS}
            if any(quips.values()):
                items.append({**h, "quips": quips})
                logger.info(f"{h['headline']} -> " + " | ".join(f"{k}: {v}" for k, v in quips.items()))
            else:
                skipped.append(h["key"])
                logger.info(f"Skipped: {h['headline']}")
    save(items, skipped)   # also drops what's too old, when nothing was new


if __name__ == "__main__":
    main()
