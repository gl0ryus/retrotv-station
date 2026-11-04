"""
The long-running mpv that plays everything, fed over its JSON IPC socket (Player), and what it plays (Item).
"""
import datetime
import json
import os
import socket
import subprocess
import threading
import time

from retrotv.jsonio import write_json
from retrotv.config import (
    AUDIO_TAP_FILE, LIVE_AUDIO_DEVICE, LIVE_AUDIO_PORT, LIVE_AUDIO_RATE, LIVE_STREAM, MPV_SOCKET, PLAYER_CMD, PLAYER_ENV,
    PLAYER_MEMORY_CHECK_SECONDS, PLAYER_MEMORY_MB, REMOTE_STATE_FILE, logger)

TAP_CHECK_SECONDS = 60
DISPLAY_CHECK_SECONDS = 30

try:   # read once, at startup, so restarting mpv never waits for the NAS to wake
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_tap.py"), "rb") as _f:
        TAP_CODE = _f.read()
except OSError:
    TAP_CODE = None


def audio_args():
    """mpv's sound. With the watch page on: the panel's speakers through ALSA's tee, which pipes a copy to
    retrotv/audio_tap.py for the stream, in a fixed shape (16-bit stereo at LIVE_AUDIO_RATE). The tap runs from a copy
    on the SD card (AUDIO_TAP_FILE). If that can't be set up, the speakers only: the panel's sound never depends on it."""
    if not LIVE_STREAM:
        return []
    try:
        if TAP_CODE is None:
            raise OSError("audio_tap.py wasn't readable at startup")
        try:
            with open(AUDIO_TAP_FILE, "rb") as f:
                current = f.read()
        except FileNotFoundError:
            current = None
        if current != TAP_CODE:
            os.makedirs(os.path.dirname(AUDIO_TAP_FILE), exist_ok=True)
            with open(AUDIO_TAP_FILE + ".tmp", "wb") as f:
                f.write(TAP_CODE)
            os.replace(AUDIO_TAP_FILE + ".tmp", AUDIO_TAP_FILE)
    except OSError as e:
        logger.error(f"The watch page will have no sound: can't set up the audio tap ({e}).")
        return []
    tap = f"|exec /usr/bin/python3 -I -S {AUDIO_TAP_FILE} {LIVE_AUDIO_PORT} 4"   # 4: bytes per stereo 16-bit frame
    return [f"--audio-device=alsa/tee:SLAVE='{LIVE_AUDIO_DEVICE}',FILE='{tap}',FORMAT=raw",
            "--audio-format=s16", f"--audio-samplerate={LIVE_AUDIO_RATE}", "--audio-channels=stereo"]


class Item:
    """One thing for mpv to play. on_end(reason) runs when it finishes, fails or is dropped."""
    def __init__(self, url, duration, kind, label, options=None, on_end=None):
        self.url, self.duration, self.kind, self.label = url, duration, kind, label
        self.options = options or {}
        self.on_end = on_end
        self.started = None
        self.show = None        # the Show an act belongs to
        self.offset = 0.0       # where in the file the act starts
        self.overlay = None     # overlay_filter() arguments, so a glitch can be added at the last moment
        self.channel = None     # (number, name) for a make-believe channel's show
        self.ended = threading.Event()   # set for a live launch when it's over (Station._wait_out)
        self.refresh = None     # remakes a card with the latest data just before it's queued (Station._refresh)


class Player:
    """Feeds a long-running mpv over its JSON IPC socket, one item ahead so playback is gapless."""
    AHEAD = 2  # the playing item plus one queued

    def __init__(self, on_failed=None):
        self.cv = threading.Condition()
        self.send_lock = threading.Lock()
        self.on_failed = on_failed  # (url, label, error): called when an item fails, before its on_end
        self.pending = {}      # playlist entry id -> Item, in queue order
        self.ended = {}        # end-file events that beat their loadfile reply
        self.started = {}      # start-file events that beat their loadfile reply
        self.replies = {}
        self.request_id = 0
        self.epoch = 0         # bumped by flush(), so an item waiting to be queued is dropped
        self.proc = None
        self.sock = None
        self.alive = False
        self.memory_checked = 0.0
        self.memory_restarts = 0
        self.tapped = []           # audio_args() mpv was started with
        self.tap_checked = 0.0
        self.started_at = 0.0      # when this mpv started
        self.display_checked = 0.0
        # Called before each mpv start: the watch page's capture must let go of the screen first. Whoever opens the
        # display while nobody holds it becomes its owner, and mpv can't draw without being the owner
        self.before_start = None
        self.volume, self.muted = 100, False
        try:
            with open(REMOTE_STATE_FILE) as f:
                state = json.load(f)
            self.volume, self.muted = int(state.get("volume", 100)), bool(state.get("muted", False))
        except (OSError, ValueError):
            pass

    def _start(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        try:
            os.remove(MPV_SOCKET)
        except FileNotFoundError:
            pass
        if self.before_start:
            try:
                self.before_start()
            except Exception as e:
                logger.error(f"Before starting the player: {e}")
        self.tapped = audio_args()
        self.started_at = time.time()
        self.proc = subprocess.Popen(PLAYER_CMD + self.tapped + [f"--volume={self.volume}", f"--mute={'yes' if self.muted else 'no'}"],
                                     env={**os.environ, **PLAYER_ENV})
        for _ in range(100):
            try:
                sock = socket.socket(socket.AF_UNIX)
                sock.connect(MPV_SOCKET)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mpv did not open its IPC socket")
        self.sock = sock
        self.alive = True
        threading.Thread(target=self._reader, args=(sock,), daemon=True).start()
        logger.info("Player started.")

    def _reader(self, sock):
        finished = []
        for line in sock.makefile("rb"):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                if "request_id" in msg:
                    self.replies[msg["request_id"]] = msg
                event = msg.get("event")
                if event == "start-file":
                    item = self.pending.get(msg.get("playlist_entry_id"))
                    if item:
                        item.started = time.time()
                        if item.kind != "static":
                            logger.info(f"Playing: {item.label}")
                    else:
                        self.started[msg.get("playlist_entry_id")] = time.time()  # beat its loadfile reply
                elif event == "end-file":
                    eid = msg.get("playlist_entry_id")
                    item = self.pending.pop(eid, None)
                    if item:
                        finished.append((item, msg))
                    else:
                        self.ended[eid] = msg
                self.cv.notify_all()
            self._finish(finished)
        with self.cv:
            if sock is self.sock:
                self.alive = False
            self.cv.notify_all()

    def _finish(self, finished):
        while finished:
            item, msg = finished.pop(0)
            reason = msg.get("reason", "error")
            if reason == "error":
                error = msg.get("file_error", "unknown error")
                logger.error(f"Player failed on {item.label}: {error}")
                if self.on_failed:
                    try:
                        self.on_failed(item.url, item.label, error)
                    except Exception as e:
                        logger.error(f"Error after {item.label} failed: {e}")
            if item.on_end:
                try:
                    item.on_end(reason)
                except Exception as e:
                    logger.error(f"Error after {item.label}: {e}")

    def _command(self, command):
        with self.cv:
            self.request_id += 1
            rid = self.request_id
        with self.send_lock:
            self.sock.sendall((json.dumps({"command": command, "request_id": rid}) + "\n").encode())
        with self.cv:
            if not self.cv.wait_for(lambda: rid in self.replies or not self.alive, timeout=10):
                raise RuntimeError(f"mpv did not answer {command}")
            if rid not in self.replies:
                raise RuntimeError("mpv went away")
            return self.replies.pop(rid)

    def _restart(self):
        logger.error("Player stopped, restarting it.")
        with self.cv:
            dropped = [(item, {"reason": "error", "file_error": "player restarted"}) for item in self.pending.values()]
            self.pending.clear()
            self.ended.clear()
            self.started.clear()
        self._finish(dropped)
        time.sleep(2)
        self._start()

    def memory_mb(self):
        """mpv's resident memory in MB, or None when it isn't running."""
        if not self.proc or self.proc.poll() is not None:
            return None
        try:
            with open(f"/proc/{self.proc.pid}/status") as f:
                return next(int(line.split()[1]) // 1024 for line in f if line.startswith("VmRSS:"))
        except (OSError, StopIteration, ValueError):
            return None

    def _too_big(self):
        if not PLAYER_MEMORY_MB or time.time() - self.memory_checked < PLAYER_MEMORY_CHECK_SECONDS:
            return False
        self.memory_checked = time.time()
        used = self.memory_mb()
        return used is not None and used > PLAYER_MEMORY_MB

    def _restart_for_memory(self, epoch):
        """Lets what's playing finish, then starts mpv afresh. False if a flush() came first."""
        with self.cv:
            self.cv.wait_for(lambda: not self.pending or not self.alive or self.epoch != epoch)
            if self.epoch != epoch:
                return False
        logger.warning(f"Player using {self.memory_mb()} MB, restarting it to free the memory.")
        self.memory_restarts += 1
        self._start()
        return True

    def _check_tap(self):
        """If the audio tap has died, every sound mpv writes fails ("Input/output error") and the panel goes quiet or
        stutters, so mpv reopens its sound, which starts a new tap. Checked between items, once a minute at most."""
        if not self.tapped or time.time() - self.tap_checked < TAP_CHECK_SECONDS or not self.proc:
            return
        self.tap_checked = time.time()
        if self._command(["get_property", "current-ao"]).get("data") != "alsa":
            return   # no sound open at the moment (or not through ALSA, as in the tests), so no tap expected
        found = subprocess.run(["pgrep", "-P", str(self.proc.pid), "-f", "audio_tap"], capture_output=True)
        if found.returncode == 1:
            logger.error("The watch page's audio tap stopped, reopening the player's sound.")
            self._command(["ao-reload"])

    def showing(self):
        """True when mpv is up and drawing on the panel (it holds the display and its video output is set up)."""
        if not self.alive:
            return False
        try:
            return self._command(["get_property", "vo-configured"]).get("data") is True
        except (OSError, RuntimeError):
            return False

    def _check_display(self):
        """A black panel: mpv decoding video but with no video output, because something else held the display when
        it started. Checked between items, twice a few seconds apart; then mpv starts afresh."""
        if time.time() - self.display_checked < DISPLAY_CHECK_SECONDS or time.time() - self.started_at < 15:
            return
        self.display_checked = time.time()
        for attempt in range(2):
            if self._command(["get_property", "video-params"]).get("error") != "success":
                return   # no video being decoded at the moment
            if self.showing():
                return
            if attempt == 0:
                time.sleep(3)
        logger.error("The panel shows no picture (mpv has no video output), restarting the player.")
        self._restart()

    def play(self, item, epoch=None):
        """Queues an item; blocks while mpv already has one playing and one waiting. Returns False, dropping
        the item, if a flush() has happened since `epoch` (default: since the call)."""
        if not self.alive:
            self._start()
        with self.cv:
            if epoch is None:
                epoch = self.epoch
            self.cv.wait_for(lambda: len(self.pending) < self.AHEAD or not self.alive or self.epoch != epoch)
            dropped = self.epoch != epoch
        if dropped:
            self._finish([(item, {"reason": "stop"})])
            return False
        if item.kind not in ("act", "detour", "launch") and self._too_big() and not self._restart_for_memory(epoch):
            self._finish([(item, {"reason": "stop"})])
            return False
        if not self.alive or (self.proc and self.proc.poll() is not None):
            self._restart()
        try:
            self._check_tap()
            self._check_display()
        except (OSError, RuntimeError) as e:
            logger.error(f"Couldn't check the player's sound or picture: {e}")
        options = {k: str(v) for k, v in item.options.items()}
        try:
            reply = self._command({"name": "loadfile", "url": item.url, "flags": "append-play", "index": -1,
                                   "options": options})
        except (OSError, RuntimeError) as e:
            logger.error(f"Could not queue {item.label}: {e}")
            self._restart()
            self._finish([(item, {"reason": "error", "file_error": str(e)})])
            return True
        if reply.get("error") != "success":
            self._finish([(item, {"reason": "error", "file_error": reply.get("error")})])
            return True
        eid = reply["data"]["playlist_entry_id"]
        with self.cv:
            early = self.ended.pop(eid, None)
            started = self.started.pop(eid, None)
            if started and not early:
                item.started = started
                if item.kind != "static":
                    logger.info(f"Playing: {item.label}")
            if not early:
                self.pending[eid] = item
        if early:
            self._finish([(item, early)])
        return True

    def busy_until(self):
        """When everything queued so far should have finished playing."""
        now = time.time()
        with self.cv:
            left = 0.0
            for item in self.pending.values():
                if item.started:
                    left += max(0.0, item.duration - (now - item.started))
                else:
                    left += item.duration
        return datetime.datetime.fromtimestamp(now + left)

    def current(self):
        with self.cv:
            return next((item for item in self.pending.values() if item.started), None)

    # --- Remote control ---

    def skip(self):
        """Ends whatever is playing; the queued item takes over."""
        if self.alive:
            self._command(["playlist-next", "force"])

    def flush(self):
        """Drops everything queued and ends the current item (skipping a whole show)."""
        if not self.alive:
            return
        with self.cv:
            self.epoch += 1
            queued = [(eid, item) for eid, item in self.pending.items() if not item.started]
            for eid, _ in queued:
                del self.pending[eid]
            now = time.time()
            for item in self.pending.values():  # being cut short: done as far as the schedule is concerned
                if item.started:
                    item.duration = min(item.duration, now - item.started)
            self.cv.notify_all()
        self._command(["playlist-clear"])
        self._finish([(item, {"reason": "stop"}) for _, item in queued])
        self._command(["playlist-next", "force"])

    def end_item(self, item):
        """Ends one item that's playing now, leaving what's queued after it."""
        with self.cv:
            if not item.started or item not in self.pending.values():
                return
            item.duration = min(item.duration, time.time() - item.started)
            self.cv.notify_all()
        self._command(["playlist-next", "force"])

    def set_volume(self, volume=None, toggle_mute=False):
        if volume is not None:
            self.volume = max(0, min(130, int(volume)))
        if toggle_mute:
            self.muted = not self.muted
        if self.alive:
            self._command(["set_property", "volume", self.volume])
            self._command(["set_property", "mute", self.muted])
        try:
            write_json(REMOTE_STATE_FILE, {"volume": self.volume, "muted": self.muted})
        except OSError:
            pass

    def position(self):
        """Where the current file is (mpv's time-pos), or None."""
        if not self.alive:
            return None
        reply = self._command(["get_property", "time-pos"])
        return reply.get("data") if reply.get("error") == "success" else None

    def add_filter(self, chain, spec):
        """Adds a labelled filter ("@label:...") to the vf or af chain while a file plays. mpv restores
        per-file options when the file ends, so on an item that set vf-append the filter ends with it."""
        self._command([chain, "remove", spec.split(":", 1)[0]])
        reply = self._command([chain, "add", spec])
        if reply.get("error") != "success":
            raise RuntimeError(f"mpv refused the {chain} filter: {reply.get('error')}")

    def remove_filter(self, chain, label):
        if self.alive:
            self._command([chain, "remove", label])
