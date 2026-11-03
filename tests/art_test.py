# Posters and the marathon ad. Runs on the Pi: python3 -I art_test.py [video file for previews]
# Uses a temporary art folder; with a video file it also looks up real art for the marathon shows and renders frames.
import datetime, io, json, os, shutil, subprocess, sys, tempfile, time, types, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
rs = harness.everything()
from retrotv import art
from retrotv import cards
from retrotv import events

TMP = tempfile.mkdtemp(prefix="art_test")
cards.CARDS_DIR = TMP + "/cards"
os.makedirs(cards.CARDS_DIR)
real_art = art.ART_DIR
art.ART_DIR, art.LOOKUPS_FILE = TMP + "/art", TMP + "/art/lookups.json"
art.REQUEST_GAP_SECONDS = 0
checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


# --- File names
check(art.key_of("The Screen Savers 06 04 2004") == art.key_of("The Screen Savers") == "the-screen-savers", "dated recordings share their show's")
check(art.key_of("Mr. Show!") == "mr-show" and art.key_of("") == "" and art.key_of("../../etc") == "etc", "safe names")
check(art.thumb("") is None and art.wide("nothing") is None, "nothing there")

# --- Only a real match counts
result = lambda name, premiered="2000-01-01", country="US", image=True: {"show": {"id": 1, "name": name, "premiered": premiered, "image": {"medium": "m"} if image else None, "network": {"country": {"code": country}}}}
office = [result("The Office", "2005-03-24", "US"), result("The Office", "2001-07-09", "GB"), result("The Office", "2024-10-18", "AU")]
check(art.pick_show(office, "The Office")["premiered"].startswith("2001"), "the oldest exact match")
scrubs = [result("Scrubs", "2026-02-25"), result("Scrubs", "2001-10-02")]
check(art.pick_show(scrubs, "Scrubs")["premiered"].startswith("2001"), "the original, not the revival")
check(art.pick_show(scrubs, "Scrubs", year=2004)["premiered"].startswith("2001"), "and with its year")
check(art.pick_show([result("The Big Thinkers", "2017-04-11")], "Big Thinkers", year=2000) is None, "a show that premiered years after the episode isn't it")
check(art.pick_show([result("Heroes", "2006-09-25")], "Heroes", year=2005)["name"] == "Heroes", "a year's slip is allowed")
check(art.pick_show(office, "The Office (UK)") is None, "an unmatched country isn't guessed: TVmaze says GB")
check(art.pick_show(office, "The Office (GB)")["premiered"].startswith("2001"), "by country")
check(art.pick_show(office, "The Office (2001)")["premiered"].startswith("2001"), "by year")
check(art.pick_show(office, "The Office", year=2003)["premiered"].startswith("2001"), "an episode from 2003 can't be a 2005 show")
check(art.pick_show([result("The New Screen Savers")], "The Screen Savers") is None, "a similar name isn't the same show")
check(art.pick_show([result("Attack of the Show!")], "Attack of the Show")["name"] == "Attack of the Show!", "punctuation doesn't matter")
check(art.pick_show([result("Simpsons")], "The Simpsons")["name"] == "Simpsons", "nor a leading The")
check(art.pick_show([result("Will & Grace")], "Will and Grace")["name"] == "Will & Grace", "nor an ampersand")
check(art.pick_show([result("Seinfeld", image=False)], "Seinfeld") is None, "no image, no use")
check(art.pick_show([], "Seinfeld") is None, "no results")

check(art.key_of("Χ-Play") == "x-play" and art._search_name("Χ-Play") == "X-Play", "a Greek letter in a file name")
check(art.pick_show([result("X-Play", "2003-04-28")], "Χ-Play", year=2011)["name"] == "X-Play", "still X-Play")
check(art._search_name("The Daily Show") == "The Daily Show with Jon Stewart", "what TVmaze calls it")
check(art.pick_show([result("The Daily Show", "2023-01-17"), result("The Daily Show with Jon Stewart", "1999-01-11")], "The Daily Show", year=2004)["premiered"].startswith("1999"), "the right era's Daily Show")
check("big-thinkers" in art.NO_ART and art.refresh([("Big Thinkers", "videos", None, False)]) == 0, "a known wrong match is skipped")

# --- Looking things up, and remembering
PNG = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=680x1000,format=rgba", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
JPG = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=1920x1080", "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-"], capture_output=True, check=True).stdout
replies, asked = {}, []


def fake(req, timeout=None):
    url = getattr(req, "full_url", req)
    asked.append(url)
    for key, reply in replies.items():
        if key in url:
            if isinstance(reply, Exception):
                raise reply
            return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply).encode())
    raise OSError(f"no route: {url}")


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake
poster, background = "https://static.tvmaze.com/p.png", "https://static.tvmaze.com/b.jpg"
replies.update({"search/shows?q=Seinfeld": [{"show": {"id": 7, "name": "Seinfeld", "premiered": "1989-07-05", "image": {"medium": poster, "original": poster}}}],
                "search/shows?q=Nothing": [], "shows/7/images": [{"type": "poster", "resolutions": {"original": {"url": poster}}}, {"type": "background", "main": True, "resolutions": {"original": {"url": background}}}],
                poster: PNG, background: JPG})
check(art.refresh([("Seinfeld", "videos", None, True), ("Nothing Here", "videos", None, False)]) == 2, "a poster and a large picture")
thumb_size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", art.thumb("Seinfeld")], capture_output=True, text=True).stdout.strip()
wide_size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", art.wide("Seinfeld")], capture_output=True, text=True).stdout.strip()
check(thumb_size == "118,68", thumb_size)
# Only a tall poster to go on: it's still 16:9, and nothing asks for a background that isn't there
replies.update({"search/shows?q=Cheers": [{"show": {"id": 8, "name": "Cheers", "premiered": "1982-09-30", "image": {"medium": poster, "original": poster}}}], "shows/8/images": []})
art.refresh([("Cheers", "videos", None, False)])
check(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", art.thumb("Cheers")], capture_output=True, text=True).stdout.strip() == "118,68", "a tall poster, boxed to 16:9")
check(wide_size == "712,400" or wide_size == "711,400", f"a wide picture has room to pan: {wide_size}")
check(art.thumb("Nothing Here") is None, "no art, none invented")
asked.clear()
check(art.refresh([("Seinfeld", "videos", None, True), ("Nothing Here", "videos", None, False)]) == 0 and not asked, "nothing asked twice")
lookups = json.load(open(art.LOOKUPS_FILE))
lookups["nothing-here"]["tried"] = time.time() - 15 * 86400
json.dump(lookups, open(art.LOOKUPS_FILE, "w"))
art.refresh([("Nothing Here", "videos", None, False)])
check(len(asked) == 1, "a miss is asked again after two weeks")
# A show marked for the ad later gets its large picture without looking it up again
os.remove(art.wide("Seinfeld")); asked.clear()
art.refresh([("Seinfeld", "videos", None, True)])
check(art.wide("Seinfeld") and asked == [background], "only the picture")
# The network down: stops, remembers nothing wrong, tries again next time
replies["search/shows?q=Friends"] = OSError("down")
asked.clear()
art.refresh([("Friends", "videos", None, False), ("Scrubs", "videos", None, False)])
check(len(asked) == 1, "stops at the first network failure")
check("friends" not in json.load(open(art.LOOKUPS_FILE)), "a failure isn't a miss")
replies["search/shows?q=Friends"] = {"not": "a list"}
art.refresh([("Friends", "videos", None, False)])  # a reply in the wrong shape doesn't take the thread down
# Pictures: only from allowed hosts, only pictures
for url, data, why in (("http://static.tvmaze.com/a.png", PNG, "not https"), ("https://evil.example/a.png", PNG, "another host"), ("https://tvmaze.com.evil.example/a.png", PNG, "a lookalike"),
                       ("https://static.tvmaze.com/h.png", b"<html>", "not a picture"), ("https://static.tvmaze.com/m3u.png", b"#EXTM3U\nhttp://x/", "a playlist dressed as a picture")):
    replies[url] = data
    try:
        art.download_image(url, art.ART_HOSTS, TMP + "/art/x.png", art.THUMB_FILTER)
        check(False, why)
    except ValueError:
        check(not os.path.exists(TMP + "/art/x.png"), why)
# Movies: Wikipedia, and only if it's a film
page = lambda desc, src="https://upload.wikimedia.org/p.jpg": {"query": {"pages": [{"title": "T", "description": desc, "thumbnail": {"source": src}}]}}
replies.update({"Fight%2BClub": page("1999 American film"), "Fight+Club": page("1999 American film"), "It+Was+Written": {"query": {"pages": [{"missing": True}]}}, "Heat+%281995": page("1995 film by Michael Mann"), "Blink": page("Rapid closing of the eyelid")})
found = art._look_up_movie("Fight Club", None)
check(found and found["poster"].startswith("https://upload.wikimedia.org"), found)
check(art._look_up_movie("02 - It Was Written in Blood", None) is None, "a concert track finds nothing")
check(art._look_up_movie("Blink", None) is None, "an article that isn't about a film")
urllib.request.urlopen = real_urlopen

# --- The picture on the lower third
vf, _ = rs.overlay_filter(1000.0, True, promo="/x/p.ass", art=[("/x/a.png", 10.0, 14.5, True), ("/x/b.png", 14.5, 19.0, False)])
check(vf.count("movie=/x/") == 2, vf)
check("overlay=x='14-(1-min(1,(t-10.00)/0.45))*260':y=153-h:eval=frame:enable='between(t,10.00,14.50)'" in vf, "the first slides in with the bar")
check("overlay=x=14:y=153-h:eval=frame:enable='between(t,14.50,19.00)'" in vf, "the others just appear")
check(vf.index("ass=filename=/x/p.ass") < vf.index("[art0]overlay"), "over the lower third")
none, _ = rs.overlay_filter(1000.0, True, promo="/x/p.ass", art=[])
check("art0" not in none and "ass=filename=/x/p.ass" in none, "no art, same as before")

# --- Clips in the filter chain: timed to their panel, and they stop being decoded with it
vf, _ = rs.overlay_filter(1000.0, True, promo="/x/p.ass", art=[("/x/c.mp4", 8.0, 16.0, True), ("/x/b.png", 16.0, 24.0, False)])
check("movie=/x/c.mp4:loop=0,setpts=N/(15*TB)+8.00/TB,trim=end=16.00[art0]" in vf, vf)
check("movie=/x/b.png[art1]" in vf, "a poster stays a picture")

# --- Which picture
class Planned:
    name, src = "Seinfeld", "/mnt/tower/.retrotv/videos/shows/Seinfeld/S01E01.mkv"


art.CLIP_DIR = TMP + "/clips"
os.makedirs(art.CLIP_DIR)
when = datetime.datetime(2026, 11, 2, 20, 0)
rs.posters.thumb, real_thumb = (lambda name: "/x/poster.png"), rs.posters.thumb
check(rs.Station.picture_of(Planned, when) == "/x/poster.png", "no clip yet: the poster")
open(art.clip_file(Planned.src), "w").close()
real_allowed = rs.allowed
rs.allowed = lambda src, hour: True
check(rs.Station.picture_of(Planned, when) == art.clip_file(Planned.src), "the clip")
rs.allowed = lambda src, hour: hour >= 22
check(rs.Station.picture_of(Planned, when) == "/x/poster.png", "standards: a late show's clip waits, its poster doesn't")
check(rs.Station.picture_of(Planned, datetime.datetime(2026, 11, 2, 23, 0)) == art.clip_file(Planned.src), "and airs late at night")
check(rs.Station.picture_of(Planned, datetime.datetime(2026, 11, 3, 1, 0)) == art.clip_file(Planned.src), "after midnight counts as late too")
rs.allowed = real_allowed
music = rs.MusicBlock.__new__(rs.MusicBlock)
music.name = "After School Videos"
check(rs.Station.picture_of(music, when) == "/x/poster.png", "a music block has no clip")
rs.posters.thumb = lambda name: None
os.remove(art.clip_file(Planned.src))
check(rs.Station.picture_of(Planned, when) is None, "neither: no picture")
rs.posters.thumb = real_thumb

# --- Cutting: real ffmpeg on a made-up video, with a black stretch and a busy stretch
video = TMP + "/video.mp4"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=15:d=300", "-vf", "drawbox=enable='between(t,80,125)':color=black:t=fill", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", video], check=True)
dest = art.clip_file("video")
check(art.make_clip(video, 300.0, dest) is True and os.path.exists(dest), "a clip")
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate", "-show_entries", "format=duration", "-of", "json", dest], capture_output=True, text=True).stdout
probe = json.loads(probe)
check(len(probe["streams"]) == 1 and probe["streams"][0]["codec_type"] == "video", "video only")
check(probe["streams"][0]["width"] == 118 and probe["streams"][0]["height"] == 68, "the poster's size")
check(29 < float(probe["format"]["duration"]) <= 30.2, probe["format"]["duration"])
check(sorted(os.listdir(art.CLIP_DIR)) == [os.path.basename(dest)], "the other candidates are gone")
check(art.make_clip(video, 60.0, art.clip_file("short")) is False, "too short to bother")
black = TMP + "/black.mp4"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=15:d=200", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", black], check=True)
check(art.make_clip(black, 200.0, art.clip_file("black")) is False and not os.path.exists(art.clip_file("black")), "all black: no clip")
try:
    art.make_clip(TMP + "/missing.mp4", 300.0, art.clip_file("missing"))
    check(False, "a missing file should raise")
except subprocess.SubprocessError:
    check(not [n for n in os.listdir(art.CLIP_DIR) if "." + "0.mp4" in n], "nothing left behind")
# A clip's timing in a real filter chain: it shows during its panel only
frame = lambda t, name: subprocess.run(["mpv", video, "--no-config", "--ao=null", "--vo=image", "--vo-image-format=png", f"--vo-image-outdir={TMP}/{name}", f"--start={t}", "--frames=1", "--really-quiet",
                                         "--vf=scale=342:200,setsar=1," + rs.overlay_filter(300.0, False, art=[(dest, 20.0, 28.0, False)])[0]], timeout=60, check=True)
for t, name in ((15, "before"), (24, "during"), (35, "after")):
    frame(t, name)
region = lambda name: subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", f"{TMP}/{name}/00000001.png", "-vf", "crop=90:50:20:125,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
gap = lambda a, b: sum(abs(x - y) for x, y in zip(region(a), region(b))) / len(region(a))
check(gap("before", "during") > 3 * gap("before", "after") and gap("before", "during") > 15, f"the clip shows during its panel only: {gap('before', 'during')}, {gap('before', 'after')}")
# Old clips nobody wants go, and only those
old, kept, recent = art.clip_file("old"), art.clip_file("kept"), art.clip_file("recent")
for path in (old, kept, recent):
    open(path, "w").close()
for path in (old, kept):
    os.utime(path, (time.time() - 20 * 86400, time.time() - 20 * 86400))
art.prune_clips({kept})
check(not os.path.exists(old) and os.path.exists(kept) and os.path.exists(recent), "pruned")

# --- Which marathon, and what it's called
class FakeIndexer:
    def episodes(self, key):
        return [((1, 1), f"/mnt/tower/.retrotv/videos/shows/{key}/{key} - S01E01.mkv")]

    def category_of(self, src):
        return "videos"


station = rs.Station.__new__(rs.Station)
station.cache = types.SimpleNamespace(schedule={"groups": [{"id": "m", "items": ["marathon:"]}, {"id": "o", "items": ["shows/X"]}],
                                                "slots": [{"group": "m", "day": 4, "start": 16.5, "end": 22.0}, {"group": "o", "day": 3, "start": 9, "end": 10}]}, indexer=FakeIndexer())
rs.marathon_series, real_series = (lambda day, idx: "seinfeld"), rs.marathon_series
monday = datetime.datetime(2026, 11, 2, 12, 0)
start, name = station.next_marathon(monday)
check(start == datetime.datetime(2026, 11, 6, 16, 30), start)
friday = datetime.datetime(2026, 11, 6, 17, 0)
check(station.next_marathon(friday)[0] == datetime.datetime(2026, 11, 13, 16, 30), "once it's started, the next one")
check(station.next_marathon(datetime.datetime(2026, 11, 6, 16, 0))[0] == datetime.datetime(2026, 11, 6, 16, 30), "still ahead today")
station.cache.schedule = {"groups": [], "slots": []}
check(station.next_marathon(monday) is None, "no marathon on the schedule")
station.cache.schedule = {"groups": [{"id": "m", "items": ["marathon:"]}], "slots": [{"group": "m", "day": 4, "start": 16.5, "end": 22.0}]}
seen = {}
real_ad = events.marathon_ad
events.marathon_ad = lambda image, name, when, start_time, season=None: seen.update(when=when, at=start_time, image=image) or "card"
rs.posters.wide, real_wide = (lambda name: "/x/w.jpg"), rs.posters.wide
for now, when in ((monday, "This Friday"), (datetime.datetime(2026, 11, 5, 20), "Tomorrow"), (datetime.datetime(2026, 11, 6, 10), "Today"), (datetime.datetime(2026, 11, 6, 17), "Next Friday"),
                  (datetime.datetime(2026, 11, 7, 1), "Next Friday"), (datetime.datetime(2026, 11, 7, 10), "This Friday")):
    check(station.marathon_ad(now, None) == "card" and seen["when"] == when, (now, seen))
check(seen["at"] == cards.clock(datetime.datetime(2026, 11, 13, 16, 30)), seen)
rs.posters.wide = lambda name: None
check(station.marathon_ad(monday, None) is None, "no picture, no ad")
station.cache.schedule = {"groups": [], "slots": []}
rs.posters.wide = lambda name: "/x/w.jpg"
check(station.marathon_ad(monday, None) is None, "no marathon, no ad")
events.marathon_ad, rs.posters.wide, rs.marathon_series = real_ad, real_wide, real_series

# --- A real run, with previews
if len(sys.argv) > 1:
    out = "/tmp/art_preview"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out + "/cards")
    cards.CARDS_DIR = out + "/cards"
    art.ART_DIR, art.LOOKUPS_FILE = real_art, real_art + "/lookups.json"
    print("new pictures:", art.refresh([("Seinfeld", "videos", None, True), ("The Simpsons", "videos", None, True), ("Attack of the Show", "g4", None, False)]))
    for name, t in (("Seinfeld", 1.5), ("Seinfeld", 8), ("Seinfeld", 14), ("The Simpsons", 8)):
        card = events.marathon_ad(art.wide(name), name, "This Friday", "4:30 PM")
        subprocess.run(["nice", "-n", "15", "mpv", card.url, "--no-config", "--ao=null", "--vo=image", "--vo-image-format=png", f"--vo-image-outdir={out}/ad-{name[:4]}-{t}", f"--start={t}", "--frames=1", "--really-quiet"], timeout=90)
    panels = [("Next", "Seinfeld"), ("Later", "The Simpsons · 8:00 PM"), ("Tonight", "Attack of the Show · 10:00 PM")]
    script = events.promo(panels, 5.0, 4.5)
    posters_on = [(art.thumb(n), 5.0 + i * 4.5, 5.0 + (i + 1) * 4.5, i == 0) for i, n in enumerate(("Seinfeld", "The Simpsons", "Attack of the Show")) if art.thumb(n)]
    print("posters:", [os.path.basename(p[0]) for p in posters_on])
    vf, _ = rs.overlay_filter(1000.0, True, promo=script, art=posters_on)
    for t in (7, 11, 16):
        subprocess.run(["nice", "-n", "15", "mpv", sys.argv[1], "--no-config", "--ao=null", "--vo=image", "--vo-image-format=png", f"--vo-image-outdir={out}/promo-{t}", f"--start={t}", "--frames=1", "--really-quiet", "--vf=scale=342:200," + vf], timeout=60)
    print(subprocess.run(f"find {out} -name '*.png' | wc -l", shell=True, capture_output=True, text=True).stdout.strip(), "previews")

shutil.rmtree(TMP)
print(f"art checks passed: {checks}")
