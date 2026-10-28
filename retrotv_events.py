"""
The GL0RYUS Network's occasions: holiday station IDs, holiday reminders, marathon openers, the "NEXT" lower third,
the late-night lottery drawing, the New Year's countdown, and the sign-off and sign-on (anthem,
the set switching off, colour bars). Same approach as retrotv_cards: ASS over an mpv lavfi URL.
"""
import datetime
import math
import os
import random
import subprocess

import retrotv_cards as cards
from retrotv_cards import (
    ARCHIVO, BARLOW_B, BARLOW_S, BARLOW_X, BLACK, CREAM, MUSTARD, SHADOW_RED, SKY, W, H, WHITE, YELLOW,
    ASSETS_DIR, CHANNEL, FONTS_DIR, NETWORK, RENDER, Card, Script, circle, clip_text, clock, fit, info_header, look,
    music, rect, stripes,
    BLUSH, BROWN, CLOVER, DEEP_NAVY, GLORY_BLUE, GLORY_RED, GOLD, KELLY, LIGHT_BLUE, LILAC_SHADOW, MAPLE, MARDI_GREEN,
    MOON, MOSS, ORANGE, PAPEL_COLORS, PASTELS, PINK, PUMPKIN, PURPLE, ROSE, RUST, SLATE, SLIME, WINE, XMAS_GREEN,
    XMAS_RED,
    HEART, LEAF, SHAMROCK, drift, fly_bats,
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
GREETING = "FROM ALL OF US AT GL0RYUS"   # the holiday IDs' sign-off line (the network's name, shortened to fit)

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

def station_id(now, good_morning=False, season=None, month=None):
    """The hourly ID: the bumper with the time over it, dressed up for a holiday (see LOOKS) when its
    sting, music/id_<season>.ogg, is there, or now and then naming a heritage month."""
    sting = music(f"id_{season}.ogg") if season else None
    dress = LOOKS.get(season)
    if not dress or not sting or not os.path.exists(sting):
        return cards.station_id(now, good_morning, month)
    d = ID_SECONDS
    s = Script()
    grade = dress(s, d, now, good_morning)
    path = s.write(f"id-{season}")
    video = (f"movie=filename={cards.ID_BACKGROUND},scale={RENDER.replace('x', ':')},setsar=1,{grade},"
             f"ass=filename={path}:fontsdir={FONTS_DIR},format=yuv420p")
    return Card(_url(video, _audio(sting, d, fade=0.2)), d, "id")


def _halloween_look(s, d, now, good_morning):
    fade = (300, 250)
    grade = "eq=brightness=-0.16:saturation=1.25:gamma_b=1.15,vignette=PI/3.5"
    # Lightning (on the sting's thunder crack), a moon and bats
    for a, b in ((0.0, 0.12), (2.22, 2.42), (2.56, 2.68)):
        s.shape(a, b, 0, 0, rect(W, H), WHITE, fade=(0, 120), layer=9, extra="\\1a&H60&")
    s.shape(0, d, 842, 52, circle(62), MOON, fade=(500, 250), layer=1, extra="\\blur6")
    fly_bats(s, 0, d, random.Random(now.hour), 4)
    top = "GOOD MORNING, BOILS & GHOULS" if good_morning else "HAPPY HALLOWEEN"
    # Up top, clear of the bumper's logo
    s.text(0.6, d, 512, 98, top, CREEPSTER, 58 if good_morning else 84, ORANGE, shadow=5,
           shadow_color=PURPLE, fade=fade, layer=7)
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{NETWORK} · {clock(now)}"
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
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{GREETING} · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 46, WHITE, spacing=4, shadow=3, shadow_color=XMAS_GREEN,
           fade=fade, layer=7)
    return grade


def _valentines_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, HEART, (PINK, ROSE, BLUSH), 16, scale=(50, 120), fall=(280, 520), sway=50, spin=20,
           rise=True, alpha="&H30&")
    if good_morning:
        top = "Good Morning, Sweetheart"
    else:
        top = "Happy Valentine's Day" if (now.month, now.day) == (2, 14) else "Be My Valentine"
    s.text(0.6, d, 512, 96, top, LOBSTER, 80, WHITE, outline=3, shadow=4, shadow_color=WINE, fade=fade, layer=7,
           extra=f"\\3c{ROSE}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{NETWORK} · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, BLUSH, spacing=4, shadow=3, shadow_color=WINE,
           fade=fade, layer=7)
    return "eq=brightness=-0.04:saturation=1.15,colorbalance=rs=0.10:bs=0.04"


def _stpatricks_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, SHAMROCK, (KELLY, CLOVER, KELLY), 14, scale=(60, 140))
    top = "Top o' the Morning!" if good_morning else "Happy St. Patrick's Day"
    s.text(0.6, d, 512, 98, top, GROVER, 74, CLOVER, outline=2, shadow=5, shadow_color=MOSS, fade=fade, layer=7,
           extra=f"\\3c{GOLD}")
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{NETWORK} · {clock(now)}"
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
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{GREETING} · {clock(now)}"
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
        color = (GLORY_RED, WHITE, LIGHT_BLUE, GOLD, GLORY_RED)[i]  # red, white, blue, gold
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
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{NETWORK} · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 50, WHITE, spacing=4, shadow=3, shadow_color=GLORY_RED,
           fade=fade, layer=7)
    return "eq=brightness=-0.12:saturation=1.2,vignette=PI/4"


def _thanksgiving_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, LEAF, (PUMPKIN, MAPLE, MUSTARD, RUST), 16, scale=(70, 140), sway=90, spin=120)
    top = "Good Morning!" if good_morning else "Happy Thanksgiving"
    s.text(0.6, d, 512, 98, top.upper(), RYE, 62, PUMPKIN, shadow=5, shadow_color=BROWN, fade=fade, layer=7)
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{GREETING} · {clock(now)}"
    s.text(1.6, d - 0.1, 512, 505, bottom, BARLOW_X, 46, CREAM, spacing=4, shadow=3, shadow_color=BROWN,
           fade=fade, layer=7)
    return "eq=brightness=-0.05:saturation=1.1,colorbalance=rs=0.08:gs=0.02:bs=-0.08"


def _day_look(season, top, font, size, color, shadow_color, outline=0, outline_color=WHITE, bottom=None,
              bottom_color=WHITE, bottom_shadow=BLACK, morning="Good Morning!", upper=False, extra="",
              grade="eq=brightness=-0.06:saturation=1.1"):
    """A station ID look for a holiday day: the props of the day's card look (solid here), the greeting up top
    in a display font, and the network (or `bottom`) and the time along the bottom."""
    def dress(s, d, now, good_morning):
        fade = (300, 250)
        look(season).dress(s, 0, d, layer=6, solid=True)
        text = morning if good_morning else top
        s.text(0.6, d, 512, 98, text.upper() if upper else text, font, size, color, outline=outline, shadow=5,
               shadow_color=shadow_color, fade=fade, layer=7, extra=(f"\\3c{outline_color}" if outline else "") + extra)
        line = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else f"{bottom or NETWORK} · {clock(now)}"
        s.text(1.6, d - 0.1, 512, 505, line, BARLOW_X, fit(line, BARLOW_X, 50, 960, spacing=4), bottom_color,
               spacing=4, shadow=3, shadow_color=bottom_shadow, fade=fade, layer=7)
        return grade
    return dress


PATRIOTIC = dict(font=ALFA, color=WHITE, shadow_color=GLORY_BLUE, outline=3, outline_color=GLORY_RED, upper=True)

LOOKS = {"halloween": _halloween_look, "christmas": _christmas_look, "valentines": _valentines_look,
         "stpatricks": _stpatricks_look, "easter": _easter_look, "july4": _july4_look,
         "thanksgiving": _thanksgiving_look,
         "newyearsday": _day_look("newyearsday", "Happy New Year!", LOBSTER, 84, GOLD, GLORY_BLUE, 3, WHITE,
                                  bottom=GREETING, bottom_shadow=GLORY_BLUE),
         "mlkday": _day_look("mlkday", "Martin Luther King Jr. Day", ALFA, 52, CREAM, GLORY_BLUE, bottom_color=GOLD,
                             grade="eq=brightness=-0.06:saturation=0.9"),
         "groundhog": _day_look("groundhog", "Happy Groundhog Day", CHEWY, 80, WHITE, SLATE, 3, SLATE,
                                bottom="SIX MORE WEEKS OF WINTER?", bottom_color=SKY,
                                grade="eq=brightness=-0.03:saturation=0.8,colorbalance=bs=0.06"),
         "mardigras": _day_look("mardigras", "Happy Mardi Gras", LOBSTER, 84, GOLD, MARDI_GREEN, 3, PURPLE,
                                bottom_color=GOLD, bottom_shadow=PURPLE,
                                grade="eq=saturation=1.2,colorbalance=rs=0.04:bs=0.06"),
         # The greeting starts upside down and flips the right way up
         "aprilfools": _day_look("aprilfools", "Happy April Fools' Day", CHEWY, 74, SLIME, PURPLE, 3, BLACK,
                                 extra="\\frz180\\t(1000,1400,\\frz360)", grade="hue=h=40,eq=saturation=1.15"),
         "cincodemayo": _day_look("cincodemayo", "¡Feliz Cinco de Mayo!", CHEWY, 76, PAPEL_COLORS[4], PAPEL_COLORS[2],
                                  3, PAPEL_COLORS[0], bottom_shadow=PAPEL_COLORS[2],
                                  grade="eq=brightness=-0.02:saturation=1.2"),
         "mothersday": _day_look("mothersday", "Happy Mother's Day", LOBSTER, 82, WHITE, WINE, 3, ROSE,
                                 bottom=GREETING, bottom_color=BLUSH, bottom_shadow=WINE, morning="Good Morning, Mom!",
                                 grade="eq=brightness=-0.04:saturation=1.15,colorbalance=rs=0.08"),
         "memorialday": _day_look("memorialday", "Memorial Day", size=70, bottom="HONORING THOSE WHO GAVE ALL",
                                  grade="eq=brightness=-0.1:saturation=0.8,vignette=PI/4", **PATRIOTIC),
         "fathersday": _day_look("fathersday", "Happy Father's Day", RYE, 62, MUSTARD, DEEP_NAVY, bottom=GREETING,
                                 bottom_color=CREAM, bottom_shadow=DEEP_NAVY, morning="Morning, Dad!", upper=True,
                                 grade="eq=brightness=-0.04:saturation=1.05"),
         "juneteenth": _day_look("juneteenth", "Happy Juneteenth", size=70, bottom_color=GOLD,
                                 grade="eq=brightness=-0.08:saturation=1.1", **PATRIOTIC),
         "laborday": _day_look("laborday", "Happy Labor Day", size=66, bottom_shadow=GLORY_RED,
                               grade="eq=brightness=-0.1:saturation=1.15", **PATRIOTIC),
         "veteransday": _day_look("veteransday", "Veterans Day", size=70, bottom="THANK YOU FOR YOUR SERVICE",
                                  grade="eq=brightness=-0.08:saturation=0.9,vignette=PI/4", **PATRIOTIC)}


# --- Holiday reminders ---

REMINDER_TIPS = {"Valentine's Day": "FLOWERS · CHOCOLATE · DINNER PLANS"}


def reminder(name, when, now, season=None):
    """A heads-up in the breaks ahead of a day you're expected to get someone something or spend time with
    them (Valentine's, Mother's and Father's Day), counting down through the day itself."""
    d = 7.0  # the Up Next card's music bed
    days = (when - now.date()).days
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    kicker = "TODAY IS" if days == 0 else "DON'T FORGET" if days == 1 else "HEADS UP"
    s.text(0, d, 512, 150, kicker, BARLOW_X, 50, lk.accent, spacing=6)
    title = name.upper()
    s.text(0.1, d, 512, 240, title, ARCHIVO, fit(title, ARCHIVO, 92, 900), lk.headline, shadow=6,
           shadow_color=lk.shadow, slant=-0.15)
    stripes(s, 0, d, 190, 305, 640, colors=lk.stripes)
    date = when.strftime("%A, %B %-d").upper()
    line = date if days == 0 else f"TOMORROW · {date}" if days == 1 else f"{date} · {days} DAYS AWAY"
    s.text(0.4, d, 512, 405, line, BARLOW_X, 46, CREAM, spacing=2)
    tip = "MAKE SOME TIME FOR THEM TODAY" if days == 0 else REMINDER_TIPS.get(name, "A CARD, A GIFT OR A VISIT")
    s.text(0.7, d, 512, 470, tip, BARLOW_B, 38, lk.accent, spacing=6)
    return Card(cards.lavfi_url(s.write("reminder"), d, music("upnext_bed.ogg"), background=lk.bg), d, "reminder")


# --- Marathons ---

def marathon(name, series_name, start, season=None):
    """Opens a marathon block: "FRIDAY NIGHT / MARATHON / The Simpsons", in the season's look."""
    d = MARATHON_SECONDS
    lk = look(season)
    s = Script()
    lk.dress(s, 0, d)
    kicker = name.upper().replace("MARATHON", "").strip() or "TONIGHT"
    s.text(0, d, 512, 120, kicker, BARLOW_X, 54, lk.accent, spacing=10, fade=(250, 300))
    s.text(0.15, d, 512, 222, "MARATHON", ARCHIVO, 128, lk.headline, shadow=7, shadow_color=lk.shadow, slant=-0.15,
           fade=(250, 300), extra="\\t(0,250,\\fscx112\\fscy112)\\t(250,450,\\fscx100\\fscy100)")
    stripes(s, 0.2, d, 190, 300, 640, colors=lk.stripes)
    series_name = clip_text(series_name.upper(), 30)
    s.text(0.5, d, 512, 405, series_name, BARLOW_X, fit(series_name, BARLOW_X, 76, 880), CREAM, shadow=3,
           shadow_color=lk.shadow)
    if season == "halloween":
        s.text(1.0, d, 512, 492, "HALLOWEEN EDITION", CREEPSTER, 52, ORANGE, shadow=4, shadow_color=PURPLE)
    elif season == "christmas":
        s.text(1.0, d, 512, 492, "HOLIDAY EDITION", XMAS, 56, XMAS_RED, outline=3, extra=f"\\3c{WHITE}\\b1")
    else:
        s.text(1.0, d, 512, 492, f"BACK TO BACK STARTING NOW · {clock(start)}", BARLOW_S, 38, lk.accent, spacing=4)
    return Card(cards.lavfi_url(s.write("marathon"), d, music("marathon_sting.ogg"), background=lk.bg), d, "card")


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
LOTTO_BAND = "&H00401A2C&"
DRUM_X, DRUM_Y, DRUM_R = 512, 330, 132   # under the info screens' header
TRAY_Y = 528
ORDINALS = ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH")


def _ball(s, start, end, x, y, number, extra="", layer=5, r=40):
    color = BALL_COLORS[min(4, number // 10)]
    s.shape(start, end, x, y, circle(r), color, fade=(0, 300), layer=layer, align=5, extra=extra)
    s.shape(start, end, x, y, circle(r * 0.62), WHITE, fade=(0, 300), layer=layer + 1, align=5, extra=extra)
    s.text(start, end, x, y + 1, str(number), ARCHIVO, int(r * 0.78), "&H00161616&", fade=(0, 300),
           layer=layer + 2, extra=extra)


def lottery(when, season=None):
    """The late-night drawing: six balls and a bonus ball from a tumbling drum, with a recap."""
    d = LOTTERY_SECONDS
    numbers = random.sample(range(1, 50), 7)
    s = Script()
    fade = (400, 600)
    # Studio: header, the drum (its chute hanging from the header), the tray
    info_header(s, d, [(0, d, "Lotto · Pick 6 + Bonus")], when, band=LOTTO_BAND, season=season)
    s.shape(0, d, DRUM_X, DRUM_Y, circle(DRUM_R + 8), "&H00603018&", fade=fade, layer=1, align=5,
            extra="\\1a&H90&")
    s.raw(0, d, f"\\an5\\pos({DRUM_X},{DRUM_Y})\\bord5\\shad0\\3c{SKY}\\1a&HFF&\\fad({fade[0]},{fade[1]})\\p1",
          circle(DRUM_R), layer=8)
    s.shape(0, d, DRUM_X - 14, 174, rect(28, DRUM_Y - DRUM_R - 172), SKY, fade=fade, layer=8, align=7,
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
        s.text(t - 1.2, until, 954, 222, call, BARLOW_X, 30, CREAM, align=6, spacing=3, fade=(200, 200), layer=12)
        s.text(t, until, 954, 292, str(n), ARCHIVO, 96, YELLOW, align=6, shadow=5, shadow_color=PURPLE,
               fade=(0, 200), layer=12, extra="\\t(0,180,\\fscx130\\fscy130)\\t(180,400,\\fscx100\\fscy100)")

    # Recap
    s.shape(50.0, d, 0, 180, rect(W, 290), "&H00100410&", fade=(400, 600), layer=14, extra="\\1a&H30&")
    s.text(50.2, d, 512, 228, "TONIGHT'S WINNING NUMBERS", BARLOW_X, 50, MUSTARD, spacing=6, fade=(300, 600),
           layer=15)
    s.text(50.6, d, 512, 318, "  ".join(f"{n:02d}" for n in numbers[:6]), ARCHIVO, 70, WHITE, shadow=5,
           shadow_color=PURPLE, fade=(300, 600), layer=15)
    s.text(51.0, d, 512, 398, f"BONUS {numbers[6]:02d}", BARLOW_X, 48, YELLOW, spacing=4, fade=(300, 600), layer=15)
    s.text(52.0, d, 512, 448, f"{CHANNEL} LOTTO IS FOR ENTERTAINMENT ONLY. NO TICKETS. NO PRIZES. NO KIDDING.",
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
    s.text(m + 1.5, d, 512, 492, f"FROM ALL OF US AT THE {NETWORK}", BARLOW_X, 40, MUSTARD, spacing=6,
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
