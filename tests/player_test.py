# The player's memory safety net, against a real mpv with no screen. Runs on the Pi: python3 -I player_test.py
import os, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
from retrotv import station as rs

TMP = tempfile.mkdtemp(prefix="player_test")
rs.MPV_SOCKET = TMP + "/mpv.sock"
rs.PLAYER_CMD = ["mpv", "--no-config", "--idle=yes", "--vo=null", "--ao=null", "--really-quiet", f"--input-ipc-server={rs.MPV_SOCKET}"]
rs.REMOTE_STATE_FILE = TMP + "/remote.json"
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
time.sleep(3.5)
check(p.memory_restarts == 0 and p.proc.pid == first, "no restart while it's small")
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
print(f"player checks passed: {checks}")
