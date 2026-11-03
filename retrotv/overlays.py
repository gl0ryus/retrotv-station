"""
The per-file filters laid over what plays: the logo bug, the progress line, lower thirds and their pictures,
reception glitches; and the on-screen text for channel changes.
"""
import os
import random

from retrotv import cards
from retrotv import art as posters
from retrotv.config import LOGO_BUG_FILE, PROGRESS_BAR, PROGRESS_COLOR


ART_X, ART_BOTTOM, ART_SLIDE = 14, 153, 260   # a lower third's poster: above the label, resting on the bar (342x200)


def glitch_filters(t0, kind):
    """About a second of bad reception at file time t0: a tracking roll, an interference burst or tearing."""
    t1 = t0 + 1.0
    on = f"enable='between(t,{t0:.2f},{t1:.2f})'"
    if kind == "roll":
        # The picture rolls with the blanking bar going past, then the tracking catches
        video = [f"scroll=v=0.045:{on}", f"drawbox=y='mod((t-{t0:.2f})*300,ih)':w=iw:h=14:color=black:t=fill:{on}",
                 f"noise=alls=25:allf=t:{on}"]
        audio = [f"volume=volume=0.45:{on}", f"highpass=f=500:{on}"]
    elif kind == "tear":
        video = []
        for i in range(6):
            a = t0 + i / 6
            shift = random.choice((-9, -6, 6, 9))
            video.append(f"rgbashift=rh={shift}:gh={shift}:bh={shift}:enable='between(t,{a:.2f},{a + 1 / 6:.2f})'")
        video += [f"noise=alls=35:allf=t:{on}", f"eq=saturation=0.5:{on}"]
        audio = [f"acrusher=bits=7:mix=0.5:{on}"]
    else:  # interference
        video = [f"noise=alls=70:allf=t:{on}", f"rgbashift=rh=4:bh=-4:{on}", f"eq=brightness=0.06:saturation=0.4:{on}",
                 f"drawbox=y='mod((t-{t0:.2f})*520,ih)':w=iw:h=6:color=white@0.35:t=fill:{on}"]
        audio = [f"volume=volume=0.4:{on}", f"acrusher=bits=6:mix=0.6:{on}"]
    return ",".join(video), ",".join(audio)


def overlay_filter(duration, bug, snipe=None, glitch=None, promo=None, popups=None, art: list | None = None):
    """
    Per-file filters added after the CRT crunch (342x200): the logo bug, the progress line, the
    NEXT / LATER / TONIGHT promo, the "NEXT" lower third, Pop-Up Video bubbles and a reception glitch.
    art: posters that sit on those lower thirds, [(file, from, to, slides in with the bar)] in file time.
    Returns (vf-append, af-append); either may be None.
    """
    graph, sources, cur, n = [], [], "in", 0

    def stage(text, extra=""):
        nonlocal cur, n
        n += 1
        graph.append(f"[{cur}]{extra}{text}[v{n}]")
        cur = f"v{n}"

    if bug and LOGO_BUG_FILE and os.path.exists(LOGO_BUG_FILE):
        # Bottom right, inside the curved corner
        sources.append(f"movie={LOGO_BUG_FILE}[bug]")
        stage("overlay=x=W-w-16:y=H-h-13", "[bug]")
    if PROGRESS_BAR and duration > 0:
        # One scanline high, sliding in from the left. The frame timestamp is the position in the whole
        # file, so the line carries on across act breaks instead of restarting each act
        sources.append(f"color=c={PROGRESS_COLOR}:s=342x1:d=1,format=rgba[bar]")
        stage(f"overlay=x='-w+w*min(1,t/{duration:.2f})':y=H-11:eval=frame", "[bar]")
    for script in (popups, promo, snipe):
        if script:
            stage(f"ass=filename={script}:fontsdir={cards.FONTS_DIR}")
    for i, (path, start, end, slides) in enumerate(art or ()):
        # A clip is timed to start with its panel and stops being decoded when the panel ends
        sources.append(f"movie={path}:loop=0,setpts=N/({posters.CLIP_FPS}*TB)+{start:.2f}/TB,trim=end={end:.2f}[art{i}]"
                       if path.endswith(".mp4") else f"movie={path}[art{i}]")
        x = str(ART_X)
        if slides:   # keeps pace with the bar's slide in (events: 450 ms from off the left edge)
            x = f"'{ART_X}-(1-min(1,(t-{start:.2f})/0.45))*{ART_SLIDE}'"
        stage(f"overlay=x={x}:y={ART_BOTTOM}-h:eval=frame:enable='between(t,{start:.2f},{end:.2f})'", f"[art{i}]")
    audio = None
    if glitch:
        video, audio = glitch_filters(*glitch)
        stage(video)
    if not graph:
        return None, audio and f"lavfi=[{audio}]"
    graph[-1] = graph[-1][:-len(f"[v{n}]")]  # the last stage feeds mpv
    return "lavfi=[" + ";".join(sources + graph) + "]", audio and f"lavfi=[{audio}]"


def osd_msg(text):
    """mpv reads C-style escapes in osd-playing-msg (a lone backslash before N is a "broken escape
    sequence"), so the ASS tags' backslashes have to be doubled there. show-text over IPC doesn't do this."""
    return text.replace("\\", "\\\\")


def channel_osd(number, name, show=None):
    """On-screen text after a channel change: the channel number in VCR green, then the show's title."""
    text = "${osd-ass-cc/0}{\\c&H66FF66&}CH " + f"{number:02d}  " + cards.esc(name).replace("$", "$$")
    if show:
        title = show.osd_title()
        if title.startswith("${osd-ass-cc/0}"):
            title = title[len("${osd-ass-cc/0}"):]
        else:
            title = cards.esc(show.title()).replace("$", "$$")
        text += "\\N{\\c&HFFFFFF&}" + title
    return text


def half_hour_floor(t):
    return t.replace(minute=t.minute - t.minute % 30, second=0, microsecond=0)
