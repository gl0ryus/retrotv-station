"""
Writes retrotv/glyphs.py: each card font's advance widths, so a label box can hug its words the way the style
guide's do. Reads the fonts in the station's fonts folder (TTF, OTF or WOFF2: WOFF2's tables are unpacked with
the Pi's libbrotlidec). Run on the Pi after adding or changing a card font:

    python3 -I tests/make_glyphs.py [OUT]     # OUT: where to write it (the Pi mounts the NAS read-only)
"""
import ctypes
import ctypes.util
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retrotv.paths import ASSETS_DIR  # noqa: E402

FONTS = {   # the family name the cards use -> its file in ASSETS_DIR/fonts
    "Anton": "Anton-latin.woff2",
    "Archivo SemiBold": "Archivo-latin.woff2",   # the variable font names itself by its default weight
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Barlow Condensed ExtraBold": "BarlowCondensed-ExtraBold.ttf",
    "Barlow Condensed Bold": "BarlowCondensed-Bold.ttf",
    "Barlow Condensed SemiBold": "BarlowCondensed-SemiBold.ttf",
}
# Every character a card is likely to draw: printable ASCII, Latin-1, and the punctuation the cards use
CHARS = ([chr(c) for c in range(0x20, 0x7f)] + [chr(c) for c in range(0xa0, 0x100)]
         + list("‘’“”–—•·…°★▲▼►◄●○™€"))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "retrotv", "glyphs.py")

KNOWN_TAGS = ["cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca",
              "prep", "CFF ", "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea",
              "vmtx", "BASE", "GDEF", "GPOS", "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL",
              "SVG ", "sbix", "acnt", "avar", "bdat", "bloc", "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar",
              "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop", "trak", "Zapf", "Silf", "Glat",
              "Gloc", "Feat", "Sill"]


def _base128(data, pos):
    value = 0
    for _ in range(5):
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7f)
        if not byte & 0x80:
            return value, pos
    raise ValueError("bad UIntBase128")


def _brotli(data, size):
    lib = ctypes.CDLL(ctypes.util.find_library("brotlidec") or "libbrotlidec.so.1")
    out = ctypes.create_string_buffer(size)
    out_size = ctypes.c_size_t(size)
    ok = lib.BrotliDecoderDecompress(ctypes.c_size_t(len(data)), data, ctypes.byref(out_size), out)
    if ok != 1:
        raise ValueError("brotli failed")
    return out.raw[:out_size.value]


def tables(path):
    """{tag: bytes} for the tables metrics need. WOFF2's transformed hmtx is returned as-is (flagged)."""
    data = open(path, "rb").read()
    if data[:4] != b"wOF2":
        num = struct.unpack(">H", data[4:6])[0]
        out = {}
        for i in range(num):
            tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            out[tag.decode("latin-1")] = data[offset:offset + length]
        return out, set()
    num = struct.unpack(">H", data[12:14])[0]
    compressed_size = struct.unpack(">I", data[20:24])[0]
    pos, entries = 48, []
    for _ in range(num):
        flags = data[pos]
        pos += 1
        if flags & 0x3f == 0x3f:
            tag = data[pos:pos + 4].decode("latin-1")
            pos += 4
        else:
            tag = KNOWN_TAGS[flags & 0x3f]
        version = flags >> 6
        length, pos = _base128(data, pos)
        transformed = (version == 0) if tag in ("glyf", "loca") else (version != 0)
        if transformed:
            length, pos = _base128(data, pos)
        entries.append((tag, length, transformed))
    total = sum(length for _, length, _ in entries)
    raw = _brotli(data[pos:pos + compressed_size], total)
    out, flagged, at = {}, set(), 0
    for tag, length, transformed in entries:
        out[tag] = raw[at:at + length]
        if transformed:
            flagged.add(tag)
        at += length
    return out, flagged


def cmap(table):
    num = struct.unpack(">H", table[2:4])[0]
    best = None
    for i in range(num):
        platform, encoding, offset = struct.unpack(">HHI", table[4 + 8 * i: 12 + 8 * i])
        fmt = struct.unpack(">H", table[offset:offset + 2])[0]
        if fmt == 4 and (platform, encoding) in ((3, 1), (0, 3), (0, 4)):
            best = offset
    if best is None:
        raise ValueError("no format 4 cmap")
    t = table[best:]
    segs = struct.unpack(">H", t[6:8])[0] // 2
    ends = struct.unpack(f">{segs}H", t[14:14 + 2 * segs])
    starts = struct.unpack(f">{segs}H", t[16 + 2 * segs:16 + 4 * segs])
    deltas = struct.unpack(f">{segs}h", t[16 + 4 * segs:16 + 6 * segs])
    range_at = 16 + 6 * segs
    ranges = struct.unpack(f">{segs}H", t[range_at:range_at + 2 * segs])
    out = {}
    for i in range(segs):
        for c in range(starts[i], ends[i] + 1):
            if c == 0xffff:
                continue
            if ranges[i] == 0:
                g = (c + deltas[i]) & 0xffff
            else:
                at = range_at + 2 * i + ranges[i] + 2 * (c - starts[i])
                g = struct.unpack(">H", t[at:at + 2])[0]
                g = (g + deltas[i]) & 0xffff if g else 0
            if g:
                out[c] = g
    return out


def metrics(path):
    t, flagged = tables(path)
    units = struct.unpack(">H", t["head"][18:20])[0]
    win_ascent, win_descent = struct.unpack(">HH", t["OS/2"][74:78])
    n_metrics = struct.unpack(">H", t["hhea"][34:36])[0]
    hmtx = t["hmtx"]
    if "hmtx" in flagged:   # WOFF2's transformed hmtx: a flags byte, then the advance widths
        advances = struct.unpack(f">{n_metrics}H", hmtx[1:1 + 2 * n_metrics])
    else:
        advances = [struct.unpack(">H", hmtx[4 * i:4 * i + 2])[0] for i in range(n_metrics)]
    chars = cmap(t["cmap"])
    widths = {}
    for ch in CHARS:
        g = chars.get(ord(ch))
        if g is not None:
            widths[ch] = advances[min(g, n_metrics - 1)]
    cap = None
    if len(t["OS/2"]) >= 90 and struct.unpack(">H", t["OS/2"][0:2])[0] >= 2:
        cap = struct.unpack(">h", t["OS/2"][88:90])[0]
    return units, win_ascent, win_ascent + win_descent, cap, widths


def main():
    fonts_dir = os.path.join(ASSETS_DIR, "fonts")
    lines = ['"""', "Advance widths of the card fonts, written by tests/make_glyphs.py from the font files: libass sizes a",
             "font so its Windows ascent plus descent is the \\fs size, so a character is width / HEIGHT[font] * size wide.",
             'Do not edit by hand."""', "", "HEIGHT = {"]
    data = {}
    for family, name in FONTS.items():
        units, ascent, height, cap, widths = metrics(os.path.join(fonts_dir, name))
        data[family] = (units, ascent, height, cap, widths)
        print(f"{family}: {units} units/em, ascent+descent {height}, cap height {cap}, {len(widths)} characters")
    for family, (_, _, height, _, _) in data.items():
        lines.append(f"    {family!r}: {height},")
    lines += ["}", "", "# Font units per em, the Windows ascent (the baseline's depth below a line's top) and the cap height",
              "UNITS = {"]
    for family, (units, _, _, _, _) in data.items():
        lines.append(f"    {family!r}: {units},")
    lines += ["}", "", "ASCENT = {"]
    for family, (_, ascent, _, _, _) in data.items():
        lines.append(f"    {family!r}: {ascent},")
    lines += ["}", "", "CAP = {"]
    for family, (_, _, _, cap, _) in data.items():
        lines.append(f"    {family!r}: {cap},")
    lines += ["}", "", "WIDTHS = {"]
    for family, (_, _, _, _, widths) in data.items():
        lines.append(f"    {family!r}: {{")
        items = [f"{ch!r}: {w}" for ch, w in widths.items()]
        for i in range(0, len(items), 10):
            lines.append("        " + ", ".join(items[i:i + 10]) + ",")
        lines.append("    },")
    lines.append("}")
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
