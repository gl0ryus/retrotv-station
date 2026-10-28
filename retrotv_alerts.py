"""
Emergency alerts for the GL0RYUS Network, aired the way 90s cable did it. National Weather Service
alerts for the station's own spot come from api.weather.gov:
- Warnings for the worst weather and civil emergencies (CUT_IN_EVENTS) cut into whatever is on: the
  data bursts, the two-tone attention signal and a synthesized voice over a full-screen bulletin, then
  the show picks up where it left off.
- Other warnings, and watches, crawl across the top of the picture with a short alert tone. Active ones
  crawl again every so often, and cut-in alerts keep crawling after their cut-in.
- A test bulletin once a week, at a break between shows.
The station stays silent while it's off the air; whatever is still active crawls once it signs back on.

The data bursts (music_src/eas_tones.py) sound like the real header and end-of-message codes, but they
are random bytes with no preamble, so no decoder can read them as an alert.
"""
import datetime
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
import unicodedata
import urllib.request
import wave

import retrotv_cards as cards
from retrotv_cards import (
    ARCHIVO, BARLOW_S, BARLOW_X, BLACK, CREAM, H, HOME_DIR, MUSTARD, ORANGE_RULE, W, WHITE, YELLOW, Card, Script,
    clip_text, clock, fit, lavfi_url, music, rect, text_width,
)
from retrotv_screens import TEXT_FACTOR, USER_AGENT, wrap

logger = logging.getLogger("RetroTV")

STATE_FILE = os.path.join(HOME_DIR, "retrotv_alerts.json")
NWS_URL = "https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
POLL_SECONDS = 60
HEADER_FILE = music("eas_header.ogg")        # half a second of quiet, then three data bursts 1 s apart
ATTENTION_FILE = music("eas_attention.ogg")  # 853 + 960 Hz for 8 s
EOM_FILE = music("eas_eom.ogg")              # three short bursts
VOICE = "kal16"                              # flite's voice closest to a weather radio
AIRED_DAYS = 7                               # how long an aired alert's id is remembered

EAS_BACKGROUND = "0x0a0a0a"
EAS_RED = "&H001C1CB4&"
CRAWL_RED = "&H001A1A9E&"
LABEL_TEXT = "&H00161110&"   # the lower thirds' label ink and underline
UNDERLINE = "&H002A7FEE&"
CRAWL_SPEED = 110.0     # px/s on the 1024-wide canvas
CAPS_CHAR = 0.315       # Barlow Condensed ExtraBold caps: average width per character as a fraction of the size, as rendered

# Interrupt the show. A flash flood warning cuts in too when it's a Flash Flood Emergency
CUT_IN_EVENTS = {
    "Tornado Warning", "Hurricane Warning", "Extreme Wind Warning", "Storm Surge Warning", "Tsunami Warning",
    "Evacuation Immediate", "Civil Emergency Message", "Civil Danger Warning", "Shelter In Place Warning",
    "Nuclear Power Plant Warning", "Radiological Hazard Warning", "Hazardous Materials Warning",
    "Law Enforcement Warning", "Fire Warning", "Earthquake Warning",
}
# Not weather: the crawl says EMERGENCY ALERT instead of WEATHER ALERT
CIVIL_EVENTS = {
    "Evacuation Immediate", "Civil Emergency Message", "Civil Danger Warning", "Shelter In Place Warning",
    "Nuclear Power Plant Warning", "Radiological Hazard Warning", "Hazardous Materials Warning",
    "Law Enforcement Warning", "Fire Warning", "Earthquake Warning", "Child Abduction Emergency", "Blue Alert",
    "Local Area Emergency", "911 Telephone Outage Emergency",
}
# Everything else ending in one of these crawls, except the marine products
CRAWL_SUFFIXES = ("Warning", "Watch", "Emergency")
MARINE_EVENTS = {"Storm Warning", "Gale Warning", "Hurricane Force Wind Warning", "Special Marine Warning",
                 "Heavy Freezing Spray Warning", "Storm Watch", "Gale Watch", "Hurricane Force Wind Watch"}

TEST_TITLE = "Required Weekly Test"
TEST_BODY = ("This is a test of the Emergency Alert System on the GL0RYUS Network. This is only a test. "
             "Had this been an actual emergency, official information and instructions for your area "
             "would have followed the tones. This concludes this test.")
TEST_VOICE = ("This is a test of the Emergency Alert System, on the GLORY US Network. This is only a test. "
              "The tones you just heard are the signals stations use to warn you of severe weather and other "
              "emergencies. Had this been an actual emergency, official information and instructions for your "
              "area would have followed. This concludes this test.")
DEMO_CRAWL = "This is a test of the GL0RYUS Network weather alert crawl. This is only a test."


# --- Text ---

def _ascii(text):
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def _clean(text):
    """NWS products are hard-wrapped with "* WHAT..." bullets; this makes them running sentences."""
    text = _ascii(text).replace("&&", " ").replace("$$", " ")
    text = re.sub(r"\*\s*([A-Z][A-Z /]+?)\.\.\.", lambda m: m.group(1).capitalize() + ": ", text)
    text = re.sub(r"\b([A-Z]{3,})\.\.\.", lambda m: m.group(1).capitalize() + ": ", text)  # HAZARD...Tornado.
    text = re.sub(r"\b(for|in|of|near|until)\.\.\.\s*", r"\1 ", text, flags=re.I)
    text = text.replace("...", ", ")
    text = re.sub(r"(^|\s)\*\s+", r"\1", text)  # leftover bullets
    text = re.sub(r"\b(\d{1,2})(\d{2}) ([AP]M)\b", r"\1:\2 \3", text)  # "415 PM" -> "4:15 PM"
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text, limit):
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + len(sentence) > limit:
            break
        out = f"{out} {sentence}".strip()
    return clip_text(out, limit) if len(out) > limit else out


def _office(sender):
    """'NWS Tampa Bay Ruskin FL' -> 'Tampa Bay Ruskin'."""
    return re.sub(r"\s+[A-Z]{2}$", "", re.sub(r"^NWS\s+", "", sender or "")).strip() or "your area"


def _areas(area):
    return re.sub(r",\s*[A-Z]{2}\b", "", area or "").replace(";", ",").strip()


def _until(ends):
    if not ends:
        return ""
    day = ends.date()
    today = datetime.date.today()
    if day == today:
        return clock(ends)
    if day == today + datetime.timedelta(days=1):
        return f"{clock(ends)} tomorrow"
    return f"{clock(ends)} {ends.strftime('%A')}"


def _speakable(text):
    text = re.sub(r"\b([AP])M\b", r"\1 M", text)
    text = re.sub(r"\b(EDT|EST|CDT|CST)\b", "", text)
    text = re.sub(r"\bmph\b", "miles per hour", text, flags=re.I)
    text = text.replace("NWS", "National Weather Service").replace("GL0RYUS", "GLORY US")
    return re.sub(r"\s+", " ", text).strip()


def _caps_width(text, size):
    return len(text) * size * CAPS_CHAR * 1.06


def _when(stamp):
    """An NWS timestamp as local naive time."""
    if not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


# --- NWS ---

def classify(event, text=""):
    if event in CUT_IN_EVENTS or (event == "Flash Flood Warning" and "FLASH FLOOD EMERGENCY" in text.upper()):
        return "cutin"
    if event.endswith(CRAWL_SUFFIXES) and event not in MARINE_EVENTS and "Marine" not in event:
        return "crawl"
    return None


def _parse(feature):
    p = feature.get("properties") or {}
    if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
        return None
    event = p.get("event") or ""
    description = _clean(p.get("description"))
    kind = classify(event, f"{description} {p.get('headline') or ''}")
    if not kind:
        return None
    return {
        "id": p.get("id") or feature.get("id"),
        "event": event,
        "kind": kind,
        "area": _ascii(p.get("areaDesc")),
        "sender": _ascii(p.get("senderName")),
        "sent": p.get("sent") or "",
        "ends": _when(p.get("ends") or p.get("expires")),
        "description": description,
        "instruction": _clean(p.get("instruction")),
        "refs": [r.get("identifier") for r in p.get("references") or [] if r.get("identifier")],
    }


def fetch(url):
    """The active alerts as GeoJSON, or None when the NWS can't be reached."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        logger.warning(f"Alert fetch failed: {e}")
        return None


class Monitor:
    """What's active, what has cut in already, when crawls are due and when the weekly test is."""

    def __init__(self, lat, lon, test_hours):
        self.url = NWS_URL.format(lat=lat, lon=lon)
        self.test_hours = test_hours
        self.lock = threading.Lock()
        self.active = {}          # id -> alert
        self.last_poll = 0.0
        self.last_crawl = 0.0
        self.crawled_ids = set()
        self.fetch_failed = False
        state = {}
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except (OSError, ValueError):
            pass
        self.aired = state.get("aired", {})   # id -> when it cut in (or arrived while off the air)
        self.next_test = state.get("next_test") or self._schedule_test(first=True)
        self._save()

    def _save(self):
        cutoff = time.time() - AIRED_DAYS * 86400
        self.aired = {k: t for k, t in self.aired.items() if t > cutoff}
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"aired": self.aired, "next_test": self.next_test}, f)
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            logger.warning(f"Could not save alert state: {e}")

    def poll(self):
        if time.time() - self.last_poll < POLL_SECONDS:
            return
        self.last_poll = time.time()
        data = fetch(self.url)
        now = datetime.datetime.now()
        with self.lock:
            if data is not None:
                fresh = {}
                for feature in data.get("features") or []:
                    alert = _parse(feature)
                    if alert:
                        fresh[alert["id"]] = alert
                replaced = {r for a in fresh.values() for r in a["refs"]}
                fresh = {k: a for k, a in fresh.items() if k not in replaced}  # an update replaces its original
                for k in fresh.keys() - self.active.keys():
                    a = fresh[k]
                    logger.info(f"Alert ({a['kind']}): {a['event']} for {a['area']}"
                                + (f" until {_until(a['ends'])}" if a["ends"] else ""))
                for k in self.active.keys() - fresh.keys():
                    logger.info(f"Alert over: {self.active[k]['event']}")
                self.active = fresh
            self.active = {k: a for k, a in self.active.items() if not a["ends"] or a["ends"] > now}

    def _aired(self, alert):
        return alert["id"] in self.aired or any(r in self.aired for r in alert["refs"])

    def new_cut_ins(self):
        with self.lock:
            alerts = [a for a in self.active.values() if a["kind"] == "cutin" and not self._aired(a)]
        return sorted(alerts, key=lambda a: a["sent"])

    def mark_aired(self, alert):
        with self.lock:
            self.aired[alert["id"]] = time.time()
            self._save()

    def hold(self):
        """Off the air: new cut-in alerts never cut in later; they crawl once the station signs on."""
        for alert in self.new_cut_ins():
            logger.info(f"{alert['event']} arrived while off the air; it will crawl at sign-on if still active.")
            self.mark_aired(alert)

    def crawl_due(self, every_minutes):
        """(text, label, first, ids) when a crawl should air now: right away for an alert that hasn't
        crawled yet (first, with the alert tone), otherwise every every_minutes while any are active."""
        with self.lock:
            alerts = sorted(self.active.values(), key=lambda a: a["sent"])
        if not alerts:
            return None
        ids = [a["id"] for a in alerts]
        first = any(k not in self.crawled_ids for k in ids)
        if not first and time.time() - self.last_crawl < every_minutes * 60:
            return None
        civil = any(a["event"] in CIVIL_EVENTS for a in alerts)
        return crawl_text(alerts), ("EMERGENCY" if civil else "WEATHER"), first, ids

    def crawled(self, ids):
        self.crawled_ids |= set(ids)
        self.last_crawl = time.time()

    def test_due(self, when):
        return time.time() >= self.next_test and self.test_hours[0] <= when.hour < self.test_hours[1]

    def test_aired(self):
        self.next_test = self._schedule_test()
        with self.lock:
            self._save()

    def _schedule_test(self, first=False):
        """A random daytime moment about a week out (1-7 days for the very first one)."""
        day = datetime.date.today() + datetime.timedelta(days=random.randint(1, 7) if first else random.randint(6, 8))
        start, end = self.test_hours
        moment = datetime.datetime.combine(day, datetime.time(random.randint(start, end - 1), random.randint(0, 59)))
        logger.info(f"Next weekly alert test: {moment:%a %b %d} around {clock(moment)}")
        return moment.timestamp()


# --- On screen ---

def crawl_text(alerts):
    parts = []
    for a in alerts:
        until = f" until {_until(a['ends'])}" if a["ends"] else ""
        line = f"The National Weather Service has issued a {a['event']} for {clip_text(_areas(a['area']), 90)}{until}."
        detail = _sentences(a["instruction"], 160)
        parts.append(f"{line} {detail}".strip())
    return "   ●   ".join(parts)


def crawl_script(text, label, start):
    """An ASS file for the vf chain: the alert crawl, built like the NEXT / LATER lower thirds (same bar
    height, label box, type and underline, sliding in from the left) but red, full width and along the
    top, with the text crawling under the label from file time `start`. Returns (path, seconds on screen)."""
    text = clip_text(text.upper(), 600)
    top, h = 30, 62
    y = top + h / 2
    tag = max(150, int(text_width(label, ARCHIVO, 40)) + 48)  # the lower thirds' label box width
    box = 40 + tag                                             # its label lines up with theirs at x = 62
    width = _caps_width(text, 46)
    crawl = (W + 10 - (box - 22) + width) / CRAWL_SPEED
    lead = 0.6                                                 # the bar slides in before the text starts
    d = round(lead + crawl + 0.5, 2)
    end = start + d

    def slide(x, yy):
        return f"\\move({x - W},{yy},{x},{yy},0,450)"
    s = Script()
    s.shape(start, end, 0, top, rect(W, h), CRAWL_RED, fade=(0, 300), layer=1, extra=slide(0, top) + "\\1a&H10&")
    s.shape(start, end, 0, top, cards.slanted_bar(box, h, 22), MUSTARD, fade=(0, 300), layer=3,
            extra=slide(0, top))
    s.shape(start, end, 0, top + h + 2, rect(W, 5), UNDERLINE, fade=(0, 300), layer=2, extra=slide(0, top + h + 2))
    s.text(start, end, 62, y, label, ARCHIVO, 40, LABEL_TEXT, align=4, fade=(0, 300), layer=4, extra=slide(62, y))
    s.text(start, end, 0, y, text, BARLOW_X, 46, CREAM, align=4, fade=None, layer=2,
           extra=f"\\move({W + 10},{y},{box - 22 - width:.0f},{y},{lead * 1000:.0f},{(lead + crawl) * 1000:.0f})")
    return s.write("crawl"), d


def crawl_tone(start):
    """An af lavfi string adding three short alert beeps at file time `start`."""
    beeps = "+".join(f"between(t,{start + i * 0.32:.2f},{start + i * 0.32 + 0.2:.2f})" for i in range(3))
    return f"lavfi=[aeval=exprs='val(ch)+0.2*sin(2*PI*1050*t)*({beeps})':c=same]"


def render_audio(voice):
    """Header bursts, attention signal, the voice (flite, filtered like a weather radio) and the
    end-of-message bursts, as one WAV. Returns (path, seconds)."""
    base = cards._next_path("easaudio")[:-len(".ass")]
    txt, wav = base + ".txt", base + ".wav"
    with open(txt, "w") as f:
        f.write(_ascii(_speakable(voice)))
    fmt = "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"
    graph = (f"[0]{fmt}[a];[1]{fmt}[b];"
             f"[2]aresample=48000,highpass=f=280,lowpass=f=3400,loudnorm=I=-16:TP=-3,{fmt},"
             f"adelay=1000:all=1,apad=pad_dur=1.2[c];"
             f"[3]{fmt}[d];[a][b][c][d]concat=n=4:v=0:a=1")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", HEADER_FILE, "-i", ATTENTION_FILE,
                    "-f", "lavfi", "-i", f"flite=textfile={txt}:voice={VOICE}", "-i", EOM_FILE,
                    "-filter_complex", graph, wav], check=True, timeout=120)
    with wave.open(wav) as w:
        seconds = w.getnframes() / w.getframerate()
    return wav, round(seconds + 0.4, 2)


def bulletin(kind, title, area, until, body, crawl, voice):
    """The full-screen alert: a red EMERGENCY ALERT SYSTEM band, the alert in big type, the body in
    pages and a crawl along the bottom, for as long as the tones and voice run."""
    wav, d = render_audio(voice)
    s = Script()
    fade = (300, 500)
    # The info screens' header: a band with an orange rule under it, here in alert red
    s.shape(0, d, 0, 0, rect(W, 112), EAS_RED, fade=fade, layer=0)
    s.shape(0, d, 0, 112, rect(W, 6), ORANGE_RULE, fade=fade, layer=0)
    s.text(0.2, d, 512, 56, "EMERGENCY ALERT SYSTEM", ARCHIVO, 50, CREAM, shadow=4, shadow_color=BLACK,
           slant=-0.15, fade=fade)
    title = clip_text(title.upper(), 30)
    s.text(0.6, d, 512, 192, title, ARCHIVO, fit(title, ARCHIVO, 78, 940), YELLOW, shadow=6, slant=-0.15, fade=fade)
    area = clip_text(area.upper(), 70)
    s.text(1.0, d, 512, 266, area, BARLOW_X, fit(area, BARLOW_X, 44, 940), CREAM, fade=fade)
    if until:
        s.text(1.2, d, 512, 318, until.upper(), BARLOW_X, 40, MUSTARD, spacing=6, fade=fade)
    lines = wrap(body, BARLOW_S, 36, 900, max_lines=12)
    pages = [lines[i:i + 3] for i in range(0, len(lines), 3)]
    span = (d - 1.6) / max(1, len(pages))
    for p, page in enumerate(pages):
        a, b = 1.6 + p * span, 1.6 + (p + 1) * span
        for i, line in enumerate(page):
            s.text(a, b, 512, 380 + i * 42, line, BARLOW_S, 36, WHITE, fade=(250, 250))
    if crawl:
        # The weather screen's ticker: same type and speed, one pass, cut short to fit the bulletin
        width = len(crawl) * 38 * TEXT_FACTOR[BARLOW_S] * 1.05
        travel = W + 10 + width
        if travel / 120.0 > d - 1.5:
            crawl = clip_text(crawl, int(len(crawl) * ((d - 1.5) * 120.0 - W - 10) / width))
            width = len(crawl) * 38 * TEXT_FACTOR[BARLOW_S] * 1.05
            travel = W + 10 + width
        s.shape(0.8, d, 0, 500, rect(W, H - 500), CRAWL_RED, fade=fade, layer=1)
        s.text(1.0, d, 0, 546, crawl, BARLOW_S, 38, WHITE, align=4, fade=None, layer=2,
               extra=f"\\move({W + 10},546,{-10 - width:.0f},546,0,{travel / 120.0 * 1000:.0f})")
    return Card(lavfi_url(s.write(kind), d, wav, background=EAS_BACKGROUND, fade_out=False), d, kind)


def cut_in_card(alert):
    until = f"Until {_until(alert['ends'])}" if alert["ends"] else ""
    office = _office(alert["sender"])
    at = re.search(r"\bAt \d{1,2}:\d{2} [AP]M[^.]*\.", alert["description"])
    voice = (f"The National Weather Service in {office} has issued a {alert['event']} for {_areas(alert['area'])}"
             + (f", until {_until(alert['ends'])}" if alert["ends"] else "") + ". "
             + (at.group(0) + " " if at else "")
             + _sentences(alert["instruction"] or alert["description"], 420)
             + " Stay tuned to this station for further information.")
    body = alert["instruction"] or alert["description"]
    return bulletin("eas", alert["event"], _areas(alert["area"]), until, body, alert["description"], voice)


def test_card():
    return bulletin("eastest", TEST_TITLE, "GL0RYUS Network", "This is only a test", TEST_BODY, None, TEST_VOICE)
