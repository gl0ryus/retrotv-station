"""
Writes the CRT shader the station uses at full size (config.HD_SHADER, ~/.config/mpv/shaders/crt-lottes-hd.glsl): the
classic look's shader (mpv.conf's crt-lottes.glsl, kept on the Pi) made softer, because shown 1:1 with the panel's
pixels its sharp pixel filter, hard scanlines and strong mask beat against them into bands, and with flatter sides, so a
picture narrower than the screen keeps straight edges. Run on the Pi, then restart the station (or reload the shader):

    python3 -I tests/make_hd_shader.py [--hard-pix -3.0] [--hard-scan -4.0] [--mask 0.8 1.2] [--boost 1.1]
                                       [--curve 0.01 0.02]
"""
import argparse
import os

SRC = os.path.expanduser("~/.config/mpv/shaders/crt-lottes.glsl")
OUT = os.path.expanduser("~/.config/mpv/shaders/crt-lottes-hd.glsl")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard-pix", default="-3.0", help="how sharp each pixel is across (classic: -10.0)")
    parser.add_argument("--hard-scan", default="-4.0", help="how hard the scanlines are (classic: -12.0)")
    parser.add_argument("--mask", nargs=2, default=("0.8", "1.2"), help="the mask's dark and light (classic: 0.35 1.65)")
    parser.add_argument("--boost", default="1.1", help="brightness boost (classic: 1.3)")
    parser.add_argument("--curve", nargs=2, default=("0.01", "0.02"), help="how much the sides and the top and bottom "
                                                                           "bow (classic: 0.04 0.04)")
    args = parser.parse_args()
    s = open(SRC).read()
    pix = args.hard_pix
    swaps = [
        ("#define HARD_SCAN -12.0\n", f"#define HARD_SCAN {args.hard_scan}\n"),
        ("#define CURVATURE vec2(0.04, 0.04)\n", f"#define CURVATURE vec2({args.curve[0]}, {args.curve[1]})\n"),
        ("#define MASK_DARK 0.35\n", f"#define MASK_DARK {args.mask[0]}\n"),
        ("#define MASK_LIGHT 1.65\n", f"#define MASK_LIGHT {args.mask[1]}\n"),
        ("#define BRIGHTNESS_BOOST 1.3\n", f"#define BRIGHTNESS_BOOST {args.boost}\n"),
        ("\tvec3 a = horz3(pos, -1.0, -10.0);\n\tvec3 b = horz5(pos, 0.0, -10.0);\n\tvec3 c = horz3(pos, 1.0, -10.0);\n",
         f"\tvec3 a = horz3(pos, -1.0, {pix});\n\tvec3 b = horz5(pos, 0.0, {pix});\n\tvec3 c = horz3(pos, 1.0, {pix});\n"),
        ("//!DESC CRT Lottes\n", "//!DESC CRT Lottes (retrotv HD: softer, for the full-size picture)\n"),
    ]
    for old, new in swaps:
        if s.count(old) != 1:
            raise SystemExit(f"crt-lottes.glsl has changed: no single {old.strip()!r}")
        s = s.replace(old, new)
    with open(OUT, "w") as f:
        f.write(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
