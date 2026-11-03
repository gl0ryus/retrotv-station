import os
"""Checks the music block's rejoin and alert-resume math (Station.join_point / act_at) on a made-up block."""
import sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the station: this is its tests/ folder
from retrotv import station as rs
vids = [(f"/mnt/tower/.retrotv/videos/music_videos/2004 - A{i} - Song{i}.mp4", {"file": f"f{i}", "duration": 200.0}) for i in range(7)]
b = rs.MusicBlock("Music Videos", 30, vids, [2])
b.plan_slot(dt.datetime(2026, 10, 29, 19, 30))
print(f"slot {b.start:%H:%M}-{b.end:%H:%M}, {b.duration:.0f}s of video, mid break {b.mid_target:.0f}s after video 3")
for secs in (0, 150, 590, 610, 650, 600 + b.mid_target + 10, 1395, 1500):
    t = b.start + dt.timedelta(seconds=secs)
    print(f"  {secs:6.0f}s in -> join {rs.Station.join_point(b, t)}")
for pos in ((0, 50.0), (2, 190.0), (6, 120.0), (6, 190.0)):
    print(f"  alert at video {pos} -> resume {rs.Station.act_at(b, pos)}")
