"""The health page in headless Firefox: live data, then a made-up bad day. Usage: health_browser_test.py http://10.0.1.163 [screenshot dir]"""
import base64, json, os, socket, subprocess, sys, tempfile, time

BASE = sys.argv[1]
SHOTS = sys.argv[2] if len(sys.argv) > 2 else tempfile.gettempdir()
PORT = 2840
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"


class Marionette:
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

    def shot(self, name):
        data = self.call("WebDriver:TakeScreenshot", {"full": True})["value"]
        path = os.path.join(SHOTS, name + ".png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        print(path)


passed = 0


def check(what, got, want=True):
    global passed
    assert got == want, f"{what}: got {got!r}, wanted {want!r}"
    passed += 1


now = time.time()
row = lambda id, group, name, status, message="", fails=0, error="": {
    "id": id, "group": group, "name": name, "provider": "Provider", "used_for": "Used for", "when_down": "What happens",
    "status": status, "message": message, "ok_at": now - 7000, "fail_at": now - 60 if error else 0, "error": error, "fails_today": fails}
BAD_DAY = {"time": now,
           "box": [{"id": "on-air", "name": "On the air", "status": "ok", "message": "Playing <b>Earl</b>"},
                   {"id": "usb", "name": "USB drive", "status": "down", "message": "Not mounted"}],
           "services": [row("nws", "Safety", "Emergency alerts", "down", "HTTP Error 500", 8, "HTTP Error 500"),
                        row("stocks", "Stocks & crypto", "Stock prices", "warn", "HTTP Error 429: Too Many Requests", 1, "HTTP Error 429"),
                        row("weather", "Weather", "Weather forecast", "ok", "", 3),
                        row("dad", "Quips & headlines", "Dad jokes", "idle", "Not used yet")],
           "bad_files": [{"id": "a1", "src": "/x", "label": "<img src=x onerror=window.pwned=1>Bad Show", "error": "unrecognized file format", "status": "moved", "when": now - 300,
                          "count": 2, "original": "/mnt/tower/.retrotv/videos/shows/A Very Long Show Name Indeed/Season 01/A Very Long Show Name Indeed - S01E01 - The First One.mkv",
                          "moved_to": "/mnt/retrotv_usb/_for_review/abc-A Very Long Show Name Indeed - S01E01.mkv", "probe": "ffprobe can't read it either: Invalid data"},
                         {"id": "b2", "src": "/y", "label": "Dial Soap", "error": "loading failed", "status": "missing", "when": now - 90000, "count": 1, "original": "https://archive.org/details/dial", "moved_to": "", "probe": ""},
                         {"id": "c3", "src": "/z", "label": "Drive", "error": "loading failed", "status": "held", "when": now - 5000, "count": 1, "original": "/z", "moved_to": "", "probe": ""}]}

profile = tempfile.mkdtemp(prefix="health-ff-")
with open(os.path.join(profile, "user.js"), "w") as f:
    f.write(f'user_pref("marionette.port", {PORT});\nuser_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
ff = subprocess.Popen([FIREFOX, "--headless", "--marionette", "--no-remote", "--profile", profile, "--width", "900", "--height", "1000"])
try:
    for _ in range(60):
        try:
            m = Marionette()
            break
        except OSError:
            time.sleep(1)
    m.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"unhandledPromptBehavior": "accept"}}})
    m.call("WebDriver:SetWindowRect", {"width": 900, "height": 1000})
    m.call("WebDriver:Navigate", {"url": BASE + "/health"})
    time.sleep(2)
    text = m.js("return document.body.textContent")
    for word in ("Emergency alerts", "Score cards", "Stock prices", "Dad jokes", "Weather forecast", "USB drive", "Bad video files"):
        check(word, word in text)
    check("guide link", m.js("return document.querySelector('nav a').getAttribute('href')"), "/")
    m.shot("health_live_wide")

    m.js("render(arguments[0])", BAD_DAY)
    check("headline", m.js("return document.getElementById('headline').textContent"), "2 things are down, 1 having trouble, 1 file to review")
    check("no injected HTML", m.js("return [window.pwned === undefined, document.querySelectorAll('#sections img, #sections b b').length]"), [True, 0])
    check("label shown as text", "<img src=x" in m.js("return document.body.textContent"))
    first = m.js("return document.querySelector('h2').textContent")
    check("problems first", first, "Needs a look")
    # An opened row stays open through a refresh
    m.js("document.querySelector('details').open = true")
    time.sleep(0.2)
    m.js("render(arguments[0])", BAD_DAY)
    check("still open", m.js("return document.querySelector('details').open"), True)
    m.shot("health_bad_wide")
    # The button posts the record's id and reloads
    m.js("window.sent = []; const real = window.fetch; window.fetch = (u, o) => { window.sent.push([u, o && o.body]); return real('/api/health'); };"
         "[...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Allow')).click()")
    time.sleep(0.5)
    check("clear", m.js("return window.sent[0]"), ["/api/bad_file/clear", '{"id":"a1"}'])
    # A station that doesn't answer
    m.js("window.fetch = () => Promise.reject(new Error('gone')); load()")
    time.sleep(0.3)
    check("lost", "Can't reach" in m.js("return document.getElementById('updated').textContent"))

    # Phone width: no sideways scrolling, even with long file names
    m.call("WebDriver:Navigate", {"url": BASE + "/health"})
    m.call("WebDriver:SetWindowRect", {"width": 450, "height": 900})
    time.sleep(1.5)
    m.js("document.body.style.width = '390px'; document.documentElement.style.width = '390px'")
    m.js("render(arguments[0])", BAD_DAY)
    time.sleep(0.2)
    print("page width at phone size:", m.js("return [window.innerWidth, document.documentElement.scrollWidth, document.querySelector('main').getBoundingClientRect().width]"))
    wide = m.js("return [...document.querySelectorAll('main *')].filter(n => n.getBoundingClientRect().right > 391).map(n => n.className || n.tagName).slice(0, 8)")
    check("nothing runs off a phone screen", wide, [])
    m.shot("health_bad_phone")
    print(f"health page checks passed: {passed}")
finally:
    ff.kill()
