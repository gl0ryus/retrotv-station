# The weather refresh and its backup, on temporary files. Runs on the Pi: python3 -I weather_test.py [live]
# With "live" it also fetches the real National Weather Service and renders the cards to /tmp/wx_cards.
import datetime, io, json, os, re, shutil, sys, tempfile, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
rs = harness.everything()
from retrotv import cards
from retrotv import screens
from retrotv import weather as wx
from retrotv import health

TMP = tempfile.mkdtemp(prefix="wx_test")
real_location = rs.WEATHER_LAT, rs.WEATHER_LON
with open(cards.WEATHER_FILE) as f:
    real_saved = json.load(f)
wx.WEATHER_FILE = TMP + "/weather.json"
cards.CARDS_DIR = TMP + "/cards"
os.makedirs(cards.CARDS_DIR)
health.DATA_DIR = TMP
checks = 0
today = datetime.date.today()
iso = lambda n: (today + datetime.timedelta(days=n)).isoformat()
stamp = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


def meteo(temp=70.0):
    n = range(5)
    return {"current": {"temperature_2m": temp, "relative_humidity_2m": 60, "apparent_temperature": temp, "is_day": 1, "weather_code": 2,
                        "wind_speed_10m": 5.0, "wind_direction_10m": 90, "pressure_msl": 1015.0, "dew_point_2m": 55.0},
            "daily": {"time": [iso(i) for i in n], "weather_code": [1] * 5, "temperature_2m_max": [80.0] * 5, "temperature_2m_min": [60.0] * 5,
                      "precipitation_probability_max": [10] * 5, "sunrise": [f"{iso(i)}T06:43" for i in n], "sunset": [f"{iso(i)}T17:45" for i in n],
                      "uv_index_max": [4.7] * 5}}


def periods(first_is_day=True):
    out = []
    for i in range(6):
        day = today + datetime.timedelta(days=i)
        if i or first_is_day:
            out.append({"startTime": f"{day}T06:00:00-05:00", "isDaytime": True, "temperature": 80 + i, "probabilityOfPrecipitation": {"value": 20},
                        "icon": "https://api.weather.gov/icons/land/day/tsra_sct,30?size=medium", "shortForecast": "Scattered Showers And Thunderstorms"})
        out.append({"startTime": f"{day}T18:00:00-05:00", "isDaytime": False, "temperature": 60 + i, "probabilityOfPrecipitation": {"value": None},
                    "icon": "https://api.weather.gov/icons/land/night/sct?size=medium", "shortForecast": "Partly Cloudy"})
    return out


def observation(age=300, temp=25.0, pressure=101500):
    return {"properties": {"timestamp": stamp(time.time() - age), "textDescription": "Cloudy", "icon": "https://api.weather.gov/icons/land/day/ovc?size=medium",
                           "temperature": {"value": temp}, "dewpoint": {"value": 18.0}, "windDirection": {"value": None}, "windSpeed": {"value": 16.09},
                           "seaLevelPressure": {"value": None}, "barometricPressure": {"value": pressure}, "relativeHumidity": {"value": 70.0},
                           "heatIndex": {"value": 26.0}, "windChill": {"value": None}}}


routes = {}
asked = []


def fake_urlopen(req, timeout=None):
    url = getattr(req, "full_url", req)
    asked.append(url)
    for key, reply in routes.items():
        if key in url:
            if isinstance(reply, Exception):
                raise reply
            return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply).encode())
    raise OSError(f"no route for {url}")


health_real = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen


def nws_ok(obs=None, grid=True, first_is_day=True):
    routes.update({
        "/points/": {"properties": {"forecast": "https://api.weather.gov/f", "forecastGridData": "https://api.weather.gov/g", "observationStations": "https://api.weather.gov/s"}},
        "weather.gov/s?": {"features": [{"id": "https://api.weather.gov/stations/A"}, {"id": "https://api.weather.gov/stations/B"}]},
        "weather.gov/f?": {"properties": {"periods": periods(first_is_day)}},
        "stations/A/": obs or observation(), "stations/B/": observation(temp=10.0),
        "weather.gov/g": {"properties": {
            "maxTemperature": {"values": [{"validTime": f"{iso(0)}T12:00:00+00:00/PT13H", "value": 30.0}]},
            "minTemperature": {"values": [{"validTime": f"{iso(0)}T07:00:00+00:00/PT7H", "value": 5.0}, {"validTime": f"{iso(1)}T00:00:00+00:00/PT14H", "value": 15.0}]},
            "probabilityOfPrecipitation": {"values": [{"validTime": f"{iso(0)}T15:00:00+00:00/PT1H", "value": 75}]}}} if grid else OSError("grid down"),
    })


def saved():
    with open(wx.WEATHER_FILE) as f:
        return json.load(f)


W = lambda: wx.Watcher(28.0, -82.5, "America/New_York")


def age(seconds):
    """Makes the saved copy this old."""
    data = saved(); now = time.time() - seconds
    data.update(fetched=now, as_of=now)
    wx._save(data)


# --- Checking a reply
good = meteo()
check(wx.problem(good, today) is None, "a good reply")
for wrong, why in (({}, "empty"), ({"error": True, "reason": "x"}, "an error in JSON"), ([], "a list"), (None, "nothing"),
                   ({**good, "current": {**good["current"], "temperature_2m": None}}, "no temperature"),
                   ({**good, "current": {**good["current"], "temperature_2m": 999}}, "silly temperature"),
                   ({**good, "daily": {**good["daily"], "time": [iso(-3)] * 5}}, "yesterday's forecast"),
                   ({**good, "daily": {**good["daily"], "temperature_2m_max": [None] * 5}}, "no high"),
                   ({**good, "daily": {**good["daily"], "sunset": []}}, "short list")):
    check(wx.problem(wrong, today), why)

# --- Open-Meteo working: saved, labeled, and the backup is tested once
routes.clear(); routes["open-meteo"] = meteo(); nws_ok()
w = W()
check(w.step() == wx.REFRESH_SECONDS, "next refresh in 10 minutes")
check(saved()["source"] == "open-meteo" and w.fails == 0, "saved")
check(any("weather.gov/f?" in u for u in asked), "backup tested")
check(saved()["source"] == "open-meteo", "testing the backup doesn't change what's saved")
asked.clear(); w.step()
check(not any("weather.gov" in u for u in asked), "the backup isn't tested again for hours")

# --- A bad reply doesn't touch the good copy
before = saved()
routes["open-meteo"] = {"error": True, "reason": "bad"}
check(w.step() == 60 and saved() == before, "good copy kept, retry in a minute")
routes["open-meteo"] = b"<html>oops"
check(w.step() == 300 and saved() == before, "retry in 5")
# --- The third failure in a row brings in the backup
routes["open-meteo"] = OSError("down")
age(1800)
check(w.step() == 900 and w.on_backup, "backup on the third failure, retry in 15")
data = saved()
check(data["source"] == "nws" and wx.problem(data, today) is None, "backup saved, usable")
cur, daily = data["current"], data["daily"]
check(abs(cur["temperature_2m"] - 77) < 0.01 and abs(cur["apparent_temperature"] - 78.8) < 0.01, "C to F")
check(abs(cur["wind_speed_10m"] - 10) < 0.01 and cur["wind_direction_10m"] == 0, "km/h to mph, no direction")
check(abs(cur["pressure_msl"] - 1015) < 0.01 and cur["weather_code"] == 3, "pressure in hPa, code")
check(daily["temperature_2m_max"][:2] == [80, 81] and daily["temperature_2m_min"][:2] == [60, 61], "highs and lows")
check(daily["weather_code"][0] == 95, "day forecast's code")
check(daily["precipitation_probability_max"][0] == 75 and daily["precipitation_probability_max"][1] == 20, "the grid's chance of rain, and the forecast's")
check(len(daily["time"]) == 5 and "uv_index_max" not in daily, "five days, no UV")
check(wx.load() is not None, "the card can read it")
check(w.step() == 900 and w.on_backup, "stays on the backup, still trying")
# --- Open-Meteo returns
routes["open-meteo"] = meteo(71.0)
check(w.step() == wx.REFRESH_SECONDS and not w.on_backup and w.fails == 0, "off the backup")
check(saved()["source"] == "open-meteo" and saved()["current"]["temperature_2m"] == 71.0, "primary saved again")

# --- Both down: the good copy stays, and retries continue
w = W(); w.backup_tested = time.time()
before = saved()
routes["open-meteo"] = OSError("down")
for key in list(routes):
    if "weather.gov" in key or "points" in key or "stations" in key:
        routes[key] = OSError("nws down")
for wait in (60, 300, 900, 900):
    check(w.step() == wait, wait)
check(saved() == before and not w.on_backup, "saved copy untouched")
# ...and the backup coming back is picked up, after re-finding its links
nws_ok()
age(1800)
check(w.step() == 900 and w.on_backup and saved()["source"] == "nws", "backup recovers")

# --- A backup observation older than the saved copy doesn't replace it
routes["open-meteo"] = meteo(); W().step()
fresh = saved()
routes["open-meteo"] = OSError("down")
nws_ok(obs=observation(age=2000))
w = W(); w.fails = 2; w.step()
check(w.on_backup and saved() == fresh, "older data doesn't replace newer")

# --- Stations: too old or incomplete moves on to the next
for bad in (observation(age=6000), observation(temp=None)):
    nws_ok(obs=bad)
    b = wx._Backup(28.0, -82.5, "America/New_York")
    check(abs(b.fetch()["current"]["temperature_2m"] - 50) < 0.01, "second station used")
routes["stations/B/"] = observation(age=6000)
try:
    b.fetch(); check(False, "no station")
except ValueError:
    check(not b.links, "failure means looking everything up again")

# --- Late in the day: no daytime period left
nws_ok(first_is_day=False)
d = wx._Backup(28.0, -82.5, "America/New_York").fetch()["daily"]
check(d["temperature_2m_max"][0] == 86, f"today's high from the grid: {d['temperature_2m_max'][0]}")
check(d["temperature_2m_min"][0] == 60 and d["weather_code"][0] == 2, "tonight's forecast")
nws_ok(first_is_day=False, grid=False)
d = wx._Backup(28.0, -82.5, "America/New_York").fetch()["daily"]
check(abs(d["temperature_2m_max"][0] - 77) < 0.01, "no grid: the current temperature rather than no card")

# --- Weather words
for icon, text, code in (("https://api.weather.gov/icons/land/day/tsra_hi,40", "", 95), ("https://x/land/night/rain_showers", "", 80), ("https://x/land/day/snow", "", 73),
                         ("https://x/land/day/fzra", "", 66), ("https://x/land/day/fog", "", 45), ("https://x/land/day/sct", "", 2), ("https://x/land/day/few", "", 1),
                         ("https://x/land/day/ovc", "", 3), ("https://x/land/night/skc", "", 0), (None, "Mostly Cloudy", 3), (None, "Partly Cloudy", 2), (None, "Mostly Sunny then Scattered Showers", 80),
                         (None, "Light Rain", 63), (None, "", 3), ("https://api.weather.gov/icons/land/day/skc", "Clear", 0)):
    check(wx.condition_code(icon, text) == code and code in cards.WMO, (icon, text, code, wx.condition_code(icon, text)))

# --- Sunrise and sunset, against what Open-Meteo gave for the real location
lat, lon = real_location
for i, day in enumerate(real_saved["daily"]["time"]):
    rise, sets = wx.sun_times(lat, lon, datetime.date.fromisoformat(day), rs.WEATHER_TZ)
    for mine, theirs in ((rise, real_saved["daily"]["sunrise"][i]), (sets, real_saved["daily"]["sunset"][i])):
        gap = abs((datetime.datetime.fromisoformat(mine) - datetime.datetime.fromisoformat(theirs)).total_seconds())
        check(gap <= 180, (mine, theirs))

# --- How old is too old
def ass_of(card):
    return open(max(glob_cards(), key=os.path.getmtime), encoding="utf-8").read()


def glob_cards():
    return [os.path.join(cards.CARDS_DIR, n) for n in os.listdir(cards.CARDS_DIR)]


routes["open-meteo"] = meteo(); W().step()
now = datetime.datetime.now()
text = ass_of(cards.weather(wx.load(), "Home", now))
check("Current Conditions" in text and "Now 70" in text and "UV Index" in text and "Conditions at" not in text, "fresh")
age(29 * 60); check("Current Conditions" in ass_of(cards.weather(wx.load(), "Home", now)), "29 minutes: no label")
age(31 * 60)
text = ass_of(cards.weather(wx.load(), "Home", now))
when = cards.clock(now - datetime.timedelta(minutes=31))
check(f"Conditions at {when}" in text and f"At {when} 70" in text and "Now 70" not in text, "31 minutes: labeled")
age(179 * 60); check(wx.load() is not None, "just under 3 hours")
age(181 * 60); check(wx.load() is None, "over 3 hours: no card")
os.remove(wx.WEATHER_FILE); check(wx.load() is None, "no file")
with open(wx.WEATHER_FILE, "w") as f:
    f.write("{bad")
check(wx.load() is None, "a damaged file")
with open(wx.WEATHER_FILE, "w") as f:
    json.dump({**meteo(), "fetched": time.time()}, f)     # as written before this change: no as_of
check(wx.load() is not None and "Current Conditions" in ass_of(cards.weather(wx.load(), "Home", now)), "the old file format still reads")

# --- No UV: the card still airs, without that line
data = meteo(); del data["daily"]["uv_index_max"]; data["as_of"] = data["fetched"] = time.time()
card = cards.weather(data, "Home", now)
text = ass_of(card)
check(card and "UV Index" not in text and "Chance of Rain" in text, "no UV line")

# --- G|TV Text: with no weather there's no page saying so, and the index doesn't list it
screens._tt_markets = lambda: screens.TeletextPage(220, "MARKETS", "green")
with_it = screens.teletext(now, data, "Home", [], [])
text = ass_of(with_it)
check("HUMIDITY" in text or "Humidity" in text, "the weather page")
without = screens.teletext(now, None, "Home", [], [])
text = ass_of(without)
check(without.duration < with_it.duration and "Weather is not" not in text and "WEATHER" not in text, "no weather page")
old = {**data, "as_of": time.time() - 3600}
check("AS OF" in ass_of(screens.teletext(now, old, "Home", [], [])), "an old forecast says so")

# --- The health page: both feeds on one host are told apart, and a backup in use is trouble, not down
check(health._service_of("https://api.weather.gov/alerts/active?point=1,2") == "nws", "alerts")
check(health._service_of("https://api.weather.gov/gridpoints/X/1,2/forecast") == "weather-backup", "forecast")
state = {"ok_at": 1, "fail_at": time.time(), "streak": 5, "error": "HTTP 500", "fails": [], "fallback": wx.USING_BACKUP}
check(health.judge(health.BY_ID["weather"], state, time.time(), True) == ("warn", wx.USING_BACKUP), "backup: trouble")
state.pop("fallback")
check(health.judge(health.BY_ID["weather"], state, time.time(), True)[0] == "down", "both failing: down")

urllib.request.urlopen = health_real
if "live" in sys.argv:
    out = "/tmp/wx_cards"
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    cards.CARDS_DIR = out
    live = wx._Backup(lat, lon, rs.WEATHER_TZ).fetch()
    check(wx.problem(live, today) is None, "the real National Weather Service")
    print("live backup:", {k: round(v, 1) for k, v in live["current"].items()})
    print({k: v[:2] for k, v in live["daily"].items()})
    print("Open-Meteo saved:", {k: real_saved["current"][k] for k in ("temperature_2m", "weather_code")}, {k: real_saved["daily"][k][:2] for k in ("temperature_2m_max", "temperature_2m_min", "weather_code")})
    live["as_of"] = time.time() - 3000
    card = cards.weather(live, rs.WEATHER_PLACE, datetime.datetime.now())
    check(bool(card), "renders from the real backup")
    with open("/tmp/wx_card_url.txt", "w") as f:
        f.write(card.url)
shutil.rmtree(TMP)
print(f"weather checks passed: {checks}")
