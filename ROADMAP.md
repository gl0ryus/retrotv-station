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
- **Show-aware bumps.** Built 2026-10-29: from 9 PM, bumps in a show's breaks riff on that show ("re: popeye"). `retrotv_news.py` writes the riffs per show from the remote's guide and adds more once they've aired.
- **Viewer mail.** Type a message or question on the phone remote, and it airs at the next break as a bump with the station's reply. Change: a text box on `retrotv_remote.html`, the reply written when it's sent, queued as the next blurb.
- **"Play something..." on the remote.** Type "something spooky from the 80s", and Claude picks from the library index and tunes to it. Change: a remote endpoint that sends the request and a compact show list to Claude, then uses the existing play path.
- **Tonight's highlights card.** A TV Guide-style card for the evening lineup with a one-line pitch per show, written from TVmaze episode summaries so no plot is made up. Change: a new card in `retrotv_screens`.
- **Commercial riffs.** A bump right after a vintage ad riffs on it. The ad's name and year come from its file name. Change: a "commercial" source like "show", written for the ads in the cache.
- **Sign-off recap.** Before sign-off, a card that sums up the day on G|TV (what aired, the weather, the night's headlines) with a closing line. Change: the job reads the station log; the card goes before `events.sign_off`.
- **The night shift.** A running story told a few beats a night through the bumps, like the station's haunted VCR. Claude keeps the story so far in a file and writes the next part each evening.
- **1-900 psychic hotline.** A 90s late-night hotline card with tonight's reading for one star sign per break, in infomercial style. Change: a new card, readings written once a day.
- **Weather with attitude.** A one-line quip on the weather card about today's forecast. Change: one line in `cards.weather`, written when the forecast is fetched.
