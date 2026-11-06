# The player's memory safety net, against a real mpv with no screen. Runs on the Pi: python3 -I player_test.py
import os, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
rs = harness.everything()

TMP = tempfile.mkdtemp(prefix="player_test")
rs.MPV_SOCKET = TMP + "/mpv.sock"
rs.PLAYER_CMD = ["mpv", "--no-config", "--idle=yes", "--vo=null", "--ao=null", "--really-quiet", f"--input-ipc-server={rs.MPV_SOCKET}"]
rs.REMOTE_STATE_FILE = TMP + "/remote.json"
rs.PLAY_LOG_FILE = TMP + "/plays.jsonl"   # not the station's play log
rs.AUDIO_TAP_FILE = TMP + "/audio_tap.py"   # the station's own copy is the live one
rs.PLAYER_MEMORY_CHECK_SECONDS = 0
checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


ends = []


def item(kind, seconds=1.5):
    it = rs.Item(f"av://lavfi:color=c=red:s=64x64:r=10:d={seconds}", seconds, kind, kind)
    it.on_end = lambda reason, kind=kind: ends.append((kind, reason))
    return it


p = rs.Player()
rs.PLAYER_MEMORY_MB = 100000   # nothing is too big
check(p.play(item("act")) and p.play(item("commercial")), "plays")
first = p.proc.pid
check(isinstance(p.memory_mb(), int) and p.memory_mb() > 1, f"reads its memory: {p.memory_mb()}")
with open(f"/proc/{p.proc.pid}/environ", "rb") as f:
    check(b"MALLOC_ARENA_MAX=2" in f.read().split(b"\0"), "mpv runs with glibc's memory pools capped")
time.sleep(3.5)
check(p.memory_restarts == 0 and p.proc.pid == first, "no restart while it's small")
from retrotv import playlog
check([e["kind"] for e in playlog.recent(1)][:2] == ["act", "commercial"], f"each start goes in the play log: {playlog.recent(1)}")
check(("act", "eof") in ends and ("commercial", "eof") in ends, ends)

rs.PLAYER_MEMORY_MB = 1        # everything is too big
ends.clear()
check(p.play(item("act")), "a show segment")
check(p.play(item("act")) and p.memory_restarts == 0, "never restarted between a show's segments")
t0 = time.time()
check(p.play(item("commercial")), "the break")
check(p.memory_restarts == 1 and p.proc.pid != first, "restarted before the break")
check(time.time() - t0 >= 1.0, "only once what was playing had finished")
check(ends[:2] == [("act", "eof"), ("act", "eof")], f"the show wasn't cut short: {ends}")
time.sleep(2)
check(ends[-1] == ("commercial", "eof"), f"and the break played after: {ends}")
check(p.position() is None or p.alive, "the new player answers")

# A flush (a skip from the remote) while it waits to restart: the item is dropped, nothing breaks
rs.PLAYER_MEMORY_MB = 100000
p.play(item("act", 4))
rs.PLAYER_MEMORY_MB = 1
ends.clear()
result = []
waiter = threading.Thread(target=lambda: result.append(p.play(item("commercial"))))
waiter.start()
time.sleep(1)
p.flush()
waiter.join(10)
check(result == [False] and ("commercial", "stop") in ends, f"dropped on a flush: {result} {ends}")
p.proc.kill()


# --- The freeze watchdog: playback stuck while it should move, as seen on air 2026-11-05 ---
class Stuck:
    """Stands in for mpv's replies: playing, its playback time wherever `at` says."""
    def __init__(self):
        self.at, self.sent, self.killed, self.idle = 36.3, [], False, False

    def command(self, cmd):
        self.sent.append(cmd)
        prop = cmd[1] if cmd[0] == "get_property" else None
        return {"data": {"idle-active": self.idle, "pause": False, "paused-for-cache": False, "playback-time": self.at}.get(prop)}

    def kill(self):
        self.killed = True


w, s = rs.Player(), Stuck()
w._command, w.proc, w.alive, w.started_at = s.command, s, True, 0.0   # type: ignore[assignment]
for t in (100, 105, 110):
    w.check_stall(now=t)
check(["ao-reload"] not in s.sent and not s.killed, "a few seconds stuck is left alone")
w.check_stall(now=116)
check(["ao-reload"] in s.sent and not s.killed, "15 s stuck: mpv reopens its sound")
w.check_stall(now=125)
check(not s.killed, "and gets another 15 s")
w.check_stall(now=132)
check(s.killed, "still stuck: mpv is stopped, for play() to start a new one")
m, s = rs.Player(), Stuck()
m._command, m.proc, m.alive, m.started_at = s.command, s, True, 0.0   # type: ignore[assignment]
for t in range(100, 200, 5):
    s.at += 5.0
    m.check_stall(now=t)
check(["ao-reload"] not in s.sent and not s.killed, "moving playback is never touched")
s.idle = True
for t in range(200, 300, 5):
    m.check_stall(now=t)
check(not s.killed, "nor an idle player between items")
print(f"player checks passed: {checks}")
