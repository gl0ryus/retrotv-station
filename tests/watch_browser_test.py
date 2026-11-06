"""The watch page in headless Firefox: hls.js plays MediaMTX's stream, picture and sound, keeps every frame, stays
close to live, and the page says it's live.
Usage (on the PC): watch_browser_test.py http://10.0.1.163 [screenshot dir]"""
import base64, json, os, socket, subprocess, sys, tempfile, time

BASE = sys.argv[1]
SHOTS = sys.argv[2] if len(sys.argv) > 2 else tempfile.gettempdir()
PORT = 2840
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"
passed = 0


class Marionette:   # as in health_browser_test.py
    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=60)
        self.id = 0
        self._read()

    def _read(self):
        size = b""
        while not size.endswith(b":"):
            size += self.sock.recv(1)
        need, data = int(size[:-1]), b""
        while len(data) < need:
            data += self.sock.recv(need - len(data))
        return json.loads(data)

    def call(self, name, params=None):
        self.id += 1
        body = json.dumps([0, self.id, name, params or {}]).encode()
        self.sock.sendall(str(len(body)).encode() + b":" + body)
        _, _, error, result = self._read()
        if error:
            raise RuntimeError(f"{name}: {error.get('message', error)}")
        return result

    def js(self, script, *args):
        return self.call("WebDriver:ExecuteScript", {"script": script, "args": list(args)}).get("value")


def check(what, ok):
    global passed
    assert ok, what
    passed += 1


profile = tempfile.mkdtemp(prefix="watch-ff-")
with open(os.path.join(profile, "user.js"), "w") as f:
    f.write(f'user_pref("marionette.port", {PORT});\nuser_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
            'user_pref("media.autoplay.default", 0);\n')
ff = subprocess.Popen([FIREFOX, "--headless", "--marionette", "--no-remote", "--profile", profile, "--width", "1024", "--height", "640"])
try:
    for _ in range(60):
        try:
            m = Marionette()
            break
        except OSError:
            time.sleep(1)
    m.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"unhandledPromptBehavior": "accept"}}})
    m.call("WebDriver:Navigate", {"url": BASE + "/watch"})
    state = ("const v = document.getElementById('tv'); return [v.readyState, v.currentTime, v.videoWidth, v.videoHeight,"
             " v.error && v.error.code, document.getElementById('status').textContent]")
    deadline = time.time() + 40
    first = m.js(state)
    while time.time() < deadline and not (first[0] >= 2 and first[1] > 0):
        time.sleep(1)
        first = m.js(state)
    check(f"the picture starts: {first}", first[0] >= 2 and first[1] > 0 and not first[4])
    check(f"at the panel's size: {first}", first[2] == 1024 and first[3] == 600)
    check("through hls.js", m.js("return !!(window.Hls && Hls.isSupported())"))
    frames = "const q = document.getElementById('tv').getVideoPlaybackQuality(); return [q.totalVideoFrames, q.droppedVideoFrames]"
    f0 = m.js(frames)
    time.sleep(8)
    f1 = m.js(frames)
    later = m.js(state)
    shown, dropped = (f1[0] - f1[1]) - (f0[0] - f0[1]), f1[1] - f0[1]
    check(f"and keeps moving: {first} -> {later}", later[1] - first[1] > 6 and not later[4])
    check(f"every frame shown: {shown} shown, {dropped} dropped in 8 s", shown >= 200 and dropped <= 8)
    check(f"the page says it's live: {later[5]!r}", later[5] == "Live")
    check("with sound in the stream", m.js("return document.getElementById('tv').mozHasAudio") is True)
    behind = m.js("const v = document.getElementById('tv'), b = v.buffered; return b.length ? b.end(b.length - 1) - v.currentTime : -1")
    check(f"about 5 s behind live, no more: {behind:.1f} s buffered ahead", 2 <= behind < 9)
    m.js("document.getElementById('sound').click()")
    check("sound comes on", m.js("return document.getElementById('tv').muted") is False)
    data = m.call("WebDriver:TakeScreenshot", {"full": False})["value"]
    path = os.path.join(SHOTS, "watch.png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print(path)
    print(f"watch page checks passed: {passed}")
finally:
    ff.kill()
