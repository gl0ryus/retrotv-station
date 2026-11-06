"""
Records what the cards, screens and lower thirds draw for fixed inputs, so a refactor can prove it changed none of it.

    python3 -I tests/snapshots.py record DIR     # before the change
    python3 -I tests/snapshots.py compare DIR    # after: lists anything that draws differently

The screens read the station's data through FROZEN=<dir> (see harness.freeze), never the network. Each builder's
output is its card URL or filter, with every script it uses written out in full, and paths that change from run to
run (temp folders, the numbered card files) evened out.
"""
import datetime
import os
import random
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402

harness.same_every_run()

NOW = datetime.datetime(2026, 11, 3, 20, 0)   # the day harness.freeze last ran: the day-by-day screens have data for it
WORK = tempfile.mkdtemp(prefix="snapshots")


def modules():
    """The station's modules, by short name: overlay_filter is in retrotv.overlays once the station module is split."""
    from retrotv import alerts, art, cards, events, screens, scores
    try:
        from retrotv.overlays import overlay_filter  # type: ignore[import-not-found]
    except ImportError:
        from retrotv.station import overlay_filter
    return alerts, art, cards, events, screens, scores, overlay_filter


alerts, art, cards, events, screens, scores, overlay_filter = modules()
data = os.path.join(WORK, "data")
shutil.copytree(harness.frozen("retrotv_data"), data, ignore=shutil.ignore_patterns("show_clips", "team_logos", "*.tmp"))
moved = harness.redirect(os.path.join(harness.HOME, "retrotv_data"), data)   # never the live files
if moved < 10:
    raise SystemExit(f"only {moved} data paths redirected: is DATA_DIR still where harness expects?")
cards.CARDS_DIR = os.path.join(WORK, "cards")
os.makedirs(cards.CARDS_DIR)


def saved(name, url, max_age_minutes, stale_hours=24):
    path = os.path.join(data, f"{name}.json")
    return harness.load_json(path)["data"] if os.path.exists(path) else None


harness.offline()
screens.fetch_json = saved

weather = harness.load_json(harness.frozen("retrotv_weather.json")) if os.path.exists(harness.frozen("retrotv_weather.json")) else None
if weather:
    weather["as_of"] = weather["fetched"] = datetime.datetime.now().timestamp() - 60
game = {"league": "nhl", "away": ("BOS", "1", None), "home": ("TB", "3", None), "detail": "3rd 12:04", "ours": True}
rows = [(NOW, "Seinfeld", "S1 E2 · Male Un-Bonding"), (NOW + datetime.timedelta(minutes=30), "Friends", "S2 E1"),
        (NOW + datetime.timedelta(hours=1), "OFF THE AIR", "Programming resumes at 9:00 AM")]
BUILDERS = {
    "cards.right_back": lambda: cards.right_back(),
    "cards.right_back.show": lambda: cards.right_back(None, "Seinfeld"),
    "cards.right_back.month": lambda: cards.right_back("blackhistory", "The Fresh Prince of Bel-Air"),
    "cards.right_back.halloween": lambda: cards.right_back("halloween", "Seinfeld"),
    "cards.back_to": lambda: cards.back_to("Seinfeld"),
    "cards.back_to.episode": lambda: cards.back_to("Seinfeld", None, "S1 E2 · Male Un-Bonding", 1990),
    "cards.up_next": lambda: cards.up_next(("Seinfeld", "S1 E2"), ("Friends", "S2 E1"), NOW, duration=7.0),
    "cards.up_next.art": lambda: cards.up_next(("Seinfeld", "S1 E2 · Male Un-Bonding"), ("Friends", "S2 E1"), NOW,
                                               duration=7.0, starts=NOW, year=1990, art=art.wide("Seinfeld")),
    "cards.up_next.thumb": lambda: cards.up_next(("The Fresh Prince of Bel-Air", "S3 E7 · The Ethnic Tip"), None, None,
                                                 duration=7.0, starts=NOW, year=1992, art=art.thumb("Futurama")),
    "cards.station_id": lambda: cards.station_id(NOW),
    "cards.station_id.morning": lambda: cards.station_id(NOW, good_morning=True),
    "cards.station_id.month": lambda: cards.station_id(NOW, month="blackhistory"),
    "cards.sign_off": lambda: cards.sign_off(NOW),
    "cards.stand_by": lambda: cards.stand_by(),
    "cards.guide": lambda: cards.guide(rows, NOW),
    "cards.weather": lambda: cards.weather(weather, "Holiday, FL", datetime.datetime.combine(datetime.date.fromisoformat(weather["daily"]["time"][0]), NOW.time())) if weather else None,
    "events.station_id.halloween": lambda: events.station_id(NOW, season="halloween"),
    "events.station_id.christmas": lambda: events.station_id(NOW, season="christmas"),
    "events.reminder": lambda: events.reminder("Mother's Day", NOW.date() + datetime.timedelta(days=3), NOW),
    "events.marathon": lambda: events.marathon("Friday Night Marathon", "Seinfeld", NOW),
    "events.marathon.lineup": lambda: events.marathon("Friday Night Marathon", "Seinfeld", NOW, None, [
        (NOW + datetime.timedelta(minutes=30 * i), f"S1 E{i + 1} · " + t) for i, t in enumerate(
            ("The Seinfeld Chronicles", "The Stake Out", "The Robbery", "Male Un-Bonding", "The Stock Tip"))]),
    "events.station_id.july4": lambda: events.station_id(NOW, season="july4"),
    "events.marathon_ad": lambda: events.marathon_ad("/x/seinfeld.jpg", "Seinfeld", "This Friday", "4:30 PM"),
    "events.snipe": lambda: events.snipe("The Simpsons", 100.0, 128.0),
    "events.promo": lambda: events.promo([("Next", "Seinfeld"), ("Later", "Friends · 8:30 PM"), ("Tonight", "Futurama · 9:00 PM")], 8.0, 8.0),
    "events.launch_cut_in": lambda: events.launch_cut_in("Falcon 9 Block 5", "Starlink Group 10-12", "Cape Canaveral SFS"),
    "events.popups": lambda: events.popups(["The video was shot in one day.", "The band hated the song.", "It went to number one."], 220.0),
    "events.lottery": lambda: events.lottery(NOW),
    "events.new_year": lambda: events.new_year(2027),
    "events.anthem": lambda: events.anthem(),
    "events.power_off": lambda: events.power_off(),
    "events.dark": lambda: events.dark(3.0),
    "events.sign_on": lambda: events.sign_on(),
    "screens.pop_quiz": lambda: screens.pop_quiz(),
    "screens.today_in_history": lambda: screens.today_in_history(NOW),
    "screens.launch_watch": lambda: screens.launch_watch(NOW),
    "screens.tonight_sky": lambda: screens.tonight_sky(NOW),
    "screens.markets": lambda: screens.markets(NOW),
    "screens.art_break": lambda: screens.art_break(NOW),
    "screens.whos_in_space": lambda: screens.whos_in_space(NOW),
    "screens.countdown": lambda: screens.countdown(screens.weekly_chart(), (1, "Artist", "Song"), NOW),
    "screens.games_today": lambda: screens.games_today(NOW, datetime.date.today(), None, lambda src: True),
    "screens.born_today": lambda: screens.born_today(NOW, datetime.date.today(), None),
    "screens.number_one": lambda: screens.number_one(NOW, datetime.date.today(), None, 1993, lambda src: True),
    "screens.anniversary": lambda: screens.anniversary({"aired": "1990-11-03", "years": 36, "week": [
        "Mariah Carey's \"Love Takes Time\" was the number one song.", "Home Alone opened in theaters that month."]},
        "Seinfeld", "S1 E3 · The Robbery", NOW),
    "screens.teletext": lambda: screens.teletext(NOW, weather, "Holiday, FL", [(NOW, "Seinfeld", "S1 E2")], ["A goat ate a car"]),
    "alerts.crawl_script": lambda: alerts.crawl_script("TORNADO WARNING FOR PASCO COUNTY UNTIL 9:15 PM", "WEATHER ALERT", 30.0),
    "alerts.test_card": lambda: alerts.test_card(),
    "scores.board_filter": lambda: scores.board_filter([game], 50.0, {("nhl", "TB"): "/x/tb.png"}),
    "overlay.bug_progress": lambda: overlay_filter(1500.0, True),
    "overlay.promo_art": lambda: overlay_filter(1500.0, True, promo="/x/p.ass", art=[("/x/c.mp4", 8.0, 16.0, True), ("/x/b.png", 16.0, 24.0, False)]),
    "overlay.glitch": lambda: overlay_filter(1500.0, False, glitch=(100.0, "roll")),
}


def render(build):
    random.seed(7)
    try:
        result = build()
    except Exception as e:   # a builder that breaks is a difference too
        return f"ERROR {type(e).__name__}: {e}"
    if result is None:
        return "None"
    text = repr(result) if not hasattr(result, "url") else f"{result.url}\nduration={result.duration} kind={result.kind}"
    for path in sorted(set(re.findall(r"[\w/.\-]+\.ass", text))):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text += f"\n--- {os.path.basename(path)}\n" + f.read()
    text = text.replace(WORK, "<work>")
    return re.sub(r"([a-z\-]+)-\d+\.ass", r"\1-N.ass", text)


def main():
    mode, folder = sys.argv[1], sys.argv[2]
    os.makedirs(folder, exist_ok=True)
    differ = 0
    for name, build in BUILDERS.items():
        now = render(build)
        path = os.path.join(folder, name + ".txt")
        if mode == "record":
            with open(path, "w", encoding="utf-8") as f:
                f.write(now)
            print(f"{name}: {now.splitlines()[0][:90]}")
        else:
            with open(path, encoding="utf-8") as f:
                before = f.read()
            if before != now:
                differ += 1
                lines = [i for i, (a, b) in enumerate(zip(before.splitlines(), now.splitlines())) if a != b]
                print(f"DIFFERENT {name}: first at line {lines[0] + 1 if lines else min(len(before.splitlines()), len(now.splitlines())) + 1}")
    if mode == "compare":
        print(f"{len(BUILDERS) - differ} of {len(BUILDERS)} draw the same" + ("" if not differ else f", {differ} differ"))
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(1 if differ else 0)


if __name__ == "__main__":
    main()
