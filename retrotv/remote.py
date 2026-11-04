"""
The phone remote: a TV-guide web page served on the home network (http://retrotv.local/). It lists the
main channel and the make-believe ones; picking a show changes the channel. Skip, volume and "play this
next" too. Also the watch page (/watch), which plays the station live (retrotv/live.py). Standard library only.
"""
import json
import logging
import os
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

from retrotv import live, schedule
from retrotv.cards import FONTS_DIR

if TYPE_CHECKING:   # only for the type checker: retrotv.station imports this module
    from retrotv.station import Station

logger = logging.getLogger("RetroTV")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PAGE_FILE = os.path.join(WEB_DIR, "remote.html")
SCHEDULE_PAGE_FILE = os.path.join(WEB_DIR, "schedule.html")
HEALTH_PAGE_FILE = os.path.join(WEB_DIR, "health.html")
WATCH_PAGE_FILE = os.path.join(WEB_DIR, "watch.html")
COMMON_CSS_FILE = os.path.join(WEB_DIR, "common.css")   # the pages' fonts and palette
FRAGMENT_WAIT = 30   # seconds without a new fragment before a viewer's stream is given up as stalled
MAX_BODY = 65536
MAX_SCHEDULE_BODY = 1024 * 1024
FONT_FILES = ("ArchivoBlack-Regular.ttf", "BarlowCondensed-Bold.ttf", "BarlowCondensed-ExtraBold.ttf",
              "BarlowCondensed-SemiBold.ttf")


class Handler(BaseHTTPRequestHandler):
    station: "Station" = None   # type: ignore[assignment]  # set by start()
    page = b""
    schedule_page = b""
    health_page = b""
    watch_page = b""
    common_css = b""
    fonts = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type="application/json", cache=False):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit=MAX_BODY):
        try:
            length = min(int(self.headers.get("Content-Length", 0)), limit)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, self.page, "text/html; charset=utf-8")
            elif url.path == "/schedule":
                self._send(200, self.schedule_page, "text/html; charset=utf-8")
            elif url.path == "/health":
                self._send(200, self.health_page, "text/html; charset=utf-8")
            elif url.path == "/watch" and self.station.live:
                self._send(200, self.watch_page, "text/html; charset=utf-8")
            elif url.path == "/live.mp4" and self.station.live:
                self._live(self.station.live)
            elif url.path == "/common.css":
                self._send(200, self.common_css, "text/css; charset=utf-8")
            elif url.path == "/api/health":
                self._send(200, self.station.remote_health())
            elif url.path == "/api/library":
                self._send(200, self.station.remote_library())
            elif url.path == "/api/schedule":
                self._send(200, schedule.load())
            elif url.path == "/api/state":
                self._send(200, self.station.remote_state())
            elif url.path == "/api/guide":
                self._send(200, self.station.remote_guide())
            elif url.path == "/api/search":
                query = urllib.parse.parse_qs(url.query).get("q", [""])[0][:80]
                self._send(200, self.station.remote_search(query))
            elif url.path.startswith("/fonts/") and os.path.basename(url.path) in self.fonts:
                self._send(200, self.fonts[os.path.basename(url.path)], "font/ttf", cache=True)
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            logger.exception("Remote request failed")
            self._send(500, {"error": str(e)})

    def _live(self, stream):
        """The station live, as one long MP4 response that lasts as long as the viewer watches (or the stream runs)."""
        try:
            viewer = stream.join()
        except live.Busy:
            self._send(503, {"error": "Too many people watching already."})
            return
        except live.NotShowing:
            self._send(503, {"error": "The panel isn't showing a picture right now (the player is starting)."})
            return
        except OSError as e:
            logger.error(f"Watch page: can't start the live stream: {e}")
            self._send(503, {"error": f"The live stream can't start: {e}"})
            return
        try:
            header = stream.wait_header()
            if header is None:
                self._send(503, {"error": "The live stream didn't start."})
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(header)
            while True:
                fragment = viewer.queue.get(timeout=FRAGMENT_WAIT)
                if fragment is None:
                    break
                self.wfile.write(fragment)
        except (OSError, queue.Empty):
            pass   # the viewer left (closed the tab), or the stream stalled
        finally:
            stream.leave(viewer)

    def _save_schedule(self):
        try:
            self._send(200, {"version": self.station.remote_save_schedule(self._body(MAX_SCHEDULE_BODY))})
        except schedule.Conflict as e:
            self._send(409, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            logger.exception("Saving the schedule failed")
            self._send(500, {"error": str(e)})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/schedule":
            self._save_schedule()
            return
        body = self._body()
        try:
            if url.path == "/api/skip":
                message = self.station.remote_skip(bool(body.get("show")))
            elif url.path == "/api/volume":
                message = self.station.remote_volume(int(body.get("change", 0)) or None, bool(body.get("mute")))
            elif url.path == "/api/play":
                message = self.station.remote_play_next(str(body.get("id", "")))
            elif url.path == "/api/tune":
                message = self.station.remote_tune(int(body.get("channel", 0)), str(body.get("id", "")))
            elif url.path == "/api/main":
                message = self.station.remote_main()
            elif url.path == "/api/bad_file/clear":
                message = self.station.remote_clear_bad_file(str(body.get("id", "")))
            elif url.path == "/api/test":
                # In the query string as well as a body: Windows shells mangle the quotes in a JSON body typed on the
                # command line. curl -X POST "http://retrotv.local/api/test?what=scores"
                ask = {**{k: str(v) for k, v in body.items()}, **{k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}}
                message = self.station.remote_test(ask)
            elif url.path == "/api/rescan":
                message = self.station.remote_rescan()
            else:
                self._send(404, {"error": "not found"})
                return
            self._send(200, {"message": message})
        except Exception as e:
            logger.exception("Remote command failed")
            self._send(500, {"error": str(e)})


def start(station, port):
    """Serves the remote in a background thread (port 80 needs CAP_NET_BIND_SERVICE; falls back to 8080)."""
    Handler.station = station
    try:
        with open(PAGE_FILE, "rb") as f:
            Handler.page = f.read()  # read once, so the NAS can sleep
    except OSError as e:
        logger.error(f"Remote page missing: {e}")
        Handler.page = b"<p>Remote page missing.</p>"
    try:
        with open(SCHEDULE_PAGE_FILE, "rb") as f:
            Handler.schedule_page = f.read()
    except OSError as e:
        logger.error(f"Schedule page missing: {e}")
        Handler.schedule_page = b"<p>Schedule page missing.</p>"
    try:
        with open(HEALTH_PAGE_FILE, "rb") as f:
            Handler.health_page = f.read()
    except OSError as e:
        logger.error(f"Health page missing: {e}")
        Handler.health_page = b"<p>Health page missing.</p>"
    try:
        with open(WATCH_PAGE_FILE, "rb") as f:
            Handler.watch_page = f.read()
    except OSError as e:
        logger.error(f"Watch page missing: {e}")
        Handler.watch_page = b"<p>Watch page missing.</p>"
    try:
        with open(COMMON_CSS_FILE, "rb") as f:
            Handler.common_css = f.read()
    except OSError as e:
        logger.error(f"Page styles missing: {e}")
    for name in FONT_FILES:
        try:
            with open(os.path.join(FONTS_DIR, name), "rb") as f:
                Handler.fonts[name] = f.read()
        except OSError:
            pass
    for p in (port, 8080):
        try:
            server = ThreadingHTTPServer(("", p), Handler)
        except OSError as e:
            logger.warning(f"Remote can't use port {p}: {e}")
            continue
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"Remote is up on port {p}.")
        return server
    return None
