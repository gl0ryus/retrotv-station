# The cut-in decision without the network: Launches.due/stream_for/still_going with fake Launch Library data.
import datetime, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
import retrotv_launches as l

l.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
stamp = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def launch(lead, status="Go", where=12, vids=("official",), id_="a"):
    return {"id": id_, "name": "Falcon 9 | Test", "net": stamp(time.time() + lead), "status": {"abbrev": status},
            "pad": {"location": {"id": where, "name": "Cape Canaveral SFS, FL, USA"}}, "rocket": {"configuration": {"total_launch_count": 500}},
            "mission": {"type": "Communications"}, "vid_urls": [{"priority": 5, "url": "unofficial"}, {"priority": 10, "url": "official"}] if vids else []}


served = {}
asked = []
l._get = lambda url: served.get("one") if "/upcoming/" not in url else {"results": served.get("list", [])}
l.resolve = lambda url: asked.append(url) or (["video", "audio"] if url in served.get("live", ()) else None)


def fresh(*rows):
    served.update(list=list(rows), one=rows[0] if rows else None, live=("official", "unofficial"))
    asked.clear()
    return l.Launches()


x = fresh(launch(3600))
assert x.due() is None, "too far off"
x = fresh(launch(600))
assert x.due() is None, "worth a look, but not yet time to find the stream"
x = fresh(launch(200))
found = x.due()
assert found and 190 < found[1] <= 200, found
assert x.stream_for(found[0]) == ["video", "audio"] and asked == ["official"], asked
x = fresh(launch(200, status="Hold"))
assert x.due() is None, "on hold"
x = fresh(launch(200, status="TBD"))
assert x.due() is None, "no firm time"
x = fresh(launch(-120))
assert x.due() is None, "too late"
x = fresh(launch(-30))
assert x.due(), "just gone: still worth it"
x = fresh(launch(200, where=11))
assert x.due() is None, "not Florida, crewed or new"
x = fresh(launch(200))
found = x.due()
for _ in range(l.MAX_TRIES):
    x.started(found[0])
assert x.due() is None, "tried enough"
assert l.Launches().tries["a"] == l.MAX_TRIES, "remembered after a restart"
os.remove(l.STATE_FILE)
x = fresh(launch(200, vids=()))
found = x.due()
assert found and x.stream_for(found[0]) is None, "no webcast listed"
# The official stream isn't up yet, the restream is
x = fresh(launch(200))
served["live"] = ("unofficial",)
found = x.due()
assert x.stream_for(found[0]) == ["video", "audio"] and asked == ["official", "unofficial"], asked
# A hold or a slip on the air
x = fresh(launch(30))
on_air = launch(30)
served["one"] = launch(30, status="Hold")
assert not x.still_going(on_air), "held"
x = fresh(launch(30))
served["one"] = launch(30 + 600)
assert not x.still_going(on_air), "slipped"
x = fresh(launch(30))
served["one"] = launch(30)
assert x.still_going(on_air), "still going"
print("launch timing checks passed")
