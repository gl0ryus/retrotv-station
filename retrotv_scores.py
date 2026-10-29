"""
Live scores: while games are on, a SCORES lower third crawls them across a show, right to left, the home
teams' games first and in mustard. The scores come from ESPN's public scoreboard (unofficial, no key)
and are fetched only when a crawl is about to air, so they're never more than a couple of minutes old.
Standard library only.
"""
import json
import logging
import re
import time
import urllib.request

from retrotv_alerts import CAPS_CHAR, CRAWL_SPEED, LABEL_TEXT, UNDERLINE
from retrotv_cards import ARCHIVO, BARLOW_X, CREAM, MUSTARD, Script, esc, rect, slanted_bar, text_width
from retrotv_events import BAR_RIGHT
from retrotv_screens import USER_AGENT

logger = logging.getLogger("RetroTV")

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
SPORTS = {"nfl": "football", "nba": "basketball", "nhl": "hockey", "mlb": "baseball"}
FRESH_SECONDS = 120         # scores older than this are fetched again before a crawl
LABEL = "SCORES"
BAR_COLOR = "&H00201810&"   # the NEXT / LATER lower thirds' bar
TOP, HEIGHT = 462, 62       # where the lower thirds sit
SIZE = 46
SPACE_CHAR = 0.15           # a space's width as a fraction of the size, as rendered (letters and digits: CAPS_CHAR)
LEAD = 0.6                  # the bar slides in before the scores start crawling


class Scoreboard:
    """The games in progress, fetched on demand. teams: {league: home team abbreviation}, in crawl order."""
    def __init__(self, teams):
        unknown = [league for league in teams if league not in SPORTS]
        if unknown:
            logger.warning(f"Scores: no scoreboard for {', '.join(unknown)}; leaving them out")
        self.teams = {league: team for league, team in teams.items() if league in SPORTS}
        self.games = []
        self.fetched = 0.0
        self.failing = set()    # leagues whose last fetch failed, so a long outage logs once

    def live(self):
        """The games in progress, home teams first, from scores at most FRESH_SECONDS old."""
        if time.time() - self.fetched > FRESH_SECONDS:
            games = []
            for league, team in self.teams.items():
                games += self._fetch(league, team)
            self.games, self.fetched = games, time.time()
        order = list(self.teams)
        return sorted(self.games, key=lambda g: (not g["ours"], order.index(g["league"])))

    def _fetch(self, league, team):
        try:
            req = urllib.request.Request(SCOREBOARD_URL.format(sport=SPORTS[league], league=league),
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
        except Exception as e:
            if league not in self.failing:
                logger.warning(f"Scores: could not fetch the {league.upper()} scoreboard ({e})")
                self.failing.add(league)
            return []
        if league in self.failing:
            logger.info(f"Scores: the {league.upper()} scoreboard is back")
            self.failing.discard(league)
        return live_games(league, team, data)


def live_games(league, team, data):
    """The in-progress games on one league's scoreboard: away side first, as the tickers had them."""
    games = []
    for event in data.get("events", []):
        status = event.get("status", {}).get("type", {})
        if status.get("state") != "in":
            continue
        try:
            sides = {c["homeAway"]: (c["team"]["abbreviation"], str(c.get("score", "0")))
                     for c in event["competitions"][0]["competitors"]}
            away, home = sides["away"], sides["home"]
        except (KeyError, IndexError, TypeError):
            continue
        # ESPN's "2:15 - 4th" (or "31.7 - 4th" in the last minute) reads as the tickers had it: "4th 2:15"
        detail = re.sub(r"^(\d+(?::\d\d|\.\d)) - (.+)$", r"\2 \1", status.get("shortDetail", ""))
        games.append({"league": league, "away": away, "home": home, "detail": detail,
                      "ours": team in (away[0], home[0])})
    return games


# --- On screen ---

def _pieces(games):
    """(text, color) runs: each league's name in orange before its games, the home teams' games in mustard."""
    pieces, league = [], None
    for g in games:
        if g["league"] != league:
            if league:
                pieces.append(("      ", CREAM))
            pieces.append((g["league"].upper() + "   ", UNDERLINE))
            league = g["league"]
        else:
            pieces.append(("   ·   ", CREAM))
        (a, a_score), (h, h_score) = g["away"], g["home"]
        pieces.append((f"{a} {a_score}  {h} {h_score}  {g['detail'].upper()}", MUSTARD if g["ours"] else CREAM))
    return pieces


def _geometry():
    """The label box's width (as the promo sizes it) and the stretch the scores crawl through."""
    tag = max(150, int(text_width(LABEL, ARCHIVO, 40)) + 48)
    return tag, 40 + tag - 22, BAR_RIGHT


def _width(pieces):
    """The text's width as rendered, a few percent generous so it's always clear of the bar at the end."""
    text = "".join(t for t, _ in pieces)
    spaces = text.count(" ")
    return ((len(text) - spaces) * CAPS_CHAR * 1.06 + spaces * SPACE_CHAR) * SIZE * 1.03


def _timing(pieces):
    """(seconds the text takes to cross, seconds on screen in all)."""
    _, left, right = _geometry()
    crawl = (right - left + _width(pieces)) / CRAWL_SPEED
    return crawl, round(LEAD + crawl + 0.5, 2)


def fit(games, max_seconds):
    """(games, seconds on screen) for a crawl, dropping games off the end to keep it within max_seconds."""
    while True:
        _, d = _timing(_pieces(games))
        if d <= max_seconds or len(games) == 1:
            return games, d
        games = games[:-1]


def ticker_script(games, start):
    """An ASS file for the vf chain: the NEXT lower third's bar, full length, with a SCORES label box and
    the games crawling through it right to left from file time `start`. Returns its path."""
    tag, left, right = _geometry()
    pieces = _pieces(games)
    width = _width(pieces)
    crawl, d = _timing(pieces)
    end = start + d
    y = TOP + HEIGHT / 2
    shift = right + 40   # everything slides in together from off the left edge

    def slide(x, yy):
        return f"\\move({x - shift},{yy},{x},{yy},0,450)"
    s = Script()
    s.shape(start, end, 40, TOP, slanted_bar(right - 40, HEIGHT, 22), BAR_COLOR, fade=(0, 300), layer=1,
            extra=slide(40, TOP) + "\\1a&H30&")
    s.shape(start, end, 40, TOP, slanted_bar(tag, HEIGHT, 22), MUSTARD, fade=(0, 300), layer=3,
            extra=slide(40, TOP))
    s.shape(start, end, 40, TOP + HEIGHT + 2, rect(right - 40 - 22, 5), UNDERLINE, fade=(0, 300), layer=3,
            extra=slide(40, TOP + HEIGHT + 2))
    s.text(start, end, 62, y, LABEL, ARCHIVO, 40, LABEL_TEXT, align=4, fade=(0, 300), layer=4,
           extra=slide(62, y))
    # Kept inside the bar, slanted end and all; it slips under the label box on its way out
    clip = f"m {left} {TOP} l {right} {TOP} l {right - 22} {TOP + HEIGHT} l {left} {TOP + HEIGHT}"
    tags = (f"\\an4\\move({right},{y:.0f},{left - width:.0f},{y:.0f},{LEAD * 1000:.0f},{(LEAD + crawl) * 1000:.0f})"
            f"\\fn{BARLOW_X}\\fs{SIZE}\\bord0\\shad0\\fad(0,300)\\clip({clip})")
    s.raw(start, end, tags, "".join(f"{{\\c{color}}}{esc(text)}" for text, color in pieces), layer=2)
    return s.write("scores")
