# The watch page's stream: the sound's timing (retrotv/capture.py), MediaMTX kept running and the capture it starts
# for viewers (retrotv/live.py), what the network can reach of it, the remote's part, and the audio tap
# (retrotv/audio_tap.py). A test pattern stands in for the screen, so nothing reads the real panel.
# Runs on the Pi: python3 -I tests/live_test.py
import atexit, json, os, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
TMP = tempfile.mkdtemp(prefix="live_test")
_FH = logging.FileHandler
logging.FileHandler = lambda path, *a, **k: _FH(TMP + "/rt.log", mode="w")   # not the station's own log
import harness
rs = harness.everything()
from retrotv import capture, live, player, remote

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


# Not the station's own: it may be streaming right now
harness.set_everywhere("MEDIAMTX_API_PORT", 19997)
harness.set_everywhere("MEDIAMTX_RTSP_PORT", 18554)
harness.set_everywhere("MEDIAMTX_HLS_PORT", 18888)
harness.set_everywhere("MEDIAMTX_CONFIG_FILE", TMP + "/mediamtx.yml")
harness.set_everywhere("CAPTURE_PID_FILE", TMP + "/capture.pid")
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# --- The sound goes in where it's heard ---
RATE = 48000
a = capture.Aligner(start=1000.0, rate=RATE, shift_ms=0)
# Packets of 1200 frames, 25 ms apart, each with 4200 frames queued for the speakers (itself included): heard 62.5 ms on
placed = [a.place(1200, 1000.0 + i * 0.025, 4200) for i in range(5)]
check(placed[0] == (3000, 0), f"the first comes after silence up to when it's heard: {placed[0]}")
check(all(p == (0, 0) for p in placed[1:]), f"steady play goes in back to back, untouched: {placed}")
check(a.written == 3000 + 5 * 1200, "every frame counted")
jitter = a.place(1200, 1000.125, 4200 + 100)   # 2 ms off: within the tolerance
check(jitter == (0, 0), f"a couple of milliseconds off is left alone: {jitter}")
late = a.place(1200, 1000.250, 4200)           # after a gap (a lost packet, the start of an item)
check(late[0] > 0 and late[1] == 0, f"late sound waits behind silence: {late}")
before = a.written
early = a.place(1200, 1000.252, 4200 - 2400)   # the speakers' queue was cut short (a skip): it's heard sooner
check(early[0] == 0 and early[1] > 0 and a.written - before == 1200 - early[1], f"early sound loses its head: {early}")
quiet = capture.Aligner(start=0.0, rate=RATE, shift_ms=0)
check(quiet.fill(0.5) == 24000 and quiet.fill(0.5) == 0, "silence fills the time as it passes, once")
quiet.place(1200, 1.0, 4200)                   # sound queued into the future...
check(quiet.fill(1.05) == 0, "...isn't overwritten by silence while it's still to be heard")
shifted = capture.Aligner(start=0.0, rate=RATE, shift_ms=40)
check(shifted.place(1200, 1.0, 1200) == (round(1.04 * RATE), 0), "LIVE_AUDIO_SHIFT_MS moves the sound later")

status = TMP + "/status"
with open(status, "w") as f:
    f.write("state: RUNNING\nowner_pid   : 123\ntrigger_time: 1.0\ntstamp      : 2.0\ndelay       : 4368\navail       : 512\n")
check(capture.queued_frames(status) == 4368, "ALSA's delay is read from procfs")
with open(status, "w") as f:
    f.write("state: PREPARED\ndelay       : 99\n")
check(capture.queued_frames(status) is None and capture.queued_frames(TMP + "/none") is None, "only while it plays")

cmd = capture.encoder_cmd(1000.0, "rtsp://127.0.0.1:8554/gtv")
check("-enc_time_base:v" in cmd and cmd[cmd.index("-enc_time_base:v") + 1] == "1/90000", "frames stamped finely (no repeats)")
check(cmd[cmd.index("-profile:v") + 1] == "578" and cmd[cmd.index("-c:a") + 1] == "aac", "H.264 Constrained Baseline and AAC")
check(cmd[-5:] == ["-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://127.0.0.1:8554/gtv"] and "-flags:v" not in cmd,
      "published to MediaMTX, SPS/PPS with the frames (the encoder knows them too late for the description)")
check(cmd.index("-use_wallclock_as_timestamps") < cmd.index("kmsgrab") and cmd.count("-use_wallclock_as_timestamps") == 1,
      "the picture on the wall clock; the sound counted, not stamped by when it arrives")


# The relay, over UDP as the tap sends it
class Pipe:
    def __init__(self):
        self.data, self.lock = bytearray(), threading.Lock()

    def write(self, b):
        with self.lock:
            self.data += b

    def flush(self):
        pass


with open(status, "w") as f:
    f.write("state: RUNNING\ndelay       : 2400\n")
out, stop = Pipe(), threading.Event()
t0 = time.time()
aligner = capture.Aligner(start=t0, rate=RATE, shift_ms=0)
relay = threading.Thread(target=capture.relay, args=(out, aligner, stop, 5097, status))
relay.start()
send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
time.sleep(0.3)   # bound by now, and some silence written
for _ in range(8):
    send.sendto(b"\x01\x00" * 2 * 1200, ("127.0.0.1", 5097))
    time.sleep(0.025)
time.sleep(0.4)
stop.set()
relay.join(2)
frames = len(out.data) // 4
check(abs(frames - (time.time() - t0) * RATE) < 0.1 * RATE, f"the relay's sound keeps up with the clock: {frames / RATE:.2f} s")
check(out.data.count(b"\x01\x00" * 2 * 1200) >= 7, "the tap's packets go through whole")


class StalledPipe(Pipe):   # ffmpeg not taking sound for a while, as when its muxer waits on the picture
    def write(self, b):
        if not self.data:
            time.sleep(0.4)
        super().write(b)


out, stop = StalledPipe(), threading.Event()
t0 = time.time()
relay = threading.Thread(target=capture.relay, args=(out, capture.Aligner(start=t0, rate=RATE, shift_ms=0), stop, 5096, status))
relay.start()
time.sleep(0.15)   # bound, and stuck in its first write
sent = time.time()
for _ in range(4):
    send.sendto(b"\x01\x00" * 2 * 1200, ("127.0.0.1", 5096))
    time.sleep(0.025)
time.sleep(0.6)
stop.set()
relay.join(2)
at = out.data.find(b"\x01\x00") // 4 / RATE   # where the first packet went, in seconds from the start
want = sent - t0 + (2400 - 1200) / RATE         # where it's heard
check(abs(at - want) < 0.02, f"a stalled ffmpeg doesn't make the sound late: placed at {at:.3f} s, heard at {want:.3f} s")

# --- MediaMTX's settings: only reading the station's stream faces the network ---
config = json.loads(live.mediamtx_config())
check(config["apiAddress"] == "127.0.0.1:19997" and config["rtspAddress"] == "127.0.0.1:18554"
      and config["rtspTransports"] == ["tcp"], "its API and RTSP stay on this machine (RTSP over TCP only: no UDP ports)")
check(not any(config[k] for k in ("rtmp", "srt", "webrtc", "moq", "playback", "metrics", "pprof")), "every other server is off")
users = config["authInternalUsers"]
check(all(u["ips"] for u in users if any(p["action"] in ("publish", "api") for p in u["permissions"])),
      "only this machine may publish or use the API")
check([p for u in users if not u["ips"] for p in u["permissions"]] == [{"action": "read", "path": live.STREAM}],
      "anyone else may only read the station's stream")
path = config["paths"][live.STREAM]
check("capture.py rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH" in path["runOnDemand"] and path["overridePublisher"] is False,
      "the capture runs on demand, and nothing can take over its stream")

FAKE = TMP + "/fake_capture.py"   # "capture.py" in the name, as Live.stop expects; MediaMTX stops it with SIGINT
with open(FAKE, "w") as f:
    f.write(f'''import os, signal, subprocess, sys
open({rs.CAPTURE_PID_FILE!r}, "w").write(str(os.getpid()))
p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc=s=320x240:r=30",
                      "-re", "-f", "lavfi", "-i", "sine=f=440:r=48000", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                      "-preset", "ultrafast", "-profile:v", "baseline", "-g", "30", "-c:a", "aac",
                      "-f", "rtsp", "-rtsp_transport", "tcp", sys.argv[1]], stdin=subprocess.DEVNULL)
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: p.terminate())
p.wait()
os.remove({rs.CAPTURE_PID_FILE!r})
''')
live.CAPTURE_FILE = FAKE
ready = {"now": True}
stream = live.Live(ready=lambda: ready["now"])


@atexit.register
def _cleanup():   # MediaMTX (and anything it runs) in its own session: gone with the test, pass or fail
    if stream.proc and stream.proc.poll() is None:
        try:
            os.killpg(stream.proc.pid, signal.SIGKILL)
        except OSError:
            pass


deadline = time.time() + 15
while time.time() < deadline:
    try:
        OPEN.open("http://127.0.0.1:19997/v3/paths/list", timeout=1)
        break
    except urllib.error.HTTPError:
        break
    except OSError:
        time.sleep(0.3)
check(stream.proc is not None and stream.proc.poll() is None, "MediaMTX is kept running")
check(live.capture_pid() is None and not stream.status()["running"], "nothing captured while nobody watches")

# A viewer starts the capture; one joining later decodes the picture too; browsers get it as HLS
URL = f"rtsp://127.0.0.1:18554/{live.STREAM}"
first_viewer = subprocess.Popen(["ffmpeg", "-v", "error", "-rtsp_transport", "tcp", "-i", URL, "-t", "16", "-f", "null", "-"],
                                stdin=subprocess.DEVNULL)
time.sleep(6)
probe = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-show_entries", "stream=codec_name",
                        "-of", "json", URL], capture_output=True, text=True, timeout=40)
codecs = {s["codec_name"] for s in json.loads(probe.stdout or "{}").get("streams", [])}
check(codecs == {"h264", "aac"}, f"a viewer starts the capture, H.264 and AAC: {codecs} {probe.stderr[-200:]}")
late = subprocess.run(["ffmpeg", "-v", "error", "-rtsp_transport", "tcp", "-i", URL, "-frames:v", "20", "-f", "null", "-"],
                      capture_output=True, text=True, timeout=40, stdin=subprocess.DEVNULL)
check(late.returncode == 0 and "PPS" not in late.stderr, f"a viewer joining later decodes the picture: {late.stderr[-200:]}")
playlist = OPEN.open("http://127.0.0.1:18888/gtv/index.m3u8", timeout=20).read()
check(playlist.startswith(b"#EXTM3U"), f"and it's there as HLS: {playlist[:40]}")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "http://127.0.0.1:18888/gtv/index.m3u8", "-t", "4", "-map", "0",
                "-c", "copy", TMP + "/hls.mkv"], stdin=subprocess.DEVNULL, timeout=40)   # (not RTSP: no AAC for ffmpeg there)
sound = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "packet=pts_time", "-of", "csv=p=0",
                        TMP + "/hls.mkv"], capture_output=True, text=True).stdout.split()
check(len(sound) > 100, f"with its sound flowing, not just a sound track: {len(sound)} packets in 4 s")
check(len(OPEN.open("http://127.0.0.1:18888/gtv/hls.min.js", timeout=10).read()) > 100000, "with hls.js for the page")
check(stream.viewers() >= 1 and stream.status()["running"], "MediaMTX counts the readers; the capture runs")
lan = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
lan.connect(("10.0.0.1", 9))   # no packet sent: just which address this machine has on the home network
here = lan.getsockname()[0]
try:
    socket.create_connection((here, 18554), timeout=3).close()
    check(False, "RTSP isn't on the network")
except OSError:
    check(True, "")
try:
    socket.create_connection((here, 19997), timeout=3).close()
    check(False, "nor the API")
except OSError:
    check(True, "")
try:
    OPEN.open(f"http://{here}:18888/other/index.m3u8", timeout=5)
    check(False, "the network reads only the station's stream")
except urllib.error.HTTPError as e:
    check(e.code >= 400, f"the network reads only the station's stream (refused: {e.code})")
check(OPEN.open(f"http://{here}:18888/gtv/index.m3u8", timeout=10).status == 200, "which it can")
first_viewer.wait(30)

# The player starting stops the capture at once (Player.before_start); MediaMTX starts it again for viewers later
viewer = subprocess.Popen(["ffmpeg", "-v", "error", "-rtsp_transport", "tcp", "-i", URL, "-t", "20", "-f", "null", "-"],
                          stdin=subprocess.DEVNULL)
deadline = time.time() + 20
while live.capture_pid() is None and time.time() < deadline:
    time.sleep(0.5)
pid = live.capture_pid()
stream.stop()
check(pid is not None and not os.path.exists(f"/proc/{pid}"), "stop() ends the capture")
viewer.kill()
deadline = time.time() + 60   # nobody left (the HLS request above counts for 20 s, then 10 s more)
while live.capture_pid() is not None and time.time() < deadline:
    time.sleep(1)
check(live.capture_pid() is None, "and it stops once the last viewer leaves")


# --- The remote: the watch page, and the capture's question ---
class FakeStation:
    pass


FakeStation.live = stream   # type: ignore[attr-defined]
port = 8097
remote.start(FakeStation(), port)
page = OPEN.open(f"http://127.0.0.1:{port}/watch", timeout=5).read()
check(b"hls.min.js" in page and b":8888/gtv/" in page and b"index.m3u8" in page, "the watch page plays MediaMTX's HLS")
check(OPEN.open(f"http://127.0.0.1:{port}/api/live/ready", timeout=5).status == 200, "ready while the panel shows")
ready["now"] = False
try:
    OPEN.open(f"http://127.0.0.1:{port}/api/live/ready", timeout=5)
    check(False, "not ready while the player is starting")
except urllib.error.HTTPError as e:
    check(e.code == 503, "not ready while the player is starting (the capture waits)")
ready["now"] = True

# MediaMTX dying is put right
old = stream.proc
assert old is not None
old.kill()
deadline = time.time() + live.RESTART_SECONDS + 10
while (stream.proc is old or stream.proc is None or stream.proc.poll() is not None) and time.time() < deadline:
    time.sleep(0.5)
check(stream.proc is not old and stream.proc is not None and stream.proc.poll() is None, "MediaMTX is started again if it stops")
stream.proc.kill()

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
