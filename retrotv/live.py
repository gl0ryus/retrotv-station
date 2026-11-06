"""
The watch page (http://retrotv.local/watch): the station as its own screen shows it, CRT look and all, with its sound.
MediaMTX (MEDIAMTX_FILE, github.com/bluenviron/mediamtx) serves it as HLS in 1 s segments, which the page plays with
hls.js (MediaMTX's own copy) about 5 s behind: over plain HTTP with a buffer to spare, so Wi-Fi hiccups never reach
the picture, and with picture and sound in one timeline. (Low-latency HLS, under a second behind, stuttered in Firefox.) MediaMTX runs retrotv/capture.py for the first viewer and stops it after
the last one leaves, so the Pi only pays for the capture (about a third of a CPU core) while someone watches.

This keeps MediaMTX running. Only its HLS server faces the network, and only to read the station's stream: publishing
and its API are for this machine alone, and its other servers (RTMP, SRT, WebRTC and the rest) are off.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from retrotv.config import (
    CAPTURE_PID_FILE, MEDIAMTX_API_PORT, MEDIAMTX_CONFIG_FILE, MEDIAMTX_FILE, MEDIAMTX_HLS_PORT, MEDIAMTX_RTSP_PORT, logger)

STREAM = "gtv"
CAPTURE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture.py")
RESTART_SECONDS = 10   # after MediaMTX stops, it starts again this much later
STOP_SECONDS = 5       # how long the capture gets to let go of the screen before it's killed
LOGGED_LINES = 50      # of MediaMTX's complaints per run, so one repeating every second can't flood the log
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCAL = ["127.0.0.1", "::1"]


def mediamtx_config():
    """MediaMTX's settings, as JSON (which YAML reads). The capture starts with the first viewer (an HLS player counts
    from its first request until 20 s after its last) and stops 10 s after the last, with SIGINT."""
    capture = f"{sys.executable} -I {CAPTURE_FILE} rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    return json.dumps({
        "logLevel": "warn",
        "authInternalUsers": [
            {"user": "any", "pass": "", "ips": LOCAL,
             "permissions": [{"action": "publish"}, {"action": "read"}, {"action": "api"}]},
            {"user": "any", "pass": "", "ips": [], "permissions": [{"action": "read", "path": STREAM}]},
        ],
        "api": True, "apiAddress": f"127.0.0.1:{MEDIAMTX_API_PORT}",
        "rtsp": True, "rtspAddress": f"127.0.0.1:{MEDIAMTX_RTSP_PORT}", "rtspTransports": ["tcp"],
        # MPEG-TS: picture and sound in the same segments, on one clock. fmp4 serves the sound as its own playlist,
        # cut at other moments, and Firefox's hls.js paired them a second or more apart (2026-11-05)
        "hls": True, "hlsAddress": f":{MEDIAMTX_HLS_PORT}", "hlsAllowOrigins": ["*"], "hlsVariant": "mpegts",
        "hlsMuxerCloseAfter": "20s",
        "rtmp": False, "srt": False, "webrtc": False, "moq": False, "playback": False, "metrics": False, "pprof": False,
        "paths": {STREAM: {"runOnDemand": capture, "runOnDemandRestart": True, "runOnDemandStartTimeout": "20s",
                           "runOnDemandCloseAfter": "10s", "overridePublisher": False}},
    }, indent=2) + "\n"


def capture_pid():
    """The running capture's pid, from CAPTURE_PID_FILE, or None (no file, or that process isn't the capture)."""
    try:
        with open(CAPTURE_PID_FILE) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            command = f.read()
    except (OSError, ValueError):
        return None
    return pid if b"capture.py" in command else None


def _children(pid):
    """The pids whose parent is pid (the capture's ffmpeg)."""
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                with open(f"/proc/{name}/stat") as f:
                    stat = f.read()
            except OSError:
                continue
            if int(stat.rsplit(")", 1)[1].split()[1]) == pid:   # field 4, after the (name), is the parent
                found.append(int(name))
    return found


def _gone(pids):
    return all(not os.path.exists(f"/proc/{p}") for p in pids)


class Live:
    def __init__(self, ready=None, run=True):
        self.ready = ready    # True when it's safe to read the screen: the player is drawing on it (Player.showing)
        self.proc = None      # MediaMTX
        self.error = None     # its last complaint, for the health page
        if run:
            threading.Thread(target=self._keep_running, daemon=True).start()

    def is_ready(self):
        """True when capture.py may read the screen (it asks through the remote's /api/live/ready)."""
        return self.ready is None or bool(self.ready())

    def stop(self):
        """Ends the capture now, before the player starts: whoever opens the display while nobody holds it becomes its
        owner, and mpv couldn't draw. MediaMTX starts it again for anyone still watching once the panel shows."""
        pid = capture_pid()
        if pid is None:
            return
        logger.info("Watch page: the player is starting, stopping the capture until it's back.")
        family = [pid] + _children(pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + STOP_SECONDS
        while not _gone(family) and time.time() < deadline:
            time.sleep(0.1)
        for p in family:
            if not _gone([p]):
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def viewers(self):
        """How many readers MediaMTX has for the stream (an HLS server counts as one, however many browsers it
        serves), 0 when it can't say."""
        try:
            with OPENER.open(f"http://127.0.0.1:{MEDIAMTX_API_PORT}/v3/paths/get/{STREAM}", timeout=2) as r:
                info = json.load(r)
        except (OSError, ValueError):
            return 0
        return len(info.get("readers") or []) if isinstance(info, dict) else 0

    def status(self):
        """For the health page: whether the capture runs and since when, the readers, and the last complaint."""
        pid = capture_pid()
        since = None
        if pid is not None:
            try:
                since = os.path.getmtime(CAPTURE_PID_FILE)
            except OSError:
                since = time.time()
        error = self.error
        if self.proc is None or self.proc.poll() is not None:
            error = error or "MediaMTX isn't running"
        return {"running": pid is not None, "since": since, "viewers": self.viewers(), "error": error}

    def _keep_running(self):
        while True:
            try:
                os.makedirs(os.path.dirname(MEDIAMTX_CONFIG_FILE), exist_ok=True)
                with open(MEDIAMTX_CONFIG_FILE, "w") as f:
                    f.write(mediamtx_config())
                proc = subprocess.Popen([MEDIAMTX_FILE, MEDIAMTX_CONFIG_FILE], stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                self.error = f"MediaMTX can't start: {e}"
                logger.error(f"Watch page: {self.error}")
                time.sleep(60)
                continue
            self.proc = proc
            logger.info("Watch page: MediaMTX is up.")
            logged = 0
            assert proc.stdout is not None
            for line in proc.stdout:
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                self.error = text[-300:]
                if logged < LOGGED_LINES:
                    logger.warning(f"MediaMTX: {text}")
                    logged += 1
            proc.wait()
            logger.error(f"Watch page: MediaMTX stopped (exit {proc.returncode}), starting it again in {RESTART_SECONDS} s.")
            time.sleep(RESTART_SECONDS)
