"""
The GL0RYUS Network's occasions: holiday station IDs, holiday reminders, marathon openers, the "NEXT" lower third,
the late-night lottery drawing, the New Year's countdown, and the sign-off and sign-on (anthem,
the set switching off, colour bars). Same approach as retrotv.cards: ASS over an mpv lavfi URL.
"""
import datetime
import math
import os
import random
import subprocess

from retrotv import brand, cards
from retrotv.brand import Part
from retrotv.cards import (
    BLACK, CREAM, MUSTARD, W, H, WHITE,
    ASSETS_DIR, CHANNEL, FONTS_DIR, RENDER, Card, Script, circle, clock, esc, info_header, look, music, rect,
    BROWN, CLOVER, DEEP_NAVY, GLORY_BLUE, GLORY_RED, GOLD, KELLY, LIGHT_BLUE, LILAC_SHADOW, MAPLE, MARDI_GREEN,
    MOON, MOSS, ORANGE, PAPEL_COLORS, PASTELS, PINK, PUMPKIN, PURPLE, ROSE, RUST, SLATE, SLIME, WINE, XMAS_GREEN,
    XMAS_RED, BLUSH,
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
MARATHON_AD_SECONDS = 15.0
LAUNCH_SECONDS = 5.0
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
    """The hourly ID: the ID card with the time on it, dressed up for a holiday (see LOOKS) when its sting,
    music/id_<season>.ogg, is there: the holiday's props and its greeting up top in a display font, over the
    sting. Otherwise cards.station_id, which now and then names a heritage month."""
    sting = music(f"id_{season}.ogg") if season else None
    dress = LOOKS.get(season)
    if not dress or not sting or not os.path.exists(sting):
        return cards.station_id(now, good_morning, month)
    d = ID_SECONDS
    s = Script()
    tagline = dress(s, d, now, good_morning)
    cards.id_card(s, d, look(season), tagline, em=110, top=150)
    path = s.write(f"id-{season}")
    video = f"color=c={brand.PURPLE_BG}:s={RENDER}:r=30:d={d:.2f},ass=filename={path}:fontsdir={FONTS_DIR},format=yuv420p"
    return Card(_url(video, _audio(sting, d, fade=0.2)), d, "id")


def _tagline(now, good_morning, bottom=None):
    """A holiday ID's line on the white chip: the time, after `bottom` if there is one."""
    if good_morning:
        return f"WE'RE ON THE AIR · {clock(now)}"
    return f"{bottom} · {clock(now)}" if bottom else clock(now)


def _halloween_look(s, d, now, good_morning):
    fade = (300, 250)
    # Lightning (on the sting's thunder crack), a moon and bats
    for a, b in ((0.0, 0.12), (2.22, 2.42), (2.56, 2.68)):
        s.shape(a, b, 0, 0, rect(W, H), WHITE, fade=(0, 120), layer=19, extra="\\1a&H60&")
    s.shape(0, d, 842, 52, circle(62), MOON, fade=(500, 250), layer=1, extra="\\blur6")
    fly_bats(s, 0, d, random.Random(now.hour), 4)
    top = "GOOD MORNING, BOILS & GHOULS" if good_morning else "HAPPY HALLOWEEN"
    s.text(0.6, d, 512, 82, top, CREEPSTER, 58 if good_morning else 84, ORANGE, shadow=5,
           shadow_color=brand.INK, fade=fade, layer=17)
    return _tagline(now, good_morning)


def _christmas_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    for _ in range(70):
        x, y = rnd.randrange(0, W), rnd.randrange(-H, H)
        r = rnd.choice((2, 3, 3, 4, 5))
        fall = rnd.randint(150, 330)
        s.shape(0, d, 0, 0, circle(r), WHITE, fade=(200, 250), layer=16, align=5,
                extra=f"\\move({x},{y},{x + rnd.randint(-60, 60)},{y + fall})\\blur1")
    top = "GOOD MORNING!" if good_morning else "HAPPY HOLIDAYS"
    s.text(0.6, d, 512, 80, top, XMAS, 84, XMAS_RED, outline=4, shadow=4, shadow_color=WHITE,
           fade=fade, layer=17, extra=f"\\3c{WHITE}\\4c{XMAS_GREEN}\\b1")
    return _tagline(now, good_morning, GREETING)


def _valentines_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, HEART, (PINK, ROSE, BLUSH), 16, scale=(50, 120), fall=(280, 520), sway=50, spin=20,
          rise=True, alpha="&H30&", layer=16)
    if good_morning:
        top = "Good Morning, Sweetheart"
    else:
        top = "Happy Valentine's Day" if (now.month, now.day) == (2, 14) else "Be My Valentine"
    s.text(0.6, d, 512, 80, top, LOBSTER, 80, WHITE, outline=3, shadow=4, shadow_color=WINE, fade=fade, layer=17,
           extra=f"\\3c{ROSE}")
    return _tagline(now, good_morning)


def _stpatricks_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, SHAMROCK, (KELLY, CLOVER, KELLY), 14, scale=(60, 140), layer=16)
    top = "Top o' the Morning!" if good_morning else "Happy St. Patrick's Day"
    s.text(0.6, d, 512, 82, top, GROVER, 74, CLOVER, outline=2, shadow=5, shadow_color=MOSS, fade=fade, layer=17,
           extra=f"\\3c{GOLD}")
    return _tagline(now, good_morning)


def _easter_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    for _ in range(12):  # glossy pastel eggs tumbling down
        x, y = rnd.randrange(0, W), rnd.randrange(-H // 2, H - 100)
        dx, dy = rnd.randint(-50, 50), rnd.randint(200, 360)
        r = rnd.randint(16, 26)
        a0, turn = rnd.randint(-40, 40), rnd.randint(-50, 50)
        spin = f"\\frz{a0}\\t(\\frz{a0 + turn})"
        s.shape(0, d, 0, 0, circle(r), rnd.choice(PASTELS), fade=(200, 250), layer=16, align=5,
                extra=f"\\move({x},{y},{x + dx},{y + dy})\\fscx78{spin}")
        s.shape(0, d, 0, 0, circle(r * 0.22), WHITE, fade=(200, 250), layer=16, align=5,
                extra=f"\\move({x - r * 0.3:.0f},{y - r * 0.45:.0f},{x + dx - r * 0.3:.0f},{y + dy - r * 0.45:.0f})"
                      f"\\1a&H50&")
    top = "Good Morning!" if good_morning else "Happy Easter"
    s.text(0.6, d, 512, 80, top, CHEWY, 88, PASTELS[0], outline=4, shadow=4, shadow_color=LILAC_SHADOW, fade=fade,
           layer=17, extra=f"\\3c{WHITE}")
    return _tagline(now, good_morning, GREETING)


def _july4_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    # Fireworks around the badge: a flash, then sparks flying out and dropping as they fade
    spots = [(rnd.randint(110, 300), rnd.randint(60, 230)), (rnd.randint(720, 920), rnd.randint(60, 230)),
             (rnd.randint(380, 640), rnd.randint(40, 120)), (rnd.randint(90, 250), rnd.randint(400, 520)),
             (rnd.randint(780, 940), rnd.randint(400, 520))]
    for i, (bx, by) in enumerate(spots):
        t0 = 0.15 + i * 0.7 + rnd.uniform(-0.1, 0.1)
        color = (GLORY_RED, WHITE, LIGHT_BLUE, GOLD, GLORY_RED)[i]  # red, white, blue, gold
        s.shape(t0, t0 + 0.3, bx, by, circle(26), WHITE, fade=(0, 250), layer=16, align=5, extra="\\blur8")
        for k in range(24):
            a = 2 * math.pi * k / 24 + rnd.uniform(-0.08, 0.08)
            reach = rnd.randint(120, 175)
            ex, ey = bx + reach * math.cos(a), by + reach * math.sin(a) + 40
            s.shape(t0, t0 + 1.7, bx, by, circle(6), color, fade=(0, 800), layer=16, align=5,
                    extra=f"\\move({bx},{by},{ex:.0f},{ey:.0f},0,1400)\\blur1.5")
    top = "Good Morning!" if good_morning else "Happy 4th of July"
    s.text(0.6, d, 512, 82, top.upper(), ALFA, 66, WHITE, outline=3, shadow=5, shadow_color=GLORY_BLUE, fade=fade,
           layer=17, extra=f"\\3c{GLORY_RED}")
    return _tagline(now, good_morning)


def _thanksgiving_look(s, d, now, good_morning):
    fade = (300, 250)
    rnd = random.Random(now.hour)
    drift(s, 0, d, rnd, LEAF, (PUMPKIN, MAPLE, MUSTARD, RUST), 16, scale=(70, 140), sway=90, spin=120, layer=16)
    top = "Good Morning!" if good_morning else "Happy Thanksgiving"
    s.text(0.6, d, 512, 82, top.upper(), RYE, 62, PUMPKIN, shadow=5, shadow_color=BROWN, fade=fade, layer=17)
    return _tagline(now, good_morning, GREETING)


def _day_look(season, top, font, size, color, shadow_color, outline=0, outline_color=WHITE, bottom=None,
              morning="Good Morning!", upper=False, extra=""):
    """A station ID look for a holiday day: the props of the day's card look (solid here), the greeting up top
    in a display font, and `bottom` (if any) before the time on the tagline."""
    def dress(s, d, now, good_morning):
        fade = (300, 250)
        look(season).dress(s, 0, d, layer=16, solid=True)
        text = morning if good_morning else top
        s.text(0.6, d, 512, 82, text.upper() if upper else text, font, size, color, outline=outline, shadow=5,
               shadow_color=shadow_color, fade=fade, layer=17, extra=(f"\\3c{outline_color}" if outline else "") + extra)
        return _tagline(now, good_morning, bottom)
    return dress


PATRIOTIC = dict(font=ALFA, color=WHITE, shadow_color=GLORY_BLUE, outline=3, outline_color=GLORY_RED, upper=True)

LOOKS = {"halloween": _halloween_look, "christmas": _christmas_look, "valentines": _valentines_look,
         "stpatricks": _stpatricks_look, "easter": _easter_look, "july4": _july4_look,
         "thanksgiving": _thanksgiving_look,
         "newyearsday": _day_look("newyearsday", "Happy New Year!", LOBSTER, 84, GOLD, GLORY_BLUE, 3, WHITE,
                                  bottom=GREETING),
         "mlkday": _day_look("mlkday", "Martin Luther King Jr. Day", ALFA, 52, CREAM, GLORY_BLUE),
         "groundhog": _day_look("groundhog", "Happy Groundhog Day", CHEWY, 80, WHITE, SLATE, 3, SLATE,
                                bottom="SIX MORE WEEKS OF WINTER?"),
         "mardigras": _day_look("mardigras", "Happy Mardi Gras", LOBSTER, 84, GOLD, MARDI_GREEN, 3, PURPLE),
         # The greeting starts upside down and flips the right way up
         "aprilfools": _day_look("aprilfools", "Happy April Fools' Day", CHEWY, 74, SLIME, PURPLE, 3, BLACK,
                                 extra="\\frz180\\t(1000,1400,\\frz360)"),
         "cincodemayo": _day_look("cincodemayo", "¡Feliz Cinco de Mayo!", CHEWY, 76, PAPEL_COLORS[4], PAPEL_COLORS[2],
                                  3, PAPEL_COLORS[0]),
         "mothersday": _day_look("mothersday", "Happy Mother's Day", LOBSTER, 82, WHITE, WINE, 3, ROSE,
                                 bottom=GREETING, morning="Good Morning, Mom!"),
         "memorialday": _day_look("memorialday", "Memorial Day", size=70, bottom="HONORING THOSE WHO GAVE ALL",
                                  **PATRIOTIC),
         "fathersday": _day_look("fathersday", "Happy Father's Day", RYE, 62, MUSTARD, DEEP_NAVY, bottom=GREETING,
                                 morning="Morning, Dad!", upper=True),
         "juneteenth": _day_look("juneteenth", "Happy Juneteenth", size=70, **PATRIOTIC),
         "laborday": _day_look("laborday", "Happy Labor Day", size=66, **PATRIOTIC),
         "veteransday": _day_look("veteransday", "Veterans Day", size=70, bottom="THANK YOU FOR YOUR SERVICE",
                                  **PATRIOTIC)}


# --- Holiday reminders ---

REMINDER_TIPS = {"Valentine's Day": "FLOWERS · CHOCOLATE · DINNER PLANS"}


def _centred(s, start, d, y, h, parts, shadow=None, fade=brand.FADE, slide=None, layer=14):
    """A strip of parts centred across the screen."""
    width = sum(p.size() for p in parts)
    return brand.strip(s, start, d, W / 2 - width / 2, y, h, parts, shadow=shadow, fade=fade, slide=slide,
                       layer=layer)


def reminder(name, when, now, season=None):
    """A heads-up in the breaks ahead of a day you're expected to get someone something or spend time with
    them (Valentine's, Mother's and Father's Day), counting down through the day itself."""
    d = 7.0  # the Up Next card's music bed
    days = (when - now.date()).days
    lk = look(season)
    s = Script()
    brand.id_ground(s, d, lk.tint)
    lk.dress(s, 0, d, layer=3)
    kicker = "TODAY IS" if days == 0 else "DON'T FORGET" if days == 1 else "HEADS UP"
    _centred(s, 0, d, 96, 46, [Part(kicker, brand.ANTON, 32, brand.INK, lk.tint, pad=22)], shadow=(5, 5, brand.INK),
             slide=(0, 350, 80))
    title = name.upper()
    em = brand.shrink(title, brand.ANTON, 84, 860)
    _centred(s, 0.1, d, 160, 112, [Part(title, brand.ANTON, em, brand.INK, brand.YELLOW, pad=34)],
             shadow=(8, 8, brand.INK), slide=(100, 450, 80))
    date = when.strftime("%A, %B %-d").upper()
    line = date if days == 0 else f"TOMORROW · {date}" if days == 1 else f"{date} · {days} DAYS AWAY"
    _centred(s, 0.4, d, 452, 42, [Part(line, brand.ARCHIVO_BLACK, 24, brand.INK, brand.WHITE, pad=20)],
             shadow=(5, 5, brand.INK), slide=(400, 750, 80))
    tip = "MAKE SOME TIME FOR THEM TODAY" if days == 0 else REMINDER_TIPS.get(name, "A CARD, A GIFT OR A VISIT")
    _centred(s, 0.7, d, 508, 38, [Part(tip, brand.ARCHIVO_BLACK, 20, brand.YELLOW, brand.INK, pad=18)],
             slide=(700, 1050, 80))
    return s.card("reminder", d, music("upnext_bed.ogg"), background=brand.PURPLE_BG, card_kind="reminder")


# --- Marathons ---

def marathon(name, series_name, start, season=None, lineup=()):
    """Opens a marathon block, as the guide's Marathon card: the G|TV MARATHON tag, the show, BACK TO BACK and
    when it started; on the purple side the date and the lineup [(start, episode)], the one on now first."""
    d = MARATHON_SECONDS
    lk = look(season)
    s = Script()
    brand.split(s, d)
    lk.dress(s, 0, d, layer=2)
    kicker = name.upper().replace("MARATHON", "").strip()
    brand.header(s, d, [(0, d, "Marathon")], accent=lk.tint)
    brand.date_chip(s, d, start, text=start.strftime("%A, %b %-d").upper())
    lines, em = cards.wrapped(series_name.upper(), brand.ANTON, 80, 500)
    y = 190
    for i, line in enumerate(lines):
        brand.words(s, 0.15, d, 52, y + i * em * 0.95, line, brand.ANTON, em, brand.WHITE, slide=(150, 550, 90))
    y += (len(lines) - 1) * em * 0.95 + em * 0.5 + 36
    tagline = {"halloween": "HALLOWEEN EDITION", "christmas": "HOLIDAY EDITION"}.get(season or "", "BACK TO BACK.")
    if kicker:
        tagline = f"{kicker}. {tagline}"
    brand.words(s, 0.3, d, 54, y, brand.clip(tagline, brand.ANTON, 36, 520), brand.ANTON, 36, brand.YELLOW,
                slide=(300, 700, 90))
    brand.strip(s, 0.5, d, 62, y + 40, 40, [Part(f"STARTING NOW · {clock(start)}", brand.ARCHIVO_BLACK, 20, brand.INK,
                                                 lk.tint, pad=18)], slide=(500, 900, 90))
    rows = list(lineup)[:6]
    if rows:
        brand.strip(s, 0.2, d, 700, 132, 36, [Part("THE LINEUP", brand.ANTON, 24, brand.INK, brand.YELLOW, pad=14,
                                                   spacing=1)], shadow=(5, 5, brand.INK), layer=6)
        for i, (when, episode) in enumerate(rows):
            top = 182 + i * 58
            first = i == 0
            a = 0.3 + i * 0.08
            brand.slab(s, a, d, 690, top, 280, 48, brand.WHITE if first else brand.INK, shadow=(6, 6, brand.INK),
                       layer=6)
            brand.slab(s, a, d, 690, top, 92, 48, brand.PURPLE if first else brand.YELLOW, layer=7)
            brand.words(s, a, d, 736, top + 24, "NOW" if first else clock(when), brand.ARCHIVO_BLACK, 16,
                        brand.WHITE if first else brand.INK, align="center", layer=8)
            code, title = cards.episode_parts(episode)
            text = brand.clip((title or code or "").upper(), brand.ARCHIVO, 18, 162)
            brand.words(s, a, d, 796, top + 24, text, brand.ARCHIVO, 18, brand.INK if first else brand.WHITE, layer=8)
    else:
        w, h = brand.badge_size(96, "id")
        brand.badge(s, 0, d, 818 - w / 2, 330 - h / 2, 96, lk.tint, kind="id", swing=(-14, 600))
    return s.card("marathon", d, music("marathon_sting.ogg"), background=brand.INK_BG)


def marathon_ad(image, name, when, start_time, season=None):
    """An ad for the next marathon: its picture panning slowly behind the G|TV MARATHON tag and, along the
    bottom, when it is (THIS FRIDAY), the show on yellow and BACK TO BACK FROM 4:30 PM."""
    d = MARATHON_AD_SECONDS
    lk = look(season)
    s = Script()
    fade = (500, 500)
    brand.header(s, d, [(0, d, "Marathon")], accent=lk.tint, fade=fade)
    brand.strip(s, 0.3, d, 56, 384, 44, [Part(when.upper(), brand.ANTON, 30, brand.INK, lk.tint, pad=18)],
                shadow=(5, 5, brand.INK), fade=fade, slide=(300, 700, 90))
    name = name.upper()
    em = brand.shrink(name, brand.ANTON, 64, 820)
    brand.strip(s, 0.5, d, 56, 436, 86, [Part(name, brand.ANTON, em, brand.INK, brand.YELLOW, pad=28)],
                shadow=(7, 7, brand.INK), fade=fade, slide=(500, 900, 90))
    brand.strip(s, 0.8, d, 84, 530, 40, [Part(f"BACK TO BACK FROM {start_time}", brand.ARCHIVO_BLACK, 21, brand.INK,
                                              brand.WHITE, pad=18)], shadow=(5, 5, brand.INK), fade=fade,
                slide=(800, 1200, 90))
    frames = int(d * 30)
    bigger = "" if cards.CRT else ",scale=iw*2:ih*2"   # the pictures are cut a little bigger than the 512x300 card
    video = (f"movie=filename={image},loop=loop=-1:size=1:start=0,setpts=N/(30*TB),trim=duration={d:.2f}{bigger},format=rgb24,"
             f"crop={RENDER.replace('x', ':')}:x='(iw-ow)*min(1,n/{frames})':y='(ih-oh)*min(1,n/{frames})',setsar=1,"
             f"ass=filename={s.write('marathon-ad')}:fontsdir={FONTS_DIR},fade=t=in:st=0:d=0.4,fade=t=out:st={d - 0.5:.2f}:d=0.5,format=yuv420p")
    return Card(_url(video, _audio(music("guide_bed.ogg"), d)), d, "card")


# --- Lower thirds over a show (the NEXT snipe, the NEXT / LATER / TONIGHT promo) ---

# Drawn on the 1024x600 canvas and laid over the 342x200 picture, so they're big: a label part and a name part
# on one leaning strip, a lower-third poster (retrotv.overlays.ART_X, ART_BOTTOM) standing on its left end
BAR_TOP, BAR_HEIGHT = 462, 66
BAR_LEFT = 44
BAR_RIGHT = 980


def _lower_third(s, start, end, label, label_fill, label_ink, name, name_fill, slide=True, fade=(0, 300)):
    """One lower third: label (NEXT, LATER...) and name on a strip with a hard shadow; the name shrinks to fit
    before it is cut. slide: rides in from off the left edge."""
    label_part = Part(label, brand.ANTON, 40, label_ink, label_fill, pad=20)
    room = BAR_RIGHT - BAR_LEFT - label_part.size() - 40
    em = brand.shrink(name, brand.ANTON, 44, room, least=0.75)
    name = brand.clip(name, brand.ANTON, em, room)
    parts = [label_part, Part(name, brand.ANTON, em, brand.WHITE, name_fill, pad=20, width=220)]
    move = (0, 450, 780) if slide else None   # in step with the poster (overlays.ART_SLIDE)
    brand.strip(s, start, end, BAR_LEFT, BAR_TOP, BAR_HEIGHT, parts, shadow=(7, 7, brand.INK), fade=fade, slide=move,
                layer=3)


def snipe(name, start, end):
    """An ASS file for the vf chain: a NEXT lower third that slides in at file time `start`."""
    s = Script()
    _lower_third(s, start, end, "NEXT", brand.YELLOW, brand.INK, name.upper(), brand.PURPLE)
    return s.write("snipe")


def launch_cut_in(rocket, mission, where, season=None):
    """Cuts in for a live launch: PARDON THE INTERRUPTION, LIVE LAUNCH, the rocket, the mission and the pad."""
    d = LAUNCH_SECONDS
    lk = look(season)
    s = Script()
    brand.id_ground(s, d, lk.tint)
    lk.dress(s, 0, d, layer=3)
    _centred(s, 0, d, 60, 44, [Part("PARDON THE INTERRUPTION", brand.ANTON, 30, brand.INK, lk.tint, pad=20)],
             shadow=(5, 5, brand.INK), slide=(0, 350, 80))
    _centred(s, 0.15, d, 120, 120, [Part("LIVE LAUNCH", brand.ANTON, 92, brand.INK, brand.YELLOW, pad=36)],
             shadow=(8, 8, brand.INK), slide=(150, 500, 80))
    rocket = rocket.upper()
    em = brand.shrink(rocket, brand.ANTON, 52, 860)
    _centred(s, 0.5, d, 338, 72, [Part(rocket, brand.ANTON, em, brand.WHITE, brand.INK, pad=28)],
             shadow=(6, 6, brand.YELLOW), slide=(500, 850, -80))
    mission = brand.clip(mission.upper(), brand.ARCHIVO_BLACK, 24, 820)
    _centred(s, 0.7, d, 432, 42, [Part(mission, brand.ARCHIVO_BLACK, 24, brand.INK, brand.WHITE, pad=20)],
             slide=(700, 1050, -80))
    where = brand.clip(f"A SPACE LAUNCH IS HAPPENING · {where.upper()}", brand.ARCHIVO_BLACK, 19, 860)
    _centred(s, 1.0, d, 500, 36, [Part(where, brand.ARCHIVO_BLACK, 19, brand.YELLOW, brand.INK, pad=18)])
    return s.card("launch", d, music("marathon_sting.ogg"), background=brand.PURPLE_BG)


PROMO_STYLES = {   # the guide's NEXT / LATER / TONIGHT: label fill, label ink, name fill
    "NEXT": (brand.YELLOW, brand.INK, brand.PURPLE),
    "LATER": (brand.ORANGE, brand.INK, brand.INK),
    "TONIGHT": (brand.WHITE, brand.INK, brand.INK),
}


def promo(panels, start, panel_seconds=4.5):
    """An ASS file for the vf chain: a lower third sliding in at file time `start`, then stepping through
    (label, name) panels such as NEXT / LATER / TONIGHT before it fades away."""
    s = Script()
    for i, (label, name) in enumerate(panels):
        a, b = start + i * panel_seconds, start + (i + 1) * panel_seconds
        first, last = i == 0, i == len(panels) - 1
        label = label.upper()
        fill, ink, name_fill = PROMO_STYLES.get(label, (brand.YELLOW, brand.INK, brand.PURPLE))
        _lower_third(s, a, b, label, fill, ink, name.upper(), name_fill, slide=first,
                     fade=(0 if first else 200, 300 if last else 150))
    return s.write("promo")


# --- Pop-Up Video ---

POPUP_SECONDS = 6.5     # each bubble's time on screen
POPUP_FIRST = 20.0      # the first, once the lower third has gone
POPUP_END_GAP = 32.0    # none this close to the end: clear of the NEXT lower third in a block's last video
                        # (config.SNIPE_SECONDS, 30 s), which sits where the bottom-left bubbles do
POPUP_EVERY = 22.0      # at most one this often
# A bubble's width and type, and where the bubbles go in turn (their top-left corners; a y of None stands the bubble
# on POPUP_BOTTOM). Under the CRT the type has to be big to read at 200 lines; at full size the bubbles are smaller,
# in mixed case, and take turns in the bottom corners, where the lower thirds are (gone by POPUP_FIRST)
if cards.CRT:
    POPUP_W, POPUP_TEXT, POPUP_FONT, POPUP_CAPS = 480, 30.0, brand.ARCHIVO_BLACK, True
    POPUP_SLOTS = ((70, 52), (420, 120), (70, 220), (420, 230))
else:
    POPUP_W, POPUP_TEXT, POPUP_FONT, POPUP_CAPS = 300, 19.0, brand.ARCHIVO, False
    POPUP_SLOTS = ((40, None), (W - 40 - POPUP_W, None))
POPUP_BOTTOM = 552      # where a bottom corner's bubble ends, tail and all


def _bubble(w, h, r, tail_x):
    """An ASS drawing of a w x h speech bubble with rounded corners of radius r and a tail under it at
    tail_x, starting at 0,0."""
    c = r * 0.552   # how far a cubic's handles sit from a quarter circle's ends
    body = (f"m {r} 0 l {w - r} 0 b {w - r + c} 0 {w} {r - c} {w} {r} l {w} {h - r} "
            f"b {w} {h - r + c} {w - r + c} {h} {w - r} {h} l {r} {h} b {r - c} {h} 0 {h - r + c} 0 {h - r} "
            f"l 0 {r} b 0 {r - c} {r - c} 0 {r} 0")
    return body + f" m {tail_x} {h - 2} l {tail_x + 34} {h - 2} l {tail_x - 6} {h + 26}"


def popups(facts, duration, begin=0.0, first=POPUP_FIRST, every=POPUP_EVERY):
    """An ASS file of Pop-Up Video bubbles for the vf chain: the facts in turn over a music video of
    `duration` seconds, spread from POPUP_FIRST to POPUP_END_GAP before the end, at file times. Bubbles
    before `begin` (where playback joins) are left out. None if none fit. first and every: when the first comes and
    how far apart they are (a test brings them sooner). White bubbles with an ink edge and a
    hard shadow (sized by POPUP_W, POPUP_TEXT and POPUP_FONT)."""
    span = duration - POPUP_END_GAP - first - POPUP_SECONDS
    if span < 0 or not facts:
        return None
    n = min(len(facts), 1 + int(span // every))
    gap = span / (n - 1) if n > 1 else 0
    s = Script()
    shown = 0
    for i, fact in enumerate(facts[:n]):
        a = first + i * gap
        if a < begin + 1:
            continue
        x, y = POPUP_SLOTS[i % len(POPUP_SLOTS)]
        lines, em = cards.wrapped(fact.upper() if POPUP_CAPS else fact, POPUP_FONT, POPUP_TEXT, POPUP_W - 48, lines=4,
                                  least=0.85)
        step = em * 1.25
        h = 34 + len(lines) * step
        if y is None:
            y = POPUP_BOTTOM - (h + 26)
        # Pops in: a quick overshoot from small, the way the bubbles did
        pop = "\\fscx25\\fscy25\\t(0,150,\\fscx108\\fscy108)\\t(150,230,\\fscx100\\fscy100)"
        tail = 70 + i % 2 * 240 if cards.CRT else (40 if x < W / 2 else POPUP_W - 80)   # towards the screen's edge
        bubble = _bubble(POPUP_W, h, 24, tail)
        s.shape(a, a + POPUP_SECONDS, x + POPUP_W / 2 + 8, y + (h + 26) / 2 + 8, bubble, brand.INK, fade=(0, 160),
                layer=1, align=5, extra=pop)
        s.shape(a, a + POPUP_SECONDS, x + POPUP_W / 2, y + (h + 26) / 2, bubble, WHITE, fade=(0, 160), layer=2,
                align=5, extra=pop + f"\\bord4\\3c{brand.INK}")
        s.raw(a, a + POPUP_SECONDS, f"\\an5\\pos({x + POPUP_W / 2:.0f},{y + h / 2:.0f})\\fn{POPUP_FONT}"
                                    f"\\fs{brand.fs(POPUP_FONT, em)}\\c{brand.INK}\\bord0\\shad0\\fad(0,160){pop}",
              "\\N".join(esc(line) for line in lines), layer=3)
        shown += 1
    return s.write("popups") if shown else None


# --- Lottery ---

BALL_COLORS = ("&H003030E0&", "&H002090F0&", "&H0020D0F0&", "&H0040C050&", "&H00E08040&")  # by tens
DRUM_X, DRUM_Y, DRUM_R = 300, 318, 128   # on the ink side, under the header
TRAY_Y = 536
ORDINALS = ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH")


def _ball(s, start, end, x, y, number, extra="", layer=5, r=40):
    color = BALL_COLORS[min(4, number // 10)]
    s.shape(start, end, x, y, circle(r), color, fade=(0, 300), layer=layer, align=5, extra=extra)
    s.shape(start, end, x, y, circle(r * 0.62), WHITE, fade=(0, 300), layer=layer + 1, align=5, extra=extra)
    s.raw(start, end, f"\\an5\\pos({x},{y + 1})\\fn{brand.ANTON}\\fs{brand.fs(brand.ANTON, r * 0.62):g}\\c{brand.INK}"
                      f"\\bord0\\shad0\\fad(0,300){extra}", str(number), layer=layer + 2)


def lottery(when, season=None):
    """The late-night drawing: six balls and a bonus ball from a tumbling drum, with a recap."""
    d = LOTTERY_SECONDS
    numbers = random.sample(range(1, 50), 7)
    lk = look(season)
    s = Script()
    fade = (400, 600)
    brand.split(s, d)
    lk.dress(s, 0, d, layer=2)
    info_header(s, d, [(0, d, "Lotto · Pick 6 + Bonus")], when, season=season)
    # The drum (its chute hanging from the header) and the tray
    s.shape(0, d, DRUM_X, DRUM_Y, circle(DRUM_R + 8), brand.PURPLE, fade=fade, layer=3, align=5, extra="\\1a&H60&")
    s.raw(0, d, f"\\an5\\pos({DRUM_X},{DRUM_Y})\\bord6\\shad0\\3c{brand.YELLOW}\\1a&HFF&\\fad({fade[0]},{fade[1]})\\p1",
          circle(DRUM_R), layer=8)
    s.shape(0, d, DRUM_X - 14, 120, rect(28, DRUM_Y - DRUM_R - 118), brand.YELLOW, fade=fade, layer=8, align=7)
    for i in range(7):
        x = 110 + i * 112 + (50 if i == 6 else 0)
        s.shape(0, d, x, TRAY_Y, circle(44), brand.INK, fade=fade, layer=4, align=5, extra=f"\\bord3\\3c{brand.YELLOW}")
    brand.words(s, 0, d, 110 + 6 * 112 + 50, TRAY_Y - 62, "BONUS", brand.ANTON, 22, brand.YELLOW, align="center",
                fade=fade)

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
        s.shape(0.3, end, x, DRUM_Y, circle(17), color, fade=(300, 0 if gone else 600), layer=5, align=5, extra=spin)
        s.shape(0.3, end, x, DRUM_Y, circle(9), WHITE, fade=(300, 0 if gone else 600), layer=6, align=5, extra=spin)

    # The draw: the call and the number on the purple side
    for i, (n, t) in enumerate(zip(numbers, reveals)):
        x = 110 + i * 112 + (50 if i == 6 else 0)
        pop = f"\\move({DRUM_X},{DRUM_Y - DRUM_R - 20},{x},{TRAY_Y},0,700)\\fscx40\\fscy40\\t(0,200,\\fscx125\\fscy125)\\t(200,700,\\fscx100\\fscy100)"
        _ball(s, t, t + 0.7, DRUM_X, DRUM_Y - DRUM_R - 20, n, extra=pop, layer=10)
        _ball(s, t + 0.7, d, x, TRAY_Y, n, layer=10)
        call = "AND THE BONUS BALL IS" if i == 6 else f"THE {ORDINALS[i]} NUMBER IS"
        until = min(t + 5.5, LOTTERY_BONUS - 0.3) if i < 6 else 50.0
        cw = brand.measure(call, brand.ARCHIVO_BLACK, 20) + 32
        brand.strip(s, t - 1.2, until, 832 - cw / 2, 168, 36, [Part(call, brand.ARCHIVO_BLACK, 20, brand.INK,
                                                                     brand.WHITE, pad=16)],
                    shadow=(5, 5, brand.INK), fade=(200, 200), layer=12)
        brand.words(s, t, until, 832, 300, str(n), brand.ANTON, 150, brand.YELLOW, align="center",
                    shadow=(8, 8, brand.INK), fade=(0, 200), layer=12,
                    extra="\\t(0,180,\\fscx130\\fscy130)\\t(180,400,\\fscx100\\fscy100)")

    # Recap
    brand.slab(s, 50.0, d, 90, 150, 844, 310, brand.INK, shadow=(10, 10, lk.tint), fade=(400, 600), layer=14)
    title = "TONIGHT'S WINNING NUMBERS"
    tw = brand.measure(title, brand.ANTON, 34) + 40
    brand.strip(s, 50.2, d, W / 2 - tw / 2, 176, 50, [Part(title, brand.ANTON, 34, brand.INK, brand.YELLOW, pad=20)],
                fade=(300, 600), layer=16)
    brand.words(s, 50.6, d, W / 2, 300, "  ".join(f"{n:02d}" for n in numbers[:6]), brand.ANTON, 78, brand.WHITE,
                align="center", shadow=(6, 6, brand.PURPLE), fade=(300, 600), layer=16)
    brand.words(s, 51.0, d, W / 2, 380, f"BONUS {numbers[6]:02d}", brand.ANTON, 40, brand.YELLOW, align="center",
                fade=(300, 600), layer=16)
    brand.words(s, 52.0, d, W / 2, 428, f"{CHANNEL} LOTTO IS FOR ENTERTAINMENT ONLY. NO TICKETS. NO PRIZES. NO KIDDING.",
                brand.ARCHIVO, 17, brand.SILVER, align="center", bold=True, fade=(400, 600), layer=16)
    return s.card("lottery", d, music("lotto_bed.ogg"), background=brand.INK_BG, card_kind="lottery")


# --- New Year's Eve ---

def new_year(year):
    """80 seconds: the last minute of the year, the final ten, then the new year with confetti."""
    d, m = NEW_YEAR_SECONDS, NEW_YEAR_MIDNIGHT
    midnight = datetime.datetime(year, 1, 1)
    s = Script()
    rnd = random.Random(year)
    brand.id_ground(s, d, brand.BLUE, slide_in=False)
    _centred(s, 0, m, 48, 46, [Part("NEW YEAR'S EVE", brand.ANTON, 32, brand.INK, brand.BLUE, pad=22)],
             shadow=(5, 5, brand.INK), fade=(400, 0))
    _centred(s, 0, m, 108, 84, [Part(f"COUNTDOWN TO {year}", brand.ANTON, 60, brand.INK, brand.YELLOW, pad=30)],
             shadow=(7, 7, brand.INK), fade=(400, 0))
    for sec in range(int(m - 10)):
        t = midnight - datetime.timedelta(seconds=m - sec)
        brand.words(s, sec, sec + 1, W / 2, 380, t.strftime("%I:%M:%S").lstrip("0") + " PM", brand.ANTON, 104,
                    brand.WHITE, align="center", shadow=(7, 7, brand.INK), fade=None, layer=13)
    for i in range(10):  # 10 .. 1
        a = m - 10 + i
        brand.words(s, a, a + 1, W / 2, 380, str(10 - i), brand.ANTON, 220, brand.YELLOW, align="center",
                    shadow=(10, 10, brand.INK), fade=(0, 250), layer=13,
                    extra="\\fscx150\\fscy150\\t(0,350,\\fscx100\\fscy100)")
    # Midnight: flash, the year, confetti and fireworks
    s.shape(m, m + 0.6, 0, 0, rect(W, H), WHITE, fade=(0, 500), layer=19)
    _centred(s, m + 0.2, d, 64, 96, [Part("HAPPY NEW YEAR!", brand.ANTON, 70, brand.INK, brand.YELLOW, pad=34)],
             shadow=(8, 8, brand.INK), fade=(200, 1200), layer=16)
    brand.words(s, m + 0.5, d, W / 2, 330, str(year), brand.ANTON, 190, brand.WHITE, align="center",
                shadow=(10, 10, brand.INK), fade=(200, 1200), layer=16,
                extra="\\fscx40\\fscy40\\t(0,500,\\fscx110\\fscy110)\\t(500,800,\\fscx100\\fscy100)")
    _centred(s, m + 1.5, d, 480, 40, [Part(f"FROM ALL OF US AT {brand.NAME}", brand.ARCHIVO_BLACK, 22, brand.INK,
                                           brand.WHITE, pad=20)], shadow=(5, 5, brand.INK), fade=(400, 1200), layer=16)
    colors = (brand.YELLOW, WHITE, brand.ORANGE, brand.PINK, brand.TEAL, brand.BLUE)
    for _ in range(140):
        t0 = m + rnd.uniform(0, 14)
        x = rnd.randrange(0, W)
        fall = rnd.randint(4000, 9000)
        s.shape(t0, min(d, t0 + fall / 1000), x, -20, rect(rnd.choice((8, 10, 12)), rnd.choice((14, 18))),
                rnd.choice(colors), fade=(0, 300), layer=18, align=5,
                extra=f"\\move({x},-20,{x + rnd.randint(-120, 120)},{H + 20})"
                      f"\\frz{rnd.randint(0, 360)}\\t(\\frz{rnd.randint(360, 1440)})\\t(\\frx{rnd.randint(180, 720)})")
    for _ in range(9):
        t0 = m + rnd.uniform(0.3, 16)
        x, y = rnd.randrange(120, W - 120), rnd.randrange(80, 300)
        color = rnd.choice(colors)
        for k in range(14):
            ang = k * 360 / 14
            s.shape(t0, t0 + 1.4, x, y, rect(3, 30), color, fade=(0, 700), layer=17, align=2,
                    extra=f"\\org({x},{y})\\frz{ang:.0f}\\fscy20\\t(0,500,\\fscy160)\\blur1")
    return s.card("newyear", d, music("newyear.ogg"), background=brand.PURPLE_BG, card_kind="newyear")


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
