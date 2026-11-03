"""
Keeps the local weather current without anyone waiting on it. A background thread refreshes WEATHER_FILE from Open-Meteo
and the weather card and G|TV Text only ever read that file. A reply is checked before it replaces the file, so a bad one
can't wipe out a good one. After FAILS_TO_BACKUP failures in a row the National Weather Service stands in, in the same
shape, while Open-Meteo keeps being tried; the backup is tested every so often so it's known to work before it's needed.
"""
import datetime
import json
import logging
import math
import re
import time
import urllib.parse
import zoneinfo

from retrotv import health
from retrotv.cards import WEATHER_FILE
from retrotv.jsonio import get_json, write_json

logger = logging.getLogger("RetroTV")

REFRESH_SECONDS = 600
RETRY_SECONDS = (60, 300, 900)    # after the first, second, third and later failures in a row
FAILS_TO_BACKUP = 3
BACKUP_TEST_SECONDS = 6 * 3600
MAX_AGE_SECONDS = 3 * 3600        # older than this, the weather isn't shown at all
OBSERVATION_MAX_AGE_SECONDS = 90 * 60
FORECAST_DAYS = 5                 # today, three more, one spare
TIMEOUT = 8
USING_BACKUP = "Open-Meteo is failing, using the backup: National Weather Service"

OPEN_METEO = "https://api.open-meteo.com/v1/forecast?"
NWS_POINTS = "https://api.weather.gov/points/{:.4f},{:.4f}"
CURRENT_FIELDS = ("temperature_2m", "relative_humidity_2m", "apparent_temperature", "weather_code", "wind_speed_10m",
                  "wind_direction_10m", "pressure_msl", "dew_point_2m")
DAILY_FIELDS = ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "sunrise", "sunset")
SANE_TEMPERATURE_F = (-70, 140)
# The weather codes the card knows (cards.WMO) from the words in an NWS icon and description, worst first
CONDITIONS = ((r"tsra|thunder|tornado|hurricane|tropical", 95), (r"snow|blizzard", 73), (r"fzra|freezing|sleet|\bice\b", 66),
              (r"showers", 80), (r"rain|drizzle", 63), (r"fog|haze|smoke|dust", 45), (r"partly|\bsct\b|scattered", 2),
              (r"mostly (sunny|clear)|\bfew\b", 1), (r"cloudy|overcast|\bovc\b|\bbkn\b", 3), (r"clear|sunny|fair|\bskc\b|\bhot\b|\bcold\b", 0))


def problem(data, today):
    """What's wrong with weather data for a day, or None if the card and G|TV Text can use it."""
    try:
        current, daily = data["current"], data["daily"]
        for field in CURRENT_FIELDS:
            if not isinstance(current[field], (int, float)):
                return f"no {field}"
        if not SANE_TEMPERATURE_F[0] < current["temperature_2m"] < SANE_TEMPERATURE_F[1]:
            return f"a temperature of {current['temperature_2m']}"
        t = daily["time"].index(today.isoformat())
        for field in DAILY_FIELDS:
            if len(daily[field]) != len(daily["time"]) or daily[field][t] is None:
                return f"no {field} for today"
    except (KeyError, TypeError, AttributeError):
        return "not the expected format"
    except ValueError:
        return "no forecast for today"
    return None


def load():
    """The saved weather if it can still be shown, otherwise None. Never touches the network."""
    try:
        with open(WEATHER_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("as_of", data["fetched"]) > MAX_AGE_SECONDS:
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None if problem(data, datetime.date.today()) else data


def _saved_as_of():
    try:
        with open(WEATHER_FILE) as f:
            data = json.load(f)
        return data.get("as_of", data["fetched"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0


def _save(data):
    write_json(WEATHER_FILE, data)


def _get(url, check=None):
    """JSON from a URL. check runs while the request is open, so a reply it refuses is recorded as that service failing."""
    return get_json(url, timeout=TIMEOUT, accept="application/geo+json, application/json", check=check)


# --- Open-Meteo ---

def fetch_open_meteo(lat, lon, tz):
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": FORECAST_DAYS,
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                   "wind_speed_10m,wind_direction_10m,pressure_msl,dew_point_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "sunrise,sunset,uv_index_max",
    })

    def check(data):
        wrong = problem(data, datetime.date.today())
        if wrong:
            raise ValueError(f"Open-Meteo sent {wrong}")
    data = _get(OPEN_METEO + query, check)
    now = time.time()
    data.update(fetched=now, as_of=now, source="open-meteo")
    return data


# --- National Weather Service ---

def condition_code(icon, text):
    """A weather code from an NWS icon URL and description."""
    tail = re.search(r"/land/(?:day|night)/([^?]*)", icon or "")
    words = re.sub(r"[_/,]", " ", tail.group(1) if tail else "") + " " + (text or "")
    for pattern, code in CONDITIONS:
        if re.search(pattern, words, re.I):
            return code
    return 3


def sun_times(lat, lon, day, tz):
    """(sunrise, sunset) on a day as local ISO times, by NOAA's approximate equations: the NWS doesn't give them."""
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1 + 0.5)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    phi = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    midnight = datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc)
    zone = zoneinfo.ZoneInfo(tz)
    return tuple((midnight + datetime.timedelta(minutes=720 - 4 * (lon + sign * ha) - eqtime)).astimezone(zone).strftime("%Y-%m-%dT%H:%M")
                 for sign in (1, -1))


def _c_to_f(celsius):
    return celsius * 9 / 5 + 32


def _value(observation, field):
    return (observation.get(field) or {}).get("value")


class _Backup:
    """Today's conditions and forecast from the NWS, in Open-Meteo's shape, minus the UV index it doesn't have."""

    def __init__(self, lat, lon, tz):
        self.lat, self.lon, self.tz = lat, lon, tz
        self.links = {}        # where this spot's forecast and stations are: they don't move, so asked for once

    def fetch(self):
        try:
            return self._fetch()
        except Exception:
            self.links = {}
            raise

    def _fetch(self):
        if not self.links:
            point = _get(NWS_POINTS.format(self.lat, self.lon))["properties"]
            stations = _get(point["observationStations"] + "?limit=3")["features"]
            self.links = {"forecast": point["forecast"] + "?units=us", "grid": point["forecastGridData"],
                          "stations": [s["id"] for s in stations]}
        zone = zoneinfo.ZoneInfo(self.tz)
        today = datetime.datetime.now(zone).date()
        highs, lows, rain = self._grid(zone)
        periods = _get(self.links["forecast"])["properties"]["periods"]
        current, as_of = self._current(zone)
        days = {}
        for period in periods:    # in time order: the last night that starts on a date is that date's "tonight"
            day = datetime.datetime.fromisoformat(period["startTime"]).date()
            found = days.setdefault(day, {})
            found["high" if period["isDaytime"] else "low"] = period["temperature"]
            rain[day] = max(rain.get(day, 0), _value(period, "probabilityOfPrecipitation") or 0)
            if period["isDaytime"] or "code" not in found:
                found["code"] = condition_code(period.get("icon"), period.get("shortForecast"))
        daily = {field: [] for field in ("time", *DAILY_FIELDS)}
        for offset in range(FORECAST_DAYS):
            day = today + datetime.timedelta(days=offset)
            found = days.get(day, {})
            high, low = found.get("high", highs.get(day)), found.get("low", lows.get(day))
            code = found.get("code")
            if offset == 0:   # late in the day there's no daytime forecast left, and the grid may have dropped it too
                high = current["temperature_2m"] if high is None else high
                low = current["temperature_2m"] if low is None else low
                code = current["weather_code"] if code is None else code
            if high is None or low is None or code is None:
                break
            sunrise, sunset = sun_times(self.lat, self.lon, day, self.tz)
            for field, value in (("time", day.isoformat()), ("weather_code", code), ("temperature_2m_max", high),
                                 ("temperature_2m_min", low), ("precipitation_probability_max", rain.get(day, 0)),
                                 ("sunrise", sunrise), ("sunset", sunset)):
                daily[field].append(value)
        sunrise, sunset = sun_times(self.lat, self.lon, today, self.tz)
        now = datetime.datetime.now(zone).strftime("%Y-%m-%dT%H:%M")
        current["is_day"] = int(sunrise <= now < sunset)
        data = {"current": current, "daily": daily, "fetched": time.time(), "as_of": as_of, "source": "nws"}
        wrong = problem(data, today)
        if wrong:
            raise ValueError(f"The National Weather Service sent {wrong}")
        return data

    def _grid(self, zone):
        """Highs, lows and the day's chance of rain by date, which also cover the part of today that's gone by. Optional."""
        highs, lows, rain = {}, {}, {}
        try:
            grid = _get(self.links["grid"])["properties"]
            for name, into, convert in (("maxTemperature", highs, _c_to_f), ("minTemperature", lows, _c_to_f)):
                for entry in grid[name]["values"]:
                    into[self._local_day(entry, zone)] = convert(entry["value"])
            for entry in grid["probabilityOfPrecipitation"]["values"]:
                day = self._local_day(entry, zone)
                rain[day] = max(rain.get(day, 0), entry["value"] or 0)
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.info(f"Weather backup: no forecast grid ({e})")
        return highs, lows, rain

    @staticmethod
    def _local_day(entry, zone):
        return datetime.datetime.fromisoformat(entry["validTime"].split("/")[0]).astimezone(zone).date()

    def _current(self, zone):
        """The nearest station with a complete, recent observation."""
        for station in self.links["stations"]:
            o = _get(f"{station}/observations/latest")["properties"]
            as_of = datetime.datetime.fromisoformat(o["timestamp"]).timestamp()
            pressure = _value(o, "seaLevelPressure") or _value(o, "barometricPressure")
            temperature, dewpoint, humidity = _value(o, "temperature"), _value(o, "dewpoint"), _value(o, "relativeHumidity")
            if (time.time() - as_of > OBSERVATION_MAX_AGE_SECONDS or temperature is None or dewpoint is None
                    or humidity is None or pressure is None):
                continue
            feels = _value(o, "heatIndex")
            feels = _value(o, "windChill") if feels is None else feels
            return {"temperature_2m": _c_to_f(temperature), "relative_humidity_2m": humidity,
                    "apparent_temperature": _c_to_f(temperature if feels is None else feels),
                    "weather_code": condition_code(o.get("icon"), o.get("textDescription")),
                    "wind_speed_10m": (_value(o, "windSpeed") or 0) * 0.621371,
                    "wind_direction_10m": _value(o, "windDirection") or 0,
                    "pressure_msl": pressure / 100, "dew_point_2m": _c_to_f(dewpoint)}, as_of
        raise ValueError("no station has a recent, complete observation")


# --- The background thread's work ---

class Watcher:
    def __init__(self, lat, lon, tz):
        self.lat, self.lon, self.tz = lat, lon, tz
        self.backup = _Backup(lat, lon, tz)
        self.fails = 0
        self.on_backup = False
        self.backup_tested = 0.0

    def step(self):
        """One refresh. Returns how many seconds until the next."""
        try:
            data = fetch_open_meteo(self.lat, self.lon, self.tz)
        except Exception as e:
            self.fails += 1
            logger.warning(f"Weather fetch failed ({self.fails} in a row): {e}")
            if self.fails >= FAILS_TO_BACKUP:
                self._use_backup()
            return RETRY_SECONDS[min(self.fails, len(RETRY_SECONDS)) - 1]
        _save(data)
        if self.on_backup:
            logger.info("Weather: Open-Meteo is working again, off the backup.")
        self.fails, self.on_backup = 0, False
        if time.time() - self.backup_tested > BACKUP_TEST_SECONDS:
            self._test_backup()
        return REFRESH_SECONDS

    def _use_backup(self):
        try:
            data = self.backup.fetch()
        except Exception as e:
            logger.error(f"Weather backup failed too: {e}")
            self.on_backup = False
            health.fallback("weather", None)
            return
        self.backup_tested = time.time()
        if not self.on_backup:
            logger.warning("Weather: using the backup, the National Weather Service.")
        self.on_backup = True
        health.fallback("weather", USING_BACKUP)
        if data["as_of"] >= _saved_as_of():   # its station may have last reported before the copy already saved
            _save(data)

    def _test_backup(self):
        self.backup_tested = time.time()
        try:
            self.backup.fetch()
        except Exception as e:
            logger.warning(f"Weather backup isn't working: {e}")
