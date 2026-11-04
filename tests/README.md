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
| `live_test.py` | The watch page's stream (shared, joinable, stopped when nobody watches) and the audio tap, with a test pattern for the screen |
| `watch_browser_test.py` | The watch page playing in headless Firefox (on the PC) |
| `launch_timing.py` | When a live launch cuts in, without the network |
| `music_join_test.py` | Rejoining a music block partway through |
| `remote_test.py`, `remote_test3.py` | The phone remote against a simulated station |
| `snapshots.py` | Records or compares what the cards, screens and lower thirds draw |
| `sim_grid.py` | Simulates days on the real schedule with a fake player and a virtual clock |
| `schedule_test.py` + `schedule_browser_test.py` | The schedule page, in headless Firefox (on the PC) |
| `health_browser_test.py` | The health page, in headless Firefox (on the PC) |
| `never_airs.py`, `repeat_weeks.py`, `fit_check.py`, `grid_wants.py` | Read-only questions about the saved schedule and cache |

`harness.py` is the shared setup: it finds the station, sets a setting in every module that has it, redirects data
paths to copies, and makes a run repeatable (fixed randomness, no network).
