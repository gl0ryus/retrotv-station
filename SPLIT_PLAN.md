# Splitting retro_station.py

`retro_station.py` is 4,300 lines, holding everything from the settings to the playout loop. Splitting it is worth
doing, as long as it happens in stages, each one tested on its own. The order below starts with the parts that are
easiest to lift out and that the simulations depend on least.

## What depends on what (measured 2026-11-03)

| Part | Lines | Needs from the rest of the file |
|---|---|---|
| Settings | 290 | nothing |
| Grid math, standards, seasons | 140 | settings |
| Media index and cache | 1,170 | settings (62 names), standards (11), break math (1) |
| Break math | 75 | settings, grid math, one cache helper (`_ffmpeg_log`) |
| Lineup, Show, MusicBlock | 520 | settings, standards, break math, `Item` |
| Item, Player | 280 | 6 settings only |
| Overlay filters, OSD text | 90 | 3 settings |
| Channels | 140 | settings, standards |
| Station, main | 1,375 | everything |

## The catch: how the simulations work

The 11 simulations in `tests/` patch names on the module, for example `rs.MANIFEST_FILE = ...` and
`rs.CacheManager._remove_file = ...`.
- **Methods:** patching a class's method keeps working after a move, because `rs.CacheManager` is still the same
  class.
- **Settings and module functions:** patching `rs.MANIFEST_FILE` stops reaching code that moved to another module,
  because that code reads its own copy.

**Fix (stage 0):** a `tests/harness.py` with one call that sets a name on every loaded `retrotv_*` module that has
it. This also gives the simulations their shared setup, including the guard against deleting real cache files.

## Stages

0. **Test harness.** The simulations switch to `harness.set_everywhere(...)` and the shared setup. Nothing in the
   station changes. About 1 hour.
1. **`retrotv_player.py`:** `Item` and `Player`, plus `PLAYER_CMD`, `MPV_SOCKET` and the memory limit, which only
   the player uses. `retro_station` re-exports `Item` and `Player`. About 30 minutes.
2. **`retrotv_overlays.py`:** `overlay_filter`, the glitch filters and the OSD helpers. About 30 minutes.
3. **`retrotv_rules.py`:** the grid math, standards, seasons and break math. These are plain functions that
   everything uses. `_ffmpeg_log` moves with the break math. About 1.5 hours.
4. **`retrotv_cache.py`:** `MediaIndexer`, `CacheManager`, `paced_copy`. This is the biggest move, and the one the
   simulations depend on most. About 2 hours.
5. **`retrotv_lineup.py`:** `Show`, `MusicBlock`, `Lineup`. About 1 hour.

What's left in `retro_station.py`: the settings, `Channels`, `Station` and `main`, roughly 1,800 lines.

## Rules for every stage

- **Moves only:** code is moved, never rewritten in the same step, so each diff is easy to check.
- **Settings:** a setting moves with its module when it's the only user; shared ones stay in `retro_station.py`
  and are passed in or imported from a small `retrotv_config.py`, decided at stage 3.
- **Checks after each stage:** pyright, every test suite on the Pi, a simulation run, and a live restart.
- **Commits:** one per stage, so any stage can be undone on its own.

## Not doing

- **No renaming.** Names and signatures stay the same through the split, and cleanups wait until it's done.
- **No splitting `Station` itself (1,375 lines).** It's large, but it's one thing: the playout loop and the remote's
  handlers. It can be looked at again once the rest is out.
