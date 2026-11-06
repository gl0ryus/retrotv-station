"""
The G|TV look, from the network graphics style guide (G TV Network Graphics.html, November 2026): its palette,
its two typefaces and the pieces every card is built from.

The guide draws at 1920x1080; the cards are laid out on 1024x600 and rendered at 512x300, which is all the CRT
stage keeps, so its smaller type is set larger here and its lists are shorter. Its marks, kept throughout:
chunky Anton headlines and heavy Archivo labels; slabs leaning 12 degrees with a hard, offset shadow; the yellow
G/TV badge, tilted; an ink panel beside a dotted purple one, split by a yellow stripe.

Everything draws into a cards.Script (anything with .shape and .raw), so this module imports none of the cards.
"""
import datetime
import math

from retrotv import glyphs

# --- Colours, as ASS writes them (&HAABBGGRR), the guide's hex beside each ---

YELLOW = "&H000AD6FF&"       # #FFD60A
INK = "&H00141011&"          # #111014
PURPLE = "&H00E6215B&"       # #5B21E6
DEEP_PURPLE = "&H00A80F3B&"  # #3B0FA8
WHITE = "&H00FFFFFF&"
GREY = "&H00BDB4AE&"         # #aeb4bd  sources and small print
SILVER = "&H00D2CCC9&"       # #c9ccd2  second lines
PANEL = "&H00291B1E&"        # #1E1B29  a card on the ink
ALERT_RED = "&H002E10C8&"    # #C8102E
ALERT_PINK = "&H006A5AFF&"   # #FF5A6A
# The accent: orange unless a holiday picks one of the guide's others
ORANGE = "&H001457FF&"       # #FF5714
PINK = "&H00882EFF&"         # #FF2E88
TEAL = "&H00A5D612&"         # #12D6A5
BLUE = "&H00FF7B3D&"         # #3D7BFF

# Backgrounds, as lavfi colours
INK_BG = "0x111014"
PURPLE_BG = "0x5b21e6"
STAGE_BG = "0x1a1c21"        # the guide's stand-in for the programme, under the cards made to sit over it

ANTON = "Anton"
ARCHIVO = "Archivo SemiBold"      # the variable Archivo, which names itself by its default weight (600)
ARCHIVO_BLACK = "Archivo Black"   # 800-900

W, H = 1024, 600
MARGIN = 56
SKEW = math.tan(math.radians(12))   # a slab h tall leans h * SKEW from bottom to top
FADE = (250, 250)


# --- Type ---

def fs(font, em):
    """The ASS \\fs that gives font an em (a CSS font-size) of em pixels."""
    return round(em * glyphs.HEIGHT[font] / glyphs.UNITS[font], 1)


def measure(text, font, em, spacing=0.0):
    """How wide text sets, in canvas pixels (kerning left out, so a hair over)."""
    widths = glyphs.WIDTHS[font]
    other = widths.get("n", glyphs.UNITS[font] // 2)
    return sum(widths.get(ch, other) for ch in text) * em / glyphs.UNITS[font] + spacing * len(text)


def shrink(text, font, em, width, spacing=0.0, least=0.6):
    """The em at which text fits width: em itself if it fits, never under least of it."""
    need = measure(text, font, em, spacing)
    return em if need <= width else max(em * least, em * width / need)


def clip(text, font, em, width, spacing=0.0):
    """text, cut short with an ellipsis if it would run past width."""
    text = " ".join(str(text).split())
    if measure(text, font, em, spacing) <= width:
        return text
    while text and measure(text + "…", font, em, spacing) > width:
        text = text[:-1]
    return text.rstrip(" ,.;:-–—·") + "…"


def cap_height(font, em):
    return glyphs.CAP[font] * em / glyphs.UNITS[font]


def ascent(font, em):
    """How far below a line's top its baseline sits."""
    return glyphs.ASCENT[font] * em / glyphs.UNITS[font]


def esc(text):
    """Keeps words from being read as ASS override tags."""
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")")


def at(x, y, slide=None):
    """Where a piece goes: \\pos, or with slide (from ms, to ms, dx[, dy]) a \\move in from dx pixels left (right,
    for a negative dx) and dy pixels up."""
    if not slide:
        return f"\\pos({x:.1f},{y:.1f})"
    t0, t1, dx, *rest = slide
    dy = rest[0] if rest else 0
    return f"\\move({x - dx:.1f},{y - dy:.1f},{x:.1f},{y:.1f},{t0},{t1})"


def words(s, start, end, x, y, text, font, em, color, align="left", spacing=0.0, bold=False, shadow=None,
          fade=FADE, slide=None, layer=5, extra=""):
    """text with its capitals centred on y, starting at x (align left), centred on it or ending at it (right).
    shadow: (dx, dy, colour), a hard offset copy like the guide's text-shadow. Returns its width."""
    size = fs(font, em)
    top = y + cap_height(font, em) / 2 - ascent(font, em)
    an = {"left": 7, "center": 8, "right": 9}[align]
    pos = "" if "\\move(" in extra else at(x, top, slide)
    tags = f"\\an{an}{pos}\\fn{font}\\fs{size}\\b{1 if bold else 0}\\c{color}\\bord0\\fsp{spacing:g}"
    if shadow:
        dx, dy, c = shadow
        tags += f"\\xshad{dx}\\yshad{dy}\\4c{c}"
    else:
        tags += "\\shad0"
    if fade:
        tags += f"\\fad({fade[0]},{fade[1]})"
    s.raw(start, end, tags + extra, esc(text), layer=layer)
    return measure(text, font, em, spacing)


# --- Paths (each starts at 0,0: libass places a drawing by its bounding box) ---

def rect(w, h):
    return f"m 0 0 l {w:g} 0 l {w:g} {h:g} l 0 {h:g}"


def rounded(w, h, r):
    k = 0.5523 * r
    return (f"m {r} 0 l {w - r} 0 b {w - r + k:.1f} 0 {w} {r - k:.1f} {w} {r} l {w} {h - r} "
            f"b {w} {h - r + k:.1f} {w - r + k:.1f} {h} {w - r} {h} l {r} {h} b {r - k:.1f} {h} 0 {h - r + k:.1f} 0 {h - r} "
            f"l 0 {r} b 0 {r - k:.1f} {r - k:.1f} 0 {r} 0")


def circle(r):
    """A circle in the box 0,0 .. 2r,2r."""
    k = 0.5523 * r
    c, e = r, 2 * r
    return (f"m {c:.1f} 0 b {c + k:.1f} 0 {e:.1f} {c - k:.1f} {e:.1f} {c:.1f} "
            f"b {e:.1f} {c + k:.1f} {c + k:.1f} {e:.1f} {c:.1f} {e:.1f} "
            f"b {c - k:.1f} {e:.1f} 0 {c + k:.1f} 0 {c:.1f} "
            f"b 0 {c - k:.1f} {c - k:.1f} 0 {c:.1f} 0")


def para(w, h, lean=SKEW, ox=0.0, oy=0.0):
    """A slab w wide and h tall leaning right by lean (a tangent), drawn from ox, oy: its bounding box is
    w + h * lean wide."""
    t = h * lean
    return (f"m {ox + t:.1f} {oy:.1f} l {ox + w + t:.1f} {oy:.1f} l {ox + w:.1f} {oy + h:.1f} "
            f"l {ox:.1f} {oy + h:.1f}")


def para_ring(w, h, border, lean=SKEW):
    """A slab's outline only, border thick: the slab, with a smaller one wound the other way cut out of it."""
    t = h * lean
    inner_h = h - 2 * border
    it = inner_h * lean
    ox, oy = border * (1 + lean), border   # the inner slab's bottom left, kept the same distance from each side
    outer = f"m {t:.1f} 0 l {w + t:.1f} 0 l {w:.1f} {h:.1f} l 0 {h:.1f}"
    inner = (f"m {ox + it:.1f} {oy:.1f} l {ox:.1f} {oy + inner_h:.1f} l {ox + w - 2 * border * (1 + lean):.1f} "
             f"{oy + inner_h:.1f} l {ox + w - 2 * border * (1 + lean) + it:.1f} {oy:.1f}")
    return outer + " " + inner


def polygon(points):
    """A path through points (absolute), and where to put it: (path, x, y)."""
    x0 = min(p[0] for p in points)
    y0 = min(p[1] for p in points)
    path = "m " + " l ".join(f"{x - x0:.1f} {y - y0:.1f}" for x, y in points)
    return path, x0, y0


def shape(s, start, end, x, y, path, color, fade=FADE, slide=None, layer=2, extra=""):
    """A drawing with its bounding box's top left at x, y (or wherever a \\move in extra takes it)."""
    pos = "" if "\\move(" in extra else at(x, y, slide)
    s.raw(start, end, f"\\an7{pos}\\bord0\\shad0\\c{color}" + (f"\\fad({fade[0]},{fade[1]})" if fade else "")
          + extra + "\\p1", path + "{\\p0}", layer=layer)


# --- Pieces ---

def slab(s, start, end, x, y, w, h, color, shadow=None, fade=FADE, slide=None, layer=3, border=None):
    """A box leaning 12 degrees, x being its left edge halfway up (as CSS skews it, about its middle).
    shadow: (dx, dy, colour), drawn a layer down. border: (thickness, colour) draws an outline instead of a fill
    (color is then unused)."""
    left = x - h * SKEW / 2
    if shadow:
        dx, dy, c = shadow
        shape(s, start, end, left + dx, y + dy, para(w, h), c, fade=fade, slide=slide, layer=layer - 1)
    if border:
        thick, c = border
        shape(s, start, end, left, y, para_ring(w, h, thick), c, fade=fade, slide=slide, layer=layer)
    else:
        shape(s, start, end, left, y, para(w, h), color, fade=fade, slide=slide, layer=layer)


class Part:
    """One label on a strip (see strip): its words, their type, the fill behind them (None: none) and the room
    either side. width: at least this wide. border: (thickness, colour) for an outlined part. align: where the
    words sit when the part is wider than they are."""
    def __init__(self, text, font, em, color, fill=None, pad=20.0, width=0.0, bold=False, spacing=0.0,
                 border=None, align="left"):
        self.text, self.font, self.em, self.color, self.fill = text, font, em, color, fill
        self.pad, self.width, self.bold, self.spacing, self.border, self.align = pad, width, bold, spacing, border, align

    def size(self):
        return max(self.width, measure(self.text, self.font, self.em, self.spacing) + 2 * self.pad)


def strip(s, start, end, x, y, h, parts, shadow=None, fade=FADE, slide=None, layer=4):
    """Labels side by side on one leaning strip, like the guide's NOW BACK TO | SHOW TITLE: each part's fill
    runs into the next, and one hard shadow sits under the lot. Returns the strip's width."""
    widths = [p.size() for p in parts]
    total = sum(widths)
    if shadow:
        dx, dy, c = shadow
        shape(s, start, end, x - h * SKEW / 2 + dx, y + dy, para(total, h), c, fade=fade, slide=slide, layer=layer - 1)
    cx = x
    for part, w in zip(parts, widths):
        if part.fill or part.border:
            slab(s, start, end, cx, y, w, h, part.fill, fade=fade, slide=slide, layer=layer, border=part.border)
        tx = {"left": cx + part.pad, "center": cx + w / 2, "right": cx + w - part.pad}[part.align]
        words(s, start, end, tx, y + h / 2, part.text, part.font, part.em, part.color, align=part.align,
              spacing=part.spacing, bold=part.bold, fade=fade, slide=slide, layer=layer + 1)
        cx += w
    return total


# The badge's measurements for a 64 px G: the corner badge's, and the ID card's (tighter round its big letters)
BADGE = {
    "corner": dict(border=4, pad_x=24, pad_top=10, pad_bottom=8, gap=10, bar_w=12, bar_h=56, bar_border=3, radius=14,
                   shadow=8),
    "id": dict(border=2.33, pad_x=17.5, pad_top=7, pad_bottom=5, gap=5.8, bar_w=9.7, bar_h=56.2, bar_border=1.75,
               radius=8.5, shadow=5.4),
}


def badge_size(em, kind="corner"):
    m = {k: v * em / 64.0 for k, v in BADGE[kind].items()}
    w = 2 * m["border"] + 2 * m["pad_x"] + measure("G", ANTON, em) + 2 * m["gap"] + m["bar_w"] + measure("TV", ANTON, em)
    return w, 2 * m["border"] + m["pad_top"] + em + m["pad_bottom"]


def badge(s, start, end, x, y, em=64.0, accent=ORANGE, tilt=4.0, kind="corner", swing=None, fade=FADE, slide=None,
          layer=7):
    """The G/TV badge with its top left corner at x, y (before its tilt, counter-clockwise like the guide's).
    em: the letters' size (the guide's corner badge is 64 at 1080p, its ID card's 330). swing: (degrees, ms) to
    turn in from. Returns (width, height)."""
    m = {k: v * em / 64.0 for k, v in BADGE[kind].items()}
    w, h = badge_size(em, kind)
    cx, cy = x + w / 2, y + h / 2
    turn = f"\\org({cx:.1f},{cy:.1f})\\frz{tilt:g}"
    if swing:
        turn = f"\\org({cx:.1f},{cy:.1f})\\frz{swing[0]:g}\\t(0,{swing[1]},0.6,\\frz{tilt:g})"
    radius, border = m["radius"], m["border"]
    shape(s, start, end, x + m["shadow"], y + m["shadow"], rounded(w, h, radius), INK, fade, slide, layer, turn)
    shape(s, start, end, x, y, rounded(w, h, radius), INK, fade, slide, layer + 1, turn)
    shape(s, start, end, x + border, y + border, rounded(w - 2 * border, h - 2 * border, max(1.0, radius - border)),
          YELLOW, fade, slide, layer + 2, turn)
    mid = y + border + m["pad_top"] + em / 2 + em / 32   # Anton sits a touch low in its line
    lx = x + border + m["pad_x"]
    g = measure("G", ANTON, em)
    words(s, start, end, lx, mid, "G", ANTON, em, INK, fade=fade, slide=slide, layer=layer + 3, extra=turn)
    lean = math.tan(math.radians(14))
    bar_w, bar_h, bar_border = m["bar_w"], m["bar_h"], m["bar_border"]
    bx = lx + g + m["gap"] - bar_h * lean / 2
    by = mid - bar_h / 2
    shape(s, start, end, bx, by, para(bar_w, bar_h, lean), INK, fade, slide, layer + 3, turn)
    shape(s, start, end, bx + bar_border * (1 + lean), by + bar_border,
          para(bar_w - 2 * bar_border, bar_h - 2 * bar_border, lean), accent, fade, slide, layer + 4, turn)
    words(s, start, end, lx + g + m["gap"] + bar_w + m["gap"], mid, "TV", ANTON, em, INK, fade=fade, slide=slide,
          layer=layer + 3, extra=turn)
    return w, h


def corner_badge(s, start, end, accent=ORANGE, fade=FADE, layer=20, em=40.0):
    """The badge in the top right corner, as the guide puts it over a programme."""
    badge(s, start, end, W - 52 - badge_size(em)[0], 30, em, accent, fade=fade, layer=layer)


def header(s, d, title_pages, accent=ORANGE, kicker="G|TV", x=MARGIN, y=30, h=62, em=46.0, fade=FADE,
           slide=(0, 350, 60), room=600.0):
    """The info screens' title: the kicker in the accent, the title on yellow, a purple shadow under both.
    title_pages: [(from, to, title)]; the yellow part is as wide as the longest, and the type shrinks to keep
    the whole header within room (clear of the date chip). Returns the header's width."""
    kick = Part(kicker, ANTON, em * 0.5, INK, accent, pad=em * 0.32)
    widest = max(measure(t.upper(), ANTON, em) for _, _, t in title_pages)
    if kick.size() + widest + em > room:
        em = max(em * 0.6, em * (room - kick.size()) / (widest + em))
        widest = max(measure(t.upper(), ANTON, em) for _, _, t in title_pages)
    title = Part("", ANTON, em, INK, YELLOW, pad=em * 0.5, width=widest + em)
    total = strip(s, 0, d, x, y, h, [kick, title], shadow=(7, 7, PURPLE), fade=fade, slide=slide, layer=4)
    tx = x + kick.size() + em * 0.5
    for a, b, text in title_pages:
        words(s, a, b, tx, y + h / 2, text.upper(), ANTON, em, INK, fade=fade, slide=slide if a == 0 else None,
              layer=6)
    return total


def date_chip(s, d, now, text=None, fade=FADE, clock=True, y=44, h=38, em=21.0, right=W - MARGIN):
    """The white chip top right: the day and date, and the time ticking over each minute. text: other words."""
    stamps = [(0.0, now)]
    if clock and text is None:
        nxt = (now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1))
        while (nxt - now).total_seconds() < d:
            stamps.append(((nxt - now).total_seconds(), nxt))
            nxt += datetime.timedelta(minutes=1)
    for i, (a, t) in enumerate(stamps):
        b = stamps[i + 1][0] if i + 1 < len(stamps) else d
        label = text or (t.strftime("%a %b %-d").upper() + (" · " + t.strftime("%I:%M %p").lstrip("0") if clock else ""))
        w = measure(label, ARCHIVO_BLACK, em) + 2 * 18
        f = fade if len(stamps) == 1 else ((fade[0] if i == 0 else 0, fade[1] if i == len(stamps) - 1 else 0) if fade else None)
        x = right - w
        slab(s, a, b, x, y, w, h, WHITE, shadow=(5, 5, INK), fade=f, layer=4)
        words(s, a, b, x + 18, y + h / 2, label, ARCHIVO_BLACK, em, INK, fade=f, layer=6)


def footer(s, d, text, fade=FADE, x=MARGIN + 4, y=570, em=17.0, room=470.0):
    """The small print along the bottom: sources, when things were updated. It shrinks, then is cut, to stay left
    of the yellow stripe (room)."""
    text = text.upper()
    em = shrink(text, ARCHIVO, em, room, spacing=em * 0.1, least=0.85)
    words(s, 0, d, x, y, clip(text, ARCHIVO, em, room, spacing=em * 0.1), ARCHIVO, em, GREY, spacing=em * 0.1,
          fade=fade, layer=5)


SPLIT_TOP, SPLIT_BOTTOM = 692.0, 564.0   # where the purple panel's leaning edge meets the top and the bottom


def split_x(y):
    """The purple panel's left edge at height y."""
    return SPLIT_TOP + (SPLIT_BOTTOM - SPLIT_TOP) * y / H


def dots(left_of, x0, x1, y0=0, y1=H, step=16, r=2.2):
    """The dotted texture as one path over x0..x1, y0..y1, leaving out dots left of left_of(y) + 6.
    Returns (path, x, y)."""
    pts = []
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        edge = left_of(y) + 6
        while x < x1:
            if x > edge:
                pts.append((x, y))
            x += step
        y += step
    if not pts:
        return None
    mx, my = min(p[0] for p in pts) - r, min(p[1] for p in pts) - r
    path = " ".join(f"m {x - mx - r:.1f} {y - my - r:.1f} l {x - mx + r:.1f} {y - my - r:.1f} "
                    f"l {x - mx + r:.1f} {y - my + r:.1f} l {x - mx - r:.1f} {y - my + r:.1f}" for x, y in pts)
    return path, mx, my


def split(s, d, fade=FADE, layer=0, textured=True):
    """The info screens' ground (on an ink background): the dotted purple panel on the right with a yellow
    stripe down its edge."""
    path, x, y = polygon([(SPLIT_TOP, 0), (W, 0), (W, H), (SPLIT_BOTTOM, H)])
    shape(s, 0, d, x, y, path, PURPLE, fade=fade, layer=layer)
    if textured:
        dotted = dots(split_x, SPLIT_BOTTOM, W)
        if dotted:
            shape(s, 0, d, dotted[1], dotted[2], dotted[0], WHITE, fade=fade, layer=layer + 1, extra="\\1a&HDB&")
    stripe, sx, sy = polygon([(SPLIT_TOP - 24, 0), (SPLIT_TOP - 12, 0), (SPLIT_BOTTOM - 12, H), (SPLIT_BOTTOM - 24, H)])
    shape(s, 0, d, sx, sy, stripe, YELLOW, fade=fade, layer=layer + 1)


def stage(s, d, fade=FADE, layer=0):
    """The guide's stand-in for a programme under the cards made to sit over one (on a STAGE_BG background):
    a soft grey glow, brightest a little above the middle."""
    for color, w, h, blur in (("&H00362F2B&", 1240, 760, 90), ("&H005A504A&", 620, 380, 110)):
        shape(s, 0, d, W / 2 - w / 2, 240 - h / 2, rounded(w, h, h / 2), color, fade=fade, layer=layer,
              extra=f"\\blur{blur}")


NAME = "GL0RYUS TV"


def id_ground(s, d, accent=ORANGE, fade=FADE, layer=0, slide_in=True):
    """The ID card's ground (on a PURPLE_BG background): dots all over, and three bands crossing it, turned 8 degrees
    up to the right: yellow, a broad ink one, the accent. slide_in: the bands sweep in from either side."""
    dotted = dots(lambda y: -999, 0, W, step=18)
    if dotted:
        shape(s, 0, d, dotted[1], dotted[2], dotted[0], WHITE, fade=fade, layer=layer, extra="\\1a&HDB&")
    for i, (color, top, h) in enumerate(((YELLOW, 308, 8), (INK, 330, 72), (accent, 411, 16))):
        turn = f"\\org({W / 2:.0f},{top + h / 2:.0f})\\frz8"
        move = (0, 450 + 90 * i, (-1 if i % 2 else 1) * 1300) if slide_in else None
        shape(s, 0, d, -140, top, rect(W + 280, h), color, fade=fade, slide=move, layer=layer + 1, extra=turn)


def lines(text, font, em, width):
    """text broken into lines no wider than width at em (a word too long for a line gets one of its own)."""
    out, line = [], ""
    for word in str(text).split():
        trial = f"{line} {word}".strip()
        if line and measure(trial, font, em) > width:
            out.append(line)
            line = word
        else:
            line = trial
    if line:
        out.append(line)
    return out


def hazard(s, start, end, y, h, fade=FADE, slide=None, layer=4, stripe=11.0):
    """The emergency banner's edge: yellow and ink stripes leaning 45 degrees, full width."""
    shape(s, start, end, 0, y, rect(W, h), INK, fade=fade, slide=slide, layer=layer)
    bars = []
    x = -h
    while x < W:
        bars.append(f"m {x + h + h:.1f} 0 l {x + h + h + stripe:.1f} 0 l {x + h + stripe:.1f} {h:.1f} l {x + h:.1f} {h:.1f}")
        x += 2 * stripe
    clip = f"\\clip(0,{y:.0f},{W},{y + h:.0f})"
    shape(s, start, end, -h, y, " ".join(bars), YELLOW, fade=fade, slide=slide, layer=layer + 1, extra=clip)


def warning_sign(size, stroke):
    """A triangle outline with an exclamation mark in it, size wide, for the alert banners."""
    hgt = size * 0.87
    outer = f"m {size / 2:.1f} 0 l {size:.1f} {hgt:.1f} l 0 {hgt:.1f}"
    k = stroke * 1.9   # the inner triangle, wound the other way, set in by about the stroke all round
    inner = f"m {size / 2:.1f} {k:.1f} l {k * 0.9:.1f} {hgt - stroke:.1f} l {size - k * 0.9:.1f} {hgt - stroke:.1f}"
    bar_w = stroke * 1.15
    cx = size / 2
    mark = (f"m {cx - bar_w / 2:.1f} {hgt * 0.36:.1f} l {cx + bar_w / 2:.1f} {hgt * 0.36:.1f} "
            f"l {cx + bar_w / 2:.1f} {hgt * 0.66:.1f} l {cx - bar_w / 2:.1f} {hgt * 0.66:.1f} "
            f"m {cx - bar_w / 2:.1f} {hgt * 0.73:.1f} l {cx + bar_w / 2:.1f} {hgt * 0.73:.1f} "
            f"l {cx + bar_w / 2:.1f} {hgt * 0.73 + bar_w:.1f} l {cx - bar_w / 2:.1f} {hgt * 0.73 + bar_w:.1f}")
    return f"{outer} {inner} {mark}"
