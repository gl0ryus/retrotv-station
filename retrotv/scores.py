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
from retrotv import brand
from retrotv.brand import Part
from retrotv.cards import FONTS_DIR, H, Script, W
from retrotv.config import FRAME_H, FRAME_W   # the picture where the filter joins it
from retrotv.jsonio import get_json
from retrotv.paths import DATA_DIR

logger = logging.getLogger("RetroTV")

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
SPORTS = {"nfl": "football", "nba": "basketball", "nhl": "hockey", "mlb": "baseball"}
FRESH_SECONDS = 120         # scores older than this are fetched again before a card
LEFT, TOP = 44, 350          # the score bug: its bottom edge on the NEXT lower third's (events.BAR_TOP + BAR_HEIGHT)
TAG_H, ROW_H = 40, 61        # the league and clock strip, and a team's row
PLATE, NAME_W, SCORE_W = 90, 290, 112   # a row's parts: the white plate behind a logo (ESPN's are drawn for a light
                                        # background), the team, the score
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
    """The score card for the vf chain, from file time `start`: the guide's score bug, standing on the NEXT lower
    third's bottom edge and showing the games one at a time:

        [ NHL | 3RD 12:04 ]
        [ (logo) | BOS       | 1 ]
        [ (logo) | TB        | 3 ]

    Pictures can't go in an ASS script, so the logos are laid over it, each only during its game."""
    s = Script()
    sources, stages, label = [], [], "base"
    for i, g in enumerate(games):
        a = start + LEAD + i * PAGE_SECONDS if i else start
        b = start + LEAD + (i + 1) * PAGE_SECONDS
        first = i == 0
        fade = (0 if first else 120, 300 if i == len(games) - 1 else 120)
        slide = (0, 450, 780) if first else None
        brand.strip(s, a, b, LEFT, TOP, TAG_H, [Part(g["league"].upper(), brand.ANTON, 30, brand.INK, brand.YELLOW,
                                                     pad=16),
                                                Part(g["detail"].upper(), brand.ANTON, 28, brand.WHITE, brand.INK,
                                                     pad=16)],
                    shadow=(6, 6, brand.INK), fade=fade, slide=slide, layer=3)
        for j, (side, fill, score_fill) in enumerate(((g["away"], brand.PURPLE, brand.YELLOW),
                                                      (g["home"], brand.INK, brand.WHITE))):
            top = TOP + TAG_H + 8 + j * (ROW_H + 8)
            brand.strip(s, a, b, LEFT, top, ROW_H,
                        [Part("", brand.ANTON, 30, brand.INK, brand.WHITE, pad=0, width=PLATE),
                         Part(side[0], brand.ANTON, 40, brand.WHITE, fill, pad=22, width=NAME_W),
                         Part(str(side[1]), brand.ANTON, 48, brand.INK, score_fill, pad=0, width=SCORE_W,
                              align="center")],
                        shadow=(7, 7, brand.INK), fade=fade, slide=slide, layer=3)
            if (g["league"], side[0]) in logos:
                source = f"logo{len(sources)}"
                bigger = "" if FRAME_W == 342 else f",scale=iw*{FRAME_W / 342:.4f}:ih*{FRAME_W / 342:.4f}"
                sources.append(f"movie={logos[g['league'], side[0]]}{bigger}[{source}]")
                following = f"v{len(sources)}"
                show_from = start + LEAD + i * PAGE_SECONDS + 0.06   # once the bar has slid in
                stages.append(f"[{label}][{source}]overlay=x={_frame_x(LEFT + PLATE / 2)}-w/2:"
                              f"y={_frame_y(top + ROW_H / 2)}-h/2:enable='between(t,{show_from:.2f},{b - 0.06:.2f})'"
                              f"[{following}]")
                label = following
            else:   # no logo to hand: the team's letters on the plate
                brand.words(s, a, b, LEFT + PLATE / 2, top + ROW_H / 2, side[0], brand.ANTON, 26, brand.INK,
                            align="center", fade=fade, slide=slide, layer=6)
    text = f"ass=filename={s.write('scores')}:fontsdir={FONTS_DIR}"
    if not stages:
        return f"lavfi=[{text}]"
    stages[-1] = stages[-1][:-len(f"[{label}]")]   # the last stage feeds mpv
    return "lavfi=[" + ";".join(sources + [f"[in]{text}[base]"] + stages) + "]"
