"""
Synthesizes the 4.5-second station ID stings for the holiday days that don't have one (music/id_<name>.ogg,
see retrotv_events.LOOKS): a few bars on simple synth voices, levelled like the stings already there. It never
overwrites a sting that exists.

    python holiday_stings.py <music dir> [name ...]

Plain Python (a few seconds a sting) plus ffmpeg with libvorbis on the PATH. The melodies are original, except
New Year's Day's opening of "Auld Lang Syne" (traditional).
"""
import array
import math
import os
import random
import subprocess
import sys
import tempfile
import wave

SR = 48000
LENGTH = 4.5
MEAN_DB = -21.0       # as loud as the existing stings on average (-20.7 to -21.7 dBFS)...
PEAK_DB = -6.0        # ...without peaking higher than they do (-6.4 to -7.9 dBFS)
FADE = 0.15           # seconds faded at the end

NOTES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
         "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

# Voices: (partial, level) pairs, with their attack, decay (None: held) and release in seconds
SINE = dict(partials=[(1, 1.0)], attack=0.02, decay=None, release=0.1)
WHISTLE = dict(partials=[(1, 1.0), (2, 0.08)], attack=0.03, decay=None, release=0.06, vibrato=0.012)
BRASS = dict(partials=[(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.35), (5, 0.22), (6, 0.12)], attack=0.03, decay=None,
             release=0.08)
BUGLE = dict(BRASS, attack=0.05, vibrato=0.006)
ORGAN = dict(partials=[(1, 1.0), (2, 0.5), (3, 0.25), (4, 0.15)], attack=0.04, decay=None, release=0.15)
PLUCK = dict(partials=[(1, 1.0), (2, 0.6), (3, 0.35), (4, 0.2), (5, 0.1)], attack=0.003, decay=0.25, release=0.05)
BANJO = dict(PLUCK, decay=0.12)
BELL = dict(partials=[(1, 1.0), (2.0, 0.55), (2.76, 0.35), (5.4, 0.2)], attack=0.002, decay=0.7, release=0.1)
MUSIC_BOX = dict(partials=[(1, 1.0), (4, 0.18)], attack=0.002, decay=0.9, release=0.1)


def hz(name):
    """'A4' -> 440.0"""
    return 440.0 * 2 ** ((NOTES[name[:-1]] + 12 * (int(name[-1]) + 1) - 69) / 12)


def tone(freq, seconds, partials, attack, decay, release, vibrato=0.0, glide_to=None):
    """One note's samples."""
    n = int(seconds * SR)
    out, phase = [0.0] * n, 0.0
    for i in range(n):
        t = i / SR
        f = freq if glide_to is None else freq * (glide_to / freq) ** (i / n)
        if vibrato:
            f *= 1 + vibrato * math.sin(2 * math.pi * 5.5 * t) * min(1.0, t / 0.2)
        phase += 2 * math.pi * f / SR
        level = min(1.0, t / attack) * min(1.0, (n - i) / (release * SR)) * (math.exp(-t / decay) if decay else 1.0)
        out[i] = level * sum(a * math.sin(k * phase) for k, a in partials)
    return out


def noise(seconds, decay, rnd):
    """A burst of high-passed noise (shaker, snare, tambourine)."""
    n = int(seconds * SR)
    out, prev = [0.0] * n, 0.0
    for i in range(n):
        x = rnd.uniform(-1, 1)
        out[i], prev = (x - prev) * math.exp(-i / SR / decay), x
    return out


class Mix:
    def __init__(self, seconds=LENGTH, wrap=False):
        """wrap: a seamless loop, notes ringing past the end carry over to the start."""
        self.n, self.wrap = int(seconds * SR), wrap
        self.left, self.right = [0.0] * self.n, [0.0] * self.n

    def add(self, start, samples, gain, pan=0.0):
        """pan: -1 (left) .. 1 (right)."""
        gl, gr = gain * math.cos((pan + 1) * math.pi / 4), gain * math.sin((pan + 1) * math.pi / 4)
        i0 = int(start * SR)
        for k, v in enumerate(samples):
            i = (i0 + k) % self.n if self.wrap else i0 + k   # a loop's tails carry over to its start
            if i >= self.n:
                break
            self.left[i] += v * gl
            self.right[i] += v * gr

    def play(self, start, bpm, notes, voice, gain, pan=0.0, legato=0.92, strum=0.0):
        """notes: (pitch, beats) in a row; a pitch can be a chord ("C4+E4+G4", strummed by `strum` seconds a
        string) or None for a rest. Returns when the last note ends."""
        beat, t = 60 / bpm, start
        for pitch, beats in notes:
            for j, p in enumerate(pitch.split("+") if pitch else []):
                self.add(t + j * strum, tone(hz(p), beats * beat * legato, **voice), gain, pan)
            t += beats * beat
        return t

    def hits(self, times, seconds, decay, gain, rnd, pan=0.0):
        for t in times:
            self.add(t, noise(seconds, decay, rnd), gain, pan)

    def write(self, path, mean_db=MEAN_DB, fade=FADE):
        """A 16-bit WAV levelled to mean_db (never peaking over PEAK_DB), faded over the last `fade` seconds
        (0 for a loop)."""
        peak = max(max(map(abs, self.left)), max(map(abs, self.right))) or 1.0
        rms = math.sqrt((sum(v * v for v in self.left) + sum(v * v for v in self.right)) / (2 * self.n)) or 1.0
        scale = min(10 ** (mean_db / 20) / rms, 10 ** (PEAK_DB / 20) / peak) * 32767
        fade = int(fade * SR)
        frames = array.array("h")
        for i in range(self.n):
            g = scale * (min(1.0, (self.n - i) / fade) if fade else 1.0)
            frames.append(int(self.left[i] * g))
            frames.append(int(self.right[i] * g))
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(frames.tobytes())


# --- The stings ---

def newyearsday(m, rnd):
    end = m.play(0.0, 160, [("C5", 1), ("F5", 1.5), ("F5", 0.5), ("F5", 1), ("A5", 1), ("G5", 1.5), ("F5", 0.5),
                            ("G5", 1), ("A5", 1), ("F5", 2.5)], BELL, 0.35)
    m.play(0.375, 160, [("F3+A3+C4", 4), ("C3+E3+G3+Bb3", 4), ("F3+A3+C4", 2.5)], SINE, 0.12)
    for _ in range(10):   # sparkle as the year rings in
        t = rnd.uniform(end - 1.2, LENGTH - 0.4)
        m.add(t, tone(hz(rnd.choice(("C7", "F7", "A6", "C6"))), 0.4, **BELL), 0.06, rnd.uniform(-0.8, 0.8))


def mlkday(m, rnd):
    m.play(0.0, 72, [("Bb2+F3+Bb3+D4", 1.5), ("Eb3+G3+Bb3", 1), ("F3+A3+C4", 1), ("Bb2+F3+Bb3+D4", 1.8)], ORGAN, 0.12,
           legato=0.99)
    m.play(0.0, 72, [("D5", 1), ("F5", 0.5), ("G5", 1), ("A5", 1), ("Bb5", 1.8)], BELL, 0.3)


def groundhog(m, rnd):
    t = m.play(0.0, 150, [("C5", 0.5), ("E5", 0.5), ("G5", 0.5), ("C6", 0.5), ("B5", 0.5), ("G5", 0.5), ("D5", 0.5)],
               PLUCK, 0.35, legato=1.0)
    m.add(t + 0.15, tone(hz("C5"), 0.45, **dict(WHISTLE, vibrato=0), glide_to=hz("C6")), 0.3)   # up he pops
    m.play(t + 0.75, 200, [("G5", 1), ("D5", 1.6)], WHISTLE, 0.3)                              # huh?
    m.play(3.1, 120, [("C4+E4+G4+C5", 2.6)], PLUCK, 0.25, strum=0.03)
    m.add(3.1, tone(hz("E6"), 1.2, **BELL), 0.12, 0.4)


def mardigras(m, rnd):
    swing = [("F4", 0.66), ("Bb4", 0.34), ("D5", 0.66), ("F5", 0.34), ("G5", 1), ("F5", 0.5), ("D5", 0.5), ("F5", 1.5)]
    m.play(0.0, 120, swing, BRASS, 0.3, pan=0.2)
    m.play(0.0, 120, [("Bb2", 1), ("D3", 1), ("F3", 1), ("G3", 0.5), ("Ab3", 0.5), ("Bb2", 1.5)], BRASS, 0.25, pan=-0.3)
    m.hits([0.5 * k + 0.33 for k in range(6)], 0.12, 0.04, 0.25, rnd)
    m.play(3.0, 120, [("Bb3+D4+F4+Ab4", 2.6)], BRASS, 0.18, legato=0.95)
    m.hits([3.0], 0.3, 0.08, 0.35, rnd)


def aprilfools(m, rnd):
    m.add(0.0, tone(hz("C5"), 0.6, **dict(WHISTLE, vibrato=0), glide_to=hz("C6")), 0.3)
    m.add(0.7, tone(hz("A3"), 0.55, **dict(SINE, vibrato=0.08), glide_to=hz("C3")), 0.4)   # boing
    t = m.play(1.4, 133, [("Bb3", 1), ("A3", 1), ("Ab3", 1)], dict(BRASS, vibrato=0.004), 0.3)
    m.add(t, tone(hz("G3"), 1.3, **dict(BRASS, vibrato=0.03)), 0.3)                           # wah-wah-wah-waaah


def cincodemayo(m, rnd):
    tune = [("A4", 0.5), ("D5", 0.5), ("F#5", 0.5), ("A5", 1), ("G5", 0.5), ("F#5", 0.5), ("E5", 0.5), ("F#5", 0.5),
            ("D5", 2)]
    third = [("F#4", 0.5), ("B4", 0.5), ("D5", 0.5), ("F#5", 1), ("E5", 0.5), ("D5", 0.5), ("C#5", 0.5), ("D5", 0.5),
             ("A4", 2)]
    m.play(0.0, 180, tune, dict(BRASS, vibrato=0.008), 0.26, pan=0.25)
    m.play(0.0, 180, third, dict(BRASS, vibrato=0.008), 0.2, pan=-0.25)
    m.play(0.0, 180, [("D3+A3+D4+F#4", 2), ("D3+A3+D4+F#4", 1), ("A2+E3+A3+C#4", 2), ("A2+E3+A3+C#4", 1),
                      ("D3+A3+D4+F#4", 3)], PLUCK, 0.18, strum=0.015)
    m.hits([k / 6 for k in range(18)], 0.06, 0.02, 0.12, rnd, pan=0.5)          # maracas
    m.play(3.0, 180, [("D4+F#4+A4+D5", 4)], BRASS, 0.16)


def mothersday(m, rnd):
    m.play(0.0, 132, [("D5", 1), ("G5", 1), ("B5", 1), ("A5", 1), ("G5", 1), ("F#5", 1), ("G5", 3)], MUSIC_BOX, 0.32)
    m.play(0.0, 132, [("G3+B3+D4", 3), ("D3+F#3+A3+C4", 3), ("G3+B3+D4", 3)], SINE, 0.1)


def fathersday(m, rnd):
    m.play(0.0, 120, [("G4", 0.5), ("B4", 0.5), ("D5", 1), ("E5", 0.5), ("D5", 0.5), ("B4", 1), ("C5", 0.5),
                      ("A4", 0.5), ("G4", 2)], WHISTLE, 0.3)
    m.play(0.0, 120, [("G2+D3+G3+B3", 1), ("G2+D3+G3+B3", 1), ("C3+E3+G3+C4", 1), ("G2+D3+G3+B3", 1),
                      ("C3+E3+G3+C4", 1), ("D3+F#3+A3+D4", 1), ("G2+D3+G3+B3", 2)], PLUCK, 0.18, strum=0.02)


def juneteenth(m, rnd):
    m.play(0.0, 100, [("C3+G3+C4+E4", 1), ("F3+A3+C4+F4", 1), ("C3+G3+C4+E4", 1), ("G2+G3+B3+D4", 1),
                      ("C3+G3+C4+E4", 3)], ORGAN, 0.11, legato=0.98)
    m.play(0.0, 100, [("C5", 0.5), ("E5", 0.5), ("G5", 1), ("A5", 1), ("G5", 0.5), ("E5", 0.5), ("C6", 3)], BRASS, 0.26)
    m.hits([0.6, 1.8, 3.0], 0.15, 0.05, 0.18, rnd, pan=0.4)                      # tambourine on the backbeat


def _snare_roll(m, rnd, seconds):
    """A field drum roll that swells over `seconds`."""
    for k in range(int(seconds / 0.045)):
        m.add(k * 0.045, noise(0.05, 0.015, rnd), 0.05 + 0.15 * k * 0.045 / seconds, -0.2)


def memorialday(m, rnd):
    _snare_roll(m, rnd, 0.8)
    m.play(0.4, 84, [("F4", 1), ("Bb4", 0.5), ("D5", 0.5), ("F5", 1.5), ("D5", 0.5), ("Bb4", 1.5)], BUGLE, 0.3)
    m.play(0.4, 84, [("Bb2+F3+Bb3", 5.5)], SINE, 0.1)


def laborday(m, rnd):
    roll = [(p, 0.5) for p in ("C4", "E4", "G4", "C5") * 4]
    m.play(0.0, 150, roll, BANJO, 0.15, pan=-0.3, legato=1.0)
    m.play(0.0, 150, [("G4", 0.5), ("C5", 0.5), ("E5", 0.5), ("G5", 1), ("E5", 0.5), ("C5", 0.5), ("D5", 0.5),
                      ("C5", 2)], BRASS, 0.26, pan=0.2)
    m.play(3.2, 150, [("C3+G3+C4+E4", 3)], BRASS, 0.14)


def veteransday(m, rnd):
    _snare_roll(m, rnd, 0.6)
    m.play(0.5, 96, [("Bb4", 0.5), ("D5", 0.5), ("F5", 1), ("Bb5", 1), ("F5", 0.5), ("D5", 0.5), ("Bb4", 2)], BUGLE, 0.3)
    m.play(2.7, 96, [("Bb2+F3+Bb3+D4", 2.4)], BRASS, 0.12)


STINGS = {f.__name__: f for f in (newyearsday, mlkday, groundhog, mardigras, aprilfools, cincodemayo, mothersday,
                                   fathersday, juneteenth, memorialday, laborday, veteransday)}


def main(music_dir, names):
    for name in names or STINGS:
        dest = os.path.join(music_dir, f"id_{name}.ogg")
        if os.path.exists(dest):
            print(f"{dest} is already there, leaving it")
            continue
        m = Mix()
        STINGS[name](m, random.Random(name))
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, f"{name}.wav")
            m.write(wav)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", "-i", wav, "-c:a", "libvorbis",
                            "-b:a", "160k", "-ar", str(SR), "-ac", "2", dest], check=True)
        print(f"Wrote {dest}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
