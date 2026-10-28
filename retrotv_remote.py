"""
The phone remote: a TV-guide web page served on the home network (http://retrotv.local/). It lists the
main channel and the make-believe ones; picking a show changes the channel. Skip, volume and "play this
next" too. Standard library only.
"""
import json
import logging
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from retrotv_cards import FONTS_DIR

logger = logging.getLogger("RetroTV")

PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retrotv_remote.html")
FONT_FILES = ("ArchivoBlack-Regular.ttf", "BarlowCondensed-Bold.ttf", "BarlowCondensed-ExtraBold.ttf",
              "BarlowCondensed-SemiBold.ttf")


class Handler(BaseHTTPRequestHandler):
    station = None
    page = b""
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

    def _body(self):
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 65536)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, self.page, "text/html; charset=utf-8")
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

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
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
            elif url.path == "/api/alert_test":
                # ?kind= too: Windows shells mangle the quotes in a JSON body typed on the command line
                kind = urllib.parse.parse_qs(url.query).get("kind", [""])[0] or str(body.get("kind", ""))
                message = self.station.remote_alert_test(kind.strip().lower())
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
