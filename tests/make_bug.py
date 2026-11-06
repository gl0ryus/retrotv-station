"""
Draws the corner bug the station lays over its shows (config.LOGO_BUG_FILE): the G/TV badge as the style guide puts it
over a programme, on a transparent background, at the size of the picture the overlays join (config.FRAME_W: 342 with
the CRT, 1024 without). Run on the Pi (it needs the fonts) after changing the badge:

    python3 -I tests/make_bug.py OUT.png [--width 342] [--accent '&H001457FF&']
"""
import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retrotv import brand, cards  # noqa: E402

EM = 40.0   # the letters on the 1024x600 canvas: the corner badge's size on the cards


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--width", type=int, default=342, help="the picture's width: 342 (CRT) or 1024")
    parser.add_argument("--accent", default=brand.ORANGE, help="the bar's colour, as ASS writes it")
    args = parser.parse_args()
    scale = args.width / cards.W
    height = round(cards.H * scale)
    cards.CARDS_DIR = tempfile.mkdtemp(prefix="bug")
    w, h = brand.badge_size(EM)
    margin = 12   # room for the tilt and the shadow
    s = cards.Script()
    brand.badge(s, 0, 1, margin, margin, EM, args.accent, fade=None)
    script = s.write("bug")
    crop_w, crop_h = round((w + 2 * margin + 6) * scale), round((h + 2 * margin + 6) * scale)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black@0:s={args.width}x{height}:d=1,format=rgba",
                    "-vf", f"ass=filename={script}:fontsdir={cards.FONTS_DIR}:alpha=1,crop={crop_w}:{crop_h}:0:0",
                    "-frames:v", "1", "-update", "1", args.out], check=True)
    print(f"wrote {args.out} ({crop_w}x{crop_h})")


if __name__ == "__main__":
    main()
