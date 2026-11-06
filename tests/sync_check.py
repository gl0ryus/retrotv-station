"""
How far the watch page's sound is from its picture, end to end: cuts a flash-and-beep clip into the station (the
launch test hook, about 85 s with its card), records the stream as browsers get it (MediaMTX's HLS), and pairs each
white flash with its beep. Interrupts the station; fine on this hobby box. Runs on the Pi, from the station folder:

    python3 -I tests/sync_check.py        # then set config.LIVE_AUDIO_SHIFT_MS to minus the mean it prints, plus
                                          # whatever LIVE_AUDIO_SHIFT_MS was when this ran

ffmpeg's RTSP reader gets no AAC from MediaMTX, so this reads HLS. A 30 fps capture beating against the panel's own
frames makes the readings a sawtooth about one frame (33 ms) tall: its middle is the offset.
"""
import http.server, math, os, re, subprocess, sys, tempfile, threading, time, urllib.request

DIR = tempfile.mkdtemp(prefix="sync_check")
CLIP = os.path.join(DIR, "sync.mp4")
REC = os.path.join(DIR, "rec.mkv")
PORT = 8765


def make_clip():
    """20 s: a white flash (2 frames) and a 66 ms 1 kHz beep at every whole second, sample-accurate."""
    beep = "0.5*sin(2*PI*1000*t)*lt(mod(t\\,1)\\,0.066)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1024x600:r=30:d=20",
                    "-f", "lavfi", "-i", "color=c=white:s=1024x600:r=30:d=20",
                    "-f", "lavfi", "-i", f"aevalsrc={beep}|{beep}:s=48000:d=20",
                    "-filter_complex", "[0:v][1:v]overlay=enable='lt(mod(t,1),0.066)'[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", "-b:a", "128k", CLIP], check=True)


def series(path, args, key):
    meta = os.path.join(DIR, "meta.txt")
    subprocess.run(["ffmpeg", "-v", "error", "-i", path, *args.replace("META", meta).split("|"), "-f", "null", "-"], check=True)
    out, t = [], None
    for line in open(meta):
        m = re.match(r"frame:\d+\s+pts:\S+\s+pts_time:(\S+)", line)
        if m:
            t = float(m.group(1))
        elif line.startswith(key + "="):
            v = line.split("=", 1)[1].strip()
            out.append((t, float(v) if v not in ("-inf", "inf", "nan", "-nan") else -120.0))
    return out


def onsets(points, high, low, gap):
    out, armed = [], True
    for t, v in points:
        if armed and v > high and (not out or t - out[-1] > gap):
            out.append(t)
            armed = False
        elif v < low:
            armed = True
    return out


def offsets(path):
    """Sound minus picture, in seconds, for each flash with a beep within half a second."""
    bright = series(path, "-an|-vf|signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=META", "lavfi.signalstats.YAVG")
    loud = series(path, "-vn|-af|asetnsamples=n=48,astats=metadata=1:reset=1,"
                  "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=META", "lavfi.astats.Overall.RMS_level")
    beeps = onsets(loud, -20, -40, 0.5)
    pairs = []
    for f in onsets(bright, 128, 60, 0.5):
        b = min(beeps, key=lambda x: abs(x - f), default=None)
        if b is not None and abs(b - f) < 0.3:
            pairs.append(b - f)
    return pairs


def main():
    make_clip()
    source = offsets(CLIP)
    assert source and max(abs(d) for d in source) < 0.002, f"the clip itself is off: {source}"
    handler = lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=DIR)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rec = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", "http://127.0.0.1:8888/gtv/index.m3u8", "-t", "150",
                            "-map", "0", "-c", "copy", REC], stdin=subprocess.DEVNULL)
    time.sleep(3)
    ask = urllib.request.Request(f"http://127.0.0.1/api/test?what=launch&url=http://127.0.0.1:{PORT}/sync.mp4&seconds=20",
                                 method="POST")
    print(urllib.request.urlopen(ask, timeout=120).read().decode())
    rec.wait()
    server.shutdown()
    found = offsets(REC)
    if len(found) < 10:
        sys.exit(f"only {len(found)} flash-and-beep pairs found: did the clip air while recording?")
    found.sort()
    middle = found[len(found) // 10: len(found) - len(found) // 10]   # the odd stray pairing left out
    mean = sum(middle) / len(middle)
    sd = math.sqrt(sum((d - mean) ** 2 for d in middle) / len(middle))
    print("sound minus picture (ms):", [round(d * 1000) for d in found])
    print(f"offset {mean * 1000:+.0f} ms (positive: sound late), spread {sd * 1000:.0f} ms")


if __name__ == "__main__":
    main()
