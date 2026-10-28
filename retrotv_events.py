"""
The GL0RYUS Network's occasions: holiday station IDs, holiday reminders, marathon openers, the "NEXT" lower third,
the late-night lottery drawing, the New Year's countdown, and the sign-off and sign-on (anthem,
the set switching off, colour bars). Same approach as retrotv_cards: ASS over an mpv lavfi URL.
"""
import datetime
import math
import os
import random
import re
import subprocess

import retrotv_cards as cards
from retrotv_cards import (
    ARCHIVO, BARLOW_B, BARLOW_S, BARLOW_X, BLACK, CREAM, MUSTARD, SHADOW_RED, SKY, W, H, WHITE, YELLOW,
    ASSETS_DIR, FONTS_DIR, RENDER, Card, Script, circle, clip_text, clock, fit, music, rect, stripes,
)

CREEPSTER = "Creepster"
XMAS = "Mountains of Christmas"
LOBSTER = "Lobster"            # Valentine's
GROVER = "Irish Grover"        # St. Patrick's
CHEWY = "Chewy"                # Easter
ALFA = "Alfa Slab One"         # the Fourth of July
RYE = "Rye"                    # Thanksgiving
FLAG_LOOP = os.path.join(ASSETS_DIR, "flag_loop.mp4")
POWER_OFF_VIDEO = os.path.join(ASSETS_DIR, "signoff_crt.mp4")
SIGN_ON_VIDEO = os.path.join(ASSETS_DIR, "signon.mp4")

SIGN_ON_SECONDS = 62.5     # warm-up (2.5 s) and a minute of bars
POWER_OFF_SECONDS = 2.0
LOTTERY_SECONDS = 62.0
LOTTERY_REVEALS = (9.0, 15.0, 21.0, 27.0, 33.0, 39.0)  # cue points in lotto_bed.ogg
LOTTERY_BONUS = 46.0
NEW_YEAR_SECONDS = 80.0
NEW_YEAR_MIDNIGHT = 60.0   # where midnight falls in the countdown
MARATHON_SECONDS = 6.0
ID_SECONDS = 4.5

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
PUMPKIN = "&H003A9CF5&"
MAPLE = "&H002B39C0&"
RUST = "&H001E43A6&"
BROWN = "&H0018345A&"

STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"


def _probe(path, default):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "default=nw=1:nk=1", path], timeout=20)
        return float(out)
    except Exception:
        return default


def _audio(path, d, fade=0.6):
    """An audio chain playing path, padded or trimmed to exactly d seconds."""
    if path and os.path.exists(path):
        chain = f"amovie=filename={path},apad,atrim=end={d:.2f}"
        if fade:
            chain += f",afade=t=out:st={max(0.0, d - fade):.2f}:d={fade}"
    else:
        chain = f"anullsrc=r=48000:cl=stereo,atrim=end={d:.2f}"
    return chain + "," + STEREO


def _url(video, audio):
    return f"av://lavfi:{video}[out0];{audio}[out1]"


# --- Station IDs ---

BAT = ("m 28 10 l 33 4 l 36 9 l 44 2 l 56 6 l 52 10 l 46 12 l 40 20 l 34 15 l 28 22 "
       "l 22 15 l 16 20 l 10 12 l 4 10 l 0 6 l 12 2 l 20 9 l 23 4")


def station_id(now, good_morning=False, season=None):
    """The hourly ID: the bumper with the time over it, dressed up for a holiday (see LOOKS) when its
    sting, music/id_<season>.ogg, is there."""
    sting = music(f"id_{season}.ogg") if season else None
    look = LOOKS.get(season)
    if not look or not sting or not os.path.exists(sting):
        return cards.station_id(now, good_morning)
    d = ID_SECONDS
    s = Script()
    grade = look(s, d, now, good_morning)
    path = s.write(f"id-{season}")
    video = (f"movie=filename={cards.ID_BACKGROUND},scale={RENDER.replace('x', ':')},setsar=1,{grade},"
             f"ass=filename={path}:fontsdir={FONTS_DIR},format=yuv420p")
    return Card(_url(video, _audio(sting, d, fade=0.2)), d, "id")


def _shape_xform(path, fn):
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


HEART = ("m 25 45 b 10 33 0 23 0 13 b 0 5 6 0 13 0 b 19 0 23 4 25 9 b 27 4 31 0 37 0 "
         "b 44 0 50 5 50 13 b 50 23 40 33 25 45")
LEAF = ("m 20 0 l 24 9 l 31 6 l 29 15 l 39 14 l 33 21 l 38 27 l 27 27 l 23 37 l 21 34 l 21 42 l 19 42 "
        "l 19 34 l 17 37 l 13 27 l 2 27 l 7 21 l 1 14 l 11 15 l 9 6 l 16 9")


def _shamrock():
    """Three small hearts around a centre at 30,30 with their points in, and a curved stem."""
    parts = []
    for angle in (0, 120, 240):
        a = math.radians(angle)

        def leaf(x, y, a=a):
            x, y = x * 0.5 - 12.5 + 30, y * 0.5 - 22.5 + 30  # half size, point on the centre
            dx, dy = x - 30, y - 30
            return 30 + dx * math.cos(a) - dy * math.sin(a), 30 + dx * math.sin(a) + dy * math.cos(a)
        parts.append(_shape_xform(HEART, leaf))
    parts.append("m 29 30 b 31 40 35 48 42 54 l 39 56 b 32 49 28 41 27 30")
    return " ".join(parts)


SHAMROCK = _shamrock()


def _drift(s, d, rnd, path, colors, count, scale=(60, 130), fall=(200, 380), sway=60, spin=40, rise=False,
           alpha="&H00&", layer=6):
    """Props drifting down the screen (or up, with rise) like the Christmas snow, each tumbling a little."""
    for _ in range(count):
        x = rnd.randrange(0, W)
        y = rnd.randrange(0, H + 100) if rise else rnd.randrange(-H // 2, H - 100)
        dy = rnd.randint(*fall) * (-1 if rise else 1)
        size = rnd.randint(*scale)
        turn = rnd.randint(-spin, spin)
        a0 = rnd.randint(-30, 30)
        s.shape(0, d, 0, 0, path, rnd.choice(colors), fade=(200, 250), layer=layer, align=5,
                extra=f"\\move({x},{y},{x + rnd.randint(-sway, sway)},{y + dy})\\fscx{size}\\fscy{size}"
                      f"\\frz{a0}\\t(\\frz{a0 + turn})\\1a{alpha}")


def _halloween_look(s, d, now, good_morning):
    fade = (300, 250)
    grade = "eq=brightness=-0.16:saturation=1.25:gamma_b=1.15,vignette=PI/3.5"
    # Lightning (on the sting's thunder crack), a moon and bats
    for a, b in ((0.0, 0.12), (2.22, 2.42), (2.56, 2.68)):
        s.shape(a, b, 0, 0, rect(W, H), WHITE, fade=(0, 120), layer=9, extra="\\1a&H60&")
    s.shape(0, d, 842, 52, circle(62), MOON, fade=(500, 250), layer=1, extra="\\blur6")
    rnd = random.Random(now.hour)
    for i in range(4):
        y0 = rnd.randint(330, 520)
        start = 0.2 + i * 0.55
        flap = "".join(f"\\t({t},{t + 90},\\fscy35)\\t({t + 90},{t + 180},\\fscy100)" for t in range(0, 4000, 180))
        scale = rnd.choice((90, 120, 150))
        s.shape(start, d, 0, 0, BAT, BLACK, fade=(100, 200), layer=6, align=5,
                extra=f"\\move(-60,{y0},{1100},{y0 - rnd.randint(260, 420)})\\fscx{scale}\\fscy{scale}{flap}")
    top = "GOOD MORNING, BOILS & GHOULS" if good_morning else "HAPPY HALLOWEEN"
    # Up top, clear of the bumper's logo
    s.text(0.6, d, 512, 98, top, CREEPSTER, 58 if good_morning else 84, ORANGE, shadow=5,
           shadow_color=PURPLE, fade=fade, layer=7)
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"GL0RYUS NETWORK · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, SLIME, spacing=4, shadow=3, shadow_color=BLACK,
           fade=fade, layer=7)
    return grade


def _christmas_look(s, d, now, good_morning):
    fade = (300, 250)
    grade = "eq=brightness=-0.06:saturation=1.1"
    rnd = random.Random(now.hour)
    for _ in range(70):
        x, y = rnd.randrange(0, W), rnd.randrange(-H, H)
        r = rnd.choice((2, 3, 3, 4, 5))
        fall = rnd.randint(150, 330)
        s.shape(0, d, 0, 0, circle(r), WHITE, fade=(200, 250), layer=6, align=5,
                extra=f"\\move({x},{y},{x + rnd.randint(-60, 60)},{y + fall})\\blur1")
    top = "GOOD MORNING!" if good_morning else "HAPPY HOLIDAYS"
    s.text(0.6, d, 512, 96, top, XMAS, 84, XMAS_RED, outline=4, shadow=4, shadow_color=WHITE,
           fade=fade, layer=7, extra=f"\\3c{WHITE}\\4c{XMAS_GREEN}\\b1")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"FROM ALL OF US AT GL0RYUS · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 46, WHITE, spacing=4, shadow=3, shadow_color=XMAS_GREEN,
           fade=fade, layer=7)
    return grade


def _valentines_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    _drift(s, d, rnd, HEART, (PINK, ROSE, BLUSH), 16, scale=(50, 120), fall=(280, 520), sway=50, spin=20,
           rise=True, alpha="&H30&")
    if good_morning:
        top = "Good Morning, Sweetheart"
    else:
        top = "Happy Valentine's Day" if (now.month, now.day) == (2, 14) else "Be My Valentine"
    s.text(0.6, d, 512, 96, top, LOBSTER, 80, WHITE, outline=3, shadow=4, shadow_color=WINE, fade=fade, layer=7,
           extra=f"\\3c{ROSE}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"GL0RYUS NETWORK · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, BLUSH, spacing=4, shadow=3, shadow_color=WINE,
           fade=fade, layer=7)
    return "eq=brightness=-0.04:saturation=1.15,colorbalance=rs=0.10:bs=0.04"


def _stpatricks_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    _drift(s, d, rnd, SHAMROCK, (KELLY, CLOVER, KELLY), 14, scale=(60, 140))
    top = "Top o' the Morning!" if good_morning else "Happy St. Patrick's Day"
    s.text(0.6, d, 512, 98, top, GROVER, 74, CLOVER, outline=2, shadow=5, shadow_color=MOSS, fade=fade, layer=7,
           extra=f"\\3c{GOLD}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"GL0RYUS NETWORK · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, GOLD, spacing=4, shadow=3, shadow_color=BLACK,
           fade=fade, layer=7)
    return "eq=brightness=-0.04:saturation=1.1,colorbalance=gs=0.08:rs=-0.03"


def _easter_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    for _ in range(12):  # glossy pastel eggs tumbling down
        x, y = rnd.randrange(0, W), rnd.randrange(-H // 2, H - 100)
        dx, dy = rnd.randint(-50, 50), rnd.randint(200, 360)
        r = rnd.randint(16, 26)
        a0, turn = rnd.randint(-40, 40), rnd.randint(-50, 50)
        spin = f"\\frz{a0}\\t(\\frz{a0 + turn})"
        s.shape(0, d, 0, 0, circle(r), rnd.choice(PASTELS), fade=(200, 250), layer=6, align=5,
                extra=f"\\move({x},{y},{x + dx},{y + dy})\\fscx78{spin}")
        s.shape(0, d, 0, 0, circle(r * 0.22), WHITE, fade=(200, 250), layer=6, align=5,
                extra=f"\\move({x - r * 0.3:.0f},{y - r * 0.45:.0f},{x + dx - r * 0.3:.0f},{y + dy - r * 0.45:.0f})"
                      f"\\1a&H50&")
    top = "Good Morning!" if good_morning else "Happy Easter"
    s.text(0.6, d, 512, 96, top, CHEWY, 88, PASTELS[0], outline=4, shadow=4, shadow_color=LILAC_SHADOW, fade=fade,
           layer=7, extra=f"\\3c{WHITE}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"FROM ALL OF US AT GL0RYUS · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 46, WHITE, spacing=4, shadow=3, shadow_color=LILAC_SHADOW,
           fade=fade, layer=7)
    return "eq=brightness=0.02:saturation=0.85,colorbalance=bs=0.05:rs=0.03"


def _july4_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    # Fireworks around the logo: a flash, then sparks flying out and dropping as they fade
    spots = [(rnd.randint(110, 330), rnd.randint(60, 230)), (rnd.randint(700, 920), rnd.randint(60, 230)),
             (rnd.randint(380, 640), rnd.randint(40, 120)), (rnd.randint(90, 250), rnd.randint(260, 400)),
             (rnd.randint(780, 940), rnd.randint(260, 400))]
    for i, (bx, by) in enumerate(spots):
        t0 = 0.15 + i * 0.7 + rnd.uniform(-0.1, 0.1)
        color = (GLORY_RED, WHITE, "&H00FF9A4A&", GOLD, GLORY_RED)[i]  # red, white, blue, gold
        s.shape(t0, t0 + 0.3, bx, by, circle(26), WHITE, fade=(0, 250), layer=6, align=5, extra="\\blur8")
        for k in range(24):
            a = 2 * math.pi * k / 24 + rnd.uniform(-0.08, 0.08)
            reach = rnd.randint(120, 175)
            ex, ey = bx + reach * math.cos(a), by + reach * math.sin(a) + 40
            s.shape(t0, t0 + 1.7, bx, by, circle(6), color, fade=(0, 800), layer=6, align=5,
                    extra=f"\\move({bx},{by},{ex:.0f},{ey:.0f},0,1400)\\blur1.5")
    top = "Good Morning!" if good_morning else "Happy 4th of July"
    s.text(0.6, d, 512, 98, top.upper(), ALFA, 66, WHITE, outline=3, shadow=5, shadow_color=GLORY_BLUE, fade=fade,
           layer=7, extra=f"\\3c{GLORY_RED}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"GL0RYUS NETWORK · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, WHITE, spacing=4, shadow=3, shadow_color=GLORY_RED,
           fade=fade, layer=7)
    return "eq=brightness=-0.12:saturation=1.2,vignette=PI/4"


def _thanksgiving_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    _drift(s, d, rnd, LEAF, (PUMPKIN, MAPLE, MUSTARD, RUST), 16, scale=(70, 140), sway=90, spin=120)
    top = "Good Morning!" if good_morning else "Happy Thanksgiving"
    s.text(0.6, d, 512, 98, top.upper(), RYE, 62, PUMPKIN, shadow=5, shadow_color=BROWN, fade=fade, layer=7)
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"FROM ALL OF US AT GL0RYUS · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 46, CREAM, spacing=4, shadow=3, shadow_color=BROWN,
           fade=fade, layer=7)
    return "eq=brightness=-0.05:saturation=1.1,colorbalance=rs=0.08:gs=0.02:bs=-0.08"


LOOKS = {"halloween": _halloween_look, "christmas": _christmas_look, "valentines": _valentines_look,
         "stpatricks": _stpatricks_look, "easter": _easter_look, "july4": _july4_look,
         "thanksgiving": _thanksgiving_look}


# --- Holiday reminders ---

REMINDER_TIPS = {"Valentine's Day": "FLOWERS · CHOCOLATE · DINNER PLANS"}


def reminder(name, when, now):
    """A heads-up in the breaks ahead of a day you're expected to get someone something or spend time with
    them (Valentine's, Mother's and Father's Day), counting down through the day itself."""
    d = 7.0  # the Up Next card's music bed
    days = (when - now.date()).days
    s = Script()
    kicker = "TODAY IS" if days == 0 else "DON'T FORGET" if days == 1 else "HEADS UP"
    s.text(0, d, 512, 150, kicker, BARLOW_X, 50, MUSTARD, spacing=6)
    title = name.upper()
    s.text(0.1, d, 512, 240, title, ARCHIVO, fit(title, ARCHIVO, 92, 900), shadow=6, slant=-0.15)
    stripes(s, 0, d, 190, 305, 640)
    date = when.strftime("%A, %B %-d").upper()
    line = date if days == 0 else f"TOMORROW · {date}" if days == 1 else f"{date} · {days} DAYS AWAY"
    s.text(0.4, d, 512, 405, line, BARLOW_X, 46, CREAM, spacing=2)
    tip = "MAKE SOME TIME FOR THEM TODAY" if days == 0 else REMINDER_TIPS.get(name, "A CARD, A GIFT OR A VISIT")
    s.text(0.7, d, 512, 470, tip, BARLOW_B, 38, MUSTARD, spacing=6)
    return Card(cards.lavfi_url(s.write("reminder"), d, music("upnext_bed.ogg")), d, "reminder")


# --- Marathons ---

def marathon(name, series_name, start, season=None):
    """Opens a marathon block: "FRIDAY NIGHT / MARATHON / The Simpsons"."""
    d = MARATHON_SECONDS
    s = Script()
    kicker = name.upper().replace("MARATHON", "").strip() or "TONIGHT"
    s.text(0, d, 512, 120, kicker, BARLOW_X, 54, MUSTARD, spacing=10, fade=(250, 300))
    s.text(0.15, d, 512, 222, "MARATHON", ARCHIVO, 128, shadow=7, slant=-0.15, fade=(250, 300),
           extra="\\t(0,250,\\fscx112\\fscy112)\\t(250,450,\\fscx100\\fscy100)")
    stripes(s, 0.2, d, 190, 300, 640)
    series_name = clip_text(series_name.upper(), 30)
    s.text(0.5, d, 512, 405, series_name, BARLOW_X, fit(series_name, BARLOW_X, 76, 880), CREAM, shadow=3)
    if season == "halloween":
        s.text(1.0, d, 512, 492, "HALLOWEEN EDITION", CREEPSTER, 52, ORANGE, shadow=4, shadow_color=PURPLE)
    elif season == "christmas":
        s.text(1.0, d, 512, 492, "HOLIDAY EDITION", XMAS, 56, XMAS_RED, outline=3, extra=f"\\3c{WHITE}\\b1")
    else:
        s.text(1.0, d, 512, 492, f"BACK TO BACK STARTING NOW · {clock(start)}", BARLOW_S, 38, MUSTARD, spacing=4)
    return Card(cards.lavfi_url(s.write("marathon"), d, music("marathon_sting.ogg")), d, "card")


# --- Snipe ---

BAR_RIGHT = 745      # the lower thirds end left of the logo bug in the bottom right corner
NAME_CHAR = 14.5     # Barlow Condensed ExtraBold caps at 46: about this wide per character, as rendered


def _bar(names, tag):
    """Width of a lower third's name area, and a font size per name so each fits inside it."""
    room = BAR_RIGHT - 40 - tag
    width = min(room, max(260, int(max(len(n) for n in names) * NAME_CHAR + 70)))
    sizes = [min(46, int(46 * (width - 70) / max(1.0, len(n) * NAME_CHAR))) for n in names]
    return width, sizes


def snipe(name, start, end):
    """An ASS file for the vf chain: a "NEXT" lower third that slides in at file time `start`."""
    s = Script()
    name = clip_text(name.upper(), 30)
    width, (size,) = _bar([name], 150)
    shift = 150 + width + 80  # everything slides in together from off the left edge

    def slide(x, y):
        return f"\\move({x - shift},{y},{x},{y},0,450)"
    s.shape(start, end, 40, 462, cards.slanted_bar(150 + width, 62, 22), "&H00201810&", fade=(0, 300),
            layer=1, extra=slide(40, 462) + "\\1a&H30&")
    s.shape(start, end, 40, 462, cards.slanted_bar(150, 62, 22), MUSTARD, fade=(0, 300), layer=2,
            extra=slide(40, 462))
    s.shape(start, end, 40, 526, rect(150 + width - 22, 5), "&H002A7FEE&", fade=(0, 300), layer=2,
            extra=slide(40, 526))
    s.text(start, end, 62, 494, "NEXT", ARCHIVO, 40, "&H00161110&", align=4, fade=(0, 300), layer=3,
           extra=slide(62, 494))
    s.text(start, end, 196, 494, name, BARLOW_X, size, CREAM, align=4, fade=(0, 300), layer=3,
           extra=slide(196, 494))
    return s.write("snipe")


def promo(panels, start, panel_seconds=4.5):
    """An ASS file for the vf chain: the snipe's bar sliding in at file time `start`, then stepping
    through (label, name) panels such as NEXT / LATER / TONIGHT before it fades away."""
    s = Script()
    def clip(name):
        # "NAME · 7:30 PM": shorten the name, never the time
        show, sep, when = name.upper().rpartition(" · ")
        return clip_text(show, 34 - len(when) - 3) + sep + when if sep else clip_text(name.upper(), 34)
    panels = [(label.upper(), clip(name)) for label, name in panels]
    end = start + panel_seconds * len(panels)
    tag = max(150, max(int(cards.text_width(label, ARCHIVO, 40)) for label, _ in panels) + 48)
    width, sizes = _bar([name for _, name in panels], tag)
    shift = tag + width + 80

    def slide(x, y):
        return f"\\move({x - shift},{y},{x},{y},0,450)"
    s.shape(start, end, 40, 462, cards.slanted_bar(tag + width, 62, 22), "&H00201810&", fade=(0, 300),
            layer=1, extra=slide(40, 462) + "\\1a&H30&")
    s.shape(start, end, 40, 462, cards.slanted_bar(tag, 62, 22), MUSTARD, fade=(0, 300), layer=2,
            extra=slide(40, 462))
    s.shape(start, end, 40, 526, rect(tag + width - 22, 5), "&H002A7FEE&", fade=(0, 300), layer=2,
            extra=slide(40, 526))
    for i, (label, name) in enumerate(panels):
        a, b = start + i * panel_seconds, start + (i + 1) * panel_seconds
        first, last = i == 0, i == len(panels) - 1
        fade = (0 if first else 200, 300 if last else 150)  # the first panel rides in with the bar
        s.text(a, b, 62, 494, label, ARCHIVO, 40, "&H00161110&", align=4, fade=fade, layer=3,
               extra=slide(62, 494) if first else "")
        s.text(a, b, 46 + tag, 494, name, BARLOW_X, sizes[i], CREAM, align=4, fade=fade, layer=3,
               extra=slide(46 + tag, 494) if first else "")
    return s.write("promo")


# --- Lottery ---

BALL_COLORS = ("&H003030E0&", "&H002090F0&", "&H0020D0F0&", "&H0040C050&", "&H00E08040&")  # by tens
LOTTO_BACKGROUND = "0x14082e"
DRUM_X, DRUM_Y, DRUM_R = 512, 292, 148
TRAY_Y = 528
ORDINALS = ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH")


def _ball(s, start, end, x, y, number, extra="", layer=5, r=40):
    color = BALL_COLORS[min(4, number // 10)]
    s.shape(start, end, x, y, circle(r), color, fade=(0, 300), layer=layer, align=5, extra=extra)
    s.shape(start, end, x, y, circle(r * 0.62), WHITE, fade=(0, 300), layer=layer + 1, align=5, extra=extra)
    s.text(start, end, x, y + 1, str(number), ARCHIVO, int(r * 0.78), "&H00161616&", fade=(0, 300),
           layer=layer + 2, extra=extra)


def lottery(when):
    """The late-night drawing: six balls and a bonus ball from a tumbling drum, with a recap."""
    d = LOTTERY_SECONDS
    numbers = random.sample(range(1, 50), 7)
    s = Script()
    fade = (400, 600)
    # Studio: header, the drum, the tray
    s.text(0, d, 70, 70, "GL0RYUS LOTTO", ARCHIVO, 58, YELLOW, align=4, shadow=5, shadow_color=PURPLE,
           slant=-0.12, fade=fade)
    stripes(s, 0, d, 70, 104, 360, bar=6, gap=3, slide=True, fade=fade)
    s.text(0, d, 954, 62, "PICK 6 + BONUS", BARLOW_X, 40, CREAM, align=6, spacing=4, fade=fade)
    s.text(0, d, 954, 106, f"{clock(when)} DRAWING · {when.strftime('%a %b %d').upper()}", BARLOW_S, 32, SKY,
           align=6, fade=fade)
    s.shape(0, d, DRUM_X, DRUM_Y, circle(DRUM_R + 8), "&H00603018&", fade=fade, layer=1, align=5,
            extra="\\1a&H90&")
    s.raw(0, d, f"\\an5\\pos({DRUM_X},{DRUM_Y})\\bord5\\shad0\\3c{SKY}\\1a&HFF&\\fad({fade[0]},{fade[1]})\\p1",
          circle(DRUM_R), layer=8)
    s.shape(0, d, DRUM_X - 14, DRUM_Y - DRUM_R - 40, rect(28, 42), SKY, fade=fade, layer=8, align=7,
            extra="\\1a&H40&")
    for i in range(7):
        x = 140 + i * 112 + (50 if i == 6 else 0)
        s.shape(0, d, x, TRAY_Y, circle(44), "&H00402010&", fade=fade, layer=2, align=5)
    s.text(0, d, 140 + 6 * 112 + 50, TRAY_Y + 58, "BONUS", BARLOW_X, 26, MUSTARD, spacing=4, fade=fade)

    # Tumbling balls: each circles the drum centre at its own radius and speed, dropping out as balls are drawn
    rnd = random.Random(sum(numbers))
    reveals = list(LOTTERY_REVEALS) + [LOTTERY_BONUS]
    pool = [n for n in range(1, 50) if n not in numbers]
    rnd.shuffle(pool)
    tumblers = [(n, None) for n in pool[:18]] + [(n, reveals[i]) for i, n in enumerate(numbers)]
    for n, gone in tumblers:
        radius = rnd.uniform(25, DRUM_R - 36)
        angle = rnd.uniform(0, 360)
        x = DRUM_X + radius
        turns = rnd.uniform(5, 11) * rnd.choice((1, -1))
        end = gone if gone else d
        spin = f"\\org({DRUM_X},{DRUM_Y})\\frz{angle:.0f}\\t(0,{int(end * 1000)},\\frz{angle + turns * 360:.0f})"
        color = BALL_COLORS[min(4, n // 10)]
        s.shape(0.3, end, x, DRUM_Y, circle(17), color, fade=(300, 0 if gone else 600), layer=3, align=5, extra=spin)
        s.shape(0.3, end, x, DRUM_Y, circle(9), WHITE, fade=(300, 0 if gone else 600), layer=4, align=5, extra=spin)

    # The draw
    for i, (n, t) in enumerate(zip(numbers, reveals)):
        x = 140 + i * 112 + (50 if i == 6 else 0)
        pop = f"\\move({DRUM_X},{DRUM_Y - DRUM_R - 20},{x},{TRAY_Y},0,700)\\fscx40\\fscy40\\t(0,200,\\fscx125\\fscy125)\\t(200,700,\\fscx100\\fscy100)"
        _ball(s, t, t + 0.7, DRUM_X, DRUM_Y - DRUM_R - 20, n, extra=pop, layer=10)
        _ball(s, t + 0.7, d, x, TRAY_Y, n, layer=10)
        call = "AND THE BONUS BALL IS" if i == 6 else f"THE {ORDINALS[i]} NUMBER IS"
        until = min(t + 5.5, LOTTERY_BONUS - 0.3) if i < 6 else 50.0
        s.text(t - 1.2, until, 954, 160, call, BARLOW_X, 34, CREAM, align=6, spacing=3, fade=(200, 200), layer=12)
        s.text(t, until, 954, 228, str(n), ARCHIVO, 96, YELLOW, align=6, shadow=5, shadow_color=PURPLE,
               fade=(0, 200), layer=12, extra="\\t(0,180,\\fscx130\\fscy130)\\t(180,400,\\fscx100\\fscy100)")

    # Recap
    s.shape(50.0, d, 0, 150, rect(W, 300), "&H00100410&", fade=(400, 600), layer=14, extra="\\1a&H30&")
    s.text(50.2, d, 512, 205, "TONIGHT'S WINNING NUMBERS", BARLOW_X, 50, MUSTARD, spacing=6, fade=(300, 600),
           layer=15)
    s.text(50.6, d, 512, 300, "  ".join(f"{n:02d}" for n in numbers[:6]), ARCHIVO, 70, WHITE, shadow=5,
           shadow_color=PURPLE, fade=(300, 600), layer=15)
    s.text(51.0, d, 512, 380, f"BONUS {numbers[6]:02d}", BARLOW_X, 48, YELLOW, spacing=4, fade=(300, 600), layer=15)
    s.text(52.0, d, 512, 430, "GL0RYUS LOTTO IS FOR ENTERTAINMENT ONLY. NO TICKETS. NO PRIZES. NO KIDDING.",
           BARLOW_S, 26, SKY, fade=(400, 600), layer=15)
    return Card(cards.lavfi_url(s.write("lottery"), d, music("lotto_bed.ogg"), background=LOTTO_BACKGROUND),
                d, "lottery")


# --- New Year's Eve ---

def new_year(year):
    """80 seconds: the last minute of the year, the final ten, then the new year with confetti."""
    d, m = NEW_YEAR_SECONDS, NEW_YEAR_MIDNIGHT
    midnight = datetime.datetime(year, 1, 1)
    s = Script()
    rnd = random.Random(year)
    s.text(0, m, 512, 96, "NEW YEAR'S EVE", BARLOW_X, 56, MUSTARD, spacing=10, fade=(400, 0))
    s.text(0, m, 512, 168, f"COUNTDOWN TO {year}", ARCHIVO, 64, shadow=5, slant=-0.12, fade=(400, 0))
    stripes(s, 0, m, 190, 214, 640, fade=(300, 0))
    for sec in range(int(m - 10)):
        t = midnight - datetime.timedelta(seconds=m - sec)
        s.text(sec, sec + 1, 512, 380, t.strftime("%I:%M:%S").lstrip("0") + " PM", ARCHIVO, 104, YELLOW, shadow=6,
               fade=None, layer=3)
    for i in range(10):  # 10 .. 1
        a = m - 10 + i
        s.text(a, a + 1, 512, 380, str(10 - i), ARCHIVO, 220, WHITE, shadow=8, shadow_color=SHADOW_RED, fade=(0, 250),
               layer=3, extra="\\fscx150\\fscy150\\t(0,350,\\fscx100\\fscy100)")
    # Midnight: flash, the year, confetti and fireworks
    s.shape(m, m + 0.6, 0, 0, rect(W, H), WHITE, fade=(0, 500), layer=9)
    s.text(m + 0.2, d, 512, 160, "HAPPY NEW YEAR!", ARCHIVO, 84, YELLOW, shadow=6, shadow_color=SHADOW_RED,
           slant=-0.12, fade=(200, 1200), layer=8)
    s.text(m + 0.5, d, 512, 330, str(year), ARCHIVO, 200, WHITE, shadow=8, shadow_color=SHADOW_RED,
           fade=(200, 1200), layer=8, extra="\\fscx40\\fscy40\\t(0,500,\\fscx110\\fscy110)\\t(500,800,\\fscx100\\fscy100)")
    s.text(m + 1.5, d, 512, 492, "FROM ALL OF US AT THE GL0RYUS NETWORK", BARLOW_X, 40, MUSTARD, spacing=6,
           fade=(400, 1200), layer=8)
    colors = (YELLOW, WHITE, "&H003030E0&", "&H0040C050&", "&H00E08040&", "&H00D040D0&", MUSTARD)
    for _ in range(140):
        t0 = m + rnd.uniform(0, 14)
        x = rnd.randrange(0, W)
        fall = rnd.randint(4000, 9000)
        s.shape(t0, min(d, t0 + fall / 1000), x, -20, rect(rnd.choice((8, 10, 12)), rnd.choice((14, 18))),
                rnd.choice(colors), fade=(0, 300), layer=6, align=5,
                extra=f"\\move({x},-20,{x + rnd.randint(-120, 120)},{H + 20})"
                      f"\\frz{rnd.randint(0, 360)}\\t(\\frz{rnd.randint(360, 1440)})\\t(\\frx{rnd.randint(180, 720)})")
    for _ in range(9):
        t0 = m + rnd.uniform(0.3, 16)
        x, y = rnd.randrange(120, W - 120), rnd.randrange(80, 300)
        color = rnd.choice(colors)
        for k in range(14):
            ang = k * 360 / 14
            s.shape(t0, t0 + 1.4, x, y, rect(3, 30), color, fade=(0, 700), layer=5, align=2,
                    extra=f"\\org({x},{y})\\frz{ang:.0f}\\fscy20\\t(0,500,\\fscy160)\\blur1")
    return Card(cards.lavfi_url(s.write("newyear"), d, music("newyear.ogg"), background="0x0a0820"), d, "newyear")


# --- Sign-off and sign-on ---

_anthem_seconds = None


def anthem():
    """The national anthem over a waving flag, before "This concludes our broadcast day"."""
    global _anthem_seconds
    track = music("anthem.ogg")
    if not (os.path.exists(track) and os.path.exists(FLAG_LOOP)):
        return None
    if _anthem_seconds is None:
        _anthem_seconds = round(_probe(track, 90.0), 2)
    d = _anthem_seconds
    video = (f"movie=filename={FLAG_LOOP}:loop=0,setpts=N/(30*TB),trim=duration={d:.2f},"
             f"fade=t=in:d=1.0,fade=t=out:st={d - 1.5:.2f}:d=1.5,format=yuv420p")
    return Card(_url(video, _audio(track, d, fade=1.0)), d, "anthem")


def power_off():
    """The set switching off: the picture collapses to a line, then a dot."""
    d = POWER_OFF_SECONDS
    return Card(_url(f"movie=filename={POWER_OFF_VIDEO},format=yuv420p", _audio(music("crt_off.ogg"), d, fade=0)),
                d, "poweroff")


def dark(seconds):
    """The set is off: black and silent."""
    d = f"{seconds:.2f}"
    return Card(_url(f"color=c=black:s=64x36:r=2:d={d}", f"anullsrc=r=48000:cl=stereo,atrim=end={d},{STEREO}"),
                seconds, "dark")


def sign_on():
    """The set warms up into colour bars, with a minute of 1 kHz tone."""
    d = SIGN_ON_SECONDS
    warm = music("crt_on.ogg")
    first = (f"amovie=filename={warm},apad,atrim=end=2.5" if os.path.exists(warm)
             else "anullsrc=r=48000:cl=stereo,atrim=end=2.5")
    audio = (f"{first},{STEREO}[warm];sine=frequency=1000:sample_rate=48000:duration={d - 2.5:.2f},volume=-22dB,"
             f"{STEREO}[tone];[warm][tone]concat=n=2:v=0:a=1")
    return Card(_url(f"movie=filename={SIGN_ON_VIDEO},format=yuv420p", audio), d, "signon")
