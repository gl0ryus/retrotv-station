# The watch page's stream (retrotv/live.py) and the audio tap (retrotv/audio_tap.py), without the screen: a test
# pattern stands in for the screen capture. Runs on the Pi: python3 -I tests/live_test.py
import json, os, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
TMP = tempfile.mkdtemp(prefix="live_test")
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(TMP + "/rt.log", mode="w")   # not the station's own log
import harness
rs = harness.everything()
from retrotv import live, player, remote

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


def pattern(start):
    """Stands in for encoder_cmd: a test pattern at real speed for the screen, and the relay's sound on stdin as the
    real one has it (silence here, unless something sends to the tap's port), in the same fragmented MP4."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc=s=320x240:r=30",
            "-use_wallclock_as_timestamps", "1", "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac",
            "-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+delay_moov+default_base_moof", "pipe:1"]


def take(viewer, n, timeout=10):
    out = []
    for _ in range(n):
        chunk = viewer.queue.get(timeout=timeout)
        if chunk is None:
            break
        out.append(chunk)
    return out


def probe(data):
    path = TMP + "/probe.mp4"
    with open(path, "wb") as f:
        f.write(data)
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=codec_type,nb_read_frames",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return {s["codec_type"]: int(s.get("nb_read_frames") or 0) for s in json.loads(out or "{}").get("streams", [])}


# --- The stream: shared, joinable at any fragment, stopped when nobody watches ---
live.LIVE_IDLE_SECONDS = 2
live.LIVE_MAX_VIEWERS = 2
live.LIVE_AUDIO_PORT = 5098   # not the station's: a stream it's running must not hear this one's tap, or the other way
stream = live.Live(cmd=pattern)
a = stream.join()
header = stream.wait_header()
check(header is not None and header[4:8] == b"ftyp" and b"moov" in header, "a viewer gets the stream's header")
first = take(a, 3)
check(len(first) == 3 and all(f[4:8] == b"moof" for f in first), "then whole fragments")
got = probe(header + b"".join(first))
check(got.get("video", 0) >= 60 and got.get("audio", 0) > 0, f"which play as picture and sound, silence filled in: {got}")

b = stream.join()
check(stream.status()["viewers"] == 2 and stream.status()["running"], "a second viewer shares the same encoder")
later = take(b, 2)
got = probe(stream.wait_header() + b"".join(later))
check(got.get("video", 0) >= 30, f"and can start from whatever fragment comes next: {got}")
try:
    stream.join()
    check(False, "a third viewer is turned away")
except live.Busy:
    check(True, "")

stream.leave(a)
stream.leave(b)
time.sleep(1)
check(stream.status()["running"], "still running just after the last viewer leaves (a reload rejoins)")
time.sleep(4)
check(not stream.status()["running"], "stopped once nobody has watched for a while")

# A viewer that stops reading is let go rather than buffered for
slow = stream.join()
deadline = time.time() + live.BACKLOG + 20   # a fragment a second, once the encoder is up (slower on a busy Pi)
while slow in stream.viewers and time.time() < deadline:
    time.sleep(0.5)
check(slow not in stream.viewers, "a stalled viewer is dropped")
leftovers = []
while not slow.queue.empty():
    leftovers.append(slow.queue.get_nowait())
check(leftovers and leftovers[-1] is None, "and told its stream is over")
stream.leave(slow)

# The encoder dying ends every viewer's stream; the next viewer starts a new one
c = stream.join()
stream.wait_header()
take(c, 1)
proc = stream.proc
check(proc is not None, "running again for a new viewer")
os.killpg(proc.pid, signal.SIGKILL)
rest = []
while True:
    chunk = c.queue.get(timeout=10)
    if chunk is None:
        break
check(not stream.status()["running"], "a dead encoder ends the stream")
stream.leave(c)
d = stream.join()
check(stream.wait_header() is not None and stream.proc is not proc, "and the next viewer starts a fresh one")
stream.leave(d)


# Never reading the screen while the player isn't drawing on it: the capture would take the display from mpv
dark = live.Live(cmd=pattern, ready=lambda: False)
try:
    dark.join()
    check(False, "no stream while the player isn't drawing")
except live.NotShowing:
    check(dark.proc is None and not dark.viewers, "no stream while the player isn't drawing")

# The player starting stops a running stream at once, and its viewers' streams end
e = stream.join()
stream.wait_header()
stream.stop()
while e.queue.get(timeout=10) is not None:   # any fragments already queued, then the end
    pass
check(not stream.status()["running"], "stop() ends the stream before the player starts")
stream.leave(e)

# --- Over HTTP, as the watch page gets it ---
class FakeStation:
    live = live.Live(cmd=pattern)


port = 8097
remote.start(FakeStation(), port)
page = urllib.request.urlopen(f"http://127.0.0.1:{port}/watch", timeout=5).read()
check(b"/live.mp4" in page, "the watch page is served")
response = urllib.request.urlopen(f"http://127.0.0.1:{port}/live.mp4", timeout=20)
check(response.headers["Content-Type"] == "video/mp4", "the stream is served as MP4")
body = b""
t0 = time.time()
while time.time() - t0 < 4:
    body += response.read(65536)
response.close()
got = probe(body)
check(got.get("video", 0) >= 60 and got.get("audio", 0) > 0, f"and plays: {got}")
time.sleep(1)
check(FakeStation.live.status()["viewers"] == 0, "closing the page leaves")


class NoCapture:
    live = live.Live(cmd=lambda start: ["/nonexistent/ffmpeg"])


remote.Handler.station = NoCapture()   # type: ignore[assignment]
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/live.mp4", timeout=5)
    check(False, "an encoder that can't start is a 503")
except urllib.error.HTTPError as e:
    check(e.code == 503 and b"can't start" in e.read(), "an encoder that can't start is a 503, saying why")

# --- The audio tap: whole frames only, never blocks, outlives stop signals, leaves at end of input ---
listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
listen.bind(("127.0.0.1", 0))
listen.settimeout(2)
tap_port = listen.getsockname()[1]
tap = subprocess.Popen([sys.executable, "-I", "-S", os.path.join(harness.STATION, "retrotv", "audio_tap.py"), str(tap_port), "4"],
                       stdin=subprocess.PIPE)
assert tap.stdin is not None
tap.stdin.write(b"\x01" * 10)   # two and a half frames
tap.stdin.flush()
sizes = [len(listen.recv(65536))]   # once this arrives, it's running (and ignoring stop signals)
tap.send_signal(signal.SIGTERM)
tap.stdin.write(b"\x02" * 4)    # finishes the third frame, half of a fourth
tap.stdin.flush()
try:
    while sum(sizes) < 12:
        sizes.append(len(listen.recv(65536)))
except socket.timeout:
    pass
check(all(s % 4 == 0 for s in sizes) and sum(sizes) == 12, f"only whole frames go out: {sizes}")
check(tap.poll() is None, "a stop signal doesn't end it (the panel's sound depends on it)")
listen.close()
for _ in range(50):   # nobody listening now: it carries on
    tap.stdin.write(b"\x00" * 4096)
tap.stdin.flush()
time.sleep(0.5)
check(tap.poll() is None, "nobody listening is fine")
tap.stdin.close()
check(tap.wait(5) == 0, "it leaves when the sound's pipe closes")

# --- The player's side: the tee and the SD card copy, or the speakers alone ---
rs.AUDIO_TAP_FILE = TMP + "/tap/audio_tap.py"
args = player.audio_args()
with open(rs.AUDIO_TAP_FILE, "rb") as f:
    check(f.read() == player.TAP_CODE, "the tap is copied to the SD card for mpv")
check(any(a.startswith("--audio-device=alsa/tee:") and rs.AUDIO_TAP_FILE in a for a in args), f"mpv's sound goes through the tee: {args}")
check("--audio-samplerate=48000" in args and "--audio-format=s16" in args, "in the shape the stream expects")
rs.LIVE_STREAM = False
check(player.audio_args() == [], "watch page off: just the speakers")
rs.LIVE_STREAM = True
rs.AUDIO_TAP_FILE = "/proc/nowhere/audio_tap.py"
check(player.audio_args() == [], "a tap that can't be set up: just the speakers")
print(f"live checks passed: {checks}")
