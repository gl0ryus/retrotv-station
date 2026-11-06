# G|TV

A retro TV station on a Raspberry Pi 4. It plays shows, commercials and music videos from a cache of the NAS library on a
half-hour schedule, with station IDs, program guides, weather, scores, Pop-Up Video facts, emergency alerts, live space
launches and more, all drawn on the fly through one long-running mpv.

The working copy lives on the NAS (`/mnt/tower/.retrotv/station`, `T:\.retrotv\station` on the PC), where it's edited
and committed. The Pi runs a release of it from its SD card (`~/retrotv_station/current`), so the station starts and
keeps running without the NAS, and an edit reaches the air only when `deploy.py` puts it there.

## Running it

| | |
|---|---|
| The station | `retrotv.service` runs `retro_station.py`, a two-line launcher for `retrotv.station`, from the release on the SD card. |
| Deploying | `python3 /mnt/tower/.retrotv/station/deploy.py` on the Pi copies the working copy to a new release, checks the station and the writing job import from it, and restarts the station in the break after the show on now (`--now`: right away; `--rollback`: back to the release before). It keeps the last three. Waiting for a break can take most of an hour: from the PC, `ssh gl0ryus@retrotv.local 'setsid nohup python3 /mnt/tower/.retrotv/station/deploy.py > /tmp/deploy.log 2>&1 &'`. |
| A stuck station | `kill -USR1 $(systemctl show -p MainPID --value retrotv)` writes every thread's stack to `journalctl -u retrotv`, and the station carries on. |
| The writing job | `retrotv-news.timer` runs `retrotv_news.py` (for `retrotv.news`) from the same release every two hours in `~/retrotv_venv`: headline quips, show riffs, the sky, the art, Pop-Up Video facts. |
| The phone remote | http://retrotv.local/ (guide), `/schedule` (the weekly schedule), `/health` (everything the station depends on), `/watch` (the station live, below), `/log` (what aired, with repeats marked). |
| Trying a screen | `curl -X POST "http://retrotv.local/api/test?what=promo"`, where `what` is `scores`, `promo`, `launch`, `alert`, `special` or `popups` (a music video with its Pop-Up Video bubbles, then back to the show) (`special&kind=marathon` and the like air at the next break). |

`location.json` (the place and coordinates for the weather and alerts) sits next to `retro_station.py` and is kept out
of git; deploys copy it. Keys live on the Pi in `~/.config/retrotv/`.

### The day's plan

At sign-on the station plans the whole broadcast day, to sign-off (`Lineup.plan`), and keeps the plan in
`~/retrotv_lineup.json`. After a restart it picks that plan back up and joins whatever is on by the clock, the way
switching a set back on would (`Lineup.restore`, `Station.resume`), rather than choosing the day again. A block
fills from its own shows: when none of them fits the time left in its slot, the show before takes that time, its
commercial breaks stretched to the slot's close, as a real station pads a program to its slot (`Lineup._stretch`).
A show stretches by at most 30 minutes (`STRETCH_MAX`); a longer gap goes to a show from outside the block. A gap
more than three hours off (`LINEUP_HOURS`) is left open instead, and a later `plan()` goes on from there, by which
time the cache may have copied more of the block's shows.

### The picture

The station shows the panel's full 1024x600 through a soft CRT shader: a gentle curve, glow, scanlines and colour
fringe (`CRT = False` in `retrotv/cards.py`, the default since 2026-11-05). The shader is `crt-lottes-hd.glsl` in
`~/.config/mpv/shaders/`, made from the classic one by `tests/make_hd_shader.py`. `CRT = True` brings back the classic
look, everything squeezed to 342x200 first, which costs mpv about a quarter of a CPU core instead of most of one. Either way the cards
are drawn at 512x300 and scaled up: drawn at 1024x600 they couldn't keep up on air, and a starved card stalled the
player. A watchdog (`Player.check_stall`, a background job) reopens mpv's sound if playback stops moving for 15 s,
and starts mpv afresh if that doesn't free it.

### The watch page

http://retrotv.local/watch plays the station in a browser as the panel shows it, CRT look and all, with its sound.
[MediaMTX](https://github.com/bluenviron/mediamtx) serves it as HLS on port 8888, and the page plays it about 5 s
behind, with buffer to spare (under a second behind, Firefox stuttered now and then), with hls.js (MediaMTX's own copy) or, in Safari, natively. The station keeps MediaMTX running
(`retrotv/live.py`); MediaMTX runs the capture (`retrotv/capture.py`, H.264 and AAC over RTSP) only while someone
watches, for about 40% of one CPU core, and the capture waits until mpv is drawing: opening the display while nobody
holds it would take it from mpv.

Only reading the station's stream faces the network: publishing and MediaMTX's API are for the Pi alone (its RTSP and
API listen on 127.0.0.1), and its other servers are off. The sound is timed by when the panel's speakers will play it
(ALSA's own count of what it has queued), so it stays with the picture across items; `LIVE_AUDIO_SHIFT_MS` trims
what's left.

MediaMTX is one file, `~/retrotv_assets/mediamtx` (v1.21.3, linux arm64). To fetch it again:

```
cd /tmp && curl -fLO https://github.com/bluenviron/mediamtx/releases/download/v1.21.3/mediamtx_v1.21.3_linux_arm64.tar.gz \
  -O https://github.com/bluenviron/mediamtx/releases/download/v1.21.3/checksums.sha256
grep linux_arm64 checksums.sha256 | sha256sum -c - && tar xzf mediamtx_v1.21.3_linux_arm64.tar.gz mediamtx \
  && install -m 755 mediamtx ~/retrotv_assets/mediamtx
```

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
| `overlays` | What's laid over a playing file: the G/TV corner badge, progress line, lower thirds and their pictures, glitches |
| `channels` | The make-believe channels on the remote's guide |
| `station` | The playout loop: what airs next, the breaks, special screens, alerts, launches, the remote's commands. `main()` |
| `schedule`, `grid` | The weekly schedule the `/schedule` page edits, and its first draft |
| `brand`, `glyphs` | The G\|TV look from the network graphics style guide: palette, Anton and Archivo, the leaning slabs, the badge, the split ground. `glyphs` holds the fonts' measured widths |
| `cards`, `events`, `screens` | Everything drawn: ASS scripts over mpv lavfi URLs, built from `brand`'s pieces. `Script.card()` finishes a screen |
| `scores`, `weather`, `alerts`, `launches`, `archive`, `blurbs`, `art`, `meta` | One feature each: score card, weather with its backup, NWS alerts, live launches, archive.org, text bumps, show posters and clips, title parsing |
| `health` | The `/health` page's data: every network call is watched, the background jobs report in, bad video files go to `_for_review` |
| `jsonio` | The User-Agent, fetching JSON and saving it safely |
| `remote` | The phone remote's web server; its pages are in `retrotv/web/` |
| `live`, `audio_tap` | The watch page's stream: the screen and the player's sound, encoded while someone watches |
| `playlog` | The `/log` page's record: a line for everything that starts playing, two weeks of it |
| `onthisday` | Data for This Day in Gaming, Born Today and #1 This Week, gathered a little each run by the writing job |
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
