"""
What aired and when, for the play log page (/log): the player adds a line as each item starts, and the page reads
the last few days back. One JSON object per line in PLAY_LOG_FILE, on the SD card, trimmed to PLAY_LOG_DAYS once a day.
"""
import json
import os
import threading
import time

from retrotv.config import PLAY_LOG_DAYS, PLAY_LOG_FILE, logger

_lock = threading.Lock()
_trimmed = 0.0   # when the file was last trimmed (time.time())


def record(kind, label, when=None):
    """Adds one play. Never raises: playback mustn't wait on the log."""
    entry = {"t": round(when or time.time(), 1), "kind": kind, "label": label}
    try:
        with _lock:
            with open(PLAY_LOG_FILE, "a+b") as f:
                if f.seek(0, os.SEEK_END):
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":   # a line cut short by a power cut: start a fresh one
                        f.write(b"\n")
                f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            _trim_daily()
    except OSError as e:
        logger.error(f"Could not write the play log: {e}")


def recent(hours):
    """The plays that started in the last `hours`, oldest first."""
    since = time.time() - hours * 3600
    plays = []
    with _lock:
        try:
            with open(PLAY_LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue   # a line cut short by a power cut
        if isinstance(entry, dict) and entry.get("t", 0) >= since:
            plays.append(entry)
    return plays


def _trim_daily():
    """Caller holds _lock. Drops lines older than PLAY_LOG_DAYS, at the first play after a start and then daily."""
    global _trimmed
    if time.time() - _trimmed < 86400:
        return
    _trimmed = time.time()
    keep = time.time() - PLAY_LOG_DAYS * 86400
    with open(PLAY_LOG_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    kept = []
    for line in lines:
        try:
            if json.loads(line).get("t", 0) >= keep:
                kept.append(line)
        except (ValueError, AttributeError):
            pass
    if len(kept) < len(lines):
        with open(PLAY_LOG_FILE + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(PLAY_LOG_FILE + ".tmp", PLAY_LOG_FILE)
