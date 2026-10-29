# G|TV Roadmap

## Feature Proposals

Ideas raised 2026-10-28. Nothing here is built yet.

### Keep it running
- **Cache on a USB SSD.** The SD card holds the OS and an 80 GB cache that turns over about every 4 days (about 20 GB of writes a day). If the card wears out, the station goes down with it. Change: point `CACHE_DIR` at the SSD.
- **Frozen-picture watchdog.** A crashed mpv is restarted (`Player._restart`). One that hangs while still running isn't noticed. Change: if `time-pos` stops moving during playback, restart the player.
- **Status panel on the phone remote.** Shows SD space, cached hours, archive.org downloads, recent warnings and Pi temperature, so checking the station doesn't need SSH.

### On the air
- **Sunday Night Movie.** A weekly movie with its own intro card, built on the Friday marathon code (`MARATHONS`, `events.marathon`).
- **Scoreboard.** Built 2026-10-29 as a live score crawl in the NEXT bar's spot (`retrotv_scores.py`): NFL, NBA, NHL and MLB, Tampa first, only while games are live.
- **Decade days.** One day a week favors shows and commercials from one decade, using the existing era matching (`ERA_YEARS`, `ERA_WEIGHT`).
- **Overnight radio.** Old-time radio from archive.org over the test pattern instead of the set turning off. The strict content filter would apply.
