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
import urllib.parse
import urllib.request

logger = logging.getLogger("RetroTV")

HOME_DIR = "/home/gl0ryus"
ASSETS_DIR = os.path.join(HOME_DIR, "retrotv_assets")
CARDS_DIR = os.path.join(ASSETS_DIR, "cards")      # generated .ass scripts
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")      # Archivo Black + Barlow Condensed (OFL)
MUSIC_DIR = os.path.join(ASSETS_DIR, "music")      # synthesized beds and stings
ID_BACKGROUND = os.path.join(ASSETS_DIR, "bumper_512.mp4")  # the bumper at render size, for station IDs
WEATHER_FILE = os.path.join(HOME_DIR, "retrotv_weather.json")

W, H = 1024, 600
RENDER = "512x300"
BACKGROUND = "0x16110d"   # the bumper's warm near-black
WEATHER_BACKGROUND = "0x1a2378"

# Two names on screen: the channel (guides, info screens, the break cards) and the network behind it, as
# on the bumper and the corner bug (station IDs, sign-off, emergency alerts, holiday greetings)
CHANNEL = "G|TV"
NETWORK = "GL0RYUS NETWORK"

# ASS colours are &HAABBGGRR
CREAM = "&H00D2E6EF&"
MUSTARD = "&H003AB3E8&"
SHADOW_RED = "&H001F2A8A&"
BLACK = "&H00000000&"
STRIPES = ("&H003DB3F2&", "&H002A7FEE&", "&H00274FDD&", "&H002A32B8&")  # yellow, orange, red-orange, red
WHITE = "&H00FFFFFF&"
YELLOW = "&H004AE1FF&"
SKY = "&H00FFD8A8&"
PANEL = "&H00963523&"
HEADER = "&H006E1E3C&"
TICKER = "&H003D0F0A&"
ORANGE_RULE = "&H002C8AF0&"

# The program guide's browns, the same as the remote's listings on retrotv.local (retrotv_remote.html)
GUIDE_BAND = "&H0017202B&"   # --panel  #2b2017
GUIDE_TIME = "&H00122A3D&"   # the main channel's column  #3d2a12
GUIDE_CELL = "&H00143A5C&"   # the main channel's shows  #5c3a14
GUIDE_NOW = "&H00164D7A&"    # ... and the one on now  #7a4d16
GUIDE_EDGE = "&H0032527A&"   # --edge  #7a5232
TAN = "&H0088ADC4&"          # --sky  #c4ad88, the listings' episode lines

# Holiday colours, for the station IDs (retrotv_events) and the cards' holiday looks (LOOKS below)
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

    def write(self, kind):
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
        for layer, start, end, body in self.events:
            lines.append(f"Dialogue: {layer},{ts(start)},{ts(end)},Default,,0,0,0,,{body}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path


def rect(w, h):
    return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"


def slanted_bar(w, h, slant):
    """A bar with a slanted right end, like the stripes in the bumper."""
    return f"m 0 0 l {w} 0 l {w - slant} {h} l 0 {h}"


def circle(r):
    """A circle in the box 0,0 .. 2r,2r."""
    k = 0.5523 * r
    c, e = r, 2 * r
    return (f"m {c:.1f} 0 b {c + k:.1f} 0 {e:.1f} {c - k:.1f} {e:.1f} {c:.1f} "
            f"b {e:.1f} {c + k:.1f} {c + k:.1f} {e:.1f} {c:.1f} {e:.1f} "
            f"b {c - k:.1f} {e:.1f} 0 {c + k:.1f} 0 {c:.1f} "
            f"b 0 {c - k:.1f} {c - k:.1f} 0 {c:.1f} 0")


def rounded(w, h, r):
    """A rectangle with rounded corners, like the remote's listings cells."""
    k = 0.5523 * r
    return (f"m {r} 0 l {w - r} 0 b {w - r + k:.1f} 0 {w} {r - k:.1f} {w} {r} l {w} {h - r} "
            f"b {w} {h - r + k:.1f} {w - r + k:.1f} {h} {w - r} {h} l {r} {h} b {r - k:.1f} {h} 0 {h - r + k:.1f} 0 {h - r} "
            f"l 0 {r} b 0 {r - k:.1f} {r - k:.1f} 0 {r} 0")


def stripes(s, start, end, x, y, width, bar=9, gap=5, slide=True, fade=(200, 250), colors=STRIPES):
    """The four network stripes, sliding in from the left. More colours (Pride's six) share the same height."""
    k = min(1.0, 4 / len(colors))
    for i, color in enumerate(colors):
        by = round(y + i * (bar + gap) * k)
        extra = f"\\move({x - width - 60:.0f},{by},{x},{by},0,{350 + i * 60})" if slide else ""
        s.shape(start, end, x, by, slanted_bar(width - i * 14, round(bar * k), round((bar + 6) * k)), color,
                fade=fade, extra=extra)


def stripe_band(s, start, end, y, colors=STRIPES, x=0, width=W, fade=(300, 400), layer=1):
    """The four stripes straight across, like the band over the remote's listings."""
    k = min(1.0, 4 / len(colors))
    for i, color in enumerate(colors):
        s.shape(start, end, x, round(y + i * 6 * k), rect(width, max(2, round(4 * k))), color, fade=fade, layer=layer)


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
WINTER_BLUE = "&H00C8A070&"
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
    """How a card dresses: its background (0xRRGGBB), the stripes (four, or Pride's six), the kicker colour,
    the headline and its shadow, the info screens' header rule, and props drifting over it in a holiday
    season. title: the day's or month's name, for the lines that mention it."""
    def __init__(self, bg, stripes, accent, headline, shadow, rule, props=None, title=""):
        self.bg, self.stripes, self.accent, self.headline = bg, stripes, accent, headline
        self.shadow, self.rule, self.props, self.title = shadow, rule, props, title

    def dress(self, s, start, end, clip="", layer=0, solid=False):
        """The look's props, if any: on the bottom layer, behind everything, unless told otherwise.
        clip: an ASS \\clip that keeps them in one area. solid: no see-through (the station IDs)."""
        if self.props:
            self.props(s, start, end, random.Random(), clip, layer, solid)


HOUSE = Look(BACKGROUND, STRIPES, MUSTARD, CREAM, SHADOW_RED, ORANGE_RULE)
_PATRIOTIC = ("0x0a0e24", (GOLD, GLORY_RED, WHITE, LIGHT_BLUE), GOLD, CREAM, GLORY_RED, GLORY_RED, _stars)
# The holiday seasons and days the station dresses up for (retro_station's HOLIDAYS and HOLIDAY_IDS)
LOOKS = {
    "halloween": Look("0x140a1c", (PUMPKIN, ORANGE, "&H00C04A8E&", SLIME), ORANGE, CREAM, PURPLE, ORANGE, _bats,
                      "Halloween"),
    "christmas": Look("0x0b1a10", (GOLD, XMAS_RED, CREAM, MARDI_GREEN), GOLD, WHITE, XMAS_RED, XMAS_RED, _snow,
                      "Christmas"),
    "thanksgiving": Look("0x1a1008", (MUSTARD, PUMPKIN, MAPLE, RUST), PUMPKIN, CREAM, RUST, PUMPKIN, _leaves,
                         "Thanksgiving"),
    "valentines": Look("0x1c0a14", (BLUSH, PINK, ROSE, "&H00501CA0&"), PINK, CREAM, ROSE, PINK, _hearts,
                       "Valentine's Day"),
    "stpatricks": Look("0x0a1a0e", (GOLD, CLOVER, KELLY, "&H00307A18&"), GOLD, CREAM, KELLY, CLOVER, _shamrocks,
                       "St. Patrick's Day"),
    "easter": Look("0x1c1626", (PASTELS[3], PASTELS[0], PASTELS[1], PASTELS[2]), PASTELS[3], WHITE, LILAC_SHADOW,
                   PASTELS[0], _eggs, "Easter"),
    "july4": Look(*_PATRIOTIC, "Independence Day"),
    "newyearsday": Look("0x0a0a1a", (GOLD, SILVER, YELLOW, SILVER), GOLD, WHITE, GLORY_BLUE, GOLD, _confetti,
                        "New Year's Day"),
    "mlkday": Look("0x0c1020", (GOLD, CREAM, STEEL, NAVY), GOLD, CREAM, GLORY_BLUE, GOLD, None,
                   "Martin Luther King Jr. Day"),
    "groundhog": Look("0x101418", (WHITE, SKY, WINTER_BLUE, SLATE), SKY, WHITE, SLATE, SKY, _snow, "Groundhog Day"),
    "mardigras": Look("0x140a1e", (GOLD, MARDI_GREEN, "&H00C04A8E&", PURPLE), GOLD, CREAM, PURPLE, MARDI_GREEN,
                      _beads, "Mardi Gras"),
    "aprilfools": Look("0x0a1a1a", tuple(reversed(STRIPES)), SLIME, CREAM, LIGHT_BLUE, SLIME, _questions,
                       "April Fools' Day"),
    "cincodemayo": Look("0x1a0c14", (PAPEL_COLORS[4], ORANGE, PAPEL_COLORS[0], PAPEL_COLORS[2]), PAPEL_COLORS[3],
                        CREAM, PAPEL_COLORS[0], ORANGE, _papel, "Cinco de Mayo"),
    "mothersday": Look("0x1c0a16", (BLUSH, PINK, LILAC, ROSE), PINK, CREAM, WINE, PINK, _flowers, "Mother's Day"),
    "memorialday": Look(*_PATRIOTIC, "Memorial Day"),
    "fathersday": Look("0x0a1420", (MUSTARD, STEEL, NAVY, SLATE), MUSTARD, CREAM, DEEP_NAVY, STEEL, _ties,
                       "Father's Day"),
    "juneteenth": Look("0x0c0e22", (CREAM, GLORY_RED, CREAM, LIGHT_BLUE), GOLD, CREAM, GLORY_RED, GLORY_RED,
                       _white_stars, "Juneteenth"),
    "laborday": Look(*_PATRIOTIC, "Labor Day"),
    "veteransday": Look(*_PATRIOTIC, "Veterans Day"),
}
# The heritage months (retro_station's MONTHS), kept subtle: their stripes and name on Right Back, their name
# on every other station ID, and on Today in History and Pop Quiz when those have something on the subject.
# A holiday inside the month takes over on its day
MONTH_LOOKS = {
    "blackhistory": Look(BACKGROUND, (GOLD, "&H002E10C8&", "&H00449A00&", MOSS), GOLD, CREAM, SHADOW_RED,
                         "&H002E10C8&", None, "Black History Month"),
    "womenshistory": Look(BACKGROUND, (GOLD, CREAM, LILAC, "&H00A03C78&"), LILAC, CREAM, SHADOW_RED,
                          "&H00A03C78&", None, "Women's History Month"),
    "aapi": Look(BACKGROUND, (GOLD, "&H005070F0&", "&H002828C8&", "&H0078A030&"), "&H005070F0&", CREAM, SHADOW_RED,
                 "&H0078A030&", None, "AAPI Heritage Month"),
    "pride": Look(BACKGROUND, ("&H000303E4&", "&H00008CFF&", "&H0000EDFF&", "&H00268000&", "&H00E06F3A&",
                               "&H00C04F9B&"), "&H00008CFF&", CREAM, SHADOW_RED, "&H00C04F9B&", None, "Pride Month"),
    "hispanic": Look(BACKGROUND, (MUSTARD, ORANGE, "&H002828D0&", "&H00508C20&"), ORANGE, CREAM, SHADOW_RED,
                     "&H00508C20&", None, "Hispanic Heritage Month"),
    "nativeamerican": Look(BACKGROUND, ("&H0080C0E0&", "&H003C64C8&", "&H00B0C030&", "&H00283C96&"), "&H00B0C030&",
                           CREAM, SHADOW_RED, "&H003C64C8&", None, "Native American Heritage Month"),
}


def look(season):
    """The look for a holiday season or day, or a heritage month (None: the house look)."""
    return LOOKS.get(season) or MONTH_LOOKS.get(season) or HOUSE


def info_header(s, d, title_pages, now, band, kicker=CHANNEL, season=None, ticking=True):
    """The info screens' header: a band with a rule under it, the kicker, a title per page
    [(start, end, text)] and, with ticking, the clock and date on the right."""
    lk = look(season)
    fade = (300, 400)
    s.shape(0, d, 0, 0, rect(W, 168), band, fade=fade, layer=1)
    s.shape(0, d, 0, 168, rect(W, 6), lk.rule, fade=fade, layer=1)
    # Long kickers and titles (a heritage month's) shrink to keep clear of the clock or the quiz's dial
    s.text(0, d, 80, 82, kicker, BARLOW_X, fit(kicker, BARLOW_X, 30, 680, spacing=6), lk.accent, align=4, spacing=6,
           fade=fade)
    for a, b, title in title_pages:
        s.text(a, b, 80, 130, title, BARLOW_X, fit(title, BARLOW_X, 56, 800), YELLOW, align=4, shadow=3,
               shadow_color=BLACK, fade=(300, 300))
    if ticking:
        s.text(0, d, 944, 132, now.strftime("%a %b %-d").upper(), BARLOW_S, 32, SKY, align=6, fade=fade)
        for sec in range(int(d) + 1):
            t = now + datetime.timedelta(seconds=sec)
            s.text(sec, min(d, sec + 1), 944, 88, clock(t), BARLOW_X, 44, WHITE, align=6, fade=None)


def lavfi_url(ass_path, duration, music=None, background=BACKGROUND, video_source=None, audio_source=None,
              fade_out=True, loop=False):
    """An mpv URL that renders the script over a background, with an audio bed trimmed to length (looped
    when the bed is shorter than the card)."""
    d = f"{duration:.2f}"
    if video_source:
        video = f"movie=filename={video_source},scale={RENDER.replace('x', ':')},setsar=1"
    else:
        video = f"color=c={background}:s={RENDER}:r=30:d={d}"
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


# --- Cards ---

def right_back(season=None):
    """season: a holiday, or a heritage month, which shows in the stripes and the line under them."""
    d = 3.5
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    s.text(0, d, 512, 205, "WE'LL BE", ARCHIVO, 62, lk.headline, shadow=5, shadow_color=lk.shadow, slant=-0.15)
    s.text(0.1, d, 512, 295, "RIGHT BACK", ARCHIVO, 104, lk.headline, shadow=6, shadow_color=lk.shadow, slant=-0.15)
    stripes(s, 0, d, 190, 362, 640, colors=lk.stripes)
    footer = f"{CHANNEL} · {lk.title.upper()}" if season in MONTH_LOOKS else CHANNEL
    s.text(0.4, d, 512, 470, footer, BARLOW_X, fit(footer, BARLOW_X, 44, 900, spacing=10), lk.accent, spacing=10)
    return Card(lavfi_url(s.write("rightback"), d, music("sting_break.ogg"), background=lk.bg), d, "card")


def back_to(name, season=None):
    d = 3.5
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    s.text(0, d, 512, 190, "NOW BACK TO", BARLOW_X, 50, lk.accent, spacing=6)
    name = clip_text(name.upper(), 34)
    s.text(0.1, d, 512, 285, name, ARCHIVO, fit(name, ARCHIVO, 88, 860), lk.headline, shadow=6,
           shadow_color=lk.shadow, slant=-0.15)
    stripes(s, 0, d, 190, 350, 640, colors=lk.stripes)
    s.text(0.4, d, 512, 470, CHANNEL, BARLOW_X, 44, lk.accent, spacing=10)
    return Card(lavfi_url(s.write("backto"), d, music("sting_back.ogg"), background=lk.bg), d, "card")


def up_next(show, later=None, later_time=None, duration=7.0, season=None):
    """show and later are (name, episode) pairs; later_time is a datetime. The duration stretches
    a few seconds either way so the next show starts right on the half hour."""
    d = round(duration, 2)
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    s.text(0, d, 90, 140, "UP NEXT", ARCHIVO, 84, lk.headline, align=4, shadow=6, shadow_color=lk.shadow, slant=-0.15)
    stripes(s, 0, d, 520, 118, 440, colors=lk.stripes)
    name, episode = show
    name = clip_text(name, 38)
    s.text(0.3, d, 92, 258, name, BARLOW_X, fit(name, BARLOW_X, 84, 840), lk.headline, align=4, shadow=3,
           shadow_color=lk.shadow)
    if episode:
        s.text(0.5, d, 94, 325, clip_text(episode, 52), BARLOW_S, 44, lk.accent, align=4)
    if later:
        s.shape(0.8, d, 90, 392, rect(844, 3), lk.accent)
        s.text(1.0, d, 92, 450, "LATER", BARLOW_X, 42, lk.accent, align=4, spacing=4)
        later_name = clip_text(later[0], 34)
        s.text(1.1, d, 225, 450, later_name, BARLOW_B, fit(later_name, BARLOW_B, 44, 560), align=4)
        if later_time:
            s.text(1.2, d, 934, 450, clock(later_time), BARLOW_X, 44, CREAM, align=6)
    return Card(lavfi_url(s.write("upnext"), d, music("upnext_bed.ogg"), background=lk.bg), d, "card")


def station_id(now, good_morning=False, month=None):
    """The bumper with the time laid over it. In a heritage month, every other hour's says so up top."""
    d = 4.5
    s = Script()
    top, color = ("GOOD MORNING!" if good_morning else "YOU'RE WATCHING"), CREAM
    if month in MONTH_LOOKS and not good_morning and now.hour % 2 == 0:
        top, color = f"CELEBRATING {MONTH_LOOKS[month].title.upper()}", MONTH_LOOKS[month].accent
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else clock(now)
    s.text(1.2, 4.1, 512, 92, top, BARLOW_X, fit(top, BARLOW_X, 46, 940, spacing=8), color, spacing=8, shadow=3,
           fade=(300, 250))
    s.text(1.8, 4.1, 512, 505, bottom, ARCHIVO, 44 if good_morning else 62, MUSTARD, shadow=4, fade=(300, 250))
    path = s.write("stationid")
    return Card(lavfi_url(path, d, video_source=ID_BACKGROUND, audio_source=ID_BACKGROUND), d, "id")


def sign_off(returns_at, season=None):
    d = 16.0
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    s.text(0.3, d, 512, 165, "THIS CONCLUDES", BARLOW_X, 54, lk.accent, spacing=6, fade=(600, 1200))
    s.text(0.8, d, 512, 245, "OUR BROADCAST DAY", ARCHIVO, 74, lk.headline, shadow=6, shadow_color=lk.shadow,
           slant=-0.12, fade=(600, 1200))
    stripes(s, 1.2, d, 190, 305, 640, fade=(300, 1200), colors=lk.stripes)
    s.text(3.0, d, 512, 405, f"{NETWORK} RETURNS AT {clock(returns_at)}", BARLOW_S, 42, fade=(600, 1200))
    s.text(6.0, d, 512, 485, "GOOD NIGHT", ARCHIVO, 56, lk.accent, shadow=4, shadow_color=lk.shadow,
           fade=(900, 1200))
    return Card(lavfi_url(s.write("signoff"), d, music("signoff.ogg"), background=lk.bg), d, "card")


def stand_by(season=None):
    d = 32.0
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    s.text(0, d, 512, 160, "PLEASE", ARCHIVO, 70, lk.headline, shadow=5, shadow_color=lk.shadow, slant=-0.15,
           fade=(400, 0))
    s.text(0, d, 512, 250, "STAND BY", ARCHIVO, 112, lk.headline, shadow=6, shadow_color=lk.shadow, slant=-0.15,
           fade=(400, 0))
    stripes(s, 0, d, 190, 320, 640, slide=False, fade=(400, 0), colors=lk.stripes)
    s.text(0, d, 512, 415, "WE'RE EXPERIENCING TECHNICAL DIFFICULTIES", BARLOW_S, 38, lk.accent, fade=(400, 0))
    # A slow blink, like a stuck slide in the master control room
    for i in range(0, int(d), 2):
        s.text(i, i + 1, 512, 480, "●  PROGRAMMING WILL RESUME SHORTLY", BARLOW_B, 32, CREAM, fade=None)
    return Card(lavfi_url(s.write("standby"), d, music("standby_loop.ogg"), fade_out=False, background=lk.bg), d,
                "card")


# --- Program guide ---

GUIDE_TOP, GUIDE_BOTTOM, ROW_H = 232, 548, 96
GUIDE_SPEED = 15.0  # px/s
GUIDE_ROWS = 10


def guide(rows, now, season=None):
    """
    A slow-scrolling listings grid in the style of the early-90s cable guide channels, in the browns of the
    remote's listings on retrotv.local. rows: (start datetime or None, name, detail) tuples, the first (picked
    out in mustard) the show starting right after this break.
    Returns None with nothing to list.
    """
    if not rows:
        return None
    rows = rows[:GUIDE_ROWS]
    hold = 6.0
    distance = len(rows) * ROW_H
    scroll_end = hold + distance / GUIDE_SPEED
    d = round(scroll_end + 3.0, 2)
    fade = (300, 400)
    lk = look(season)
    s = Script()
    info_header(s, d, [(0, d, "Program Guide")], now, band=GUIDE_BAND, season=season)
    lk.dress(s, 0, d, clip="\\clip(0,0,1024,168)", layer=1)   # over the band, under its type
    stripe_band(s, 0, d, 198, colors=lk.stripes, x=60, width=904)

    clip = f"\\clip(60,{GUIDE_TOP},964,{GUIDE_BOTTOM})"
    cell_h = ROW_H - 8
    for i, (start, name, detail) in enumerate(rows):
        y0 = GUIDE_TOP + i * ROW_H
        y1 = y0 - distance

        def mv(x, dy=0):
            return f"\\move({x},{y0 + dy},{x},{y1 + dy},{int(hold * 1000)},{int(scroll_end * 1000)}){clip}"
        # The time in the channel column (with the main channel's mustard edge), the show in a cell beside it
        s.shape(0, d, 60, y0, rounded(186, cell_h, 8), GUIDE_TIME, extra=mv(60), fade=fade)
        s.shape(0, d, 242, y0, rect(4, cell_h), MUSTARD, extra=mv(242), fade=fade, layer=2)
        s.shape(0, d, 254, y0, rounded(710, cell_h, 8), MUSTARD if i == 0 else GUIDE_EDGE, extra=mv(254), fade=fade)
        s.shape(0, d, 256, y0 + 2, rounded(706, cell_h - 4, 7), GUIDE_NOW if i == 0 else GUIDE_CELL,
                extra=mv(256, 2), fade=fade, layer=2)
        s.raw(0, d, f"\\an5\\fn{BARLOW_X}\\fs44\\c{MUSTARD}\\bord0\\shad2\\4c{BLACK}\\fad({fade[0]},{fade[1]})"
              + mv(150, cell_h // 2), esc(clock(start) if start else ""), layer=3)
        name = clip_text(name, 40)
        s.raw(0, d, f"\\an4\\fn{BARLOW_X}\\fs{fit(name, BARLOW_X, 44, 660)}\\c{CREAM}\\bord0\\shad2"
              f"\\4c{BLACK}\\fad({fade[0]},{fade[1]})" + mv(276, 30), esc(name), layer=3)
        if detail:
            s.raw(0, d, f"\\an4\\fn{BARLOW_S}\\fs31\\c{TAN}\\bord0\\shad0\\fad({fade[0]},{fade[1]})" + mv(278, 66),
                  esc(clip_text(detail, 52)), layer=3)
    return Card(lavfi_url(s.write("guide"), d, music("guide_bed.ogg"), loop=True), d, "guide")


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
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def fetch_weather(lat, lon, tz, max_age_minutes=10, stale_minutes=60, timeout=8):
    """Current conditions and a forecast from today on from Open-Meteo, cached on the SD card. The cache only
    stands in for a fetch while it's under max_age_minutes old and from today (after midnight yesterday's
    forecast would show yesterday as today), or for up to stale_minutes when the fetch fails. weather() then
    picks today out by date, so a forecast from late last night still works."""
    cached = None
    try:
        with open(WEATHER_FILE) as f:
            cached = json.load(f)
        if (time.time() - cached["fetched"] < max_age_minutes * 60
                and cached["daily"]["time"][0] == datetime.date.today().isoformat()):
            return cached
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 5,   # today, three more, one spare
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                   "wind_speed_10m,wind_direction_10m,pressure_msl,dew_point_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "sunrise,sunset,uv_index_max",
    })
    try:
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=timeout) as r:
            data = json.load(r)
        data["fetched"] = time.time()
        tmp = WEATHER_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, WEATHER_FILE)
        return data
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
    if cached and time.time() - cached.get("fetched", 0) < stale_minutes * 60:
        return cached
    return None


def _disc(s, start, end, cx, cy, r, color, layer, fade):
    s.shape(start, end, cx - r, cy - r, circle(r), color, fade=fade, layer=layer)


def _icon(s, start, end, kind, x, y, scale=1.0, night=False):
    """Simple weather icons drawn with ASS vector shapes, centred on x, y."""
    def sc(v):
        return round(v * scale)
    fade = (300, 300)
    sun_c, moon_c, cloud_c, dark_cloud = "&H0030C8FF&", "&H00B8E8F4&", "&H00E8E4E0&", "&H00A09890&"
    if kind in ("sun", "partly"):
        cx, cy = (x, y) if kind == "sun" else (x + sc(18), y - sc(18))
        r = sc(34) if kind == "sun" else sc(28)
        if night:
            # A crescent: the moon with a panel-coloured disc over one side
            _disc(s, start, end, cx, cy, r, moon_c, 3, fade)
            _disc(s, start, end, cx + sc(14), cy - sc(10), r, PANEL, 4, fade)
        else:
            for a in range(0, 360, 45):
                s.shape(start, end, cx, cy - r - sc(11), rect(sc(8), sc(10)), "&H0020B4FF&", fade=fade,
                        layer=3, align=5, extra=f"\\org({cx},{cy})\\frz{a}")
            _disc(s, start, end, cx, cy, r, sun_c, 4, fade)
    if kind in ("partly", "cloud", "rain", "snow", "storm"):
        c = dark_cloud if kind in ("rain", "storm", "snow") else cloud_c
        ox, oy = x - sc(4), y + sc(6)
        for dx, dy, r in ((-24, 6, 20), (0, -8, 28), (26, 4, 22)):
            _disc(s, start, end, ox + sc(dx), oy + sc(dy), sc(r), c, 5, fade)
        s.shape(start, end, ox - sc(44), oy + sc(4), rect(sc(92), sc(22)), c, fade=fade, layer=5)
    if kind == "rain":
        drop = f"m {sc(8)} 0 l {sc(14)} 0 l {sc(6)} {sc(22)} l 0 {sc(22)}"
        for dx in (-26, -2, 22):
            s.shape(start, end, x + sc(dx) - sc(4), y + sc(36), drop, "&H00F0A040&", fade=fade, layer=5)
    if kind == "snow":
        for dx in (-26, -2, 22):
            _disc(s, start, end, x + sc(dx), y + sc(49), sc(5), WHITE, 5, fade)
    if kind == "storm":
        bolt = (f"m {sc(12)} 0 l {sc(26)} 0 l {sc(16)} {sc(14)} l {sc(26)} {sc(14)} l {sc(2)} {sc(40)} "
                f"l {sc(10)} {sc(20)} l 0 {sc(20)}")
        s.shape(start, end, x - sc(10), y + sc(30), bolt, "&H0020E0FF&", fade=fade, layer=6)
    if kind == "fog":
        for i, dy in enumerate((-18, 0, 18)):
            s.shape(start, end, x - sc(44) + sc(i * 6), y + sc(dy), rect(sc(80), sc(9)), "&H00C8C0B8&",
                    fade=fade, layer=5)


def weather(data, place, now=None, season=None):
    """Three pages (current conditions, extended forecast, almanac) with a crawl along the bottom. Today is
    the forecast day dated `now` (the time the card airs), never just the first one in the data; None when
    the data has no forecast for today."""
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
        uv = round(daily["uv_index_max"][t])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logger.warning(f"Weather data unusable for {now:%a %b %d}: {e!r}")
        return None

    page, pages = 21.0, 3   # 63 s, inside the 64 s bed
    d = page * pages
    s = Script()
    fade = (300, 300)
    frame_fade = (300, 400)

    # The frame (header, panel and crawl bar) stays up the whole time
    info_header(s, d, [(0, page, "Current Conditions"), (page, 2 * page, "Extended Forecast"),
                       (2 * page, 3 * page, "Almanac")], now, band=HEADER, kicker=f"{CHANNEL} · LOCAL WEATHER",
                season=season)
    s.shape(0, d, 60, 186, rect(904, 292), PANEL, fade=frame_fade, layer=0)
    s.shape(0, d, 0, 488, rect(W, 112), TICKER, fade=frame_fade, layer=0)

    # Page 1: current conditions
    a, b = 0.0, page
    s.text(a, b, 100, 220, place, BARLOW_B, 38, WHITE, align=4, fade=fade, layer=3)
    s.text(a, b, 96, 320, f"{temp}°", ARCHIVO, 132, WHITE, align=4, shadow=4, shadow_color=BLACK,
           fade=fade, layer=3)
    s.text(a, b, 100, 432, cond, BARLOW_X, 46, YELLOW, align=4, fade=fade, layer=3)
    _icon(s, a, b, icon, 420, 315, 1.25, night)
    details = [
        ("Humidity", f"{humidity}%"),
        ("Dewpoint", f"{round(cur['dew_point_2m'])}°"),
        ("Feels Like", f"{round(cur['apparent_temperature'])}°"),
        ("Wind", wind),
        ("Pressure", pressure),
        ("Rain Today", f"{rain_today}%"),
    ]
    for i, (label, value) in enumerate(details):
        y = 222 + i * 44
        s.text(a, b, 560, y, label, BARLOW_B, 36, SKY, align=4, fade=fade, layer=3)
        s.text(a, b, 930, y, value, BARLOW_X, 38, WHITE, align=6, fade=fade, layer=3)

    # Page 2: extended forecast for the next three days
    a, b = page, 2 * page
    for col, i in enumerate(range(t + 1, t + 4)):
        try:
            day = datetime.date.fromisoformat(daily["time"][i])
            dcond, dicon = WMO.get(daily["weather_code"][i], ("", "cloud"))
            hi, lo = round(daily["temperature_2m_max"][i]), round(daily["temperature_2m_min"][i])
            pop = daily["precipitation_probability_max"][i]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        x = 212 + col * 300
        s.text(a, b, x, 220, day.strftime("%a").upper(), BARLOW_X, 50, YELLOW, fade=fade, layer=3)
        _icon(s, a, b, dicon, x, 300, 0.95)
        s.text(a, b, x, 372, dcond, BARLOW_B, fit(dcond, BARLOW_B, 36, 270), WHITE, fade=fade, layer=3)
        s.text(a, b, x - 8, 420, f"{hi}°", BARLOW_X, 44, WHITE, align=6, fade=fade, layer=3)
        s.text(a, b, x + 8, 420, f"{lo}°", BARLOW_X, 44, SKY, align=4, fade=fade, layer=3)
        s.text(a, b, x, 460, f"Rain {pop}%", BARLOW_S, 30, SKY, fade=fade, layer=3)

    # Page 3: almanac
    a, b = 2 * page, 3 * page
    almanac = [
        ("Sunrise", sunrise), ("Sunset", sunset), ("Today's High", f"{today_hi}°"),
        ("Tonight's Low", f"{today_lo}°"), ("UV Index", f"{uv}"), ("Chance of Rain", f"{rain_today}%"),
    ]
    for i, (label, value) in enumerate(almanac):
        col, row = divmod(i, 3)
        x = 100 + col * 440
        y = 236 + row * 80
        s.text(a, b, x, y, label, BARLOW_B, 38, SKY, align=4, fade=fade, layer=3)
        s.text(a, b, x + 380, y, value, BARLOW_X, 42, WHITE, align=6, fade=fade, layer=3)

    # The crawl
    crawl = (f"{place}  ●  Now {temp}°F {cond}  ●  Humidity {humidity}%  ●  Wind {wind}"
             f"  ●  Today: High {today_hi}°, Low {today_lo}°  ●  Chance of rain {rain_today}%"
             f"  ●  Sunset {sunset}")
    one = crawl
    while text_width(crawl, BARLOW_S, 38) < 120.0 * d - 1000:  # round again rather than leave the bar empty
        crawl += "  ●  " + one
    end_x = min(1000 - 120.0 * d, -text_width(crawl, BARLOW_S, 38))  # the whole line crosses in time
    s.raw(0, d, f"\\an4\\move(1010,544,{end_x:.0f},544)\\clip(0,488,{W},{H})\\fn{BARLOW_S}\\fs38"
                f"\\c{WHITE}\\bord0\\shad0\\fad({frame_fade[0]},{frame_fade[1]})", esc(crawl), layer=3)
    return Card(lavfi_url(s.write("weather"), d, music("weather_bed.ogg"), background=WEATHER_BACKGROUND),
                d, "weather")


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
