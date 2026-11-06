"""
On-screen graphics for the GL0RYUS Network: title cards, station IDs, the program guide and the
local weather. Each one is an ASS subtitle script drawn by libass inside an mpv "av://lavfi:" URL,
so it plays like any other video and gets the same CRT filter chain and shader as the shows.
Everything is laid out on a 1024x600 canvas (the panel's shape) and rendered at 512x300, which is
all the 300-line CRT stage keeps anyway.
"""
import datetime
import json
import logging
import math
import os
import random
import re
import time

from retrotv import brand, glyphs
from retrotv.brand import circle, rect
from retrotv.paths import ASSETS_DIR, HOME_DIR

logger = logging.getLogger("RetroTV")

CARDS_DIR = os.path.join(ASSETS_DIR, "cards")      # generated .ass scripts
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")      # Archivo Black + Barlow Condensed (OFL)
MUSIC_DIR = os.path.join(ASSETS_DIR, "music")      # synthesized beds and stings
ID_BACKGROUND = os.path.join(ASSETS_DIR, "bumper_512.mp4")  # the bumper at render size, for station IDs
WEATHER_FILE = os.path.join(HOME_DIR, "retrotv_weather.json")

W, H = 1024, 600
# False (the station's look since 2026-11-05): the panel's full 1024x600 through a soft CRT shader (config.HD_SHADER).
# True: the classic CRT, every picture squeezed to 342x200 and drawn by mpv.conf's shader, so the cards render at
# 512x300, all that stage keeps. retrotv.config builds the player's filters from it
CRT = False
# Cards are drawn at 512x300 either way; at full size mpv scales them up. libass redraws every frame on the CPU, and at
# 1024x600 the weather card drew at just real time and the guide at 0.8x on air (2026-11-06), which starved their
# sound and stalled the player; at 512x300 they draw at 2.4-2.7x
RENDER = "512x300"
BACKGROUND = "0x16110d"   # the bumper's warm near-black

# Two names on screen: the channel (guides, info screens, the break cards) and the network behind it, as
# on the bumper and the corner bug (station IDs, sign-off, emergency alerts, holiday greetings)
CHANNEL = "G|TV"
NETWORK = "GL0RYUS NETWORK"

# ASS colours are &HAABBGGRR
CREAM = "&H00D2E6EF&"
MUSTARD = "&H003AB3E8&"
SHADOW_RED = "&H001F2A8A&"
BLACK = "&H00000000&"
WHITE = "&H00FFFFFF&"
YELLOW = "&H004AE1FF&"
SKY = "&H00FFD8A8&"
PANEL = "&H00963523&"


# Holiday colours, for the station IDs (retrotv.events) and the cards' holiday looks (LOOKS below)
ORANGE = "&H001A7AFF&"
PURPLE = "&H008C1A5B&"
SLIME = "&H005DE19B&"
MOON = "&H00B8EEF8&"
XMAS_RED = "&H002828D6&"
XMAS_GREEN = "&H00406E1E&"
GOLD = "&H0037AFD4&"
PINK = "&H00B469FF&"
ROSE = "&H004B1FD6&"
BLUSH = "&H00DCC8FF&"
WINE = "&H00401050&"
KELLY = "&H0040A01E&"
CLOVER = "&H0050C83C&"
MOSS = "&H00205A10&"
PASTELS = ("&H00D9B3FF&", "&H00FFB3C9&", "&H00C8F0B3&", "&H00A0F0FF&", "&H00FFD8A8&")  # pink, lilac, mint, butter, sky
LILAC_SHADOW = "&H00B04F7A&"
GLORY_RED = "&H003422B2&"
GLORY_BLUE = "&H006E3B3C&"
LIGHT_BLUE = "&H00FF9A4A&"
PUMPKIN = "&H003A9CF5&"
MAPLE = "&H002B39C0&"
RUST = "&H001E43A6&"
BROWN = "&H0018345A&"

ARCHIVO = "Archivo Black"
BARLOW_X = "Barlow Condensed ExtraBold"
BARLOW_B = "Barlow Condensed Bold"
BARLOW_S = "Barlow Condensed SemiBold"
# Rough average glyph width as a fraction of the font size, used to shrink long names to fit
WIDTH_FACTOR = {ARCHIVO: 0.78, BARLOW_X: 0.47, BARLOW_B: 0.45, BARLOW_S: 0.43}

_serial = 0


def _next_path(kind):
    """A rotating file name per card, so a card already queued in mpv is never rewritten under it."""
    global _serial
    _serial = (_serial + 1) % 16
    os.makedirs(CARDS_DIR, exist_ok=True)
    return os.path.join(CARDS_DIR, f"{kind}-{_serial}.ass")


# --- ASS helpers ---

def ts(seconds):
    cs = int(round(max(0.0, seconds) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(text):
    """Keeps names from being read as ASS override tags."""
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")")


def clock(t):
    return t.strftime("%I:%M %p").lstrip("0")


def text_width(text, font, size, spacing=0):
    """How wide text sets at \\fs size: measured from the font's own widths (retrotv.glyphs) where it has them."""
    if font in glyphs.WIDTHS:
        return brand.measure(text, font, size * glyphs.UNITS[font] / glyphs.HEIGHT[font], spacing)
    return len(text) * (size * WIDTH_FACTOR[font] + spacing)


def fit(text, font, size, max_width, spacing=0):
    """Shrinks a font size until text roughly fits max_width (never below 60%)."""
    need = text_width(text, font, size, spacing)
    if need <= max_width:
        return size
    return max(int(size * 0.6), int(size * max_width / need))


def clip_text(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"




class Script:
    """Collects ASS events and writes the script file."""
    def __init__(self):
        self.events = []

    def text(self, start, end, x, y, text, font, size, color=CREAM, align=5, shadow=0, outline=0,
             shadow_color=SHADOW_RED, spacing=0, slant=0.0, fade=(250, 250), extra="", layer=2):
        # libass takes the first of \pos and \move, so a \move in extra replaces the position
        pos = "" if "\\move(" in extra else f"\\pos({x:.0f},{y:.0f})"
        tags = (f"\\an{align}{pos}\\fn{font}\\fs{size}\\c{color}\\bord{outline}"
                f"\\shad{shadow}\\4c{shadow_color}\\3c{shadow_color}\\fsp{spacing}")
        if slant:
            tags += f"\\fax{slant}"
        if fade:
            tags += f"\\fad({fade[0]},{fade[1]})"
        self.events.append((layer, start, end, "{" + tags + extra + "}" + esc(text)))

    def shape(self, start, end, x, y, path, color, fade=(250, 250), extra="", layer=1, align=7):
        """Draws a vector path. Paths start at 0,0, since libass aligns a drawing by its bounding box."""
        pos = "" if "\\move(" in extra else f"\\pos({x:.0f},{y:.0f})"
        tags = f"\\an{align}{pos}\\bord0\\shad0\\c{color}"
        if fade:
            tags += f"\\fad({fade[0]},{fade[1]})"
        self.events.append((layer, start, end, "{" + tags + extra + "\\p1}" + path + "{\\p0}"))

    def raw(self, start, end, tags, body, layer=2):
        self.events.append((layer, start, end, "{" + tags + "}" + body))

    def card(self, kind, duration, music=None, card_kind="card", picture=None, **options):
        """This script as a finished screen: written out, laid over its background with its music, as a Card.
        picture: a Picture laid between the events below its layer and the rest (two scripts, then).
        options go to lavfi_url (background=, loop=, fade_out=, ...)."""
        if picture:
            under = self.write(kind + "-under", [e for e in self.events if e[0] < picture.layer])
            over = self.write(kind, [e for e in self.events if e[0] >= picture.layer])
            return Card(lavfi_url(over, duration, music, picture=picture, under=under, **options), duration, card_kind)
        return Card(lavfi_url(self.write(kind), duration, music, **options), duration, card_kind)

    def write(self, kind, events=None):
        path = _next_path(kind)
        lines = [
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{BARLOW_B},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for layer, start, end, body in self.events if events is None else events:
            lines.append(f"Dialogue: {layer},{ts(start)},{ts(end)},Default,,0,0,0,,{body}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path


# --- Holiday props ---

BAT = ("m 28 10 l 33 4 l 36 9 l 44 2 l 56 6 l 52 10 l 46 12 l 40 20 l 34 15 l 28 22 "
       "l 22 15 l 16 20 l 10 12 l 4 10 l 0 6 l 12 2 l 20 9 l 23 4")
HEART = ("m 25 45 b 10 33 0 23 0 13 b 0 5 6 0 13 0 b 19 0 23 4 25 9 b 27 4 31 0 37 0 "
         "b 44 0 50 5 50 13 b 50 23 40 33 25 45")
LEAF = ("m 20 0 l 24 9 l 31 6 l 29 15 l 39 14 l 33 21 l 38 27 l 27 27 l 23 37 l 21 34 l 21 42 l 19 42 "
        "l 19 34 l 17 37 l 13 27 l 2 27 l 7 21 l 1 14 l 11 15 l 9 6 l 16 9")
STAR = "m 20 0 l 25 14 l 40 15 l 28 24 l 32 39 l 20 30 l 8 39 l 12 24 l 0 15 l 15 14"
EGG = "m 16 0 b 26 0 32 14 32 26 b 32 36 25 42 16 42 b 7 42 0 36 0 26 b 0 14 6 0 16 0"
PAPEL = "m 0 0 l 40 0 l 40 26 l 35 31 l 30 26 l 25 31 l 20 26 l 15 31 l 10 26 l 5 31 l 0 26"   # a papel picado flag
CONFETTI = "m 0 0 l 9 0 l 9 15 l 0 15"
QUESTION = ("m 3 13 b 3 5 9 0 17 0 b 26 0 31 6 31 13 b 31 20 26 23 22 26 b 19 28 19 30 19 34 l 13 34 "
            "b 13 28 14 24 19 21 b 23 18 24 16 24 13 b 24 9 21 7 17 7 b 13 7 10 10 10 13 "
            "m 13 38 l 19 38 l 19 44 l 13 44")
TIE = "m 8 0 l 20 0 l 17 8 l 22 36 l 14 46 l 6 36 l 11 8"


def shape_xform(path, fn):
    """Applies fn(x, y) -> (x, y) to every point of a drawing path."""
    tokens = iter(re.findall(r"-?\d+(?:\.\d+)?|[a-z]", path))
    out = []
    for tok in tokens:
        if tok.isalpha():
            out.append(tok)
        else:
            x, y = fn(float(tok), float(next(tokens)))
            out.append(f"{x:.1f} {y:.1f}")
    return " ".join(out)


def _shamrock():
    """Three small hearts around a centre at 30,30 with their points in, and a curved stem."""
    parts = []
    for angle in (0, 120, 240):
        a = math.radians(angle)

        def leaf(x, y, a=a):
            x, y = x * 0.5 - 12.5 + 30, y * 0.5 - 22.5 + 30  # half size, point on the centre
            dx, dy = x - 30, y - 30
            return 30 + dx * math.cos(a) - dy * math.sin(a), 30 + dx * math.sin(a) + dy * math.cos(a)
        parts.append(shape_xform(HEART, leaf))
    parts.append("m 29 30 b 31 40 35 48 42 54 l 39 56 b 32 49 28 41 27 30")
    return " ".join(parts)


SHAMROCK = _shamrock()


def _flower():
    """Five round petals around a middle, centred on 24,24."""
    parts = [shape_xform(circle(9), lambda x, y, a=a: (x - 9 + 24 + 13 * math.cos(a), y - 9 + 24 + 13 * math.sin(a)))
             for a in (math.radians(90 + 72 * k) for k in range(5))]
    parts.append(shape_xform(circle(8), lambda x, y: (x + 16, y + 16)))
    return " ".join(parts)


FLOWER = _flower()


def drift(s, start, end, rnd, path, colors, count, scale=(60, 130), fall=(200, 380), sway=60, spin=40, rise=False,
          alpha="&H00&", layer=6, clip="", extra=""):
    """Props drifting down the screen (or up, with rise) like the Christmas snow, each tumbling a little."""
    for _ in range(count):
        x = rnd.randrange(0, W)
        y = rnd.randrange(0, H + 100) if rise else rnd.randrange(-H // 2, H - 100)
        dy = rnd.randint(*fall) * (-1 if rise else 1)
        size = rnd.randint(*scale)
        turn = rnd.randint(-spin, spin)
        a0 = rnd.randint(-30, 30)
        s.shape(start, end, 0, 0, path, rnd.choice(colors), fade=(200, 250), layer=layer, align=5,
                extra=f"\\move({x},{y},{x + rnd.randint(-sway, sway)},{y + dy})\\fscx{size}\\fscy{size}"
                      f"\\frz{a0}\\t(\\frz{a0 + turn})\\1a{alpha}{clip}{extra}")


def fly_bats(s, start, end, rnd, count, color=BLACK, layer=6, clip="", alpha="&H00&"):
    """Bats flapping across the screen from left to right, climbing as they go."""
    for i in range(count):
        y0 = rnd.randint(330, 520)
        t0 = start + 0.2 + i * 0.55
        flap = "".join(f"\\t({t},{t + 90},\\fscy35)\\t({t + 90},{t + 180},\\fscy100)"
                       for t in range(0, int((end - t0) * 1000), 180))
        scale = rnd.choice((90, 120, 150))
        s.shape(t0, end, 0, 0, BAT, color, fade=(100, 200), layer=layer, align=5,
                extra=f"\\move(-60,{y0},{1100},{y0 - rnd.randint(260, 420)})\\fscx{scale}\\fscy{scale}"
                      f"\\1a{alpha}{flap}{clip}")


def _drifting(path, colors, count, alpha, **kw):
    """A look's props: path drifting by (see drift), see-through by alpha on the cards so type reads over it,
    solid on the station IDs. Called as props(s, start, end, rnd, clip, layer, solid)."""
    def props(s, start, end, rnd, clip, layer, solid):
        drift(s, start, end, rnd, path, colors, count, layer=layer, clip=clip, alpha="&H00&" if solid else alpha, **kw)
    return props


def _bats(s, start, end, rnd, clip, layer, solid):
    fly_bats(s, start, end, rnd, 4, color="&H00C04A8E&", layer=layer, clip=clip, alpha="&H00&" if solid else "&H60&")


# Colours only these looks use
PAPEL_COLORS = ("&H008A33E8&", ORANGE, "&H006AA01F&", "&H00C8C81E&", "&H0030D2FF&")   # pink, orange, green, teal, yellow
SILVER = "&H00D0C8C8&"
MARDI_GREEN = "&H0050A830&"
LILAC = "&H00E0A0C8&"
STEEL = "&H00C88C50&"
NAVY = "&H00785028&"
DEEP_NAVY = "&H00502810&"
SLATE = "&H00805838&"

_snow = _drifting(circle(4), (WHITE,), 50, "&H40&", scale=(50, 125), fall=(150, 330), spin=0, extra="\\blur1")
_leaves = _drifting(LEAF, (PUMPKIN, MAPLE, MUSTARD, RUST), 12, "&H80&", scale=(70, 140), sway=90, spin=120)
_hearts = _drifting(HEART, (PINK, ROSE, BLUSH), 12, "&H70&", scale=(50, 120), fall=(280, 520), sway=50, spin=20,
                    rise=True)
_shamrocks = _drifting(SHAMROCK, (KELLY, CLOVER), 10, "&H70&", scale=(60, 140))
_eggs = _drifting(EGG, PASTELS, 10, "&H60&", scale=(60, 110))
_stars = _drifting(STAR, (GLORY_RED, WHITE, LIGHT_BLUE, GOLD), 14, "&H60&", scale=(50, 110))
_white_stars = _drifting(STAR, (WHITE, CREAM), 8, "&H70&", scale=(40, 90), fall=(80, 160), spin=20)
_confetti = _drifting(CONFETTI, (GOLD, SILVER, YELLOW, WHITE, LIGHT_BLUE, PINK), 40, "&H50&", scale=(60, 120),
                      fall=(250, 450), spin=360)
_beads = _drifting(circle(7), (PURPLE, "&H00C04A8E&", MARDI_GREEN, GOLD), 30, "&H50&", scale=(60, 130),
                   fall=(250, 450), spin=0)
_papel = _drifting(PAPEL, PAPEL_COLORS, 14, "&H60&", scale=(70, 130), fall=(150, 300), sway=80, spin=60)
_questions = _drifting(QUESTION, (SLIME, LIGHT_BLUE, PINK, YELLOW), 10, "&H60&", scale=(60, 120), fall=(200, 380),
                       spin=180, rise=True)
_flowers = _drifting(FLOWER, (PINK, BLUSH, LILAC, ROSE), 12, "&H70&", scale=(50, 110), sway=70, spin=90)
_ties = _drifting(TIE, (STEEL, MUSTARD, NAVY, GLORY_RED), 10, "&H60&", scale=(70, 130), sway=60, spin=70)


class Look:
    """How a card dresses for a holiday season or a heritage month: the G|TV accent it picks from the style guide's
    four (tint), props drifting over it, and title: the day's or month's name, for the lines that mention it."""
    def __init__(self, title="", tint=brand.ORANGE, props=None):
        self.title, self.tint, self.props = title, tint, props

    def dress(self, s, start, end, clip="", layer=0, solid=False):
        """The look's props, if any: on the bottom layer, behind everything, unless told otherwise.
        clip: an ASS \\clip that keeps them in one area. solid: no see-through (the station IDs)."""
        if self.props:
            self.props(s, start, end, random.Random(), clip, layer, solid)


HOUSE = Look()
# The holiday seasons and days the station dresses up for (retrotv.station's HOLIDAYS and HOLIDAY_IDS)
LOOKS = {
    "halloween": Look("Halloween", brand.ORANGE, _bats),
    "christmas": Look("Christmas", brand.TEAL, _snow),
    "thanksgiving": Look("Thanksgiving", brand.ORANGE, _leaves),
    "valentines": Look("Valentine's Day", brand.PINK, _hearts),
    "stpatricks": Look("St. Patrick's Day", brand.TEAL, _shamrocks),
    "easter": Look("Easter", brand.PINK, _eggs),
    "july4": Look("Independence Day", brand.BLUE, _stars),
    "newyearsday": Look("New Year's Day", brand.BLUE, _confetti),
    "mlkday": Look("Martin Luther King Jr. Day", brand.BLUE),
    "groundhog": Look("Groundhog Day", brand.BLUE, _snow),
    "mardigras": Look("Mardi Gras", brand.TEAL, _beads),
    "aprilfools": Look("April Fools' Day", brand.TEAL, _questions),
    "cincodemayo": Look("Cinco de Mayo", brand.PINK, _papel),
    "mothersday": Look("Mother's Day", brand.PINK, _flowers),
    "memorialday": Look("Memorial Day", brand.BLUE, _stars),
    "fathersday": Look("Father's Day", brand.BLUE, _ties),
    "juneteenth": Look("Juneteenth", brand.ORANGE, _white_stars),
    "laborday": Look("Labor Day", brand.BLUE, _stars),
    "veteransday": Look("Veterans Day", brand.BLUE, _stars),
}
# The heritage months (retrotv.station's MONTHS), kept subtle: their name on Right Back, on every other station ID,
# and on Today in History and Pop Quiz when those have something on the subject. A holiday inside the month takes
# over on its day
MONTH_LOOKS = {
    "blackhistory": Look("Black History Month"),
    "womenshistory": Look("Women's History Month"),
    "aapi": Look("AAPI Heritage Month"),
    "pride": Look("Pride Month", brand.PINK),
    "hispanic": Look("Hispanic Heritage Month"),
    "nativeamerican": Look("Native American Heritage Month"),
}


def look(season):
    """The look for a holiday season or day, or a heritage month (None: the house look)."""
    return LOOKS.get(season) or MONTH_LOOKS.get(season) or HOUSE


def info_header(s, d, title_pages, now, kicker=CHANNEL, season=None, ticking=True):
    """The info screens' header, as the guide draws it: the kicker in the accent and the title on yellow, a purple
    shadow under both, a title per page [(start, end, text)]; with ticking, the day and time in a white chip top
    right."""
    brand.header(s, d, title_pages, accent=look(season).tint, kicker=kicker)
    if ticking and now:
        brand.date_chip(s, d, now)


class Picture:
    """A picture laid on a card between its lower layers and the rest (see Script.card): it fills w x h at x, y on
    the canvas (cropped to fit) inside a white border, turned tilt degrees clockwise. inset: pixels to trim off
    each edge first (the lower-third thumbnails come with a frame of their own)."""
    def __init__(self, path, x, y, w, h, layer=3, border=8, tilt=0.0, inset=0):
        self.path, self.x, self.y, self.w, self.h = path, x, y, w, h
        self.layer, self.border, self.tilt, self.inset = layer, border, tilt, inset

    def shadow(self, s, d, offset=10, color=None, fade=(250, 250)):
        """Its hard shadow, drawn under it."""
        b = self.border
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        brand.shape(s, 0, d, self.x - b + offset, self.y - b + offset, rect(self.w + 2 * b, self.h + 2 * b),
                    color or brand.INK, fade=fade, layer=self.layer - 1,
                    extra=f"\\org({cx + offset:.1f},{cy + offset:.1f})\\frz{-self.tilt:g}")

    def graph(self, d):
        """The picture as a lavfi chain at render size, and where it goes: (chain, x, y) for overlay."""
        scale = int(RENDER.split("x")[0]) / W   # canvas to render
        w, h, b = round(self.w * scale), round(self.h * scale), max(1, round(self.border * scale))
        chain = f"movie=filename={self.path},loop=loop=-1:size=1:start=0,setpts=N/(30*TB),trim=duration={d:.2f}"
        if self.inset:
            chain += f",crop=iw-{2 * self.inset}:ih-{2 * self.inset}:{self.inset}:{self.inset}"
        chain += (f",scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
                  f"pad={w + 2 * b}:{h + 2 * b}:{b}:{b}:color=white,format=rgba")
        if self.tilt:
            a = f"{self.tilt}*PI/180"
            chain += f",rotate={a}:c=none:ow=rotw({a}):oh=roth({a})"
        cx, cy = (self.x + self.w / 2) * scale, (self.y + self.h / 2) * scale
        return chain, f"{cx:.1f}-w/2", f"{cy:.1f}-h/2"


def lavfi_url(ass_path, duration, music=None, background=BACKGROUND, video_source=None, audio_source=None,
              fade_out=True, loop=False, picture=None, under=None):
    """An mpv URL that renders the script over a background, with an audio bed trimmed to length (looped
    when the bed is shorter than the card). picture: a Picture laid over the background and the under script."""
    d = f"{duration:.2f}"
    if video_source:
        video = f"movie=filename={video_source},scale={RENDER.replace('x', ':')},setsar=1"
    else:
        video = f"color=c={background}:s={RENDER}:r=30:d={d}"
    if picture:
        if under:
            video += f",ass=filename={under}:fontsdir={FONTS_DIR}"
        chain, x, y = picture.graph(duration)
        video += f"[bg];{chain}[pic];[bg][pic]overlay=x={x}:y={y}"
    video += f",ass=filename={ass_path}:fontsdir={FONTS_DIR},format=yuv420p"
    if audio_source:
        audio = f"amovie=filename={audio_source}"
    elif music and os.path.exists(music):
        audio = f"amovie=filename={music}" + (":loop=0,asetpts=N/SR/TB" if loop else "") + f",apad,atrim=end={d}"
        if fade_out:
            audio += f",afade=t=out:st={max(0.0, duration - 0.6):.2f}:d=0.6"
    else:
        audio = f"anullsrc=r=48000:cl=stereo,atrim=end={d}"
    audio += ",aformat=sample_rates=48000:channel_layouts=stereo"
    return f"av://lavfi:{video}[out0];{audio}[out1]"


def music(name):
    return os.path.join(MUSIC_DIR, name)


class Card:
    def __init__(self, url, duration, kind):
        self.url, self.duration, self.kind = url, duration, kind
        self.follow = None   # a cached file to air right after the card: its game's ad, its song's video


# --- Cards ---

Part = brand.Part


def episode_parts(episode):
    """("S1 E2", "Male Un-Bonding") from "S1 E2 · Male Un-Bonding"; a dated episode's date is its code too."""
    if not episode:
        return None, None
    code, _, rest = episode.partition(" · ")
    if rest:
        return code, rest
    return (code, None) if re.match(r"^S\d+ E\d+$", code) else (None, code)


def _over_programme(s, d, lk):
    """The ground for the cards the guide lays over a programme: the stand-in glow, the season's props and the
    badge in the corner."""
    brand.stage(s, d)
    lk.dress(s, 0, d, layer=1)
    brand.corner_badge(s, 0, d, lk.tint)


def right_back(season=None, name=None):
    """We'll Be Right Back: the pause sign and the words on yellow, WITH MORE and the show under them. season: a
    holiday (its accent and props) or a heritage month (named on a chip below)."""
    d = 3.5
    lk = look(season)
    s = Script()
    _over_programme(s, d, lk)
    month = lk.title.upper() if season in MONTH_LOOKS else None
    top = 600 - 64 - (8 + 40 if name else 0) - (8 + 30 if month else 0) - 60
    slide = (0, 350, 120)
    pause = Part("", brand.ANTON, 40, brand.INK, lk.tint, pad=0, width=58)
    brand.strip(s, 0, d, 56, top, 64, [pause, Part("WE'LL BE RIGHT BACK", brand.ANTON, 46, brand.INK, brand.YELLOW,
                                                   pad=26)], shadow=(6, 6, brand.INK), slide=slide)
    for bx in (72, 89):   # the pause sign, upright in its leaning box
        brand.shape(s, 0, d, bx, top + 17, rect(9, 30), brand.INK, slide=slide, layer=6)
    y = top + 72
    if name:
        name = brand.clip(name.upper(), brand.ANTON, 28, 520)
        brand.strip(s, 0.15, d, 84, y, 40, [Part("WITH MORE", brand.ANTON, 21, brand.YELLOW, brand.INK, pad=14, spacing=0.8),
                                            Part(name, brand.ANTON, 28, brand.WHITE, brand.PURPLE, pad=18, width=220)],
                    shadow=(5, 5, brand.INK), slide=(150, 500, 120))
        y += 48
    if month:
        brand.strip(s, 0.3, d, 98, y, 30, [Part(f"CELEBRATING {month}", brand.ARCHIVO_BLACK, 16, brand.INK, brand.WHITE,
                                                pad=14)], shadow=(4, 4, brand.INK), slide=(300, 650, 120))
    return s.card("rightback", d, music("sting_break.ogg"), background=brand.STAGE_BG)


def back_to(name, season=None, episode=None, year=None):
    """Now Back To: the words on yellow and the show on purple, and under them the episode, its title and its
    year, each on its own part of a strip."""
    d = 3.5
    lk = look(season)
    s = Script()
    _over_programme(s, d, lk)
    code, title = episode_parts(episode)
    under = []
    if code:
        under.append(Part(brand.clip(code.upper(), brand.ANTON, 24, 200), brand.ANTON, 24, brand.YELLOW, brand.INK, pad=14))
    if title:
        under.append(Part(brand.clip(title.upper(), brand.ARCHIVO_BLACK, 18, 460), brand.ARCHIVO_BLACK, 18, brand.INK,
                          brand.WHITE, pad=16))
    if year:
        under.append(Part(str(year), brand.ARCHIVO_BLACK, 18, brand.YELLOW, brand.INK, pad=14))
    top = 600 - 62 - 62 - (8 + 38 if under else 0)
    name = brand.clip(name.upper(), brand.ANTON, 42, 600)
    brand.strip(s, 0, d, 56, top, 62, [Part("NOW BACK TO", brand.ANTON, 30, brand.INK, brand.YELLOW, pad=20),
                                       Part(name, brand.ANTON, 42, brand.WHITE, brand.PURPLE, pad=24, width=380)],
                shadow=(6, 6, brand.INK), slide=(0, 350, 120))
    if under:
        brand.strip(s, 0.15, d, 86, top + 70, 38, under, shadow=(5, 5, brand.INK), slide=(150, 500, 120))
    return s.card("backto", d, music("sting_back.ogg"), background=brand.STAGE_BG)


def wrapped(text, font, em, width, lines=2, least=0.66):
    """text in at most `lines` lines no wider than width, at the largest em (down to least of it) that makes it
    fit; the last line is cut short if it still doesn't. Returns (lines, em)."""
    words_ = text.split()
    size = em
    while True:
        out, line = [], ""
        for w in words_:
            trial = f"{line} {w}".strip()
            if line and brand.measure(trial, font, size) > width:
                out.append(line)
                line = w
            else:
                line = trial
        out.append(line)
        if (len(out) <= lines and all(brand.measure(x, font, size) <= width for x in out)) or size <= em * least:
            break
        size = max(em * least, size - 2)
    if len(out) > lines:
        out = out[:lines - 1] + [" ".join(out[lines - 1:])]
    out[-1] = brand.clip(out[-1], font, size, width)
    return out, size


def up_next(show, later=None, later_time=None, duration=7.0, season=None, starts=None, year=None, art=None):
    """Up Next, full screen: the words in yellow, the show under them in white with its start time, episode and
    year on chips, the episode's title, and LATER along the bottom; the show's picture on the purple side, or the
    badge when it has none. show and later are (name, episode) pairs; later_time and starts are datetimes; art
    is a picture file. The duration stretches a few seconds either way so the next show starts right on time."""
    d = round(duration, 2)
    lk = look(season)
    s = Script()
    brand.split(s, d)
    lk.dress(s, 0, d, layer=2)
    slide = (0, 400, 90)
    brand.words(s, 0, d, 52, 104, "UP NEXT", brand.ANTON, 92, brand.YELLOW, shadow=(6, 6, brand.PURPLE), slide=slide)
    name, episode = show
    lines, em = wrapped(name.upper(), brand.ANTON, 62, 520)
    y = 196
    for i, line in enumerate(lines):
        brand.words(s, 0.15, d, 54, y + i * em * 0.98, line, brand.ANTON, em, brand.WHITE, slide=(150, 550, 90))
    y += (len(lines) - 1) * em * 0.98 + em * 0.5 + 34
    code, title = episode_parts(episode)
    chips = []
    if starts:
        chips.append(Part(clock(starts), brand.ARCHIVO_BLACK, 21, brand.INK, lk.tint, pad=16))
    if code:
        chips.append(Part(code.upper(), brand.ARCHIVO_BLACK, 21, brand.INK, brand.WHITE, pad=16))
    if year:
        chips.append(Part(str(year), brand.ARCHIVO_BLACK, 21, brand.WHITE, pad=14, border=(3, brand.WHITE)))
    cx = 62
    for chip in chips:
        cx += brand.strip(s, 0.3, d, cx, y - 19, 38, [chip], slide=(300, 700, 90)) + 10
    if title:
        brand.words(s, 0.45, d, 58, y + 46, brand.clip(title.upper(), brand.ARCHIVO, 23, 500), brand.ARCHIVO, 23,
                    brand.SILVER, bold=True, slide=(450, 850, 90))
    if later:
        later_name = brand.clip(later[0].upper(), brand.ARCHIVO_BLACK, 20, 300)
        parts = [Part("LATER", brand.ANTON, 28, brand.INK, brand.YELLOW, pad=16),
                 Part(later_name, brand.ARCHIVO_BLACK, 20, brand.INK, brand.WHITE, pad=18)]
        if later_time:
            parts.append(Part(clock(later_time), brand.ARCHIVO_BLACK, 20, brand.WHITE, brand.PURPLE, pad=16))
        brand.strip(s, 0.6, d, 58, 504, 46, parts, shadow=(6, 6, brand.PURPLE), slide=(600, 1000, 90))
    picture = None
    if art:
        picture = Picture(art, 650, 236, 336, 189, layer=4, tilt=3, inset=2 if "/promos/" in art else 0)
        picture.shadow(s, d)
        brand.corner_badge(s, 0, d, lk.tint)
    else:
        w, h = brand.badge_size(96, "id")
        brand.badge(s, 0, d, 818 - w / 2, 330 - h / 2, 96, lk.tint, kind="id", swing=(-14, 600))
    return s.card("upnext", d, music("upnext_bed.ogg"), background=brand.INK_BG, picture=picture)


def id_card(s, d, lk, tagline, em=150.0, top=96, name_at=None):
    """The ID card (the guide's G|TV ID): the dotted purple ground and its bands, the badge swinging in, the
    network's name on ink and a tagline on white. Returns where the tagline chip ends (its bottom)."""
    brand.id_ground(s, d, lk.tint)
    lk.dress(s, 0, d, layer=3, solid=True)
    w, h = brand.badge_size(em, "id")
    brand.badge(s, 0.1, d, W / 2 - w / 2, top, em, lk.tint, kind="id", swing=(-16, 500), layer=8)
    y = name_at or top + h + 34
    name_w = brand.measure(brand.NAME, brand.ANTON, 38, spacing=6.8) + 60
    brand.strip(s, 0.45, d, W / 2 - name_w / 2, y, 56, [Part(brand.NAME, brand.ANTON, 38, brand.WHITE, brand.INK,
                                                            pad=30, spacing=6.8)],
                shadow=(6, 6, brand.YELLOW), slide=(450, 800, -60), layer=14)
    if tagline:
        tag_w = brand.measure(tagline, brand.ARCHIVO_BLACK, 19, spacing=1.2) + 36
        brand.strip(s, 0.75, d, W / 2 - tag_w / 2, y + 66, 32, [Part(tagline, brand.ARCHIVO_BLACK, 19, brand.INK,
                                                                     brand.WHITE, pad=18, spacing=1.2)],
                    slide=(750, 1100, 60), layer=14)
    return y + 98


def station_id(now, good_morning=False, month=None):
    """The station ID: the ID card with the time on its tagline, over the bumper's jingle. In a heritage month,
    every other hour's names the month too."""
    d = 4.5
    s = Script()
    tagline = f"GOOD MORNING! WE'RE ON THE AIR · {clock(now)}" if good_morning else clock(now)
    if month in MONTH_LOOKS and not good_morning and now.hour % 2 == 0:
        tagline = f"CELEBRATING {MONTH_LOOKS[month].title.upper()} · {clock(now)}"
    id_card(s, d, look(month), tagline)
    path = s.write("stationid")
    return Card(lavfi_url(path, d, background=brand.PURPLE_BG, audio_source=ID_BACKGROUND), d, "id")


def sign_off(returns_at, season=None):
    d = 16.0
    lk = look(season)
    s = Script()
    fade = (600, 1200)
    brand.id_ground(s, d, lk.tint, fade=fade)
    lk.dress(s, 0, d, layer=3, solid=True)
    w, h = brand.badge_size(84, "id")
    brand.badge(s, 0.2, d, W / 2 - w / 2, 44, 84, lk.tint, kind="id", swing=(-16, 600), fade=fade, layer=8)
    line = "THIS CONCLUDES OUR BROADCAST DAY"
    lw = brand.measure(line, brand.ANTON, 40) + 56
    brand.strip(s, 0.8, d, W / 2 - lw / 2, 290, 62, [Part(line, brand.ANTON, 40, brand.WHITE, brand.INK, pad=28)],
                shadow=(6, 6, brand.YELLOW), fade=fade, slide=(800, 1200, -60), layer=14)
    back = f"{brand.NAME} RETURNS AT {clock(returns_at)}"
    bw = brand.measure(back, brand.ARCHIVO_BLACK, 21) + 36
    brand.strip(s, 3.0, d, W / 2 - bw / 2, 372, 36, [Part(back, brand.ARCHIVO_BLACK, 21, brand.INK, brand.WHITE,
                                                          pad=18)], fade=fade, layer=14)
    gw = brand.measure("GOOD NIGHT", brand.ANTON, 50) + 56
    brand.strip(s, 6.0, d, W / 2 - gw / 2, 440, 70, [Part("GOOD NIGHT", brand.ANTON, 50, brand.INK, brand.YELLOW,
                                                          pad=28)], shadow=(7, 7, brand.INK), fade=(900, 1200),
                layer=14)
    return s.card("signoff", d, music("signoff.ogg"), background=brand.PURPLE_BG)


def stand_by(season=None):
    d = 32.0
    lk = look(season)
    s = Script()
    fade = (400, 0)
    brand.id_ground(s, d, lk.tint, fade=fade, slide_in=False)
    lk.dress(s, 0, d, layer=3, solid=True)
    w, h = brand.badge_size(84, "id")
    brand.badge(s, 0, d, W / 2 - w / 2, 44, 84, lk.tint, kind="id", fade=fade, layer=8)
    pw = brand.measure("PLEASE STAND BY", brand.ANTON, 60) + 64
    brand.strip(s, 0, d, W / 2 - pw / 2, 280, 84, [Part("PLEASE STAND BY", brand.ANTON, 60, brand.INK, brand.YELLOW,
                                                       pad=32)], shadow=(7, 7, brand.INK), fade=fade, layer=14)
    trouble = "WE'RE EXPERIENCING TECHNICAL DIFFICULTIES"
    tw = brand.measure(trouble, brand.ARCHIVO_BLACK, 20) + 36
    brand.strip(s, 0, d, W / 2 - tw / 2, 390, 36, [Part(trouble, brand.ARCHIVO_BLACK, 20, brand.INK, brand.WHITE,
                                                        pad=18)], fade=fade, layer=14)
    # A slow blink, like a stuck slide in the master control room
    resume = "PROGRAMMING WILL RESUME SHORTLY"
    rw = brand.measure(resume, brand.ARCHIVO_BLACK, 20) + 36 + 30
    for i in range(0, int(d), 2):
        brand.strip(s, i, i + 1, W / 2 - rw / 2, 446, 36, [Part("●", brand.ARCHIVO_BLACK, 20, lk.tint, brand.INK, pad=10),
                                                         Part(resume, brand.ARCHIVO_BLACK, 20, brand.WHITE, brand.INK,
                                                              pad=8)], fade=None, layer=14)
    return s.card("standby", d, music("standby_loop.ogg"), fade_out=False, background=brand.PURPLE_BG)


# --- Program guide ---

GUIDE_TOP, GUIDE_BOTTOM, ROW_H, ROW_GAP = 128, 586, 80, 16   # a row and the gap after it: 96, as the old grid's rows
GUIDE_SPEED = 15.0  # px/s
GUIDE_ROWS = 10


def guide(rows, now, season=None):
    """
    A slow-scrolling listings grid in the style of the early-90s cable guide channels, its rows the guide's lineup
    rows: the time, the show and its episode on leaning ink slabs, the first (the show starting right after this
    break) on white. rows: (start datetime or None, name, detail) tuples.
    Returns None with nothing to list.
    """
    if not rows:
        return None
    rows = rows[:GUIDE_ROWS]
    hold = 6.0
    step = ROW_H + ROW_GAP
    distance = len(rows) * step   # every row scrolls up and away, so the card runs as long as it always has
    scroll_end = hold + distance / GUIDE_SPEED
    d = round(scroll_end + 3.0, 2)
    lk = look(season)
    s = Script()
    brand.split(s, d)
    lk.dress(s, 0, d, layer=2)
    info_header(s, d, [(0, d, "Program Guide")], now, season=season)
    clip = f"\\clip(0,{GUIDE_TOP - 8},{W},{GUIDE_BOTTOM + 10})"
    x, width = 62, 880
    for i, (start, name, detail) in enumerate(rows):
        y0 = GUIDE_TOP + i * step
        first = i == 0
        fill, ink, time_c, detail_c = ((brand.WHITE, brand.INK, brand.PURPLE, brand.PURPLE) if first else
                                       (brand.INK, brand.WHITE, brand.YELLOW, brand.SILVER))

        def mv(px, py):
            return (f"\\move({px:.1f},{py:.1f},{px:.1f},{py - distance:.1f},{int(hold * 1000)},{int(scroll_end * 1000)})"
                    + clip)
        lean = ROW_H * brand.SKEW
        brand.shape(s, 0, d, 0, 0, brand.para(width, ROW_H), brand.INK, layer=3, extra=mv(x - lean / 2 + 7, y0 + 7))
        brand.shape(s, 0, d, 0, 0, brand.para(width, ROW_H), fill, layer=4, extra=mv(x - lean / 2, y0))
        when = clock(start) if start else ""
        _row_words(s, d, x + 22, y0 + ROW_H / 2, when, brand.ARCHIVO_BLACK, 22, time_c, mv)
        name = brand.clip(name.upper(), brand.ANTON, 32, 640)
        _row_words(s, d, x + 170, y0 + (27 if detail else ROW_H / 2), name, brand.ANTON, 32, ink, mv)
        if detail:
            _row_words(s, d, x + 172, y0 + 58, brand.clip(detail.upper(), brand.ARCHIVO, 19, 640), brand.ARCHIVO, 19,
                       detail_c, mv)
    return s.card("guide", d, music("guide_bed.ogg"), loop=True, card_kind="guide", background=brand.INK_BG)


def _row_words(s, d, x, y, text, font, em, color, mv, bold=False):
    """Words on a scrolling row: brand.words' placing, moved with the row by mv(x, top)."""
    top = y + brand.cap_height(font, em) / 2 - brand.ascent(font, em)
    s.raw(0, d, f"\\an7{mv(x, top)}\\fn{font}\\fs{brand.fs(font, em)}\\b{1 if bold else 0}\\c{color}\\bord0\\shad0"
                f"\\fad(250,250)", esc(text), layer=6)


# --- Local weather ---

WMO = {
    0: ("Clear", "sun"), 1: ("Mostly Clear", "sun"), 2: ("Partly Cloudy", "partly"), 3: ("Cloudy", "cloud"),
    45: ("Fog", "fog"), 48: ("Freezing Fog", "fog"),
    51: ("Light Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy Drizzle", "rain"),
    56: ("Freezing Drizzle", "rain"), 57: ("Freezing Drizzle", "rain"),
    61: ("Light Rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy Rain", "rain"),
    66: ("Freezing Rain", "rain"), 67: ("Freezing Rain", "rain"),
    71: ("Light Snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy Snow", "snow"), 77: ("Snow Grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy Showers", "rain"),
    85: ("Snow Showers", "snow"), 86: ("Snow Showers", "snow"),
    95: ("T'Storms", "storm"), 96: ("T'Storms", "storm"), 99: ("T'Storms", "storm"),
}
WEATHER_LABEL_SECONDS = 30 * 60    # older than this, the weather card says what time it's from
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _disc(s, start, end, cx, cy, r, color, layer, fade):
    s.shape(start, end, cx - r, cy - r, circle(r), color, fade=fade, layer=layer)


def _icon(s, start, end, kind, x, y, scale=1.0, night=False, cutout=None, base=0):
    """Simple weather icons drawn with ASS vector shapes, centred on x, y, in the G|TV colours. cutout: the colour
    behind the icon (the moon's crescent is cut with it). base: added to every layer."""
    def sc(v):
        return round(v * scale)
    fade = (300, 300)
    sun_c, rays_c, moon_c, cloud_c, dark_cloud = brand.YELLOW, brand.ORANGE, "&H00C8F0FF&", brand.WHITE, "&H00B0A8A4&"
    if kind in ("sun", "partly"):
        cx, cy = (x, y) if kind == "sun" else (x + sc(18), y - sc(18))
        r = sc(34) if kind == "sun" else sc(28)
        if night:
            # A crescent: the moon with a disc of the background over one side
            _disc(s, start, end, cx, cy, r, moon_c, base + 3, fade)
            _disc(s, start, end, cx + sc(14), cy - sc(10), r, cutout or brand.INK, base + 4, fade)
        else:
            for a in range(0, 360, 45):
                s.shape(start, end, cx, cy - r - sc(11), rect(sc(8), sc(10)), rays_c, fade=fade,
                        layer=base + 3, align=5, extra=f"\\org({cx},{cy})\\frz{a}")
            _disc(s, start, end, cx, cy, r, sun_c, base + 4, fade)
    if kind in ("partly", "cloud", "rain", "snow", "storm"):
        c = dark_cloud if kind in ("rain", "storm", "snow") else cloud_c
        ox, oy = x - sc(4), y + sc(6)
        for dx, dy, r in ((-24, 6, 20), (0, -8, 28), (26, 4, 22)):
            _disc(s, start, end, ox + sc(dx), oy + sc(dy), sc(r), c, base + 5, fade)
        s.shape(start, end, ox - sc(44), oy + sc(4), rect(sc(92), sc(22)), c, fade=fade, layer=base + 5)
    if kind == "rain":
        drop = f"m {sc(8)} 0 l {sc(14)} 0 l {sc(6)} {sc(22)} l 0 {sc(22)}"
        for dx in (-26, -2, 22):
            s.shape(start, end, x + sc(dx) - sc(4), y + sc(36), drop, brand.BLUE, fade=fade, layer=base + 5)
    if kind == "snow":
        for dx in (-26, -2, 22):
            _disc(s, start, end, x + sc(dx), y + sc(49), sc(5), WHITE, base + 5, fade)
    if kind == "storm":
        bolt = (f"m {sc(12)} 0 l {sc(26)} 0 l {sc(16)} {sc(14)} l {sc(26)} {sc(14)} l {sc(2)} {sc(40)} "
                f"l {sc(10)} {sc(20)} l 0 {sc(20)}")
        s.shape(start, end, x - sc(10), y + sc(30), bolt, brand.YELLOW, fade=fade, layer=base + 6)
    if kind == "fog":
        for i, dy in enumerate((-18, 0, 18)):
            s.shape(start, end, x - sc(44) + sc(i * 6), y + sc(dy), rect(sc(80), sc(9)), "&H00C8C0B8&",
                    fade=fade, layer=base + 5)


def weather_as_of(data):
    """When weather data is from, as a clock time, if it's old enough to need saying; otherwise None."""
    as_of = data.get("as_of") or data.get("fetched")
    if as_of and time.time() - as_of > WEATHER_LABEL_SECONDS:
        return clock(datetime.datetime.fromtimestamp(as_of))
    return None


def _value_rows(s, a, b, rows, x=690, y=136, w=270, h=46, gap=10, label_em=17.0, value_em=27.0):
    """Label and value rows on the purple side, like the guide's lineup rows: the label in yellow, the value in
    white, on leaning ink slabs."""
    for i, (label, value) in enumerate(rows):
        top = y + i * (h + gap)
        brand.slab(s, a, b, x, top, w, h, brand.INK, shadow=(6, 6, brand.INK), layer=4, fade=(300, 300))
        brand.words(s, a, b, x + 18, top + h / 2, label.upper(), brand.ARCHIVO_BLACK, label_em, brand.YELLOW,
                    fade=(300, 300), layer=6)
        brand.words(s, a, b, x + w - 18, top + h / 2, value, brand.ANTON, value_em, brand.WHITE, align="right",
                    fade=(300, 300), layer=6)


def _crawl_bar(s, d, label, crawl, tag=None, accent=brand.ORANGE, fade=(300, 400)):
    """The ticker along the bottom, as the guide's score ticker draws it: a yellow rule, the label on the accent,
    the crawl on ink, and tag (the temperature, say) in purple at the right end."""
    top, h = 552, 48
    brand.shape(s, 0, d, 0, top - 4, rect(W, 4), brand.YELLOW, fade=fade, layer=6)
    brand.shape(s, 0, d, 0, top, rect(W, h), brand.INK, fade=fade, layer=6)
    label_w = brand.measure(label, brand.ANTON, 26) + 40
    brand.shape(s, 0, d, 0, top, rect(label_w, h), accent, fade=fade, layer=8)
    brand.words(s, 0, d, 20, top + h / 2, label, brand.ANTON, 26, brand.INK, fade=fade, layer=9)
    right = W
    if tag:
        tag_w = brand.measure(tag, brand.ANTON, 28) + 44
        right = W - tag_w
        brand.shape(s, 0, d, right, top, rect(tag_w, h), brand.PURPLE, fade=fade, layer=8)
        brand.words(s, 0, d, right + 22, top + h / 2, tag, brand.ANTON, 28, brand.WHITE, fade=fade, layer=9)
    em, speed = 22.0, 120.0
    one = crawl
    while brand.measure(crawl, brand.ARCHIVO_BLACK, em) < speed * d - 1000:   # round again rather than leave it empty
        crawl += "   ●   " + one
    width = brand.measure(crawl, brand.ARCHIVO_BLACK, em)
    end_x = min(right - speed * d, -width)   # the whole line crosses in time
    y_top = top + h / 2 + brand.cap_height(brand.ARCHIVO_BLACK, em) / 2 - brand.ascent(brand.ARCHIVO_BLACK, em)
    s.raw(0, d, f"\\an7\\move({right - 10:.0f},{y_top:.1f},{end_x:.0f},{y_top:.1f})\\clip({label_w:.0f},{top},{right:.0f},{top + h})"
                f"\\fn{brand.ARCHIVO_BLACK}\\fs{brand.fs(brand.ARCHIVO_BLACK, em)}\\c{brand.WHITE}\\bord0\\shad0"
                f"\\fad({fade[0]},{fade[1]})", esc(crawl), layer=7)


def weather(data, place, now=None, season=None):
    """Three pages (current conditions, the next three days, the almanac) with a crawl along the bottom. Today is
    the forecast day dated `now` (the time the card airs), never just the first one in the data; None when
    the data has no forecast for today. The backup source has no UV index, so that line is left off."""
    now = now or datetime.datetime.now()
    try:
        cur, daily = data["current"], data["daily"]
        t = daily["time"].index(now.date().isoformat())
        temp = round(cur["temperature_2m"])
        humidity = round(cur["relative_humidity_2m"])
        cond, icon = WMO.get(cur["weather_code"], ("", "cloud"))
        night = not cur.get("is_day", 1)
        wind_dir = COMPASS[int((cur["wind_direction_10m"] % 360) / 22.5 + 0.5) % 16]
        wind = "Calm" if cur["wind_speed_10m"] < 1 else f"{wind_dir} {round(cur['wind_speed_10m'])} mph"
        pressure = f"{cur['pressure_msl'] * 0.02953:.2f} in"
        today_hi, today_lo = round(daily["temperature_2m_max"][t]), round(daily["temperature_2m_min"][t])
        rain_today = daily["precipitation_probability_max"][t]
        sunrise = clock(datetime.datetime.fromisoformat(daily["sunrise"][t]))
        sunset = clock(datetime.datetime.fromisoformat(daily["sunset"][t]))
        uv = daily["uv_index_max"][t] if "uv_index_max" in daily else None
        as_of = weather_as_of(data)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logger.warning(f"Weather data unusable for {now:%a %b %d}: {e!r}")
        return None

    page, pages = 21.0, 3   # 63 s, inside the 64 s bed
    d = page * pages
    lk = look(season)
    s = Script()
    fade = (300, 300)
    brand.split(s, d)
    lk.dress(s, 0, d, layer=2)
    info_header(s, d, [(0, page, f"Conditions at {as_of}" if as_of else "Current Conditions"), (page, 2 * page, "Extended Forecast"),
                       (2 * page, 3 * page, "Almanac")], now, kicker=f"{CHANNEL} WEATHER", season=season)

    # Page 1: current conditions
    a, b = 0.0, page
    brand.words(s, a, b, 60, 138, place.upper(), brand.ARCHIVO_BLACK, 22, brand.WHITE, fade=fade)
    brand.words(s, a, b, 50, 262, f"{temp}°", brand.ANTON, 170, brand.WHITE, shadow=(8, 8, brand.PURPLE), fade=fade)
    brand.strip(s, a, b, 60, 380, 50, [Part(cond.upper(), brand.ANTON, 34, brand.INK, brand.YELLOW, pad=20)],
                shadow=(6, 6, brand.PURPLE), fade=fade)
    _icon(s, a, b, icon, 440, 250, 1.35, night, base=2)
    details = [
        ("Humidity", f"{humidity}%"),
        ("Dewpoint", f"{round(cur['dew_point_2m'])}°"),
        ("Feels Like", f"{round(cur['apparent_temperature'])}°"),
        ("Wind", wind.upper()),
        ("Pressure", pressure.upper()),
        ("Rain Today", f"{rain_today}%"),
    ]
    _value_rows(s, a, b, details, x=690, w=272)
    brand.words(s, a, b, 60, 470, f"TODAY {today_hi}° / {today_lo}°", brand.ANTON, 34, brand.YELLOW, fade=fade)

    # Page 2: the next three days, as the guide's market cards: the day on yellow over an ink body
    a, b = page, 2 * page
    for col, i in enumerate(range(t + 1, t + 4)):
        try:
            day = datetime.date.fromisoformat(daily["time"][i])
            dcond, dicon = WMO.get(daily["weather_code"][i], ("", "cloud"))
            hi, lo = round(daily["temperature_2m_max"][i]), round(daily["temperature_2m_min"][i])
            pop = daily["precipitation_probability_max"][i]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        x, w = 58 + col * 312, 284
        brand.shape(s, a, b, x + 10, 136, rect(w, 386), lk.tint, fade=fade, layer=3)
        brand.shape(s, a, b, x, 126, rect(w, 386), brand.PANEL, fade=fade, layer=4)
        brand.shape(s, a, b, x, 126, rect(w, 70), brand.YELLOW, fade=fade, layer=5)
        brand.words(s, a, b, x + 20, 161, day.strftime("%a").upper(), brand.ANTON, 46, brand.INK, fade=fade, layer=6)
        brand.words(s, a, b, x + w - 20, 161, day.strftime("%b %-d").upper(), brand.ARCHIVO_BLACK, 18, brand.INK,
                    align="right", fade=fade, layer=6)
        _icon(s, a, b, dicon, x + w / 2, 262, 1.0, cutout=brand.PANEL, base=3)
        brand.words(s, a, b, x + w / 2, 352, brand.clip(dcond.upper(), brand.ARCHIVO_BLACK, 20, w - 30),
                    brand.ARCHIVO_BLACK, 20, brand.WHITE, align="center", fade=fade, layer=6)
        brand.words(s, a, b, x + w / 2 - 10, 412, f"{hi}°", brand.ANTON, 56, brand.WHITE, align="right", fade=fade,
                    layer=6)
        brand.words(s, a, b, x + w / 2 + 10, 412, f"{lo}°", brand.ANTON, 56, brand.SILVER, fade=fade, layer=6)
        brand.strip(s, a, b, x + 22, 458, 34, [Part(f"RAIN {pop}%", brand.ARCHIVO_BLACK, 18, brand.INK, lk.tint,
                                                    pad=14)], fade=fade, layer=6)

    # Page 3: the almanac
    a, b = 2 * page, 3 * page
    for i, (label, value, fill) in enumerate((("SUNRISE", sunrise, lk.tint), ("SUNSET", sunset, brand.YELLOW))):
        brand.strip(s, a, b, 60 + i * 290, 150, 54, [Part(label, brand.ANTON, 30, brand.INK, fill, pad=16),
                                                     Part(value, brand.ARCHIVO_BLACK, 24, brand.INK, brand.WHITE, pad=18)],
                    shadow=(6, 6, brand.PURPLE), fade=fade)
    brand.words(s, a, b, 60, 262, "HIGH", brand.ANTON, 30, brand.YELLOW, fade=fade)
    brand.words(s, a, b, 56, 340, f"{today_hi}°", brand.ANTON, 110, brand.WHITE, shadow=(6, 6, brand.PURPLE), fade=fade)
    brand.words(s, a, b, 300, 262, "LOW", brand.ANTON, 30, brand.YELLOW, fade=fade)
    brand.words(s, a, b, 296, 340, f"{today_lo}°", brand.ANTON, 110, brand.SILVER, shadow=(6, 6, brand.PURPLE),
                fade=fade)
    almanac = [("Chance of Rain", f"{rain_today}%")]
    if uv is not None:
        almanac.insert(0, ("UV Index", f"{round(uv)}"))
    almanac.append(("Humidity", f"{humidity}%"))
    _value_rows(s, a, b, almanac, x=690, y=160, w=272)

    # The crawl
    crawl = (f"{place.upper()}   ●   {f'AT {as_of}' if as_of else 'NOW'} {temp}°F {cond.upper()}   ●   HUMIDITY {humidity}%"
             f"   ●   WIND {wind.upper()}   ●   TODAY: HIGH {today_hi}°, LOW {today_lo}°   ●   CHANCE OF RAIN {rain_today}%"
             f"   ●   SUNSET {sunset}")
    _crawl_bar(s, d, "WEATHER", crawl, tag=f"{temp}°F", accent=lk.tint)
    return s.card("weather", d, music("weather_bed.ogg"), background=brand.INK_BG, card_kind="weather")


# --- Titles ---

JUNK = re.compile(
    r"\b(480p|540p|576p|720p|1080p|2160p|web[ -]?dl|webrip|hdtv|sdtv|dvdrip|bluray|bdrip|brrip|"
    r"x264|x265|h ?264|h ?265|hevc|xvid|divx|aac2?|ac3|dd\+?\d|ddp\d|dts|mp3|ntsc|pal|remastered|internal|"
    r"tvrip|dsr|amzn|repack|proper|uncut|standard def)\b.*$", re.I)
EPISODE = re.compile(r"\b(?:s(\d{1,2})[ .]?e(\d{1,3})|(\d{1,2})x(\d{2,3}))\b", re.I)
DATE = re.compile(r"\b((?:19|20)\d\d)[ .\-](\d\d)[ .\-](\d\d)\b|\b(\d{1,2})[ .\-](\d{1,2})[ .\-]((?:19|20)?\d\d)\b")
YEAR = re.compile(r"[ (\[]((?:19|20)\d\d)[)\]]?(?: |$)")
SEASON_DIR = re.compile(r"^(season|series|s)\s*\d+$", re.I)


def _clean(text):
    text = text.replace("_", " ")
    # Dotted scene names ("The.Office.US.S02E02") use dots as spaces
    if text.count(".") >= 3 or (text.count(".") >= 2 and text.count(" ") < 2):
        text = re.sub(r"\.(?=\S)", " ", text)
    text = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", text)
    text = JUNK.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" -.")


def _capitalize(text):
    """Capitalizes all-lowercase names ("the colbert report"); str.title() would give "Chappelle'S"."""
    if text != text.lower():
        return text
    return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))


def _date_text(m):
    try:
        if m.group(1):
            dt = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            year = int(m.group(6))
            year += 2000 if year < 50 else 1900 if year < 100 else 0
            dt = datetime.date(year, int(m.group(4)), int(m.group(5)))
        return dt.strftime("%b %-d, %Y")
    except ValueError:
        return m.group(0)


def _folder_name(path, media_root):
    """The nearest folder above the file that names the show (skips 'Season 2' and the category folder)."""
    rel = os.path.relpath(os.path.dirname(path), media_root).split(os.sep)
    for part in reversed(rel[1:]):  # rel[0] is the category folder
        name = _clean(part)
        if EPISODE.search(name):
            name = EPISODE.split(name)[0].strip(" -")
        name = re.sub(r"\b(season|series)\s*\d+.*$", "", name, flags=re.I).strip(" -")
        name = re.sub(r"\s+S\d{1,2}$", "", name)
        if len(name) > 3 and not SEASON_DIR.match(part):
            return name
    return ""


def parse_title(path, category, media_root):
    """Turns a NAS file name into a (show name, episode) pair for cards and the guide. Best effort."""
    stem = os.path.splitext(os.path.basename(path))[0]
    name, episode = _parse_title(stem, _folder_name(path, media_root), category)
    name = name.strip(" -.") or _clean(stem) or stem
    return _capitalize(name), _capitalize(episode.strip(" -."))


def _parse_title(stem, folder, category):
    text = _clean(stem) or stem
    # Abbreviated folders ("g4vids", "aots") don't make good show names
    if folder and " " not in folder and folder == folder.lower():
        folder = ""

    if category == "movies":
        years = list(YEAR.finditer(text + " "))
        if years and years[-1].start() > 0:  # the last year, so "2001 A Space Odyssey 1968" keeps its name
            m = years[-1]
            return f"{text[:m.start()].strip(' -(')} ({m.group(1)})", ""
        return text, ""

    m = EPISODE.search(text)
    if m:
        season, number = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        name = text[:m.start()].strip(" -.")
        rest = _clean(text[m.end():]).strip(" -")
        episode = f"S{int(season)} E{int(number)}" + (f" · {rest}" if rest else "")
        if len(name) <= 3:
            name = folder or name
        return name or stem, episode

    m = DATE.search(text)
    if m:
        name = text[:m.start()].strip(" -")
        rest = text[m.end():].strip(" -")
        episode = _date_text(m) + (f" · {rest}" if rest else "")
        return (name if len(name) > 3 else folder or name), episode

    if " - " in text:
        name, rest = text.split(" - ", 1)
        if re.match(r"^(episode|ep)\.?\s*\d+$", name, re.I) and folder:
            return folder, text
        return name, rest

    if folder and len(folder) <= 28 and folder.lower() not in text.lower():
        return folder, text
    return text, ""
