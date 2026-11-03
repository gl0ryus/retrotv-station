"""
Live launches cut into the broadcast: which launches are worth it, when they go, and the stream to show. Launch data
comes from Launch Library 2, whose free tier allows 15 requests an hour (Launch Watch shares that). Streams are resolved
by yt-dlp, which lives in ~/retrotv_venv with a JavaScript runtime (node).
"""
import datetime
import json
import logging
import os
import subprocess
import time
import urllib.error

from retrotv import health
from retrotv.jsonio import get_json, write_json
from retrotv.paths import DATA_DIR

logger = logging.getLogger("RetroTV")

API = "https://ll.thespacedevs.com/2.3.0/launches"
STATE_FILE = os.path.join(DATA_DIR, "launch_live.json")
YT_DLP = os.path.expanduser("~/retrotv_venv/bin/yt-dlp")
STREAM_FORMAT = "bv*[height<=480]+ba/b[height<=480]/b"   # the picture is crunched to 200 lines anyway
FLORIDA = (12, 27)         # Launch Library's Cape Canaveral and Kennedy
LIST_MINUTES = 30          # how often the list of launches is fetched
NEAR_MINUTES = 20          # inside this long before a launch, it's looked up on its own...
NEAR_SECONDS = 180         # ...this often
LIVE_POLL_SECONDS = 45     # while it's on the air, until SCRUB_WATCH_SECONDS in, looking for a hold or a scrub
SCRUB_WATCH_SECONDS = 180
RESOLVE_SECONDS = 300      # the stream is looked for this long before liftoff, every RESOLVE_RETRY_SECONDS
RESOLVE_RETRY_SECONDS = 30
TOO_LATE_SECONDS = 90      # this long after liftoff, it isn't worth cutting in any more
MAX_TRIES = 3              # how many times one launch can cut in, scrubs included
SLIP_SECONDS = 120         # a liftoff time moved later by more than this means it hasn't gone


def liftoff(launch):
    return datetime.datetime.fromisoformat(launch["net"].replace("Z", "+00:00")).timestamp()


def worth_it(launch):
    """Florida launches, crewed flights and the first flight of a new rocket."""
    location = ((launch.get("pad") or {}).get("location") or {}).get("id")
    rocket = launch.get("rocket") or {}
    stages = rocket.get("spacecraft_stage") or []
    crewed = any(stage.get("launch_crew") for stage in stages if isinstance(stage, dict))
    mission = ((launch.get("mission") or {}).get("type") or "").lower()
    first = (rocket.get("configuration") or {}).get("total_launch_count") == 0
    return location in FLORIDA or crewed or "human" in mission or first


def status_of(launch):
    return (launch.get("status") or {}).get("abbrev", "")


def resolve(url):
    """[video URL] or [video URL, audio URL] for a web page's stream, or None if it isn't live or can't be read."""
    try:
        done = subprocess.run([YT_DLP, "--js-runtimes", "node", "--no-warnings", "-q", "--no-playlist", "-f", STREAM_FORMAT, "-g", url],
                              capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Launch stream: {e}")
        health.record("streams", False, e)
        return None
    urls = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if done.returncode or not urls:
        logger.info(f"Launch stream not ready at {url}: {done.stderr.strip()[-120:]}")
        return None
    health.record("streams", True)
    return urls[:2]


def _get(url):
    try:
        return get_json(url, timeout=20)
    except (OSError, ValueError) as e:   # includes the free tier's 429: the next check tries again
        logger.info(f"Launch data: {e}")
        return None


class Launches:
    def __init__(self):
        self.launches = {}     # id -> launch
        self.fetched = self.looked_up = self.live_polled = self.resolve_tried = 0.0
        self.streams = {}      # id -> what resolve() found
        try:
            with open(STATE_FILE) as f:
                self.tries = dict(json.load(f))   # id -> times it has cut in
        except (OSError, ValueError, TypeError):
            self.tries = {}

    def _save(self):
        write_json(STATE_FILE, self.tries)

    def _lookup(self, launch):
        fresh = _get(f"{API}/{launch['id']}/?mode=detailed")
        if fresh and fresh.get("id"):
            self.launches[launch["id"]] = fresh
            return fresh
        return launch

    def due(self):
        """(launch, stream URLs) when it's time to cut in, lead_seconds before liftoff; otherwise None."""
        now = time.time()
        if now - self.fetched > LIST_MINUTES * 60:
            self.fetched = now
            data = _get(f"{API}/upcoming/?mode=detailed&limit=25")
            if data and data.get("results"):
                self.launches = {x["id"]: x for x in data["results"]}
        candidates = [x for x in self.launches.values() if worth_it(x) and self.tries.get(x["id"], 0) < MAX_TRIES and x.get("net")]
        for launch in sorted(candidates, key=liftoff):
            lead = liftoff(launch) - now
            if lead < -TOO_LATE_SECONDS:
                continue
            if lead > NEAR_MINUTES * 60:
                break     # later ones can wait too
            if now - self.looked_up > NEAR_SECONDS:
                self.looked_up = now
                launch = self._lookup(launch)
                lead = liftoff(launch) - now
            if status_of(launch) != "Go" or lead > RESOLVE_SECONDS or lead < -TOO_LATE_SECONDS:
                continue
            return launch, lead
        return None

    def stream_for(self, launch):
        """The stream's URLs, trying each webcast the launch lists, official ones first. None until one is live."""
        have = self.streams.get(launch["id"])
        now = time.time()
        if have or now - self.resolve_tried < RESOLVE_RETRY_SECONDS:
            return have
        self.resolve_tried = now
        links = sorted(launch.get("vid_urls") or [], key=lambda v: -(v.get("priority") or 0))
        for link in links:
            found = resolve(link.get("url", ""))
            if found:
                self.streams[launch["id"]] = found
                logger.info(f"Launch stream for {launch.get('name')}: {link.get('url')}")
                return found
        if liftoff(launch) < now:   # before liftoff, a stream that isn't up yet is normal
            health.record("streams", False, f"No readable stream at liftoff: {launch.get('name')}")
        return None

    def started(self, launch):
        self.tries[launch["id"]] = self.tries.get(launch["id"], 0) + 1
        self.streams.pop(launch["id"], None)
        self._save()

    def still_going(self, launch):
        """False once a launch on the air has held or scrubbed, looked at every so often for the first few minutes."""
        now = time.time()
        if now - self.live_polled < LIVE_POLL_SECONDS:
            return True
        self.live_polled = now
        fresh = self._lookup(launch)
        return status_of(fresh) != "Hold" and liftoff(fresh) - liftoff(launch) <= SLIP_SECONDS
