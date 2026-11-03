"""Drives the schedule page with real pointer events in headless Firefox (raw Marionette, standard library only),
against tests/schedule_test.py on the Pi. Usage: schedule_browser_test.py http://10.0.1.163:8099 [screenshot dir]"""
import base64, json, os, socket, subprocess, sys, tempfile, time, urllib.request

BASE = sys.argv[1]
SHOTS = sys.argv[2] if len(sys.argv) > 2 else tempfile.gettempdir()
PORT = 2839
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

    def rect(self, selector, index=0):
        r = self.js("const n=document.querySelectorAll(arguments[0])[arguments[1]]; if(!n) return null;"
                    "n.scrollIntoView({block:'nearest'}); const r=n.getBoundingClientRect(); return [r.x,r.y,r.width,r.height]",
                    selector, index)
        assert r, f"no element {selector}[{index}]"
        return r

    def center(self, selector, index=0):
        x, y, w, h = self.rect(selector, index)
        return x + w / 2, y + h / 2

    def actions(self, *steps):
        self.call("WebDriver:PerformActions", {"actions": [{"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"},
                                                           "actions": list(steps)}]})
        self.call("WebDriver:ReleaseActions")

    def drag(self, start, end, keys=None):
        move = lambda p: {"type": "pointerMove", "x": int(p[0]), "y": int(p[1]), "duration": 40}
        mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        self.actions(move(start), {"type": "pointerDown", "button": 0}, move((start[0] + 8, start[1] + 8)), move(mid), move(end), move(end),
                     {"type": "pointerUp", "button": 0})
        time.sleep(0.15)

    def click(self, selector, index=0):
        x, y = self.center(selector, index)
        self.actions({"type": "pointerMove", "x": int(x), "y": int(y)}, {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0})
        time.sleep(0.1)

    def button(self, text):
        i = self.js("return [...document.querySelectorAll('button')].findIndex(b=>b.textContent===arguments[0])", text)
        assert i >= 0, f"no button {text}"
        self.click("button", i)

    def keys(self, selector, text):
        el = self.call("WebDriver:FindElement", {"using": "css selector", "value": selector})["value"]
        self.call("WebDriver:ElementSendKeys", {"id": list(el.values())[0], "text": text})
        time.sleep(0.1)

    def shot(self, name):
        data = self.call("WebDriver:TakeScreenshot", {"full": False})["value"]
        path = os.path.join(SHOTS, name + ".png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


passed = 0


def check(what, got, want):
    global passed
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")
    passed += 1
    print("ok:", what)


profile = tempfile.mkdtemp(prefix="sched-ff-")
with open(os.path.join(profile, "user.js"), "w") as f:
    f.write(f'user_pref("marionette.port", {PORT});\nuser_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
ff = subprocess.Popen([FIREFOX, "--headless", "--marionette", "--no-remote", "--profile", profile, "--width", "1500", "--height", "1250"])
try:
    for _ in range(60):
        try:
            m = Marionette()
            break
        except OSError:
            time.sleep(1)
    m.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"unhandledPromptBehavior": "accept"}}})
    m.call("WebDriver:SetWindowRect", {"width": 1500, "height": 1250})
    m.call("WebDriver:Navigate", {"url": BASE + "/schedule"})
    time.sleep(1.5)
    count = lambda sel: m.js("return document.querySelectorAll(arguments[0]).length", sel)
    slot_titles = lambda: m.js("return [...document.querySelectorAll('.slot')].map(s=>s.dataset.tip.split('\\n')[0]+'|'+s.parentNode.dataset.day)")
    slot_rects = lambda: m.js("return [...document.querySelectorAll('.slot')].map(s=>{const r=s.getBoundingClientRect();return [s.dataset.tip.split('\\n')[0],r.x,r.y,r.width,r.height]})")
    day_x = lambda day: m.center(f'.col[data-day="{day}"]')[0]

    def row_y(hour):
        x, y, w, h = m.rect('.col[data-day="0"]')
        return y + (hour - 9) * 2 * h / 34 + 6

    m.shot("sched_1_draft")
    check("the draft loads", count(".slot"), 161)
    check("the library lists shows", count("#library .row") > 100, True)
    check("the notice says the TV isn't following yet", "keeps its current lineup" in m.js("return document.getElementById('notice').textContent"), True)

    # Typed blocks
    m.keys("#blockline", "Mon-Fri 9am-12pm Kids Mornings")
    check("the block line previews what it read", m.js("return document.getElementById('parsed').textContent"), "Mon-Fri, 9:00 AM to 12:00 PM: Kids Mornings")
    m.keys("#blockline", "\ue007")
    check("a typed block shows on 5 days", count(".block"), 5)
    m.keys("#blockline", "\ue007")  # cleared: Enter again adds nothing
    check("the line clears", m.js("return document.getElementById('blockline').value"), "")
    m.button("Done")
    m.keys("#blockline", "Thu 6-8pm G4 Night\ue007")
    check("a second block", count(".block"), 6)
    m.button("Done")
    m.keys("#blockline", "Mon 25-30 Nope\ue007")
    check("a bad line is refused", count(".block"), 6)
    check("and says why", bool(m.js("return document.getElementById('parsed').textContent")), True)
    m.js("document.getElementById('blockline').value='';document.getElementById('blockline').dispatchEvent(new Event('input'))")

    # Not in a group: a toggle inside whichever section is picked
    names = lambda: m.js("return [...document.querySelectorAll('#library .row .name')].map(n=>n.textContent)")
    m.button("Shows")
    shows = names()
    m.click("#onlyfree")
    free = names()
    check("the funnel narrows the section", 0 < len(free) < len(shows), True)
    check("to shows not in a group", "Juiced" in free and "Friends" not in free, True)
    check("its count matches", m.js("return document.getElementById('onlyfree').textContent"), str(len(free)))
    m.button("Movies")
    check("it stays on across sections", m.js("return document.getElementById('onlyfree').getAttribute('aria-pressed')"), "true")
    m.button("Shows")
    i = free.index("Juiced")
    m.drag(m.center("#library .row", i), m.center("#newgroup"))
    check("a show put in a group leaves the list", "Juiced" not in names(), True)
    m.button("Undo")
    check("undo puts it back", "Juiced" in names(), True)
    m.click("#onlyfree")
    check("the funnel turns off", len(names()), len(shows))
    m.button("All")

    # Standards: held shows are marked in the library and warned about on the grid
    hold_of = lambda name: m.js("const r=[...document.querySelectorAll('#library .row')].find(r=>r.querySelector('.name').textContent===arguments[0]);"
                                "if(!r) return 'no row'; const h=r.querySelector('.hold'); return h?h.textContent:null", name)
    check("a late-night show is marked in the library", hold_of("The Jerry Springer Show"), "Blocked until 10pm")
    check("a kids' show is marked in the library", hold_of("SpongeBob SquarePants"), "Blocked after 9pm")
    check("an any-time show isn't", hold_of("Friends"), None)
    m.js("[...document.querySelectorAll('#library .row')].find(r=>r.querySelector('.name').textContent==='The Jerry Springer Show').scrollIntoView({block:'center'})")
    m.shot("sched_1b_labels")

    # Tooltips
    i = m.js("return [...document.querySelectorAll('#library .row')].findIndex(r=>r.querySelector('.name').textContent==='The Jerry Springer Show')")
    x, y = m.center("#library .row", i)
    m.actions({"type": "pointerMove", "x": int(x), "y": int(y)})
    time.sleep(0.6)
    tip = m.js("const t=document.querySelector('.tip'),r=t.getBoundingClientRect();return {hidden:t.hidden,text:t.textContent,px:parseFloat(getComputedStyle(t).fontSize),"
               "fits:r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}")
    m.shot("sched_1c_tip")
    check("hovering a show opens its tooltip", tip["hidden"], False)
    check("it names the show and why it's blocked", "The Jerry Springer Show" in tip["text"] and "standards hold it until 10:00 PM" in tip["text"], True)
    check("in large type", tip["px"] >= 18, True)
    check("on screen", tip["fits"], True)
    check("no browser tooltips are left", count("[title]"), 0)
    m.actions({"type": "pointerMove", "x": 900, "y": 20})
    time.sleep(0.1)
    check("moving away closes it", m.js("return document.querySelector('.tip').hidden"), True)

    # Music & specials say what they are
    m.button("Music & specials")
    check("the section explains itself", "puts them together when they air" in m.js("return document.querySelector('#library .note').textContent"), True)
    check("each row says what's in it", count("#library .row .about"), count("#library .row"))
    check("music rows give the video count", any(c.isdigit() for c in m.js("return [...document.querySelectorAll('#library .row')].find(r=>r.querySelector('.name').textContent==='Music Videos').querySelector('.about').textContent")), True)
    m.shot("sched_1d_specials")
    m.button("All")

    # A clean week
    m.button("Clear the week")
    check("the week clears", count(".slot"), 0)

    # Library to group
    m.keys("#search", "Friends")
    time.sleep(0.2)
    friends = m.center("#library .row")
    m.drag(friends, m.center("#newgroup"))
    check("a show dropped on New makes a group", m.js("return document.querySelector('#groups .card .name').textContent"), "Friends")
    m.js("const s=document.getElementById('search');s.value='';s.dispatchEvent(new Event('input'))")
    m.keys("#search", "Seinfeld")
    time.sleep(0.2)
    # Searching filters groups too, so bring the new group back by name
    m.js("const s=document.getElementById('search');s.value='Sei';s.dispatchEvent(new Event('input'))")
    sein = m.center("#library .row")
    m.js("const s=document.getElementById('search');s.value='';s.dispatchEvent(new Event('input'))")
    i = m.js("return [...document.querySelectorAll('#library .row')].findIndex(r=>r.querySelector('.name').textContent==='Seinfeld')")
    sein = m.center("#library .row", i)
    m.drag(sein, m.center("#groups .card"))
    check("a second show joins the group", m.js("return document.querySelector('#groups .card').querySelectorAll('.chip').length"), 2)
    toggle = "#groups .card [id^=overflow-]"
    check("a new group runs past its slot", m.js("return document.querySelector(arguments[0]).textContent", toggle), "Runs past its slot")
    m.click(toggle)
    check("the toggle flips it", m.js("return document.querySelector(arguments[0]).textContent", toggle), "Cut off at slot end")
    m.shot("sched_2_group")

    # Group to grid
    group = m.center("#groups .card .row .name")
    m.drag(group, (day_x(0), row_y(20)))
    titles = slot_titles()
    check("a group dropped on the grid makes a slot", len(titles), 1)
    check("at the time it was dropped", titles[0].startswith("Friends, 8:00 PM to 8:30 PM") or "8:00 PM" in titles[0], True)
    m.button("Mon–Fri")
    check("Mon–Fri repeats it", count(".slot"), 5)

    # Filling a block from its strip
    m.drag(group, m.center(".block .strip"))
    check("a group dropped on a strip fills the block's 5 days", count(".slot"), 10)
    filled = [t for t in slot_titles() if "9:00 AM to 12:00 PM" in t]
    check("each one spans the block", len(filled), 5)

    # Move, overlap, resize
    x, y = m.center(".slot")  # the first slot in the DOM: Monday's
    before = slot_titles()
    move_from = [r for r in slot_rects() if "8:00 PM" in r[0]][0]
    start = (move_from[1] + move_from[3] / 2, move_from[2] + move_from[4] / 2)
    m.drag(start, (day_x(5), row_y(13)))
    check("a slot moves to another day and time", any("1:00 PM to 1:30 PM" in t and t.endswith("|5") for t in slot_titles()), True)
    check("the old spot is empty", sum("8:00 PM" in t for t in slot_titles()), 4)

    moved = [r for r in slot_rects() if "1:00 PM" in r[0]][0]
    m.drag((moved[1] + moved[3] / 2, moved[2] + moved[4] - 3), (moved[1] + moved[3] / 2, row_y(15) ))
    check("the handle lengthens a slot", any("1:00 PM to 3:30 PM" in t for t in slot_titles()), True)

    block_slot = [r for r in slot_rects() if "9:00 AM" in r[0]][0]
    m.drag((block_slot[1] + block_slot[3] / 2, block_slot[2] + 10), (day_x(5), row_y(14)))
    titles = slot_titles()
    check("a slot dropped over another trims it", any("1:00 PM to 2:00 PM" in t and t.endswith("|5") for t in titles), True)
    check("and takes its place", any("2:00 PM to 5:00 PM" in t and t.endswith("|5") for t in titles), True)
    m.shot("sched_3_edited")

    # Undo
    m.button("Undo")
    check("undo brings the trimmed slot back", any("1:00 PM to 3:30 PM" in t for t in slot_titles()), True)

    # Warnings: a late-night show dropped at noon
    i = m.js("return [...document.querySelectorAll('#library .row')].findIndex(r=>r.querySelector('.name').textContent==='The Jerry Springer Show')")
    m.drag(m.center("#library .row", i), (day_x(6), row_y(12)))
    check("a held show at the wrong hour warns on the grid", count(".slot .warnmark"), 1)

    # Save and reload
    check("Save is on", m.js("return document.getElementById('save').disabled"), False)
    m.click("#save")
    time.sleep(1)
    check("it says Saved", m.js("return document.getElementById('status').textContent"), "Saved")
    saved = json.load(urllib.request.urlopen(BASE + "/api/schedule"))
    check("the server has version 1", saved["version"], 1)
    check("with the blocks", sorted(b["name"] for b in saved["blocks"]), ["G4 Night", "Kids Mornings"])
    check("and the slots", len(saved["slots"]), count(".slot"))
    check("the new group holds both shows", next(g["items"] for g in saved["groups"] if g["name"] == "Friends"), ["shows/Friends", "shows/Seinfeld"])
    check("the toggle is saved", next(g["overflow"] for g in saved["groups"] if g["name"] == "Friends" and len(g["items"]) == 2), False)
    m.call("WebDriver:Navigate", {"url": BASE + "/schedule"})
    time.sleep(1.5)
    check("a reload shows the same slots", count(".slot"), len(saved["slots"]))
    check("and the same blocks", count(".block"), 6)
    m.shot("sched_4_reloaded")

    # A phone
    m.call("WebDriver:SetWindowRect", {"width": 500, "height": 900})
    time.sleep(0.5)
    wide = m.js("return document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("no sideways scroll on a phone-width page", wide, False)
    m.shot("sched_5_phone")
    print(f"{passed} checks passed; screenshots in {SHOTS}")
finally:
    ff.kill()
