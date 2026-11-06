"""
The watch page's picture and sound, run by MediaMTX for as long as anyone watches (retrotv/live.py starts MediaMTX;
MediaMTX starts this for the first viewer and stops it with SIGINT a little after the last one leaves):

    python3 -I retrotv/capture.py rtsp://127.0.0.1:8554/gtv    (where MediaMTX takes it)

The picture is the panel's frame buffer (kmsgrab, which needs the copy of ffmpeg that may read the screen, see
README); the sound is the player's, copied by ALSA's tee to a UDP port (retrotv/audio_tap.py). They're encoded as
H.264 Constrained Baseline and AAC, which every browser's HLS player takes, and published to MediaMTX over RTSP. The
encoder repeats its SPS/PPS with every frame, which MediaMTX picks up for viewers who join later (the stream's
description can't carry them: this encoder only knows them after its first frame).

Reading the screen while mpv doesn't hold it would make this the display's owner, and mpv couldn't draw at all. So
it waits for the station to say the panel is showing (/api/live/ready), and leaves CAPTURE_PID_FILE so the station
can stop it before mpv restarts (Live.stop).

Timing: the picture is stamped with the wall clock as each frame is read, at 1/90000 s so no two frames share a
stamp. The tap's sound arrives 75-100 ms before it's heard, and more at the start of an item, but ALSA says exactly
how far ahead: the frames it has queued for the speakers (its "delay", read from procfs as each packet comes). So
each packet goes in where it's heard, in one unbroken run of samples on the picture's clock: after silence if it's
late, trimmed if it's early, only past ALIGN_TOLERANCE so steady play isn't nudged packet by packet (Aligner).
"""
import ctypes
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # run as a script: the station folder

from retrotv.config import (  # noqa: E402
    CAPTURE_PID_FILE, LIVE_ALSA_STATUS, LIVE_AUDIO_PORT, LIVE_AUDIO_RATE, LIVE_AUDIO_SHIFT_MS, LIVE_CAPTURE_FFMPEG,
    LIVE_DRM_DEVICE, LIVE_VIDEO_KBPS, REMOTE_PORT, logger)

FRAME_BYTES = 4           # 16-bit stereo
ALIGN_TOLERANCE = 0.005   # seconds a packet may sit off its place before it's moved
QUIET_SECONDS = 0.15      # this long without sound from the tap, and silence fills the time as it passes
TYPICAL_QUEUE = 0.087     # seconds ALSA usually has queued (measured 2026-11-05), when it can't say
READY_WAIT = 10           # seconds to wait for the panel to be showing; MediaMTX tries again after that
H264_CONSTRAINED_BASELINE = 578   # avctx profile for h264_v4l2m2m: 66 | FF_PROFILE_H264_CONSTRAINED


def encoder_cmd(start, output):
    """ffmpeg's command line. start: the wall-clock time both picture and sound are measured from; output: where to
    publish (MediaMTX's RTSP)."""
    return [LIVE_CAPTURE_FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-copyts",
            "-use_wallclock_as_timestamps", "1", "-f", "kmsgrab", "-device", LIVE_DRM_DEVICE, "-framerate", "30", "-i", "-",
            # The sound, from relay() on stdin: one unbroken run of samples starting at `start`, so counting them is
            # its clock. Raw PCM needs no probing; a deep queue, so ffmpeg takes it as it comes
            "-thread_queue_size", "64", "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(LIVE_AUDIO_RATE), "-ac", "2", "-i", "pipe:0",
            # Picture and sound filtered apart, each in its own thread: in one graph, waiting on the sound held up the
            # capture (24 frames a second with stalls, 2026-11-04)
            "-map", "0:v", "-map", "1:a",
            "-vf", f"setpts=PTS-{start:.6f}/TB,hwdownload,format=bgra,format=yuv420p",
            "-fps_mode", "passthrough", "-enc_time_base:v", "1/90000",
            "-c:v", "h264_v4l2m2m", "-profile:v", str(H264_CONSTRAINED_BASELINE), "-b:v", f"{LIVE_VIDEO_KBPS}k", "-g", "30",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "rtsp", "-rtsp_transport", "tcp", output]


class Aligner:
    """Where each packet of the tap's sound goes in the stream. Frame n of the stream is heard at start + shift + n/rate
    (shift: LIVE_AUDIO_SHIFT_MS). `written` is how many frames have gone in so far."""

    def __init__(self, start, rate=LIVE_AUDIO_RATE, shift_ms=LIVE_AUDIO_SHIFT_MS):
        self.start, self.rate, self.shift = start, rate, shift_ms / 1000
        self.tolerance = round(ALIGN_TOLERANCE * rate)
        self.written = 0

    def place(self, frames, arrived, queued):
        """(frames of silence before it, frames to cut from its head) for a packet of `frames` that arrived at
        `arrived` (wall clock) with `queued` frames, itself included, waiting for the speakers."""
        heard = arrived + (queued - frames) / self.rate   # when its first sample reaches the speakers
        gap = round((heard + self.shift - self.start) * self.rate) - self.written
        silence = gap if gap > self.tolerance else 0
        cut = min(-gap, frames) if gap < -self.tolerance else 0
        self.written += silence + frames - cut
        return silence, cut

    def fill(self, now):
        """Frames of silence that bring the stream up to `now`, while the speakers have nothing to play."""
        n = int((now + self.shift - self.start) * self.rate) - self.written
        if n <= 0:
            return 0
        self.written += n
        return n


def queued_frames(status=LIVE_ALSA_STATUS):
    """Frames ALSA has waiting for the panel's speakers, or None when it isn't playing."""
    try:
        with open(status) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^delay\s*:\s*(-?\d+)", text, re.M)
    return int(m.group(1)) if m and "RUNNING" in text else None


def relay(stdin, aligner, stop, port=LIVE_AUDIO_PORT, status=LIVE_ALSA_STATUS):
    """The tap's sound into ffmpeg, each packet where it's heard; silence as time passes whenever none comes. Packets
    are timed as they arrive, on a thread of their own: writing to ffmpeg blocks while it isn't taking sound, and
    timing packets only once that let up put the sound 0.2-0.4 s late (flash-and-beep, 2026-11-05)."""
    sock = None
    for _ in range(40):   # the last capture may still hold the port for a moment
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            s.close()
            time.sleep(0.05)
            continue
        s.settimeout(0.05)
        sock = s
        break
    if sock is None:
        logger.error(f"Watch page: UDP port {port} is taken, the stream will be silent.")
    packets = queue.Queue()   # (frames, when it arrived, frames ALSA had queued then, the sound)
    last = [0.0]              # when the tap last sent anything

    def receive(sock):
        queued = round(TYPICAL_QUEUE * aligner.rate)
        while not stop.is_set():
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            arrived = time.time()
            q = queued_frames(status)
            if q is not None and q >= 0:
                queued = q
            frames = len(data) // FRAME_BYTES
            last[0] = arrived
            packets.put((frames, arrived, queued, data[:frames * FRAME_BYTES]))
    if sock:
        threading.Thread(target=receive, args=(sock,), daemon=True).start()
    try:
        while not stop.is_set():
            try:
                frames, arrived, queued, data = packets.get(timeout=0.05)
            except queue.Empty:
                now = time.time()
                if now - last[0] <= QUIET_SECONDS:
                    continue
                out = bytes(aligner.fill(now) * FRAME_BYTES)
            else:
                silence, cut = aligner.place(frames, arrived, queued)
                out = bytes(silence * FRAME_BYTES) + data[cut * FRAME_BYTES:]
            if out:
                stdin.write(out)
                stdin.flush()
    except (OSError, ValueError):
        pass   # ffmpeg is gone
    finally:
        if sock:
            sock.close()


def station_ready():
    """True when the station says mpv is drawing on the panel, so the screen can be read."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port in dict.fromkeys((REMOTE_PORT, 8080)):   # the remote falls back to 8080 when it can't have its port
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/live/ready", timeout=2) as r:
                return r.status == 200
        except urllib.error.HTTPError:
            return False
        except OSError:
            continue
    return False


def _die_with_parent():
    """ffmpeg gets SIGTERM if this process dies, even by SIGKILL, so it never holds the screen on its own."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # The pid file first, so a player restart from here on finds this and stops it (see Live.stop)
    with open(CAPTURE_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    proc = None
    try:
        deadline = time.time() + READY_WAIT
        while not station_ready():
            if stop.wait(0.5) or time.time() > deadline:
                logger.info("Watch page: the panel isn't showing a picture yet, not capturing.")
                return 1
        start = time.time()
        proc = subprocess.Popen(encoder_cmd(start, argv[1]), stdin=subprocess.PIPE, preexec_fn=_die_with_parent)
        assert proc.stdin is not None
        logger.info("Watch page: someone tuned in, capturing the panel.")
        feeding = threading.Event()
        thread = threading.Thread(target=relay, args=(proc.stdin, Aligner(start), feeding), daemon=True)
        thread.start()
        while proc.poll() is None and not stop.wait(0.2):
            pass
        if proc.poll() is None:
            # ffmpeg ends cleanly only while the sound still flows, so the relay keeps feeding until it's gone
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        feeding.set()
        thread.join(2)
        logger.info(f"Watch page: capture stopped (ffmpeg exit {proc.returncode}).")
        return 0 if stop.is_set() else proc.returncode or 1
    finally:
        try:
            with open(CAPTURE_PID_FILE) as f:
                mine = f.read().strip() == str(os.getpid())
            if mine:
                os.remove(CAPTURE_PID_FILE)
        except OSError:
            pass
        if proc and proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
