"""
Live scores: while games are on, a large SCORES lower third shows them over a show one game at a time, with the teams'
logos, the home teams' games first and in mustard. The scores come from ESPN's public scoreboard (unofficial, no key)
and are fetched only when the card is about to air, so they're never more than a couple of minutes old. Logos are
downloaded once and kept. Standard library, plus ffmpeg to size the logos.
"""
import logging
import os
import re
import subprocess
import time

from retrotv.art import download_image
from retrotv.cards import LABEL_INK, ARCHIVO, BARLOW_X, CREAM, FONTS_DIR, H, MUSTARD, Script, W, rounded
from retrotv.cards import fit as cards_fit
from retrotv.events import BAR_RIGHT, BAR_SLANT, lower_third
from retrotv.jsonio import get_json
from retrotv.paths import DATA_DIR

logger = logging.getLogger("RetroTV")

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
SPORTS = {"nfl": "football", "nba": "basketball", "nhl": "hockey", "mlb": "baseball"}
FRESH_SECONDS = 120         # scores older than this are fetched again before a card
TOP, HEIGHT = 414, 110      # the same bottom edge as those lower thirds, about twice as tall
TAG_WIDTH = 176             # the mustard box with the league and the game's clock
FIRST_TEAM_X, TEAM_WIDTH = 214, 256
CHIP = 86                   # the cream plate behind a logo: ESPN's are drawn for a light background
SCORE_SIZE, NAME_SIZE = 56, 38
FRAME_W, FRAME_H = 342, 200 # the picture at the point in the chain the filter joins it, after the CRT crunch
PAGE_SECONDS = 6.0          # each game this long
LEAD = 0.6                  # the bar slides in before the first game shows
LOGO_DIR = os.path.join(DATA_DIR, "team_logos")
LOGO_HOST = "espncdn.com"
LOGO_PIXELS = 25            # of the 342x200 picture
LOGO_MAX_BYTES = 3 * 1024 * 1024
LOGO_RETRY_SECONDS = 3600
LOGO_FILTER = f"scale={LOGO_PIXELS}:{LOGO_PIXELS}:force_original_aspect_ratio=decrease:flags=lanczos,format=rgba"
_logo_failed = {}           # file -> when it last couldn't be downloaded


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

    def today(self):
        """Every game on today's scoreboards, whatever its state, to try the card out when none are live."""
        return [g for league, team in self.teams.items() for g in self._fetch(league, team, ("pre", "in", "post"))]

    def _fetch(self, league, team, states=("in",)):
        try:
            data = get_json(SCOREBOARD_URL.format(sport=SPORTS[league], league=league), timeout=10)
        except Exception as e:
            if league not in self.failing:
                logger.warning(f"Scores: could not fetch the {league.upper()} scoreboard ({e})")
                self.failing.add(league)
            return []
        if league in self.failing:
            logger.info(f"Scores: the {league.upper()} scoreboard is back")
            self.failing.discard(league)
        return live_games(league, team, data, states)


def live_games(league, team, data, states=("in",)):
    """The in-progress games on one league's scoreboard: away side first, as the tickers had them. Each side is
    (abbreviation, score, logo URL)."""
    games = []
    for event in data.get("events", []):
        status = event.get("status", {}).get("type", {})
        if status.get("state") not in states:
            continue
        try:
            sides = {c["homeAway"]: (c["team"]["abbreviation"], str(c.get("score", "0")), c["team"].get("logo"))
                     for c in event["competitions"][0]["competitors"]}
            away, home = sides["away"], sides["home"]
        except (KeyError, IndexError, TypeError):
            continue
        # ESPN's "2:15 - 4th" (or "31.7 - 4th" in the last minute) reads as the tickers had it: "4th 2:15"
        detail = re.sub(r"^(\d+(?::\d\d|\.\d)) - (.+)$", r"\2 \1", status.get("shortDetail", ""))
        games.append({"league": league, "away": away, "home": home, "detail": detail,
                      "ours": team in (away[0], home[0])})
    return games


# --- Team logos ---

def logo_file(league, abbreviation, url):
    """A team's logo sized for the card, downloaded once from ESPN. None for now if it can't be had: the card goes without."""
    name = re.sub(r"[^a-z0-9]", "", abbreviation.lower())
    path = os.path.join(LOGO_DIR, f"{league}-{name}.png")
    if os.path.exists(path):
        return path
    if not name or not url or time.time() - _logo_failed.get(path, 0) < LOGO_RETRY_SECONDS:
        return None
    try:
        download_image(url, (LOGO_HOST,), path, LOGO_FILTER, LOGO_MAX_BYTES)
        return path
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.warning(f"Scores: no logo for {abbreviation} ({e})")
        _logo_failed[path] = time.time()
        return None


def logos_for(games):
    """{(league, abbreviation): file} for the games' teams, as far as they can be had."""
    logos = {}
    for g in games:
        for side in (g["away"], g["home"]):
            found = logo_file(g["league"], side[0], side[2] if len(side) > 2 else None)
            if found:
                logos[g["league"], side[0]] = found
    return logos


# --- On screen ---

def seconds_for(count):
    return round(LEAD + count * PAGE_SECONDS + 0.5, 2)


def fit(games, max_seconds):
    """(games, seconds on screen) for a card, dropping games off the end to keep it within max_seconds."""
    while len(games) > 1 and seconds_for(len(games)) > max_seconds:
        games = games[:-1]
    return games, seconds_for(len(games))


def _frame_x(x):
    return round(x * FRAME_W / W)


def _frame_y(y):
    return round(y * FRAME_H / H)


def board_filter(games, start, logos):
    """The score card for the vf chain, from file time `start`: a large lower third (the same bottom edge as the NEXT
    one, reaching higher), showing the games one at a time, each team as its logo, score and name:

        [ NHL SCORES ]  (logo)  3        (logo)  1
        [ 3RD 12:04  ]  (logo)  TB       (logo)  BOS

    Pictures can't go in an ASS script, so the logos are laid over it, each only during its game."""
    bar_end = start + LEAD + len(games) * PAGE_SECONDS + 0.3
    s = Script()
    lower_third(s, start, bar_end, TAG_WIDTH, BAR_RIGHT - 40 - TAG_WIDTH, top=TOP, height=HEIGHT, bar_alpha="&H20&")
    sources, stages, label = [], [], "base"
    room = (TAG_WIDTH - BAR_SLANT - 22) * 1.45    # fit's estimates run wide
    for i, g in enumerate(games):
        a = start + LEAD + i * PAGE_SECONDS
        b = a + PAGE_SECONDS
        fade = (120, 120)
        title = f"{g['league'].upper()} SCORES"
        detail = g["detail"].upper()
        s.text(a, b, 54, TOP + 36, title, BARLOW_X, cards_fit(title, BARLOW_X, 40, room), LABEL_INK, align=4, fade=fade, layer=3)
        s.text(a, b, 54, TOP + 78, detail, BARLOW_X, cards_fit(detail, BARLOW_X, 40, room), LABEL_INK, align=4, fade=fade, layer=3)
        color = MUSTARD if g["ours"] else CREAM
        for j, side in enumerate((g["away"], g["home"])):
            x = FIRST_TEAM_X + j * TEAM_WIDTH
            has_logo = (g["league"], side[0]) in logos
            s.shape(a, b, x, TOP + (HEIGHT - CHIP) / 2, rounded(CHIP, CHIP, 10), CREAM, fade=fade, layer=3)
            if not has_logo:   # no logo to hand: the team's letters on the same plate
                s.text(a, b, x + CHIP / 2, TOP + HEIGHT / 2, side[0], BARLOW_X, cards_fit(side[0], BARLOW_X, 44, CHIP - 8), LABEL_INK, fade=fade, layer=4)
            middle = x + CHIP + (TEAM_WIDTH - CHIP - 8) / 2
            s.text(a, b, middle, TOP + 40, str(side[1]), ARCHIVO, SCORE_SIZE, color, fade=fade, layer=3)
            s.text(a, b, middle, TOP + 88, side[0], BARLOW_X, NAME_SIZE, color, fade=fade, layer=3)
            if has_logo:
                source = f"logo{len(sources)}"
                sources.append(f"movie={logos[g['league'], side[0]]}[{source}]")
                following = f"v{len(sources)}"
                stages.append(f"[{label}][{source}]overlay=x={_frame_x(x + CHIP / 2)}-w/2:y={_frame_y(TOP + HEIGHT / 2)}-h/2:"
                              f"enable='between(t,{a + 0.06:.2f},{b - 0.06:.2f})'[{following}]")
                label = following
    text = f"ass=filename={s.write('scores')}:fontsdir={FONTS_DIR}"
    if not stages:
        return f"lavfi=[{text}]"
    stages[-1] = stages[-1][:-len(f"[{label}]")]   # the last stage feeds mpv
    return "lavfi=[" + ";".join(sources + [f"[in]{text}[base]"] + stages) + "]"
