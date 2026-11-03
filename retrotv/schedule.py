"""
The weekly schedule built on the remote's /schedule page: groups of shows, and which group airs when. Saved on the
SD card, so it needs neither the NAS nor a restart to change. Standard library only, no station imports.

Blocks are typed labels for the general picture ("Mon-Fri 9am-12pm Kids Mornings"). Only slots decide what airs.

An item is something a group can hold: a library folder ("shows/Friends", "g4/X-Play", "movies/Wrestling"), "movies/"
for the feature films, a "tape:" name for the recordings in shows/.Tapes whose file names start with it ("tape:" alone
is all of them), or a special block the station builds itself ("music:...", "marathon:", "archive:cartoons").
"""
import collections
import json
import logging
import os
import random
import re
import statistics
import subprocess
import threading
from retrotv.jsonio import write_json
from retrotv.paths import DATA_DIR, SCHEDULE_FILE

logger = logging.getLogger("RetroTV")

LENGTHS_FILE = os.path.join(DATA_DIR, "folder_lengths.json")   # {item id: minutes}, from a sample of files
FIRST_HOUR, LAST_HOUR = 9, 26    # as the station counts them: 26 is 2 AM, when it signs off
MODES = ("in_order", "shuffle")  # a group's shows take turns, next episode each | whatever has waited longest
# A group's "overflow": whether a show too long for its slot runs past it (the default), or gets cut off at the end of it
COLORS = ("g4", "kids", "sitcom", "drama", "late", "tape", "movie", "music")
MAX_GROUPS, MAX_ITEMS, MAX_SLOTS, MAX_BLOCKS = 500, 200, 400, 100   # far more than a week holds
NOT_FEATURES = ("Wrestling", "Concerts")           # movies/ folders that are their own items
TAPE_FAMILY_FILES = 3                              # file names sharing a start, to be listed as a tape item
PROBE_SAMPLE = 3
SPECIALS = (
    ("archive:cartoons", "Classic Cartoons", "Old cartoon shorts, downloaded from archive.org"),
    ("music:After School Videos", "After School Videos", "{music} music videos, opening with the Weekly Countdown"),
    ("music:Saturday Night Videos", "Saturday Night Videos", "{music} music videos, opening with the Weekly Countdown"),
    ("music:Music Videos", "Music Videos", "{music} music videos, opening with the Weekly Countdown"),
    ("marathon:", "Friday Night Marathon", "One show back to back; the show changes each week"),
)
TOPS = (("videos", "shows", "shows"), ("g4", "g4", "g4"), ("movies", "movies", "movies"))  # category, folder, kind

_lock = threading.Lock()


class Conflict(Exception):
    """The schedule changed since the page loaded it."""


def _tape_family(base):
    return re.split(r" - | \d", base, maxsplit=1)[0].strip()


def item_files(media_map, media_root):
    """{item id: (name, kind, [files])} for everything in the index, with no NAS access."""
    out, tapes = {}, []
    features = []
    for category, top, kind in TOPS:
        root = f"{media_root}/{top}/"
        for src in media_map.get(category, []):
            if not src.startswith(root):
                continue   # a stale path from an older layout
            parts = src[len(root):].split("/")
            if top == "shows" and parts[0] == ".Tapes":
                tapes.append(src)
            elif top == "movies" and parts[0] not in NOT_FEATURES:
                features.append(src)
            elif len(parts) > 1:
                name = "G4 Tapes" if parts[0] == ".Tapes" else parts[0]
                out.setdefault(f"{top}/{parts[0]}", (name, kind, []))[2].append(src)
    if features:
        out["movies/"] = ("All Movies", "movies", features)
    if tapes:
        out["tape:"] = ("All Tapes", "tapes", tapes)
        families = collections.Counter(_tape_family(os.path.basename(src)) for src in tapes)
        for family, count in families.items():
            if family and count >= TAPE_FAMILY_FILES:
                out["tape:" + family] = (family, "tapes", _tape_matches(family, tapes))
    return out


def _tape_matches(family, tapes):
    return [src for src in tapes if os.path.basename(src).startswith(family)]


def library(media_map, media_root, rating, referenced=()):
    """What can go in a group, for the page: [{id, name, kind, files, ratings, minutes}]. `referenced`: item ids the
    saved schedule uses, listed even when too small to show up otherwise."""
    groups = item_files(media_map, media_root)
    tapes = groups.get("tape:", (None, None, []))[2]
    for item in referenced:
        if item.startswith("tape:") and item not in groups:
            found = _tape_matches(item[5:], tapes)
            if found:
                groups[item] = (item[5:], "tapes", found)
    lengths = _read_json(LENGTHS_FILE, {})
    out = []
    for item, (name, kind, files) in groups.items():
        ratings = collections.Counter(rating(src) or "any" for src in files)
        out.append({"id": item, "name": name, "kind": kind, "files": len(files), "ratings": dict(ratings),
                    "minutes": lengths.get(item)})
    music = f"{len(media_map.get('music', [])):,}"
    out.extend({"id": item, "name": name, "kind": "specials", "files": 0, "ratings": {}, "minutes": None,
                "about": about.format(music=music)} for item, name, about in SPECIALS)
    return sorted(out, key=lambda x: (x["kind"], x["name"].lower()))


def probe_lengths(media_map, media_root):
    """Fills LENGTHS_FILE for items that aren't in it yet. Slow and it reads the NAS: run it in the background."""
    lengths = _read_json(LENGTHS_FILE, {})
    for item, (_, _, files) in item_files(media_map, media_root).items():
        if item in lengths:
            continue
        durations = [d for d in map(_probe, random.sample(files, min(PROBE_SAMPLE, len(files)))) if d]
        if durations:
            lengths[item] = round(statistics.median(durations) / 60)
            _write_json(LENGTHS_FILE, lengths)
    logger.info(f"Schedule page: {len(lengths)} show lengths known.")


def _probe(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def seed():
    """The first draft, from retrotv.grid, until a schedule has been saved."""
    from retrotv import grid
    groups = [{"id": key, "name": spec[0], "items": list(spec[1]), "mode": "shuffle" if len(spec) > 2 else "in_order",
               "color": grid.COLOR_OF.get(key, "sitcom")} for key, spec in grid.PROGRAMS.items()]
    slots = []
    for day, entries in grid.WEEK.items():
        for i, (start, key) in enumerate(entries):
            end = entries[i + 1][0] if i + 1 < len(entries) else LAST_HOUR
            slots.append({"day": day, "start": start, "end": end, "group": key})
    return {"version": 0, "groups": groups, "slots": slots, "blocks": []}


def load():
    with _lock:
        saved = _read_json(SCHEDULE_FILE, None)
    return {"blocks": [], **saved} if saved else seed()


def slot_at(doc, day, hour):
    """The slot on the air on a broadcast day at a broadcast hour, or None."""
    return next((s for s in doc["slots"] if s["day"] == day and s["start"] <= hour < s["end"]), None)


def group_of(doc, group_id):
    return next((g for g in doc["groups"] if g["id"] == group_id), None)


def lengths():
    """{item id: minutes} as sampled by probe_lengths."""
    return _read_json(LENGTHS_FILE, {})


def referenced_items(doc):
    return {item for group in doc["groups"] for item in group["items"]}


def save(body, known):
    """Validates and stores a schedule from the page; returns its new version. `known`: the item ids a group may use
    (those in the library, plus any it already held). Raises ValueError or Conflict."""
    with _lock:
        current = _read_json(SCHEDULE_FILE, None) or seed()
        if body.get("version") != current["version"]:
            raise Conflict(f"Saved version is {current['version']}, this page has {body.get('version')}")
        doc = validate(body, set(known) | referenced_items(current))
        doc["version"] = current["version"] + 1
        _write_json(SCHEDULE_FILE, doc)
    logger.info(f"Schedule saved: version {doc['version']}, {len(doc['groups'])} groups, {len(doc['slots'])} slots.")
    return doc["version"]


def validate(body, known):
    """A clean copy holding only the expected fields. Raises ValueError naming what's wrong."""
    groups, slots = body.get("groups"), body.get("slots")
    if not isinstance(groups, list) or not isinstance(slots, list):
        raise ValueError("groups and slots must be lists")
    if len(groups) > MAX_GROUPS or len(slots) > MAX_SLOTS:
        raise ValueError("too many groups or slots")
    clean_groups, ids = [], set()
    for g in groups:
        if not isinstance(g, dict):
            raise ValueError("bad group")
        gid, name, items = g.get("id"), g.get("name"), g.get("items")
        if not isinstance(gid, str) or not 0 < len(gid) <= 40 or gid in ids:
            raise ValueError(f"bad or repeated group id: {gid!r}")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 60:
            raise ValueError(f"group {gid}: a name needs 1 to 60 characters")
        if not isinstance(items, list) or len(items) > MAX_ITEMS or not all(isinstance(i, str) for i in items):
            raise ValueError(f"group {name}: bad item list")
        unknown = [i for i in items if i not in known]
        if unknown:
            raise ValueError(f"group {name}: not in the library: {unknown[0]}")
        if g.get("mode") not in MODES or g.get("color") not in COLORS:
            raise ValueError(f"group {name}: bad mode or color")
        if not isinstance(g.get("overflow", True), bool):
            raise ValueError(f"group {name}: overflow is on or off")
        ids.add(gid)
        clean_groups.append({"id": gid, "name": name.strip(), "items": list(dict.fromkeys(items)),
                             "mode": g["mode"], "color": g["color"], "overflow": g.get("overflow", True)})
    clean_slots, by_day = [], collections.defaultdict(list)
    for s in slots:
        if not isinstance(s, dict):
            raise ValueError("bad slot")
        day, group = _day(s.get("day")), s.get("group")
        start, end = _span(s.get("start"), s.get("end"))
        if group not in ids:
            raise ValueError(f"slot uses an unknown group: {group!r}")
        by_day[day].append((start, end))
        clean_slots.append({"day": day, "start": start, "end": end, "group": group})
    for day, spans in by_day.items():
        spans.sort()
        for (_, a_end), (b_start, _) in zip(spans, spans[1:]):
            if b_start < a_end:
                raise ValueError(f"two slots overlap on day {day} at {b_start}")
    clean_slots.sort(key=lambda s: (s["day"], s["start"]))
    return {"groups": clean_groups, "slots": clean_slots, "blocks": _clean_blocks(body.get("blocks", []))}


def _day(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 6:
        raise ValueError(f"bad day: {value!r}")
    return value


def _span(start, end):
    """(start, end) as floats, checked: on the half hour, in order, in the broadcast day."""
    for t in (start, end):
        if not isinstance(t, (int, float)) or isinstance(t, bool) or t * 2 != int(t * 2):
            raise ValueError(f"times must be on the half hour: {t!r}")
    if not FIRST_HOUR <= start < end <= LAST_HOUR:
        raise ValueError(f"outside the broadcast day: {start} to {end}")
    return float(start), float(end)


def _clean_blocks(blocks):
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise ValueError("bad or too many blocks")
    clean, by_day, ids = [], collections.defaultdict(list), set()
    for b in blocks:
        if not isinstance(b, dict):
            raise ValueError("bad block")
        bid, name, days = b.get("id"), b.get("name"), b.get("days")
        if not isinstance(bid, str) or not 0 < len(bid) <= 40 or bid in ids:
            raise ValueError(f"bad or repeated block id: {bid!r}")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 60:
            raise ValueError("a block's name needs 1 to 60 characters")
        if not isinstance(days, list) or not days:
            raise ValueError(f"block {name}: no days")
        days = [_day(d) for d in days]
        if len(set(days)) != len(days):
            raise ValueError(f"block {name}: a repeated day")
        start, end = _span(b.get("start"), b.get("end"))
        ids.add(bid)
        for day in days:
            by_day[day].append((start, end))
        clean.append({"id": bid, "name": name.strip(), "days": sorted(days), "start": start, "end": end})
    for day, spans in by_day.items():
        spans.sort()
        for (_, a_end), (b_start, _) in zip(spans, spans[1:]):
            if b_start < a_end:
                raise ValueError(f"two blocks overlap on day {day} at {b_start}")
    return clean


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    write_json(path, data, sync=True)   # the schedule is the one thing a power cut mustn't lose
