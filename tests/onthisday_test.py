# This Day in Gaming, Born Today and #1 This Week: their data (retrotv/onthisday.py) from made-up samples, no network,
# and their cards (retrotv/screens.py). Runs on the Pi or the PC: python3 -I tests/onthisday_test.py
import datetime, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
TMP = tempfile.mkdtemp(prefix="onthisday_test")
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(TMP + "/rt.log", mode="w")   # not the station's own log
from retrotv import cards, onthisday as otd, screens

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


G = "/mnt/tower/.retrotv/commercials/games/"
# --- Game ads: the game and year in the file's name ---
for name, want in [
    ("PlayStation 2/2002 - Tekken 4 (Playstation 2) - Retro Video Game Commercial 4-240p.mkv", (2002, "Tekken 4", "Playstation 2")),
    ("Game Boy Advance/2003 - Game Boy Advance - F-ZERO GP Legend GBA Commercial [IDkuUkG3XdU].mp4", (2003, "F-ZERO GP Legend", "Game Boy Advance")),
    ("Wii/2006 - The Legend of Zelda - Twilight Princess (Nintendo Gamecube _ Wii) - Retro Video Game Commercial.mp4",
     (2006, "The Legend of Zelda: Twilight Princess", "Nintendo Gamecube / Wii")),
    ("PlayStation 3/2013 - PlayStation 3 - The Last of Us - Spot TV 30 Ita [abcdefghijk].mp4", (2013, "The Last of Us", "PlayStation 3")),
    ("GameCube/2005 - GameCube - Star Fox Assault [30s] TV Commercial 2 for Nintendo GameCube [abcdefghijk].mp4",
     (2005, "Star Fox Assault", "GameCube")),
    ("Wii/2007 - Wii - Shrek the Third TV Commercial for PlayStation 2 & Xbox 360 & Nintendo Wii [abcdefghijk].mp4",
     (2007, "Shrek the Third", "Wii")),
    ("N64/1997 - N64 - Nintendo 64 (Nintendo, 1997) UK TV ad [ADGZHzDiW_k].mp4", None),   # the console, not a game
    ("Other/Pac-Man Fever (Atari 2600) - Retro Video Game Commercial _ Ad-240p.mkv", None),   # no year
]:
    got = otd.ad_game(G + name)
    check(got == want, f"{name}: {got}")

index = {"commercials": [G + "PlayStation 2/2002 - Tekken 4 (Playstation 2) - Retro Video Game Commercial 4-240p.mkv",
                         G + "PlayStation 2/2002 - PlayStation 2 - Tekken 4 TV Spot [abcdefghijk].mp4",
                         "/mnt/tower/.retrotv/commercials/food/1995 - Toaster Strudel.mp4"]}
found = otd.game_ads(index)
check(list(found) == ["tekken 4|2002"] and len(found["tekken 4|2002"]["ads"]) == 2, f"one game, both its ads: {found}")


# --- Release dates: North America's to the day, then one with no place, then the earliest ---
def statement(time, precision=11, places=(), rank="normal"):
    quals = {"P291": [{"datavalue": {"value": {"id": p}}} for p in places]} if places else {}
    return {"rank": rank, "mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}}, "qualifiers": quals}


check(otd.release_date([statement("+2001-12-07T00:00:00Z", places=["Q17"]), statement("+2002-01-29T00:00:00Z", places=["Q49"])])
      == "2002-01-29", "North America's date over Japan's")
check(otd.release_date([statement("+2001-12-07T00:00:00Z", places=["Q17"]), statement("+2001-12-20T00:00:00Z")]) == "2001-12-20",
      "then a date with no place")
check(otd.release_date([statement("+2001-00-00T00:00:00Z", precision=9), statement("+2001-11-15T00:00:00Z", rank="deprecated")])
      is None, "only real dates to the day")
entities = {"Q1": {"labels": {"en": {"value": "Tekken 4"}}, "claims": {"P577": [statement("+2002-03-28T00:00:00Z")]},
                   "sitelinks": {"enwiki": {}, "frwiki": {}}},
            "Q2": {"labels": {"en": {"value": "Tekken 4 Remastered"}}, "claims": {"P577": [statement("+2020-03-28T00:00:00Z")]}}}
check(otd.pick_game(["Q2", "Q1"], entities, 2002, "Tekken 4") == {"q": "Q1", "label": "Tekken 4", "date": "2002-03-28", "links": 2},
      "the game whose year fits the ad's, not the remake")
check(otd.pick_game(["Q2"], entities, 2002, "Tekken 4") is None, "none when no year fits")
games = {"tekken 4|2002": {"q": "Q1", "label": "Tekken 4", "date": "2002-03-28", "links": 2}}
on = otd.games_on(datetime.date(2026, 3, 28), found, games)
check(len(on) == 1 and on[0]["year"] == 2002 and len(on[0]["ads"]) == 2, f"games on the day: {on}")
check(otd.games_on(datetime.date(2026, 3, 29), found, games) == [], "and not on others")

# --- Born today ---
casts = {"It's Always Sunny in Philadelphia": {"name": "It's Always Sunny in Philadelphia", "cast": [
            ["Danny DeVito", "1944-11-17", None, "Frank Reynolds"], ["Kaitlin Olson", "1975-08-18", None, "Dee Reynolds"]]},
         "Taxi": {"name": "Taxi", "cast": [["Danny DeVito", "1944-11-17", None, "Louie De Palma"]]},
         "Old Show": {"name": "Old Show", "cast": [["Somebody", "1920-11-17", "1990-05-01", "Narrator"]]}}
born = otd.born_on(datetime.date(2026, 11, 17), casts)
check([p["name"] for p in born] == ["Danny DeVito", "Somebody"] and len(born[0]["roles"]) == 2,
      f"born today, in the most shows first, roles gathered: {born}")
check(otd.library_shows({"videos": ["/mnt/tower/.retrotv/videos/shows/Taxi/Season 1/a.mkv",
                                    "/mnt/tower/.retrotv/videos/shows/.Tapes/x.mkv"],
                         "g4": ["/mnt/tower/.retrotv/videos/g4/X-Play/b.mkv"]}) == ["Taxi", "X-Play"], "the library's shows")

# --- The Hot 100: a song's rows span its weeks; an act's second hit in a row has no artist cell ---
wikitext = '''{| class="wikitable"
!Issue date
!Song
!Artist(s)
|-
| bgcolor=#EDEAE0 align=center rowspan=2|784
!scope=row|{{dts|November 5}}
|align="center" rowspan="2"|"[[I'll Make Love to You]]"
|align="center" rowspan="4"|[[Boyz II Men]]
|align="center"|<ref>{{cite web|url=https://www.billboard.com/charts/hot-100/1994-11-05|title=x}}</ref>
|-
!scope=row|{{dts|November 12}}
|align="center"|<ref>{{cite web|url=https://www.billboard.com/charts/hot-100/1994-11-12|title=x}}</ref>
|-
| bgcolor=#EDEAE0 align=center rowspan=2|785
!scope=row|{{dts|December 3}}
|align="center" rowspan="2"|"[[On Bended Knee]]"
|align="center"|<ref>{{cite web|url=https://www.billboard.com/charts/hot-100/1994-12-03|title=x}}</ref>
|-
!scope=row|{{dts|December 10}}
|align="center"|<ref>{{cite web|url=https://www.billboard.com/charts/hot-100/1994-12-10|title=x}}</ref>
|-
| bgcolor=#EDEAE0 align=center|786
!scope=row|{{dts|December 17}}
|align="center"|"[[Here Comes the Hotstepper]]"
|align="center"|{{sort|Kamoze|[[Ini Kamoze]]}}
|align="center"|<ref>{{cite web|url=https://www.billboard.com/charts/hot-100/1994-12-17|title=x}}</ref>
|}'''
weeks = otd.hot100_weeks(1994, wikitext)
check(weeks == [["1994-11-05", "I'll Make Love to You", "Boyz II Men"], ["1994-11-12", "I'll Make Love to You", "Boyz II Men"],
                ["1994-12-03", "On Bended Knee", "Boyz II Men"], ["1994-12-10", "On Bended Knee", "Boyz II Men"],
                ["1994-12-17", "Here Comes the Hotstepper", "Ini Kamoze"]], f"Hot 100 weeks: {weeks}")
videos = otd.music_videos({"music": ["/mnt/tower/.retrotv/videos/music/Boyz II Men - On Bended Knee (Official Video).mp4",
                                     "/mnt/tower/.retrotv/videos/music/In The End (Official Video) - Linkin Park-360p.mkv"]})
check(otd.find_video("On Bended Knee", "Boyz II Men", videos).endswith("On Bended Knee (Official Video).mp4"), "its video")
check(otd.find_video("In the End", "Linkin Park", videos) is not None, "a name the wrong way round still matches")
check(otd.find_video("Here Comes the Hotstepper", "Ini Kamoze", videos) is None, "no video, none")
numbers = otd.number_ones_on(datetime.date(2026, 12, 8), {"1994": weeks}, videos)
check(len(numbers) == 1 and numbers[0]["song"] == "On Bended Knee" and numbers[0]["issue"] == "1994-12-03"
      and numbers[0]["weeks"] == 2 and numbers[0]["video"], f"the chart in force that day: {numbers}")

# --- The cards, from a picks file ---
cards.CARDS_DIR = TMP + "/cards"
os.makedirs(cards.CARDS_DIR, exist_ok=True)
screens.ONTHISDAY_FILE = TMP + "/onthisday.json"
day, now = datetime.date(2026, 11, 17), datetime.datetime(2026, 11, 17, 20, 15)
ad1, ad2 = G + "a/2004 - Halo 2 (Xbox) - Retro Video Game Commercial.mp4", G + "b/1998 - Metal Gear Solid (PS1) - Retro.mp4"
video = "/mnt/tower/.retrotv/videos/music/Boyz II Men - On Bended Knee (Official Video).mp4"
with open(screens.ONTHISDAY_FILE, "w") as f:
    json.dump({"days": {day.isoformat(): {
        "games": [{"title": "Halo 2", "year": 2004, "platform": "Xbox", "links": 60, "ads": [ad1]},
                  {"title": "Metal Gear Solid", "year": 1998, "platform": "PS1", "links": 50, "ads": [ad2]}],
        "born": born,
        "number_ones": [{"year": 1994, "song": "On Bended Knee", "artist": "Boyz II Men", "issue": "1994-11-12", "weeks": 6,
                         "video": video},
                        {"year": 1987, "song": "Here I Go Again", "artist": "Whitesnake", "issue": "1987-11-14", "weeks": 1,
                         "video": None}],
        "featured_year": 1994}}}, f)


def script_of(card):
    path = card.url.split("ass=filename=")[1].split(":")[0]
    with open(path, encoding="utf-8") as f:
        return f.read()


card = screens.games_today(now, day, None, lambda src: src == ad2)
text = script_of(card)
check(card.follow == ad2 and text.index("METAL GEAR SOLID") < text.index("HALO 2") and "THE TV AD FOR METAL GEAR SOLID" in text,
      "the game whose ad is on the SD card leads, and its ad follows")
card = screens.games_today(now, day, None, lambda src: False)
check(card.follow is None and "NEXT" not in script_of(card), "no ad on the SD card: the card alone")
card = screens.born_today(now, day, None, [(datetime.datetime(2026, 11, 17, 22, 0), "Taxi")])
text = script_of(card)
check("DANNY DEVITO" in text and "TURNS 82" in text and "1920–1990" in text and "LOUIE DE PALMA ON TAXI · TONIGHT AT 10:00 PM" in text,
      "ages, years, and the role whose show is on tonight")
card = screens.number_one(now, day, None, 1985, lambda src: src == video)
check(card.follow == video and "ON BENDED KNEE" in script_of(card) and "NEXT" in script_of(card), "the featured year with its video")
card = screens.number_one(now, day, None, 1985, lambda src: False)
check(card.follow is None and "HERE I GO AGAIN" in script_of(card), "no video ready: the year nearest the show's era")
check(screens.games_today(now, day + datetime.timedelta(days=1), None, lambda s: True) is None, "no picks, no card")
print(f"on this day checks passed: {checks}")
