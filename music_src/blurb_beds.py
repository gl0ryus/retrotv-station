"""
Synthesizes the beds under the late-night text bumps (retrotv_blurbs): two lo-fi loops (lazy boom-bap,
electric piano, vinyl crackle) and two vaporwave ones (slow, detuned, chorused pads and a dreamy bell line),
each a few bars that loop seamlessly, levelled a little under the stings. Never overwrites a bed that's there.

    python blurb_beds.py <music dir> [name ...]

Same voices and mixer as holiday_stings.py; plain Python plus ffmpeg with libvorbis.
"""
import os
import random
import subprocess
import sys
import tempfile

from holiday_stings import SR, Mix, hz, noise, tone

MEAN_DB = -24.0   # under the text, a little quieter than the stings

EPIANO = dict(partials=[(1, 1.0), (2, 0.3), (3, 0.08), (4, 0.05)], attack=0.008, decay=1.4, release=0.25)
PAD = dict(partials=[(1, 1.0), (2, 0.35), (3, 0.15)], attack=0.6, decay=None, release=0.8)
DREAM_BELL = dict(partials=[(1, 1.0), (2.0, 0.4), (3.0, 0.15)], attack=0.01, decay=1.6, release=0.4, vibrato=0.004)
BASS = dict(partials=[(1, 1.0), (2, 0.25)], attack=0.01, decay=0.7, release=0.1)
KICK = dict(partials=[(1, 1.0)], attack=0.001, decay=0.14, release=0.03)


def bar_beats(bpm, bars, beats=4):
    return 60 / bpm * beats * bars


def chord(m, start, seconds, notes, voice, gain, detune=0.0, strum=0.0, pan=0.0):
    """A chord; with detune, each note twice a hair apart (a chorus, the vaporwave smear)."""
    for j, p in enumerate(notes.split("+")):
        f = hz(p)
        for k, spread in enumerate((1 + detune, 1 - detune) if detune else (1,)):
            side = pan + (0.35 if k else -0.35) * bool(detune)
            m.add(start + j * strum, tone(f * spread, seconds, **voice), gain, side)


def drums(m, bpm, bars, rnd, swing=0.58, gain=1.0):
    """A lazy boom-bap: kick on 1 and the "and" of 3, snare on 2 and 4, swung hats."""
    beat = 60 / bpm
    for b in range(bars):
        t0 = b * 4 * beat
        for k in (0, 2.5):
            m.add(t0 + k * beat, tone(120, 0.3, glide_to=42, **KICK), 0.55 * gain)
        for k in (1, 3):
            m.add(t0 + k * beat + rnd.uniform(0, 0.012), noise(0.2, 0.06, rnd), 0.22 * gain, 0.1)
        for k in range(8):
            offset = (k // 2 + (swing if k % 2 else 0)) * beat
            m.add(t0 + offset, noise(0.05, 0.012, rnd), (0.07 if k % 2 else 0.1) * gain, 0.35)


def crackle(m, seconds, rnd, gain=1.0):
    """Vinyl: a faint hiss and the odd pop."""
    m.add(0.0, noise(seconds, 1e9, rnd), 0.006 * gain)
    for _ in range(int(seconds * 7)):
        m.add(rnd.uniform(0, seconds), noise(0.004, 0.001, rnd), rnd.uniform(0.05, 0.18) * gain, rnd.uniform(-0.7, 0.7))


def lofi1(rnd):
    bpm, bars = 84, 4
    m = Mix(bar_beats(bpm, bars), wrap=True)
    bar = bar_beats(bpm, 1)
    for i, (c, root) in enumerate((("F3+A3+C4+E4", "F2"), ("E3+G3+B3+D4", "E2"), ("D3+F3+A3+C4", "D2"),
                                   ("C3+E3+G3+B3", "C2"))):
        chord(m, i * bar, bar * 0.95, c, EPIANO, 0.11, strum=0.02)
        chord(m, i * bar + bar * 0.625, bar * 0.3, c, EPIANO, 0.06, strum=0.015)
        m.add(i * bar, tone(hz(root), bar * 0.6, **BASS), 0.32)
    drums(m, bpm, bars, rnd)
    crackle(m, bar * bars, rnd)
    return m


def lofi2(rnd):
    bpm, bars = 78, 4
    m = Mix(bar_beats(bpm, bars), wrap=True)
    bar = bar_beats(bpm, 1)
    beat = bar / 4
    for i, (c, root) in enumerate((("A2+E3+G3+C4", "A1"), ("D3+F3+A3+C4", "D2"), ("G2+F3+B3+D4", "G1"),
                                   ("C3+E3+G3+B3", "C2"))):
        chord(m, i * bar, bar * 0.95, c, EPIANO, 0.1, strum=0.03)
        m.add(i * bar, tone(hz(root), bar * 0.45, **BASS), 0.3)
        m.add(i * bar + 2.5 * beat, tone(hz(root), bar * 0.2, **BASS), 0.22)
    lick = [("E5", 0.5), ("G5", 0.5), ("A5", 1), (None, 2), ("C6", 0.5), ("A5", 0.5), ("G5", 1), (None, 2)]
    t = 0.0
    for p, beats in lick * 2:
        if p:
            m.add(t, tone(hz(p), beats * beat, **DREAM_BELL), 0.07, 0.3)
        t += beats * beat
    drums(m, bpm, bars, rnd, swing=0.62, gain=0.9)
    crackle(m, bar * bars, rnd, 1.3)
    return m


def vapor1(rnd):
    bpm, bars = 68, 4
    m = Mix(bar_beats(bpm, bars), wrap=True)
    bar = bar_beats(bpm, 1)
    beat = bar / 4
    for i, (c, root) in enumerate((("Eb3+G3+Bb3+D4+F4", "Eb2"), ("C3+Eb3+G3+Bb3+D4", "C2"),
                                   ("Ab2+C3+Eb3+G3+Bb3", "Ab1"), ("Bb2+D3+F3+A3+C4", "Bb1"))):
        chord(m, i * bar, bar * 1.05, c, PAD, 0.06, detune=0.0035)
        m.add(i * bar, tone(hz(root), bar * 0.9, **dict(BASS, decay=1.8)), 0.3)
    melody = [("G5", 1.5), ("F5", 0.5), ("D5", 2), ("Eb5", 1.5), ("D5", 0.5), ("Bb4", 2), ("C5", 1), ("Eb5", 1),
              ("G5", 2), ("F5", 4)]
    t = 0.0
    for p, beats in melody:
        m.add(t, tone(hz(p), beats * beat * 1.4, **DREAM_BELL), 0.09, 0.2)
        t += beats * beat
    for b in range(bars):   # a big, slow, gated snare on 3
        m.add(b * bar + 2 * beat, noise(0.5, 0.18, rnd), 0.14, -0.1)
        m.add(b * bar, tone(110, 0.35, glide_to=40, **KICK), 0.45)
    crackle(m, bar * bars, rnd, 0.6)
    return m


def vapor2(rnd):
    bpm, bars = 72, 4
    m = Mix(bar_beats(bpm, bars), wrap=True)
    bar = bar_beats(bpm, 1)
    beat = bar / 4
    for i, (c, root) in enumerate((("D3+F#3+A3+C#4+E4", "D2"), ("B2+D3+F#3+A3+C#4", "B1"),
                                   ("G2+B2+D3+F#3+A3", "G1"), ("A2+C#3+E3+G#3+B3", "A1"))):
        chord(m, i * bar, bar * 1.05, c, PAD, 0.055, detune=0.004)
        chord(m, i * bar + beat * 1.5, beat * 1.5, c.split("+", 1)[1], EPIANO, 0.05, strum=0.04)
        m.add(i * bar, tone(hz(root), bar * 0.9, **dict(BASS, decay=1.6)), 0.28)
    drums(m, bpm, bars, rnd, swing=0.5, gain=0.7)
    crackle(m, bar * bars, rnd, 0.5)
    return m


BEDS = {"blurb_lofi1": lofi1, "blurb_lofi2": lofi2, "blurb_vapor1": vapor1, "blurb_vapor2": vapor2}


def main(music_dir, names):
    for name in names or BEDS:
        dest = os.path.join(music_dir, f"{name}.ogg")
        if os.path.exists(dest):
            print(f"{dest} is already there, leaving it")
            continue
        m = BEDS[name](random.Random(name))
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, f"{name}.wav")
            m.write(wav, mean_db=MEAN_DB, fade=0)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", "-i", wav, "-c:a", "libvorbis",
                            "-b:a", "160k", "-ar", str(SR), "-ac", "2", dest], check=True)
        print(f"Wrote {dest} ({m.n / SR:.1f} s loop)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
