"""
Renders the cards and screens in tests/snapshots.py to PNG frames, at the size the CRT stage gets them (512x300,
saved doubled), for looking over a restyle. Run on the Pi (it needs the fonts and ffmpeg):

    python3 -I tests/preview_cards.py OUTDIR [NAME ...]     # NAME: only builders whose names contain it

Each card gives a frame early on (once the type has faded in), one in the middle and one near the end; long
paged screens give one per page. The lower thirds, crawl and score card are drawn over a plain grey picture at
the 342x200 they join the show at (OVERLAY_TIMES). Nothing touches the live card folder: snapshots.py moves it to
a temp folder.
"""
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(sys.argv[1])
WANTED = sys.argv[2:]
import snapshots  # noqa: E402  (reads no arguments on import; it re-runs this script with a fixed hash seed)
from retrotv import config  # noqa: E402

FPS = 30


def times(duration):
    if duration > 30:
        step = 21.0 if duration >= 60 else duration / 3
        return [min(duration - 0.5, 2.0 + i * step) for i in range(int(duration // step) + 1)]
    return sorted({min(duration - 0.4, 1.6), duration / 2, max(0.0, duration - 0.6)})


def render(name, card):
    match = re.match(r"av://lavfi:(.*)\[out0\];", card.url)
    if not match:
        return f"{name}: not a lavfi card"
    frames = [round(t * FPS) for t in times(card.duration)]
    pick = "+".join(f"eq(n,{n})" for n in frames)
    graph = f"{match.group(1)},fps={FPS},select='{pick}',scale=1024:600:flags=bicubic"
    target = os.path.join(OUT, f"{name}-%02d.png")
    run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, "-fps_mode", "passthrough",
                          "-frames:v", str(len(frames)), target], capture_output=True, text=True, timeout=600)
    if run.returncode:
        return f"{name}: ffmpeg failed: {run.stderr.strip()[-300:]}"
    return f"{name}: {len(frames)} frames ({card.duration:.1f} s)"


# The lower thirds and crawls laid over a show: when to look at each (file time), over a plain grey picture at the
# 342x200 the overlays join the chain at. The score card's logos are left out (snapshots' are made-up paths).
OVERLAY_TIMES = {"events.snipe": (101.0, 110.0), "events.promo": (9.5, 17.5, 25.5), "events.popups": (21.0, 110.5, 199.5),
                 "alerts.crawl_script": (30.9, 40.0), "scores.board_filter": (51.2, 57.5)}
OVERLAYS = {"scores.board_filter": lambda: snapshots.scores.board_filter(
    [snapshots.game, dict(snapshots.game, league="nfl", away=("MIA", "17", None), home=("TB", "24", None),
                          detail="FINAL", ours=False)], 50.0, {})}
# Cards whose snapshot inputs can't draw here: a made-up picture path, or data that's only good on its own day
OVERLAYS.update({
    "events.marathon_ad": lambda: snapshots.events.marathon_ad(snapshots.art.wide("Seinfeld"), "Seinfeld",
                                                               "This Friday", "4:30 PM"),
    "screens.tonight_sky": lambda: snapshots.screens.tonight_sky(datetime.datetime.now()),
    "screens.art_break": lambda: snapshots.screens.art_break(datetime.datetime.now()),
})


def render_overlay(name, result):
    path = result[0] if isinstance(result, tuple) else result
    if not isinstance(path, str):
        return f"{name}: nothing to draw"
    stamps = OVERLAY_TIMES[name]
    fps = 10
    pick = "+".join(f"eq(n,{round(t * fps)})" for t in stamps)
    chain = (path[len("lavfi=["):-1].replace("[in]", "[0:v]") if path.startswith("lavfi=[")
             else f"[0:v]ass=filename={path}:fontsdir={snapshots.cards.FONTS_DIR}")
    graph = f"{chain}[o];[o]select='{pick}',scale=1024:600:flags=bicubic[out]"
    size = f"{config.FRAME_W}x{config.FRAME_H}"   # 342x200 under the CRT
    run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                          f"color=c=0x3a3f48:s={size}:r={fps}:d={max(stamps) + 0.5}", "-filter_complex", graph,
                          "-map", "[out]", "-fps_mode", "passthrough", "-frames:v", str(len(stamps)),
                          os.path.join(OUT, f"{name}-%02d.png")], capture_output=True, text=True, timeout=600)
    if run.returncode:
        return f"{name}: ffmpeg failed: {run.stderr.strip()[-300:]}"
    return f"{name}: {len(stamps)} frames over the show"


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, build in snapshots.BUILDERS.items():
        if WANTED and not any(w in name for w in WANTED):
            continue
        try:
            result = OVERLAYS.get(name, build)()
        except Exception as e:
            print(f"{name}: ERROR {type(e).__name__}: {e}")
            continue
        if result is None:
            print(f"{name}: None")
        elif hasattr(result, "url"):
            print(render(name, result))
        elif name in OVERLAY_TIMES:
            print(render_overlay(name, result))
        else:
            print(f"{name}: an overlay, not drawn here")


if __name__ == "__main__":
    main()
