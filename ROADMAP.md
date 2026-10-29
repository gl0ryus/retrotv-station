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

### With the Claude API key
Raised 2026-10-29, once the key was on the Pi for the headline bumps (`retrotv_news.py`, built that day).
- **Show-aware bumps.** Bumps that riff on what just aired or what's on next. The lineup is planned 3 hours ahead, so the quips can be written in a batch ahead of time, like the headline ones. Change: give the news job the upcoming lineup and add a "lineup" source to `retrotv_blurbs`.
- **Viewer mail.** Type a message or question on the phone remote, and it airs at the next break as a bump with the station's reply. Change: a text box on `retrotv_remote.html`, the reply written when it's sent, queued as the next blurb.
- **"Play something..." on the remote.** Type "something spooky from the 80s", and Claude picks from the library index and tunes to it. Change: a remote endpoint that sends the request and a compact show list to Claude, then uses the existing play path.
- **Tonight's highlights card.** A TV Guide-style card for the evening lineup with a one-line pitch per show, written from TVmaze episode summaries so no plot is made up. Change: a new card in `retrotv_screens`.
