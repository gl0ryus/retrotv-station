"""
The watch page's picture and sound (http://retrotv.local/watch): the station as its own screen shows it, CRT look
and all, as one fragmented MP4 that every viewer shares. ffmpeg starts with the first viewer and stops a little after
the last one leaves, so the Pi only pays for it (about a third of a CPU core) while someone watches.

The picture is the panel's frame buffer (kmsgrab, which needs the copy of ffmpeg that may read the screen, see
README). The sound is the player's, copied by ALSA to a UDP port on the Pi (retrotv/audio_tap.py), which this relays
into ffmpeg, filling in silence whenever the panel is quiet: ffmpeg writes nothing at all until it has sound, so the
sound must never stop. Both are stamped with the wall clock as ffmpeg reads them, and measured from the moment it
starts, which lines them up (the system clock and the panel's sound clock drift apart by 2 parts in a million); the
sound is then moved LIVE_AUDIO_SHIFT_MS, as measured with a flash-and-beep clip.

Every fragment starts on a keyframe (one a second), so a viewer can join at any fragment: it gets the stream's
header, then the fragments from then on.
"""
import queue
import socket
import struct
import subprocess
import threading
import time

from retrotv.config import (
    LIVE_AUDIO_PORT, LIVE_AUDIO_RATE, LIVE_AUDIO_SHIFT_MS, LIVE_CAPTURE_FFMPEG, LIVE_DRM_DEVICE, LIVE_IDLE_SECONDS,
    LIVE_MAX_VIEWERS, LIVE_VIDEO_KBPS, logger)

BACKLOG = 6          # fragments (seconds) a viewer may fall behind before it's let go
HEADER_SECONDS = 15  # how long a new viewer waits for a starting stream
FRAME_BYTES = 4      # 16-bit stereo
QUIET_SECONDS = 0.1  # this long without sound from the tap and the relay starts filling in silence


class Busy(Exception):
    """Too many viewers already."""


class NotShowing(Exception):
    """The player isn't drawing on the panel (starting up, say). Reading the screen then would make the capture the
    display's owner, and mpv couldn't draw at all."""


def encoder_cmd(start):
    """ffmpeg's command line. start: the wall-clock time the stream is measured from."""
    audio_start = start - LIVE_AUDIO_SHIFT_MS / 1000   # a later start makes the sound earlier
    return [LIVE_CAPTURE_FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-copyts",
            "-use_wallclock_as_timestamps", "1", "-f", "kmsgrab", "-device", LIVE_DRM_DEVICE, "-framerate", "30", "-i", "-",
            # The sound, from _relay on stdin. Raw PCM needs no probing
            "-use_wallclock_as_timestamps", "1", "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(LIVE_AUDIO_RATE), "-ac", "2", "-i", "pipe:0",
            # Picture and sound filtered apart, each in its own thread: in one graph, waiting on the sound held up the
            # capture, which got 24 frames a second with stalls of up to 0.2 s instead of 30 (2026-11-04)
            "-map", "0:v", "-map", "1:a",
            "-vf", f"setpts=PTS-{start:.6f}/TB,hwdownload,format=bgra,format=yuv420p",
            "-af", f"asetpts=PTS-{audio_start:.6f}/TB,aresample=async=1:first_pts=0",
            "-c:v", "h264_v4l2m2m", "-b:v", f"{LIVE_VIDEO_KBPS}k", "-g", "30", "-c:a", "aac", "-b:a", "128k",
            # delay_moov: the hardware encoder only says how to decode its picture (SPS/PPS) with its first frame,
            # so the header waits for that
            "-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+delay_moov+default_base_moof", "pipe:1"]


def boxes(stream):
    """Yields (type, bytes) for each top-level MP4 box read from a file object, until it ends."""
    while True:
        head = stream.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        if size == 1:   # a 64-bit size follows
            extra = stream.read(8)
            if len(extra) < 8:
                return
            size = struct.unpack(">Q", extra)[0]
            head += extra
        if size < len(head):   # 0 means "to the end of the file", which a live stream never has
            return
        body = stream.read(size - len(head))
        if len(body) < size - len(head):
            return
        yield kind.decode("latin-1"), head + body


class Viewer:
    def __init__(self):
        self.queue = queue.Queue(BACKLOG)   # fragments, then None when the stream is over for this viewer


class Live:
    def __init__(self, cmd=encoder_cmd, ready=None):
        self.cmd = cmd
        self.ready = ready    # True when it's safe to read the screen: the player is drawing on it (Player.showing)
        self.lock = threading.Condition()
        self.proc = None
        self.header = None    # ftyp + moov: what a viewer gets first
        self.viewers = set()
        self.started = None   # when the encoder started (time.time())
        self.left = None      # when the last viewer left (time.monotonic())
        self.error = None     # the encoder's last complaint

    def join(self):
        """A new viewer, starting the encoder if it isn't running. Raises Busy, NotShowing, or OSError if ffmpeg
        can't start."""
        with self.lock:
            if len(self.viewers) >= LIVE_MAX_VIEWERS:
                raise Busy
            if self.proc is None:
                if self.ready and not self.ready():
                    raise NotShowing
                try:
                    self._start()
                except OSError as e:
                    self.error = f"ffmpeg can't start: {e}"   # for the health page
                    raise
            viewer = Viewer()
            self.viewers.add(viewer)
            self.left = None
            return viewer

    def leave(self, viewer):
        with self.lock:
            self.viewers.discard(viewer)
            if not self.viewers:
                self.left = time.monotonic()

    def wait_header(self):
        """The stream's header once the encoder has written it, or None if it doesn't in time."""
        with self.lock:
            self.lock.wait_for(lambda: self.header is not None or self.proc is None, timeout=HEADER_SECONDS)
            return self.header

    def stop(self):
        """Ends the stream now, before the player starts: see NotShowing. Viewers' pages tune in again by themselves."""
        with self.lock:
            proc = self.proc
        if proc is None:
            return
        logger.info("Watch page: the player is starting, stopping the live stream until it's back.")
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)

    def status(self):
        with self.lock:
            return {"running": self.proc is not None, "viewers": len(self.viewers), "since": self.started,
                    "error": self.error}

    def _start(self):
        # Caller holds the lock
        self.header, self.error = None, None
        self.proc = proc = subprocess.Popen(self.cmd(time.time()), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, start_new_session=True)
        self.started = time.time()
        logger.info("Watch page: someone tuned in, the live stream is starting.")
        threading.Thread(target=self._relay, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        threading.Thread(target=self._complaints, args=(proc,), daemon=True).start()
        threading.Thread(target=self._idle_stop, args=(proc,), daemon=True).start()

    def _relay(self, proc):
        """The tap's sound into ffmpeg, as it comes; silence in real time whenever none has come for a moment."""
        stdin = proc.stdin
        if stdin is None:
            return
        sock = None
        for _ in range(40):   # the last stream's relay may still hold the port for a moment
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(("127.0.0.1", LIVE_AUDIO_PORT))
            except OSError:
                s.close()
                time.sleep(0.05)
                continue
            s.settimeout(0.05)
            sock = s
            break
        if sock is None:
            logger.error(f"Watch page: UDP port {LIVE_AUDIO_PORT} is taken, the stream will be silent.")
        heard = 0.0                     # when the tap last sent anything (time.monotonic())
        covered = time.monotonic()      # the stream's sound so far reaches up to here
        try:
            while proc.poll() is None:
                data = b""
                if sock:
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        pass
                else:
                    time.sleep(0.05)
                now = time.monotonic()
                if data:
                    stdin.write(data)
                    heard = covered = now
                elif now - heard > QUIET_SECONDS and now > covered:
                    frames = int((now - covered) * LIVE_AUDIO_RATE)
                    stdin.write(bytes(frames * FRAME_BYTES))
                    covered += frames / LIVE_AUDIO_RATE
                stdin.flush()
        except (OSError, ValueError):
            pass   # ffmpeg is gone
        finally:
            if sock:
                sock.close()

    def _read(self, proc):
        head, fragment = [], []
        for kind, data in boxes(proc.stdout):
            if self.header is None:
                head.append(data)
                if kind == "moov":
                    with self.lock:
                        self.header = b"".join(head)
                        self.lock.notify_all()
                continue
            fragment.append(data)
            if kind == "mdat":
                self._send(b"".join(fragment))
                fragment = []
        proc.wait()
        with self.lock:
            if self.proc is proc:
                self.proc, self.header = None, None
                for viewer in self.viewers:
                    self._end(viewer)
                self.lock.notify_all()
        logger.info(f"Watch page: the live stream stopped (ffmpeg exit {proc.returncode}).")

    def _send(self, fragment):
        with self.lock:
            for viewer in list(self.viewers):
                try:
                    viewer.queue.put_nowait(fragment)
                except queue.Full:   # it stopped reading (a stalled tab): let it go rather than buffer for it
                    self.viewers.discard(viewer)
                    self._end(viewer)

    @staticmethod
    def _end(viewer):
        while True:   # make room for the end marker
            try:
                viewer.queue.get_nowait()
            except queue.Empty:
                break
        viewer.queue.put_nowait(None)

    def _complaints(self, proc):
        logged = 0
        for line in proc.stderr:
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            if logged < 20:   # a complaint repeated every frame would flood the journal
                logger.warning(f"Watch page stream: {text}")
                logged += 1
            with self.lock:
                self.error = text

    def _idle_stop(self, proc):
        while True:
            time.sleep(1)
            with self.lock:
                if self.proc is not proc:
                    return
                if self.viewers or self.left is None or time.monotonic() - self.left < LIVE_IDLE_SECONDS:
                    continue
            logger.info("Watch page: nobody's watching, stopping the live stream.")
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return
