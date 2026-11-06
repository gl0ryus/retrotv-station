# A show that runs past its slot keeps its commercial breaks and ends on the :00/:30 like any show, so what follows
# starts on the grid (Dan, 2026-11-06: a 42 min X-Play XL in the 10:00-10:30 PM slot pushed late night to 10:42).
# A show that fits by the close on fewer, shorter breaks still does. No network: python3 -I tests/overrun_test.py
import datetime, os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
rs = harness.everything()
harness.offline()

checks = 0


def check(ok, what):
    global checks
    assert ok, what
    checks += 1


FRIDAY = datetime.datetime(2026, 11, 6)


def at(hour, minute=0):
    return FRIDAY + datetime.timedelta(hours=hour, minutes=minute)


def show(folder, name, seconds, category, window, overrun=False):
    top = "g4" if category == "g4" else "shows"
    src = f"{rs.MEDIA_ROOT}/{top}/{folder}/{name}"
    e = {"file": "x.mkv", "category": category, "size": 1, "duration": seconds, "breaks": None,
         "series": None, "order": None}
    s = rs.Show(src, "/tmp/x.mkv", e, in_order=True)
    s.window, s.overrun = window, overrun
    return s


# --- Runs over: X-Play XL (42 min, the real file's length) at the top of its half hour
xl = show("X-Play", "x-play.xl.12.11.2008.dsr.xvid-sys.mp4", 2520.0, "g4", (at(22), at(22, 30)), overrun=True)
xl.plan_slot(at(22))
mids = len(rs.act_targets(xl.duration, xl.category, xl.src))
check(xl.end == at(23), f"runs to the hour, not {xl.end:%H:%M:%S}")
check(xl.keep_breaks is None and len(xl.kept_breaks()) == mids, f"keeps all {mids} breaks: {xl.keep_breaks}")
check(rs.MID_BREAK_SECONDS[0] <= xl.mid_target <= rs.MID_BREAK_SECONDS[1], f"ordinary mid breaks: {xl.mid_target:.0f} s")
check(xl.end_target >= rs.MIN_END_BREAK_SECONDS, f"an end break: {xl.end_target:.0f} s")
filled = xl.duration + rs.INTRO_SECONDS + mids * xl.mid_target + xl.end_target - rs.INTRO_SECONDS
check(abs(filled - 3600) < 1, f"show and breaks fill the hour: {filled:.0f} s")

# What follows it starts on the grid
lineup = object.__new__(rs.Lineup)
lineup.lock, lineup.last = threading.Lock(), None
curb = show("Curb Your Enthusiasm", "Curb Your Enthusiasm - S01E03 - Porno Gil.mkv", 1700.0, "videos", (at(22, 30), at(26)))
xl2 = show("X-Play", "x-play.xl.12.11.2008.dsr.xvid-sys.mp4", 2520.0, "g4", (at(22), at(22, 30)), overrun=True)
lineup.shows = [xl2, curb]
lineup.retime(at(22))
check(curb.start == at(23) and rs.on_grid(curb.start), f"late night starts at 11:00 PM, not {curb.start:%H:%M:%S}")
check(not rs.Lineup._out_of_slot(xl2) and not rs.Lineup._out_of_slot(curb), "both still belong to their slots")

# --- Fits by the close on fewer, shorter breaks: still squeezed into its slot
room = 1800 - rs.INTRO_SECONDS - rs.TIGHT_END_SECONDS
tight = show("Some Show", "Some Show - S01E01 - Pilot.mkv", room - 80, "videos", (at(22), at(22, 30)))
mids = len(rs.act_targets(tight.duration, tight.category, tight.src))
aligned, _, _ = rs.slot_end(tight.duration, tight.category, tight.src, at(22))
check(mids >= 1 and aligned > at(22, 30), f"the case: {mids} breaks, would end {aligned:%H:%M} with them all")
tight.plan_slot(at(22))
check(tight.end == at(22, 30), f"ends at its slot's close: {tight.end:%H:%M:%S}")
check(tight.keep_breaks == min(mids, 1), f"one short break left: {tight.keep_breaks}")

print(f"overrun checks passed: {checks}")
