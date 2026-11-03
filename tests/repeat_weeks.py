# Read-only: how many weeks each show lasts before it starts over, on the saved schedule and with the proposed theme nights.
import json, logging, math, os, sys, collections
logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
rs = harness.everything()
from retrotv import schedule as sch
from retrotv import meta
doc = sch.load()
idx = json.load(open(os.path.expanduser("~/retrotv_index.json")))["media_map"]
items = sch.item_files(idx, rs.MEDIA_ROOT)
usual = sch.lengths()
def episodes(i):
    if i not in items: return 0
    orders = set()
    n = 0
    for f in items[i][2]:
        _, _, _, o = meta.describe(f, items[i][1] if items[i][1] in ("g4", "movies") else "videos", rs.MEDIA_ROOT)
        if o:
            orders.add(tuple(o))
        else:
            n += 1
    return len(orders) + n
def slot_min(m):
    return m * 1.4 if m < 15 else math.ceil(m / 0.8 / 30) * 30
def table(name, hours, ids):
    ids = [i for i in ids if i in items]
    cycle = sum(slot_min(usual.get(i, 25)) for i in ids)
    per_week = hours * 60 / cycle
    rows = sorted((episodes(i) / per_week, items[i][0], episodes(i), usual.get(i)) for i in ids)
    print(f"{name}: {len(ids)} shows, {hours} h/wk, each show airs {per_week:.2f}x a week")
    print("   ", ", ".join(f"{n} {e}ep {w:.0f}wk" for w, n, e, m in rows))
print("--- as saved ---")
for g in doc["groups"]:
    hrs = sum(s["end"] - s["start"] for s in doc["slots"] if s["group"] == g["id"])
    if hrs and g["mode"] == "in_order" and not all(i.startswith(("music:", "marathon:")) for i in g["items"]):
        table(g["name"], hrs, [i for i in g["items"] if not i.startswith("music:")])
    if hrs and g["mode"] == "shuffle":
        n = sum(episodes(i) for i in g["items"])
        print(f"{g['name']}: shuffle, {n} files, {hrs} h/wk")
print("--- proposed theme nights ---")
s = "shows/"
table("Mon family", 3, [s + x for x in ("Home Improvement", "Boy Meets World", "Malcolm in the Middle", "The Simpsons", "Futurama")])
table("Tue competition", 3, [s + x for x in ("American Gladiators (2008)", "BattleBots", "MXC", "Extreme Dodgeball", "Celebrity Deathmatch")])
table("Wed MTV", 3, [s + x for x in ("Viva La Bam", "Punk'd", "Nitro Circus", "The Osbournes", "Tony Hawk's Gigantic Skatepark Tour", "The Soup")])
table("Thu must-see", 3, [s + x for x in ("Friends", "Seinfeld", "Scrubs", "The Office (US)", "Parks and Recreation", "Joey")])
table("Late Nighte Mon-Thu midnight", 8, [s + x for x in ("The Colbert Report", "The Daily Show", "Unsolved Mysteries", "Saturday Night Live", "Late Night with Conan O'Brien", "Conan", "Days of our Lives")])
late = next(g for g in doc["groups"] if g["name"].startswith("Late Night (")) 
table("Late Night 10:30-midnight Mon-Fri", 7.5, late["items"])
