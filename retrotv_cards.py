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
import os
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
GUIDE_BACKGROUND = "0x0c1a52"
WEATHER_BACKGROUND = "0x1a2378"

# ASS colours are &HAABBGGRR
CREAM = "&H00D2E6EF&"
MUSTARD = "&H003AB3E8&"
SHADOW_RED = "&H001F2A8A&"
BLACK = "&H00000000&"
STRIPES = ("&H003DB3F2&", "&H002A7FEE&", "&H00274FDD&", "&H002A32B8&")  # yellow, orange, red-orange, red
WHITE = "&H00FFFFFF&"
YELLOW = "&H004AE1FF&"
SKY = "&H00FFD8A8&"
NAVY_CELL = "&H007A2610&"
BLUE_CELL = "&H00B33F1D&"
CELL_EDGE = "&H00FF8F6F&"
PANEL = "&H00963523&"
HEADER = "&H006E1E3C&"
TICKER = "&H003D0F0A&"
ORANGE_RULE = "&H002C8AF0&"

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


def stripes(s, start, end, x, y, width, bar=9, gap=5, slide=True, fade=(200, 250)):
    """The four network stripes, sliding in from the left."""
    for i, color in enumerate(STRIPES):
        by = y + i * (bar + gap)
        extra = f"\\move({x - width - 60:.0f},{by},{x},{by},0,{350 + i * 60})" if slide else ""
        s.shape(start, end, x, by, slanted_bar(width - i * 14, bar, bar + 6), color, fade=fade, extra=extra)


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

def right_back():
    d = 3.5
    s = Script()
    s.text(0, d, 512, 205, "WE'LL BE", ARCHIVO, 62, shadow=5, slant=-0.15)
    s.text(0.1, d, 512, 295, "RIGHT BACK", ARCHIVO, 104, shadow=6, slant=-0.15)
    stripes(s, 0, d, 190, 362, 640)
    s.text(0.4, d, 512, 470, "GL0RYUS NETWORK", BARLOW_B, 38, MUSTARD, spacing=10)
    return Card(lavfi_url(s.write("rightback"), d, music("sting_break.ogg")), d, "card")


def back_to(name):
    d = 3.5
    s = Script()
    s.text(0, d, 512, 190, "NOW BACK TO", BARLOW_X, 50, MUSTARD, spacing=6)
    name = clip_text(name.upper(), 34)
    s.text(0.1, d, 512, 285, name, ARCHIVO, fit(name, ARCHIVO, 88, 860), shadow=6, slant=-0.15)
    stripes(s, 0, d, 190, 350, 640)
    s.text(0.4, d, 512, 470, "GL0RYUS NETWORK", BARLOW_B, 38, MUSTARD, spacing=10)
    return Card(lavfi_url(s.write("backto"), d, music("sting_back.ogg")), d, "card")


def up_next(show, later=None, later_time=None, duration=7.0):
    """show and later are (name, episode) pairs; later_time is a datetime. The duration stretches
    a few seconds either way so the next show starts right on the half hour."""
    d = round(duration, 2)
    s = Script()
    s.text(0, d, 90, 140, "UP NEXT", ARCHIVO, 84, align=4, shadow=6, slant=-0.15)
    stripes(s, 0, d, 520, 118, 440)
    name, episode = show
    name = clip_text(name, 38)
    s.text(0.3, d, 92, 258, name, BARLOW_X, fit(name, BARLOW_X, 84, 840), align=4, shadow=3)
    if episode:
        s.text(0.5, d, 94, 325, clip_text(episode, 52), BARLOW_S, 44, MUSTARD, align=4)
    if later:
        s.shape(0.8, d, 90, 392, rect(844, 3), MUSTARD)
        s.text(1.0, d, 92, 450, "LATER", BARLOW_X, 42, MUSTARD, align=4, spacing=4)
        later_name = clip_text(later[0], 34)
        s.text(1.1, d, 225, 450, later_name, BARLOW_B, fit(later_name, BARLOW_B, 44, 560), align=4)
        if later_time:
            s.text(1.2, d, 934, 450, clock(later_time), BARLOW_X, 44, CREAM, align=6)
    return Card(lavfi_url(s.write("upnext"), d, music("upnext_bed.ogg")), d, "card")


def station_id(now, good_morning=False):
    """The bumper with the time laid over it."""
    d = 4.5
    s = Script()
    top = "GOOD MORNING!" if good_morning else "YOU'RE WATCHING"
    bottom = f"WE'RE ON THE AIR · {clock(now)}" if good_morning else clock(now)
    s.text(1.2, 4.1, 512, 92, top, BARLOW_X, 46, CREAM, spacing=8, shadow=3, fade=(300, 250))
    s.text(1.8, 4.1, 512, 505, bottom, ARCHIVO, 44 if good_morning else 62, MUSTARD, shadow=4, fade=(300, 250))
    path = s.write("stationid")
    return Card(lavfi_url(path, d, video_source=ID_BACKGROUND, audio_source=ID_BACKGROUND), d, "id")


def sign_off(returns_at):
    d = 16.0
    s = Script()
    s.text(0.3, d, 512, 165, "THIS CONCLUDES", BARLOW_X, 54, MUSTARD, spacing=6, fade=(600, 1200))
    s.text(0.8, d, 512, 245, "OUR BROADCAST DAY", ARCHIVO, 74, shadow=6, slant=-0.12, fade=(600, 1200))
    stripes(s, 1.2, d, 190, 305, 640, fade=(300, 1200))
    s.text(3.0, d, 512, 405, f"GL0RYUS NETWORK RETURNS AT {clock(returns_at)}", BARLOW_S, 42,
           fade=(600, 1200))
    s.text(6.0, d, 512, 485, "GOOD NIGHT", ARCHIVO, 56, MUSTARD, shadow=4, fade=(900, 1200))
    return Card(lavfi_url(s.write("signoff"), d, music("signoff.ogg")), d, "card")


def stand_by():
    d = 32.0
    s = Script()
    s.text(0, d, 512, 160, "PLEASE", ARCHIVO, 70, shadow=5, slant=-0.15, fade=(400, 0))
    s.text(0, d, 512, 250, "STAND BY", ARCHIVO, 112, shadow=6, slant=-0.15, fade=(400, 0))
    stripes(s, 0, d, 190, 320, 640, slide=False, fade=(400, 0))
    s.text(0, d, 512, 415, "WE'RE EXPERIENCING TECHNICAL DIFFICULTIES", BARLOW_S, 38, MUSTARD, fade=(400, 0))
    # A slow blink, like a stuck slide in the master control room
    for i in range(0, int(d), 2):
        s.text(i, i + 1, 512, 480, "●  PROGRAMMING WILL RESUME SHORTLY", BARLOW_B, 32, CREAM, fade=None)
    return Card(lavfi_url(s.write("standby"), d, music("standby_loop.ogg"), fade_out=False), d, "card")


# --- Program guide ---

GUIDE_TOP, GUIDE_BOTTOM, ROW_H = 232, 548, 96
GUIDE_SPEED = 15.0  # px/s
GUIDE_ROWS = 10


def guide(rows, now):
    """
    A slow-scrolling listings grid in the style of the early-90s cable guide channels.
    rows: (start datetime or None, name, detail) tuples. Returns None with nothing to list.
    """
    if not rows:
        return None
    rows = rows[:GUIDE_ROWS]
    hold = 6.0
    distance = len(rows) * ROW_H
    scroll_end = hold + distance / GUIDE_SPEED
    d = round(scroll_end + 3.0, 2)
    fade = (300, 400)
    s = Script()
    # Header
    s.text(0, d, 80, 100, "GL0RYUS", ARCHIVO, 62, align=4, shadow=5, slant=-0.15, fade=fade)
    stripes(s, 0, d, 80, 138, 330, bar=7, gap=4, slide=False, fade=fade)
    s.text(0, d, 410, 196, "NETWORK", BARLOW_X, 30, MUSTARD, align=6, spacing=10, fade=fade)
    s.text(0, d, 944, 92, "PROGRAM GUIDE", BARLOW_X, 52, align=6, shadow=3, shadow_color=BLACK, fade=fade)
    s.text(0, d, 944, 146, now.strftime("%A, %b %-d").upper(), BARLOW_S, 34, SKY, align=6, fade=fade)
    for sec in range(int(d) + 1):  # a ticking clock
        t = now + datetime.timedelta(seconds=sec)
        s.text(sec, min(d, sec + 1), 944, 196, clock(t), BARLOW_X, 44, YELLOW, align=6, fade=None)

    clip = f"\\clip(60,{GUIDE_TOP},964,{GUIDE_BOTTOM})"
    for i, (start, name, detail) in enumerate(rows):
        y0 = GUIDE_TOP + i * ROW_H
        y1 = y0 - distance

        def mv(x, dy=0):
            return f"\\move({x},{y0 + dy},{x},{y1 + dy},{int(hold * 1000)},{int(scroll_end * 1000)}){clip}"
        s.shape(0, d, 60, y0, rect(190, ROW_H - 4), NAVY_CELL, extra=mv(60), fade=fade)
        s.shape(0, d, 254, y0, rect(710, ROW_H - 4), BLUE_CELL, extra=mv(254), fade=fade)
        s.shape(0, d, 60, y0, rect(904, 2), CELL_EDGE, extra=mv(60, ROW_H - 4), fade=fade)
        s.raw(0, d, f"\\an5\\fn{BARLOW_X}\\fs44\\c{YELLOW}\\bord0\\shad2\\4c{BLACK}\\fad({fade[0]},{fade[1]})"
              + mv(155, ROW_H // 2 - 2), esc(clock(start) if start else ""))
        name = clip_text(name, 40)
        s.raw(0, d, f"\\an4\\fn{BARLOW_X}\\fs{fit(name, BARLOW_X, 44, 660)}\\c{WHITE}\\bord0\\shad2"
              f"\\4c{BLACK}\\fad({fade[0]},{fade[1]})" + mv(274, 32), esc(name))
        if detail:
            s.raw(0, d, f"\\an4\\fn{BARLOW_S}\\fs31\\c{SKY}\\bord0\\shad0\\fad({fade[0]},{fade[1]})" + mv(276, 70),
                  esc(clip_text(detail, 52)))
    return Card(lavfi_url(s.write("guide"), d, music("guide_bed.ogg"), background=GUIDE_BACKGROUND, loop=True), d,
                "guide")


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


def fetch_weather(lat, lon, tz, max_age_minutes=30):
    """Current conditions and a 4-day forecast from Open-Meteo, cached on the SD card."""
    cached = None
    try:
        with open(WEATHER_FILE) as f:
            cached = json.load(f)
        if time.time() - cached["fetched"] < max_age_minutes * 60:
            return cached
    except (OSError, ValueError, KeyError):
        pass
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 4,
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                   "wind_speed_10m,wind_direction_10m,pressure_msl,dew_point_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "sunrise,sunset,uv_index_max",
    })
    try:
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=8) as r:
            data = json.load(r)
        data["fetched"] = time.time()
        tmp = WEATHER_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, WEATHER_FILE)
        return data
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
    # A stale forecast is fine for a few hours
    if cached and time.time() - cached.get("fetched", 0) < 3 * 3600:
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


def weather(data, place, now=None):
    """Three pages (current conditions, extended forecast, almanac) with a crawl along the bottom."""
    now = now or datetime.datetime.now()
    try:
        cur, daily = data["current"], data["daily"]
        temp = round(cur["temperature_2m"])
        humidity = round(cur["relative_humidity_2m"])
        cond, icon = WMO.get(cur["weather_code"], ("", "cloud"))
        night = not cur.get("is_day", 1)
        wind_dir = COMPASS[int((cur["wind_direction_10m"] % 360) / 22.5 + 0.5) % 16]
        wind = "Calm" if cur["wind_speed_10m"] < 1 else f"{wind_dir} {round(cur['wind_speed_10m'])} mph"
        pressure = f"{cur['pressure_msl'] * 0.02953:.2f} in"
        today_hi, today_lo = round(daily["temperature_2m_max"][0]), round(daily["temperature_2m_min"][0])
        rain_today = daily["precipitation_probability_max"][0]
        sunrise = clock(datetime.datetime.fromisoformat(daily["sunrise"][0]))
        sunset = clock(datetime.datetime.fromisoformat(daily["sunset"][0]))
        uv = round(daily["uv_index_max"][0])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logger.warning(f"Weather data unusable: {e}")
        return None

    page, pages = 21.0, 3   # 63 s, inside the 64 s bed
    d = page * pages
    s = Script()
    fade = (300, 300)
    frame_fade = (300, 400)

    # The frame (header, orange rule, panel and crawl bar) stays up the whole time
    s.shape(0, d, 0, 0, rect(W, 168), HEADER, fade=frame_fade, layer=0)
    s.shape(0, d, 0, 168, rect(W, 6), ORANGE_RULE, fade=frame_fade, layer=0)
    s.shape(0, d, 60, 186, rect(904, 292), PANEL, fade=frame_fade, layer=0)
    s.shape(0, d, 0, 488, rect(W, 112), TICKER, fade=frame_fade, layer=0)
    s.text(0, d, 80, 82, "LOCAL WEATHER", BARLOW_X, 30, MUSTARD, align=4, spacing=6, fade=frame_fade)
    s.text(0, d, 944, 132, now.strftime("%a %b %-d").upper(), BARLOW_S, 32, SKY, align=6, fade=frame_fade)
    for sec in range(int(d) + 1):
        t = now + datetime.timedelta(seconds=sec)
        s.text(sec, min(d, sec + 1), 944, 88, clock(t), BARLOW_X, 44, WHITE, align=6, fade=None)

    def title(a, b, text):
        s.text(a, b, 80, 130, text, BARLOW_X, 56, YELLOW, align=4, shadow=3, shadow_color=BLACK, fade=fade)

    # Page 1: current conditions
    a, b = 0.0, page
    title(a, b, "Current Conditions")
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
    title(a, b, "Extended Forecast")
    for col, i in enumerate(range(1, 4)):
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
    title(a, b, "Almanac")
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
