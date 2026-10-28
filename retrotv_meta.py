"""
What the GL0RYUS Network can tell about a file from its name: which series it belongs to and where
it falls in that series, when it first aired, roughly what year it's from (so the commercials can
match its era), and which holiday it suits. All best effort, from NAS paths only.
"""
import datetime
import os
import re

from retrotv_cards import _clean, parse_title

MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ORD = r"(?:st|nd|rd|th)?"
ISO_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)[ ._\-](\d{1,2})[ ._\-](\d{1,2})(?!\d)")
US_DATE = re.compile(r"(?<![\d.])(\d{1,2})[ ._\-](\d{1,2})[ ._\-]((?:19|20)\d\d|\d\d)(?![\d])")
TEXT_DATE = re.compile(rf"\b{_MON}[ ._\-]+(\d{{1,2}}){_ORD}(?:[ ._\-]+\d{{1,2}}{_ORD})?,?[ ._\-]+((?:19|20)\d\d)\b", re.I)
TEXT_DATE_DMY = re.compile(rf"\b(\d{{1,2}}){_ORD}[ ._\-]+{_MON},?[ ._\-]+((?:19|20)\d\d)\b", re.I)
YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-2]\d)(?!\d)")
SEASON_EPISODE = re.compile(r"\b(?:s(\d{1,2})[ ._\-]?e(\d{1,3})|(\d{1,2})x(\d{2,3}))\b", re.I)
EPISODE_ONLY = re.compile(r"(?:^|[ ._\-])(?:e|ep|episode)[ .]?(\d{1,4})\b", re.I)

# First season's year for the bigger series, so a season number becomes a rough year (season 5 of
# a 2005 show is about 2009). Keys are series_key() values
SERIES_YEARS = {
    "scrubs": 2001, "colbertreport": 2005, "jerryspringershow": 1996, "jerryspringerppv": 1998,
    "unsolvedmysteries": 1988, "friends": 1994, "dexterslaboratory": 1996, "drewcarey": 1995,
    "drewcareyshow": 1995, "that70sshow": 1998, "latenightwithconanobrien": 1993, "conan": 2010,
    "dailyshow": 1999, "dailyshowwithjonstewart": 1999, "officeus": 2005, "spongebobsquarepants": 1999,
    "seinfeld": 1989, "malcolminthemiddle": 2000, "freshprinceofbelair": 1990, "inlivingcolor": 1990,
    "simpsons": 1989, "legendsofthehiddentemple": 1993, "howimetyourmother": 2005,
    "billnyethescienceguy": 1993, "jamiefoxxshow": 1996, "twoandahalfmen": 2003, "baywatch": 1989,
    "powerpuffgirls": 1998, "unscrewedwithmartinsargent": 2002, "ededdneddy": 1999, "futurama": 1999,
    "curbyourenthusiasm": 2000, "reno911": 2003, "itsalwayssunnyinphiladelphia": 2005, "osbournes": 2002,
    "osbournesuncensored": 2002, "bigbangtheory": 2007, "vivalabam": 2003, "wildboyz": 2003,
    "parksandrecreation": 2009, "tomgreenshowmtvseries": 1999, "kingofqueens": 1998, "mxc": 2003,
    "bullybeatdown": 2009, "mynameisearl": 2005, "joey": 2004, "battlebots": 2000, "flavoroflove": 2006,
    "heroes": 2006, "everybodyhateschris": 2005, "nitrocircus": 2009, "punkd": 2003,
    "toddmcfarlanesspawn": 1997, "freaksandgeeks": 1999, "aafthecontender": 2005, "chappellesshow": 2003,
    "americangladiators": 2008, "attackoftheshow": 2005, "screensavers": 1998, "xplay": 2003,
    "dexter": 2006, "nathanforyou": 2013, "hoganknowsbest": 2005, "totallybusted": 2003, "mtvcribs": 2000,
    "wayansbros": 1995, "extremedodgeball": 2004, "soup": 2004, "comedycentralroast": 1998,
    "saluteyourshorts": 1991,
}
SERIES_ALIASES = {"joeys01ws": "joey", "joeys02ws": "joey", "drewcareyshow": "drewcarey",
                  "dailyshowwithjonstewart": "dailyshow", "theosbournes": "osbournes"}

HALLOWEEN = re.compile(r"hallowe+n|trick.or.treat|tree ?house.of.horror|haunted|spooky|zombie|frankenstein|"
                       r"dracula|werewolf|vampire|ghostbusters|\bghosts?\b|\bwitch(es)?\b|scary|\bhorror\b|"
                       r"pumpkin|monster mash|creepshow|tales from the crypt|goosebumps|scooby", re.I)
CHRISTMAS = re.compile(r"christmas|x-?mas|\bsanta\b|grinch|rudolph the|reindeer|frosty|\bnoel\b|"
                       r"nutcracker|jingle|holiday (special|guide|party|buyers|extravaganza)|\belf\b|"
                       r"winter wonderland|mistletoe|yule|chanukah|hanukkah", re.I)
THANKSGIVING = re.compile(r"thanksgiving|turkey ?day|\bmacy'?s.{0,25}parade|(?<!scott[ ._])\bpilgrims?\b|mayflower",
                          re.I)
VALENTINES = re.compile(r"valentine'?s|\bcupid\b", re.I)
# Dated recordings from these stretches (month, first day, last day) carry the season's ads and bumps.
# Thanksgiving is always the 22nd-28th
HOLIDAY_DATES = {"halloween": (10, 20, 31), "christmas": (12, 8, 25), "thanksgiving": (11, 20, 28),
                 "valentines": (2, 10, 14)}


def _valid(year, month, day):
    try:
        d = datetime.date(year, month, day)
    except ValueError:
        return None
    return d if 1950 <= year <= datetime.date.today().year else None


def find_date(text):
    """The first plausible calendar date in a file name, as a datetime.date, or None."""
    for m in ISO_DATE.finditer(text):
        d = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return d
    for m in TEXT_DATE.finditer(text):
        d = _valid(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            return d
    for m in TEXT_DATE_DMY.finditer(text):
        d = _valid(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        if d:
            return d
    for m in US_DATE.finditer(text):
        year = int(m.group(3))
        if year < 100:
            year += 2000 if year < 50 else 1900
        d = _valid(year, int(m.group(1)), int(m.group(2)))
        if d:
            return d
    return None


def long_date(d):
    return f"{MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"


def air_date(src):
    """When an episode first aired, if its file name says (talk shows, recordings, dated tapes)."""
    return find_date(os.path.splitext(os.path.basename(src))[0])


def series_key(name):
    """A loose identity for a show name: 'The Office US', 'the.office.us' -> 'officeus'."""
    key = re.sub(r"\((?:19|20)\d\d\)|\[[^\]]*\]", "", name.lower())
    key = re.sub(r"^the\s+", "", key.strip())
    key = re.sub(r"[^a-z0-9]+", "", key)
    key = re.sub(r"(?:19|20)\d\d$", "", key) or key
    return SERIES_ALIASES.get(key, key)


def order_key(src):
    """Where an episode falls in its series, or None: [0, season, episode], [1, ordinal day] or [2, n]."""
    stem = _clean(os.path.splitext(os.path.basename(src))[0])
    m = SEASON_EPISODE.search(stem)
    if m:
        season, episode = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return [0, int(season), int(episode)]
    d = find_date(stem)
    if d:
        return [1, d.toordinal()]
    m = EPISODE_ONLY.search(stem)
    if m:
        return [2, int(m.group(1))]
    return None


def describe(src, category, media_root):
    """(name, episode, series key, order key) for a show file."""
    name, episode = parse_title(src, category, media_root)
    return name, episode, series_key(name), order_key(src)


def era_year(src, category, key=None, order=None):
    """Roughly what year a show is from: its air date, a year in its name, or its series' run."""
    d = air_date(src)
    if d:
        return d.year
    base = os.path.basename(src)
    years = YEAR.findall(base)
    if category == "movies" and years:
        return int(years[-1])
    if years:
        return int(years[0])
    folder_years = YEAR.findall(os.path.basename(os.path.dirname(src)))
    if len(folder_years) == 1:  # "Cartoon Network (1999-2007) ..." spans too much to mean anything
        return int(folder_years[0])
    first = SERIES_YEARS.get(key) if key else None
    if first:
        if order and order[0] == 0:
            return first + max(0, order[1] - 1)
        return first + 2
    return None


def commercial_year(src):
    years = YEAR.findall(os.path.basename(src))
    return int(years[0]) if years else None


def holiday(src):
    """'halloween', 'christmas', 'thanksgiving', 'valentines' or None, from the file name (or the date of a
    dated recording)."""
    base = os.path.basename(src)
    for name, pattern in (("halloween", HALLOWEEN), ("christmas", CHRISTMAS), ("thanksgiving", THANKSGIVING),
                          ("valentines", VALENTINES)):
        if pattern.search(base.replace("_", " ")):
            return name
    d = find_date(os.path.splitext(base)[0])
    if d:
        for name, (month, first, last) in HOLIDAY_DATES.items():
            if d.month == month and first <= d.day <= last:
                return name
    return None
