# Which items on the saved schedule can never air where they are, because of standards. Usage on the Pi: python3 -I never_airs.py
import json, urllib.request
doc = json.load(urllib.request.urlopen("http://127.0.0.1/api/schedule"))
lib = json.load(urllib.request.urlopen("http://127.0.0.1/api/library"))
items = {i["id"]: i for i in lib["items"]}
kids_end, late = lib["rules"]["kids_end"], lib["rules"]["late_start"]
for g in doc["groups"]:
    slots = [s for s in doc["slots"] if s["group"] == g["id"]]
    never = []
    for i in g["items"]:
        it = items.get(i)
        if not it or not it["files"]:
            continue
        r = it["ratings"]
        if r.get("late", 0) == it["files"] and not any(s["end"] > late for s in slots):
            never.append(it["name"] + " (late-night)")
        if r.get("kids", 0) == it["files"] and not any(s["start"] < kids_end for s in slots):
            never.append(it["name"] + " (kids)")
    if never:
        print(g["name"], ":", never)
missing = sorted(i for g in doc["groups"] for i in g["items"] if i not in items)
print("not in the library:", missing)
