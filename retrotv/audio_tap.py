"""
Copies the station's sound to the live stream (retrotv.live). Not imported: ALSA's tee plugin runs this with the
player's sound piped in (config.LIVE_AUDIO_ARGS), and plays the same sound on the panel. Whole sample frames go out
as UDP datagrams to a port on this machine, where the stream's ffmpeg listens while someone watches.

The panel's sound waits on this: if it stopped reading, mpv would stall, and if it died, mpv's next write would fail.
So it never blocks (nobody listening is fine, send errors are dropped), it ignores the usual stop signals, and
it leaves when mpv closes the pipe. The player runs a copy on the SD card, so it starts even while the NAS sleeps.
Standard library only.  python3 -I -S audio_tap.py PORT FRAME_BYTES
"""
import os
import signal
import socket
import sys

CHUNK = 16384   # bytes per datagram, at most (a multiple of any frame size used here)


def main():
    port, frame = int(sys.argv[1]), int(sys.argv[2])
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    rest = b""
    while True:
        try:
            data = os.read(0, 65536)
        except InterruptedError:
            continue
        if not data:
            return
        data = rest + data
        whole = len(data) - len(data) % frame   # never split a frame, so a lost datagram can't shift the samples
        for i in range(0, whole, CHUNK):
            try:
                sock.sendto(data[i:min(i + CHUNK, whole)], ("127.0.0.1", port))
            except OSError:
                pass
        rest = data[whole:]


if __name__ == "__main__":
    main()
