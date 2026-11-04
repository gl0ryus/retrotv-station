# G|TV

A retro TV station on a Raspberry Pi 4. It plays shows, commercials and music videos from a cache of the NAS library on a
half-hour schedule, with station IDs, program guides, weather, scores, Pop-Up Video facts, emergency alerts, live space
launches and more, all drawn on the fly through one long-running mpv.

The code lives on the NAS (`/mnt/tower/.retrotv/station`, `T:\.retrotv\station` on the PC), and the Pi runs it from there.

## Running it

| | |
|---|---|
| The station | `retrotv.service` runs `retro_station.py`, a two-line launcher for `retrotv.station`. `sudo systemctl restart retrotv` after a change. |
| The writing job | `retrotv-news.timer` runs `retrotv_news.py` (for `retrotv.news`) every two hours in `~/retrotv_venv`: headline quips, show riffs, the sky, the art, Pop-Up Video facts. |
| The phone remote | http://retrotv.local/ (guide), `/schedule` (the weekly schedule), `/health` (everything the station depends on), `/watch` (the station live, below). |
| Trying a screen | `curl -X POST "http://retrotv.local/api/test?what=promo"`, where `what` is `scores`, `promo`, `launch`, `alert` or `special` (`special&kind=marathon` and the like air at the next break). |

`location.json` (the place and coordinates for the weather and alerts) sits next to `retro_station.py` and is kept out
of git. Keys live on the Pi in `~/.config/retrotv/`.

### The watch page

http://retrotv.local/watch plays the station in a browser as the panel shows it, CRT look and all, with its sound,
about a second and a half behind. The page feeds the stream to the player through Media Source (Firefox drops most
frames playing it as a plain download); tested in Firefox and Chrome, and an iPhone, which has no Media Source, can't
play it. It's made only while someone watches, for about 40% of one CPU core (`retrotv/live.py`; the sound reaches it
through `retrotv/audio_tap.py`), and only while mpv is drawing: opening the display while nobody holds it would take
it from mpv.

Reading the screen needs a copy of ffmpeg allowed to, once (and again after a major OS upgrade):

```
sudo install -d /usr/local/lib/retrotv
sudo install -o root -g gl0ryus -m 0750 /usr/bin/ffmpeg /usr/local/lib/retrotv/ffmpeg
sudo setcap cap_sys_admin=ep /usr/local/lib/retrotv/ffmpeg
```

Only that copy gets the right (`CAP_SYS_ADMIN`, which kmsgrab needs to read the panel's frame buffer), and only
`gl0ryus` can run it. `LIVE_STREAM = False` in `config.py` turns the page and the sound tap off.

## The code

Everything is in the `retrotv` package. The arrows show which modules each one builds on.

| Module | What it does |
|---|---|
| `paths` | Where the station's files are. No side effects, so the writing job uses it too |
| `config` | The station's settings and its log |
| `rules` | The broadcast day: the half-hour grid, standards (kids' fare, late-night), holidays, marathons, where breaks go |
| `cache` | The NAS index and the cache of it on the SD card and USB drive; archive.org downloads |
| `lineup` | `Show`, `MusicBlock`, and the `Lineup` that plans them from the weekly schedule |
| `player` | The one mpv that plays everything, fed over its IPC socket; restarts it when its memory grows too big |
| `overlays` | What's laid over a playing file: logo bug, progress line, lower thirds and their pictures, glitches |
| `channels` | The make-believe channels on the remote's guide |
| `station` | The playout loop: what airs next, the breaks, special screens, alerts, launches, the remote's commands. `main()` |
| `schedule`, `grid` | The weekly schedule the `/schedule` page edits, and its first draft |
| `cards`, `events`, `screens` | Everything drawn: ASS scripts over mpv lavfi URLs. `Script.card()` finishes a screen |
| `scores`, `weather`, `alerts`, `launches`, `archive`, `blurbs`, `art`, `meta` | One feature each: score card, weather with its backup, NWS alerts, live launches, archive.org, text bumps, show posters and clips, title parsing |
| `health` | The `/health` page's data: every network call is watched, the background jobs report in, bad video files go to `_for_review` |
| `jsonio` | The User-Agent, fetching JSON and saving it safely |
| `remote` | The phone remote's web server; its pages are in `retrotv/web/` |
| `live`, `audio_tap` | The watch page's stream: the screen and the player's sound, encoded while someone watches |
| `news` | The writing job (Claude, Wikipedia, the sky) |

```
paths ─ config ─ rules ─┬─ cache ─┐
                        ├─ lineup ┼─ station
             player ────┤         │
             overlays ──┴ channels┘
```

## Checking a change

On the Pi, from the station folder:

```
python3 tests/run_all.py --baseline ~/baseline     # once, before the change
python3 tests/run_all.py --against ~/baseline      # after: the suites, every screen, a simulated Friday
```

Then `sudo systemctl restart retrotv`. On the PC, `python ~/.claude/tools/pyright/check_station.py` runs the type
check against `pyright-baseline.json`. See `tests/README.md` for what each test covers.

`ROADMAP.md` lists ideas, built and not.
