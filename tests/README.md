# Tests

Run on the Pi from the station folder: `python3 tests/run_all.py`. Nothing here touches the live station's files.
Simulations work on copies, and `harness.guard` stops them deleting cache files.

**Before a big change**, record a baseline: `python3 tests/run_all.py --baseline ~/baseline`. Afterwards,
`python3 tests/run_all.py --against ~/baseline` also checks that every screen draws the same
(`snapshots.py`) and that a simulated Friday plays out the same (`sim_grid.py` with `FROZEN`).

| File | What it checks |
|---|---|
| `health_test.py` | The health page, the watched network calls, bad files, background jobs |
| `weather_test.py` | The weather refresh, its backup, and stale data on the cards |
| `scores_test.py` | The score card and team logos |
| `art_test.py` | Show posters, clips, and the marathon ad |
| `player_test.py` | The player's memory safety net, against a real mpv |
| `live_test.py` | The watch page's stream: the sound timed by ALSA's queue, MediaMTX kept up and capturing only while someone watches, late joiners decoding, only the station's stream readable from the network, and the audio tap, with a test pattern for the screen |
| `watch_browser_test.py` | The watch page playing MediaMTX's HLS in headless Firefox, picture and sound (on the PC) |
| `sync_check.py` | How far the watch page's sound is from its picture, end to end (flash-and-beep clip cut into the station, read back from MediaMTX's HLS): what `LIVE_AUDIO_SHIFT_MS` comes from |
| `playlog_test.py` | The play log: recording, trimming, `/api/plays` and the `/log` page |
| `onthisday_test.py` | This Day in Gaming, Born Today and #1 This Week: parsing, picks and their cards, from samples |
| `launch_timing.py` | When a live launch cuts in, without the network |
| `music_join_test.py` | Rejoining a music block partway through |
| `repeats_test.py` | What keeps repeats off: a show counts as aired once it's on, the 30-day wait for episodes and movies, ads once a broadcast day |
| `overrun_test.py` | A show that runs past its slot keeps its breaks and ends on the :00/:30; one that fits on shorter breaks is still squeezed in |
| `remote_test.py`, `remote_test3.py` | The phone remote against a simulated station |
| `snapshots.py` | Records or compares what the cards, screens and lower thirds draw |
| `preview_cards.py` | Renders the snapshot cards, screens and lower thirds to PNGs, as the CRT stage gets them, to look over |
| `make_glyphs.py` | Writes `retrotv/glyphs.py`, the card fonts' widths, after a font changes |
| `make_bug.py` | Draws the corner badge laid over the shows (`config.LOGO_BUG_FILE`) |
| `make_hd_shader.py` | Writes the soft CRT shader the full-size picture uses (`config.HD_SHADER`) |
| `sim_grid.py` | Simulates days on the real schedule with a fake player and a virtual clock; `RESTART_AT=HH:MM` restarts the station then, to check the day's plan is picked back up |
| `schedule_test.py` + `schedule_browser_test.py` | The schedule page, in headless Firefox (on the PC) |
| `health_browser_test.py` | The health page, in headless Firefox (on the PC) |
| `never_airs.py`, `repeat_weeks.py`, `fit_check.py`, `grid_wants.py` | Read-only questions about the saved schedule and cache |

`harness.py` is the shared setup: it finds the station, sets a setting in every module that has it, redirects data
paths to copies, and makes a run repeatable (fixed randomness, no network).
