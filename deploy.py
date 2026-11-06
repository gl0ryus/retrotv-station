"""
Puts the working copy of the station (the NAS folder, /mnt/tower/.retrotv/station) on the air. It copies the folder to
a new release on the Pi's SD card, checks that the station and the writing job both import from the copy, points
~/retrotv_station/current at it and restarts the station in the break after the show on now. The services run from
current (systemd/), so the station starts without the NAS, and edits to the working copy reach the air only through
here. Run on the Pi:

    python3 /mnt/tower/.retrotv/station/deploy.py               # restart in the break after the show on now
    python3 /mnt/tower/.retrotv/station/deploy.py --now         # restart right away
    python3 /mnt/tower/.retrotv/station/deploy.py --no-restart  # copy and switch only; the next start runs it
    python3 /mnt/tower/.retrotv/station/deploy.py --rollback    # back to the release before current

The wait for a break can take most of an hour, so from the PC run it detached and read the log afterwards:

    ssh gl0ryus@retrotv.local 'setsid nohup python3 /mnt/tower/.retrotv/station/deploy.py > /tmp/deploy.log 2>&1 &'
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

from retrotv.paths import HOME_DIR, NAS_ROOT

SOURCE = os.path.join(NAS_ROOT, "station")
RUN_DIR = os.path.join(HOME_DIR, "retrotv_station")
RELEASES = os.path.join(RUN_DIR, "releases")     # one folder per deploy, named by when it was made
CURRENT = os.path.join(RUN_DIR, "current")       # a link to the release on the air
KEEP = 3                                         # releases kept, the one on the air included
SKIP = (".git", "__pycache__", "*.pyc", "backups", "legacy", "music_src")   # nothing the air needs
NEWS_PYTHON = os.path.join(HOME_DIR, "retrotv_venv", "bin", "python")      # the writing job's venv
PLAYS = os.path.join(HOME_DIR, "retrotv_plays.jsonl")                     # retrotv.playlog's record
SHOW_KINDS = ("act", "music")
BREAK_WAIT_SECONDS = 2 * 3600


def say(text):
    print(time.strftime("%H:%M:%S ") + text, flush=True)


def releases():
    """The release folders, oldest first."""
    if not os.path.isdir(RELEASES):
        return []
    return sorted(os.path.join(RELEASES, name) for name in os.listdir(RELEASES))


def on_air():
    return os.path.realpath(CURRENT) if os.path.islink(CURRENT) else None


def copy():
    dest = os.path.join(RELEASES, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(RELEASES, exist_ok=True)
    shutil.copytree(SOURCE, dest, ignore=shutil.ignore_patterns(*SKIP), symlinks=True)
    return dest


def check(release):
    """Both programs import from the copy, so a typo or a missing file stops the deploy here instead of on the air."""
    for python, module in ((sys.executable, "retrotv.station"), (NEWS_PYTHON, "retrotv.news")):
        if not os.path.exists(python):
            say(f"no {python}: {module} not checked")
            continue
        run = subprocess.run([python, "-I", "-c", f"import sys; sys.path.insert(0, {release!r}); import {module}"],
                             capture_output=True, text=True, timeout=180, cwd=release)
        if run.returncode:
            shutil.rmtree(release, ignore_errors=True)
            raise SystemExit(f"{module} doesn't import from the copy, nothing changed:\n{run.stderr.strip()[-2000:]}")


def in_break_after_show():
    """True once a show's last part has played and something else has started (or nothing has played for an
    hour): the latest show part in the play log is its final act, "(act N/N)", or a show in one piece."""
    try:
        with open(PLAYS) as f:
            lines = f.readlines()[-40:]
    except OSError:
        return True
    plays = []
    for line in lines:
        try:
            plays.append(json.loads(line))
        except ValueError:
            pass
    if not plays or time.time() - plays[-1].get("t", 0) > 3600:
        return True
    shows = [i for i, p in enumerate(plays) if p.get("kind") in SHOW_KINDS]
    if not shows:
        return True
    last = shows[-1]
    if last == len(plays) - 1:          # a show part is on now
        return False
    m = re.search(r"\(act (\d+)/(\d+)\)$", plays[last].get("label", ""))
    return not m or m.group(1) == m.group(2)


def wait_for_break():
    say("waiting for the break after the show on now")
    deadline = time.time() + BREAK_WAIT_SECONDS
    while not in_break_after_show():
        if time.time() > deadline:
            say("no break in two hours: restarting anyway")
            return
        time.sleep(5)


def news_running():
    return subprocess.run(["systemctl", "is-active", "--quiet", "retrotv-news.service"]).returncode == 0


def switch(release):
    """Points current at release in one step (a link renamed over the old one)."""
    link = CURRENT + ".new"
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(release, link)
    os.replace(link, CURRENT)
    say(f"on the air from now on: {release}")


def restart():
    subprocess.run(["sudo", "-n", "systemctl", "restart", "retrotv"], check=True)
    time.sleep(20)
    state = subprocess.run(["systemctl", "is-active", "retrotv"], capture_output=True, text=True).stdout.strip()
    say(f"the station is {state}")


def prune():
    keep = set(releases()[-KEEP:]) | {on_air()}
    for path in releases():
        if path not in keep:
            shutil.rmtree(path, ignore_errors=True)
            say(f"removed the old release {os.path.basename(path)}")


def main():
    parser = argparse.ArgumentParser(description="Puts the working copy of the station on the air (see the top of deploy.py).")
    parser.add_argument("--now", action="store_true", help="restart right away, not in the next break")
    parser.add_argument("--no-restart", action="store_true", help="copy and switch only")
    parser.add_argument("--rollback", action="store_true", help="go back to the release before current")
    args = parser.parse_args()
    if args.rollback:
        live = on_air()
        older = [r for r in releases() if live and r < live]
        if not older:
            raise SystemExit("no earlier release to go back to")
        target = older[-1]
    else:
        say(f"copying {SOURCE}")
        target = copy()
        say(f"checking {os.path.basename(target)}")
        check(target)
    if not args.no_restart:
        if not args.now:
            wait_for_break()
        while news_running():      # the writing job runs from current too: let it finish on the code it started with
            time.sleep(5)
    switch(target)
    if not args.no_restart:
        restart()
    prune()


if __name__ == "__main__":
    main()
