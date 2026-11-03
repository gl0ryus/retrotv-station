# The score card, off the air. Runs on the Pi: python3 -I scores_test.py [video file to render previews over]
import io, json, os, shutil, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
import retrotv_cards as cards, retrotv_scores as scores

TMP = tempfile.mkdtemp(prefix="scores_test")
cards.CARDS_DIR = TMP + "/cards"
os.makedirs(cards.CARDS_DIR)
real_logos = scores.LOGO_DIR
scores.LOGO_DIR = TMP + "/logos"
checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


def event(state, away="BOS", home="TB", detail="12:04 - 3rd"):
    side = lambda abbr, where, score: {"homeAway": where, "score": score, "team": {"abbreviation": abbr, "logo": f"https://a.espncdn.com/i/teamlogos/nhl/500/scoreboard/{abbr.lower()}.png"}}
    return {"status": {"type": {"state": state, "shortDetail": detail}}, "competitions": [{"competitors": [side(home, "home", "3"), side(away, "away", "1")]}]}


data = {"events": [event("in"), event("pre", "NYR", "FLA", "11/2 - 7:00 PM EST"), event("post", "OTT", "DET", "Final"), {"status": {"type": {"state": "in"}}, "competitions": []}]}
games = scores.live_games("nhl", "TB", data)
check(len(games) == 1, "only the live game; a broken event is skipped")
g = games[0]
check(g["away"][:2] == ("BOS", "1") and g["home"][:2] == ("TB", "3") and g["ours"] and g["detail"] == "3rd 12:04", g)
check(g["away"][2].endswith("bos.png"), "logo URL kept")
check(len(scores.live_games("nhl", "TB", data, ("pre", "in", "post"))) == 3, "today's games, for the test")
check(not scores.live_games("nhl", "XXX", data)[0]["ours"], "not ours")

# Timing: 6 seconds a game, and games dropped to fit
many = games * 20
shown, d = scores.fit(many, 60)
check(len(shown) == 9 and d <= 60 and d == scores.seconds_for(9), (len(shown), d))
shown, d = scores.fit(games, 60)
check(len(shown) == 1 and d == 7.1, d)
check(len(scores.fit(games * 3, 1)[0]) == 1, "always at least one")

# Logos: only PNGs, only from ESPN, only once
PNG = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=500x300,format=rgba", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
replies = {}
asked = []


def fake(req, timeout=None):
    url = req.full_url
    asked.append(url)
    reply = replies.get(url)
    if isinstance(reply, Exception):
        raise reply
    return io.BytesIO(reply)


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake
good = "https://a.espncdn.com/i/teamlogos/nhl/500/scoreboard/tb.png"
replies[good] = PNG
path = scores.logo_file("nhl", "TB", good)
check(path and os.path.exists(path) and path.endswith("nhl-tb.png"), path)
size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,pix_fmt", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
check(size.startswith(f"{scores.LOGO_PIXELS},15,") and "rgba" in size, f"sized, proportions kept: {size}")
check(sorted(os.listdir(scores.LOGO_DIR)) == ["nhl-tb.png"], "no leftovers")
asked.clear()
check(scores.logo_file("nhl", "TB", good) == path and not asked, "only downloaded once")
for url, why in (("http://a.espncdn.com/x.png", "not https"), ("https://evil.example/x.png", "another host"), ("https://espncdn.com.evil.example/x.png", "a lookalike"), (None, "no URL"), ("", "empty")):
    asked.clear()
    check(scores.logo_file("nhl", "AA" + str(checks), url) is None and not asked, why)
replies["https://a.espncdn.com/h.png"] = b"<html>not a picture"
check(scores.logo_file("nhl", "HTM", "https://a.espncdn.com/h.png") is None, "not a PNG")
replies["https://a.espncdn.com/big.png"] = b"\x89PNG" + b"0" * scores.LOGO_MAX_BYTES
check(scores.logo_file("nhl", "BIG", "https://a.espncdn.com/big.png") is None, "too big")
replies["https://a.espncdn.com/bad.png"] = b"\x89PNG not really"
check(scores.logo_file("nhl", "BAD", "https://a.espncdn.com/bad.png") is None, "a corrupt PNG")
check(sorted(os.listdir(scores.LOGO_DIR)) == ["nhl-tb.png"], "failures leave nothing behind")
asked.clear()
check(scores.logo_file("nhl", "BAD", "https://a.espncdn.com/bad.png") is None and not asked, "a failure isn't retried straight away")
replies["https://a.espncdn.com/down.png"] = OSError("network down")
check(scores.logo_file("nhl", "DWN", "https://a.espncdn.com/down.png") is None, "network failure")
check(scores.logo_file("nhl", "../../x", None) is None and "x" not in os.listdir(scores.LOGO_DIR), "an odd name can't leave the folder")
check(os.path.basename(scores.logo_file("nhl", "T/B", good) or "") == "nhl-tb.png", "name is cleaned")
sample = {"league": "nfl", "away": ("BUF", "7"), "home": ("TB", "14"), "detail": "2nd 3:00", "ours": True}
check(scores.logos_for([sample]) == {}, "a game with no logo URLs (the showcase's sample) still works")
urllib.request.urlopen = real_urlopen

# The filter graph
start = 100.0
two = [dict(g), {**g, "league": "nba", "ours": False, "away": ("MIA", "101", None), "home": ("ORL", "99", None), "detail": "End of the 3rd Quarter"}]
logos = {("nhl", "TB"): "/x/nhl-tb.png", ("nhl", "BOS"): "/x/nhl-bos.png"}
spec = scores.board_filter(two, start, logos)
check(spec.startswith("lavfi=[movie=/x/nhl-tb.png[logo0]") or spec.startswith("lavfi=[movie=/x/nhl-bos.png[logo0]"), spec[:60])
check(spec.count("movie=") == 2 and spec.count("overlay=") == 2, "one overlay per logo")
check("[in]ass=filename=" in spec and spec.endswith("]") and "[v2]" not in spec, "ends feeding mpv")
check("between(t,100.66,106.54)" in spec, "only during its game")
script = open(os.path.join(cards.CARDS_DIR, sorted(os.listdir(cards.CARDS_DIR))[-1]), encoding="utf-8").read()
check("NHL SCORES" in script and "NBA SCORES" in script and "3RD 12:04" in script and "MIA" in script and "101" in script, "the text")
plain = scores.board_filter(two[1:], start, {})
check(plain.startswith("lavfi=[ass=filename=") and "movie" not in plain, "no logos, no overlays")

# Previews over a real picture, the way the station lays it on: after the crunch to 342x200
if len(sys.argv) > 1:
    out = "/tmp/score_preview"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    scores.LOGO_DIR = real_logos
    today = scores.Scoreboard({"nfl": "TB", "nba": "ORL", "nhl": "TB"}).today()
    print(len(today), "games on today's boards")
    picks = [g for g in today if g["league"] == "nhl"][:1] + [g for g in today if g["league"] == "nba"][:1] + [g for g in today if g["league"] == "nfl"][:1]
    picks = picks or today[:3]
    picks = [{**picks[0], "ours": True, "away": (picks[0]["away"][0], "3", picks[0]["away"][2]), "home": (picks[0]["home"][0], "12", picks[0]["home"][2]), "detail": "3rd 12:04"}] + picks[1:]
    found = scores.logos_for(picks)
    print("logos:", sorted(os.path.basename(p) for p in found.values()))
    spec = scores.board_filter(picks, 5.0, found)
    for t in (5.3, 6.5, 9.5, 12.5, 18.5):
        subprocess.run(["nice", "-n", "15", "mpv", sys.argv[1], "--no-config", "--ao=null", "--vo=image", "--vo-image-format=png", f"--vo-image-outdir={out}/{t}", f"--start={t}", "--frames=1", "--really-quiet",
                        "--vf=scale=342:200,@scores:" + spec], timeout=60)
    print(subprocess.run(f"ls {out}/*", shell=True, capture_output=True, text=True).stdout.count(".png"), "previews")

shutil.rmtree(TMP)
print(f"score checks passed: {checks}")
